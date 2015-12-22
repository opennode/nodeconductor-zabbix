from datetime import timedelta
import logging
import sys
import warnings

from django.db import connections, DatabaseError
from django.utils import six, timezone
import pyzabbix
import requests
from requests.exceptions import RequestException
from requests.packages.urllib3 import exceptions

from nodeconductor.core.tasks import send_task
from nodeconductor.core.utils import datetime_to_timestamp
from nodeconductor.structure import ServiceBackend, ServiceBackendError, SupportedServices
from ..import models


logger = logging.getLogger(__name__)


class ZabbixLogsFilter(logging.Filter):
    def filter(self, record):
        # Mute useless Zabbix log concerning JSON-RPC server endpoint.
        if record.getMessage().startswith('JSON-RPC Server Endpoint'):
            return False

        return super(ZabbixLogsFilter, self).filter(record)

pyzabbix.logger.addFilter(ZabbixLogsFilter())


class ZabbixBackendError(ServiceBackendError):
    pass


class ZabbixBackend(object):

    def __init__(self, settings, *args, **kwargs):
        backend_class = ZabbixDummyBackend if settings.dummy else ZabbixRealBackend
        self.backend = backend_class(settings, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.backend, name)


class ZabbixBaseBackend(ServiceBackend):

    def provision(self, host):
        send_task('zabbix', 'provision')(
            host.uuid.hex,
        )

    def destroy(self, host, force=False):
        if force:
            host.delete()
            return

        # Skip stopping, because host can be deleted directly from state ONLINE
        host.schedule_deletion()
        host.save()
        send_task('zabbix', 'destroy')(
            host.uuid.hex,
        )

    def update_visible_name(self, host):
        new_visible_name = host.get_visible_name_from_scope(host.scope)
        if new_visible_name != host.visible_name:
            send_task('zabbix', 'update_visible_name')(
                host.uuid.hex,
            )


class QuietSession(requests.Session):
    """Session class that suppresses warning about unsafe TLS sessions and clogging the logs.
    Inspired by: https://github.com/kennethreitz/requests/issues/2214#issuecomment-110366218
    """
    def request(self, *args, **kwargs):
        if not kwargs.get('verify', self.verify):
            with warnings.catch_warnings():
                if hasattr(exceptions, 'InsecurePlatformWarning'):  # urllib3 1.10 and lower does not have this warning
                    warnings.simplefilter('ignore', exceptions.InsecurePlatformWarning)
                warnings.simplefilter('ignore', exceptions.InsecureRequestWarning)
                return super(QuietSession, self).request(*args, **kwargs)
        else:
            return super(QuietSession, self).request(*args, **kwargs)


class ZabbixRealBackend(ZabbixBaseBackend):
    """ Zabbix backend methods """

    DEFAULT_HOST_GROUP_NAME = 'nodeconductor'
    DEFAULT_TEMPLATES_NAMES = ('NodeConductor',)
    DEFAULT_INTERFACE_PARAMETERS = {
        'dns': '',
        'ip': '0.0.0.0',
        'main': 1,
        'port': '10050',
        'type': 1,
        'useip': 1
    }
    DEFAULT_DATABASE_PARAMETERS = {
        'host': 'localhost',
        'port': '3306',
        'name': 'zabbix',
        'user': 'admin',
        'password': ''
    }

    """
    Map resource type to description of trigger.
    Then trigger is passed as argument to service.create method of Zabbix IT service API.
    """
    DEFAULT_SERVICE_TRIGGERS = {
        'OpenStack.Instance': 'Missing data about the VM'
    }

    TREND_DELAY_SECONDS = 60 * 60 # One hour
    HISTORY_DELAY_SECONDS = 15 * 60

    def __init__(self, settings):
        self.settings = settings
        self.options = settings.options or {}
        self.host_group_name = self.options.get('host_group_name', self.DEFAULT_HOST_GROUP_NAME)
        self.templates_names = self.options.get('templates_names', self.DEFAULT_TEMPLATES_NAMES)
        self.interface_parameters = self.options.get('interface_parameters', self.DEFAULT_INTERFACE_PARAMETERS)
        self.database_parameters = self.options.get('database_parameters', self.DEFAULT_DATABASE_PARAMETERS)
        self.service_triggers = self.options.get('service_triggers', self.DEFAULT_SERVICE_TRIGGERS)

    @property
    def api(self):
        if not hasattr(self, '_api'):
            self._api = self._get_api(self.settings.backend_url,
                                      self.settings.username,
                                      self.settings.password)
        return self._api

    def sync(self):
        self._get_or_create_group_id(self.host_group_name)
        self.pull_templates()
        for name in self.templates_names:
            if not models.Template.objects.filter(name=name).exists():
                raise ZabbixBackendError('Cannot find template with name "%s".' % name)
        if not self.options.get('interface_parameters'):
            raise ZabbixBackendError('Interface parameters should not be empty.')

    def provision_host(self, host):
        interface_parameters = host.interface_parameters or self.interface_parameters
        host_group_name = host.host_group_name or self.host_group_name

        templates_ids = [t.backend_id for t in host.templates.all()]
        group_id, _ = self._get_or_create_group_id(host_group_name)

        zabbix_host_id, created = self._get_or_create_host_id(
            host_name=host.name,
            visible_name=host.visible_name,
            group_id=group_id,
            templates_ids=templates_ids,
            interface_parameters=interface_parameters,
        )

        if not created:
            logger.warning('Host with name "%s" already exists', host.name)

        host.interface_parameters = interface_parameters
        host.host_group_name = host_group_name
        host.backend_id = zabbix_host_id
        host.save()

        if host.agreed_sla:
            self.provision_service(host)

    def provision_service(self, host):
        resource_type = SupportedServices.get_name_for_model(host.scope)
        description = self.service_triggers.get(resource_type)
        if not description:
            logger.warning('Zabbix IT service is not created because trigger '
                           'description for resource with type %s is missing', resource_type)
            return
        trigger_id = self._get_trigger_id(host.backend_id, description)

        service_name = self._get_service_name(host.scope.backend_id)
        service_id, created = self.get_or_create_service(service_name, host.agreed_sla, trigger_id)

        host.service_id = service_id
        host.trigger_id = trigger_id
        host.save(update_fields=['service_id', 'trigger_id'])

    def destroy_host(self, host):
        try:
            self.api.host.delete(host.backend_id)
        except (pyzabbix.ZabbixAPIException, RequestException) as e:
            raise ZabbixBackendError('Cannot delete host with name "%s". Exception: %s' % (host.name, e))

    def update_host_visible_name(self, host):
        """ Update visible name based on host scope """
        host.visible_name = host.get_visible_name_from_scope(host.scope)
        self._update_host(host.backend_id, name=host.visible_name)
        host.save()

    def pull_templates(self):
        """ Update existing NodeConductor templates and their items """
        logger.debug('About to pull zabbix templates from backend.')
        try:
            zabbix_templates = self.api.template.get(output=['name', 'templateid'])
            zabbix_templates_ids = set([t['templateid'] for t in zabbix_templates])
            # Delete stale templates
            models.Template.objects.exclude(backend_id__in=zabbix_templates_ids).delete()
            # Update or create zabbix templates
            for zabbix_template in zabbix_templates:
                nc_template, created = models.Template.objects.get_or_create(
                    backend_id=zabbix_template['templateid'],
                    settings=self.settings,
                    defaults={'name': zabbix_template['name']})
                if not created and nc_template.name != zabbix_template['name']:
                    nc_template.name = zabbix_template['name']
                    nc_template.save()
        except pyzabbix.ZabbixAPIException as e:
            raise ZabbixBackendError('Cannot pull templates. Exception: %s' % e)
        else:
            logger.info('Successfully pulled Zabbix templates.')

        logger.debug('About to pull Zabbix items for all templates.')
        errors = []
        for template in models.Template.objects.all():
            try:
                self.pull_items(template)
            except ZabbixBackendError as e:
                logger.error(str(e))
                errors.append(e)
        if errors:
            raise ZabbixBackendError('Cannot pull template items.')
        else:
            logger.info('Successfully pulled Zabbix items.')

    def pull_items(self, template):
        """ Update existing NodeConductor items from Zabbix templates """
        logger.debug('About to pull Zabbix items for template %s', template.name)
        try:
            fields = ('itemid', 'key_', 'value_type', 'units', 'history', 'delay')
            zabbix_items = self.api.item.get(output=fields, templateids=template.backend_id)
        except pyzabbix.ZabbixAPIException as e:
            message = 'Cannot pull template items for template %s. Exception: %s' % (template.name, e)
            raise ZabbixBackendError(message)

        zabbix_items_ids = set([i['itemid'] for i in zabbix_items])
        # Delete stale template items
        template.items.exclude(backend_id__in=zabbix_items_ids).delete()

        # Update or create zabbix items
        for zabbix_item in zabbix_items:
            defaults = {
                'name': zabbix_item['key_'],
                'value_type': int(zabbix_item['value_type']),
                'units': zabbix_item['units'],
                'history': int(zabbix_item['history']),
                'delay': int(zabbix_item['delay'])
            }
            nc_item, created = template.items.get_or_create(
                backend_id=zabbix_item['itemid'], defaults=defaults)
            if not created:
                update_fields = []
                for (name, value) in defaults.items():
                    if getattr(nc_item, name) != value:
                        setattr(nc_item, name, value)
                        update_fields.append(name)
                if update_fields:
                    nc_item.save(update_fields=update_fields)
        logger.debug('Successfully pulled Zabbix items for template %s.', template.name)

    def _update_host(self, host_id, **kwargs):
        try:
            kwargs.update({'hostid': host_id})
            self.api.host.update(kwargs)
        except pyzabbix.ZabbixAPIException as e:
            raise ZabbixBackendError('Cannot update host with id "%s". Update parameters: %s. Exception: %s' % (
                                     host_id, kwargs, e))

    def _get_or_create_group_id(self, group_name):
        try:
            exists = self.api.hostgroup.exists(name=group_name)
            if not exists:
                # XXX: group creation code is not tested
                group_id = self.api.hostgroup.create({'name': group_name})['groupids'][0]
                return group_id, True
            else:
                return self.api.hostgroup.get(filter={'name': group_name})[0]['groupid'], False
        except (pyzabbix.ZabbixAPIException, IndexError, KeyError) as e:
            raise ZabbixBackendError('Cannot get or create group with name "%s". Exception: %s' % (group_name, e))

    def _get_or_create_host_id(self, host_name, visible_name, group_id, templates_ids, interface_parameters):
        """ Create Zabbix host with given parameters.

        Return (<host>, <is_created>) tuple as result.
        """
        try:
            if not self.api.host.exists(host=host_name):
                templates = [{'templateid': template_id} for template_id in templates_ids]
                host_parameters = {
                    "host": host_name,
                    "name": visible_name,
                    "interfaces": [interface_parameters],
                    "groups": [{"groupid": group_id}],
                    "templates": templates,
                }
                host = self.api.host.create(host_parameters)['hostids'][0]
                return host, True
            else:
                host = self.api.host.get(filter={'host': host_name})[0]['hostid']
                return host, False
        except (pyzabbix.ZabbixAPIException, RequestException, IndexError, KeyError) as e:
            raise ZabbixBackendError(
                'Cannot get or create host with parameters: %s. Exception: %s' % (host_parameters, str(e)))

    def get_or_create_service(self, name, agreed_sla, trigger_id):
        """ Get or create Zabbix IT service with given parameters.

        Return (<service>, <is_created>) tuple as result.
        """

        # Zabbix service API does not have service.exists method
        try:
            services = self.api.service.get(filter={'name': name})
        except (pyzabbix.ZabbixAPIException, RequestException) as e:
            message = 'Cannot get Zabbix IT service with name: %s. Exception: %s'
            raise ZabbixBackendError(message % (name, str(e)))

        if len(services) == 1:
            return services[0]['serviceid'], False
        elif len(services) > 1:
            raise ZabbixBackendError('Multiple services found with name %s' % name)

        try:
            data = self.api.service.create({
                'algorithm': 1,
                'name': name,
                'showsla': 1,
                'sortorder': 1,
                'goodsla': six.text_type(agreed_sla),
                'triggerid': trigger_id
            })
            logger.debug('Zabbix IT service with name %s has been created', name)
            return data['serviceids'][0], True
        except (pyzabbix.ZabbixAPIException, RequestException, IndexError, KeyError) as e:
            message = 'Cannot create Zabbix IT service with name: %s. Exception: %s'
            raise ZabbixBackendError(message % (name, str(e)))

    def _get_service_name(self, backend_id):
        return 'Availability of %s' % backend_id

    def _get_trigger_id(self, host_id, description):
        """
        Find trigger ID by host ID and trigger description
        """
        try:
            data = self.api.trigger.get(
                filter={'description': description},
                hostids=host_id,
                output=['triggerid'])
            return data[0]['triggerid']
        except (pyzabbix.ZabbixAPIException, RequestException, IndexError, KeyError) as e:
            message = 'No trigger for host %s and description %s'
            raise ZabbixBackendError(message % (host_id, description))

    def delete_service(self, service_id):
        try:
            self.api.service.delete(service_id)
        except (pyzabbix.ZabbixAPIException, RequestException) as e:
            message = 'Can not delete Zabbix service with ID %s. Exception: %s'
            raise ZabbixBackendError(message, service_id)

    def get_sla(self, service_id, start_time, end_time):
        try:
            data = self.api.service.getsla(
                filter={'serviceids': service_id},
                intervals={'from': start_time, 'to': end_time}
            )
            return data[service_id]['sla'][0]['sla']
        except (pyzabbix.ZabbixAPIException, RequestException, IndexError, KeyError) as e:
            message = 'Can not get Zabbix IT service SLA value for service with ID %s. Exception: %s'
            raise ZabbixBackendError(message % (service_id, e))

    def get_trigger_events(self, trigger_id, start_time, end_time):
        try:
            event_data = self.api.event.get(
                output='extend',
                objectids=trigger_id,
                time_from=start_time,
                time_till=end_time,
                sortfield=["clock"],
                sortorder="ASC")
        except (pyzabbix.ZabbixAPIException, RequestException) as e:
            message = 'Can not get events for trigger with ID %s. Exception: %s'
            raise ZabbixBackendError(message % (trigger_id, e))
        else:
            return [{'timestamp': e['clock'], 'value': e['value']} for e in event_data]

    def _get_api(self, backend_url, username, password):
        unsafe_session = QuietSession()
        unsafe_session.verify = False

        api = pyzabbix.ZabbixAPI(server=backend_url, session=unsafe_session)
        api.login(username, password)
        return api

    def get_item_stats(self, hostid, item, points):
        if item.value_type == models.Item.ValueTypes.FLOAT:
            history_table = 'history'
            trend_table = 'trends'
        elif item.value_type == models.Item.ValueTypes.INTEGER:
            # Integer value
            history_table = 'history_uint'
            trend_table = 'trends_uint'
        else:
            raise ZabbixBackendError('Cannot get statistics for non-numerical item %s' % item.name)

        history_retention_days = item.history
        history_delay_seconds = item.delay or self.HISTORY_DELAY_SECONDS
        trend_delay_seconds = self.TREND_DELAY_SECONDS

        trends_start_date = datetime_to_timestamp(timezone.now() - timedelta(days=history_retention_days))

        points = points[::-1]
        history_cursor = self._get_history(
            item.name, hostid, history_table, points[-1] - history_delay_seconds, points[0])
        trends_cursor = self._get_history(
            item.name, hostid, trend_table, points[-1] - trend_delay_seconds, points[0])

        values = []
        if points[0] > trends_start_date:
            next_value = history_cursor.fetchone()
        else:
            next_value = trends_cursor.fetchone()

        for end, start in zip(points[:-1], points[1:]):
            if start > trends_start_date:
                interval = history_delay_seconds
            else:
                interval = trend_delay_seconds

            value = None
            while True:
                if next_value is None:
                    break
                time, value = next_value
                if item.is_byte():
                    value = self.b2mb(value)

                if time <= end:
                    if end - time < interval or time > start:
                        break
                else:
                    if start > trends_start_date:
                        next_value = history_cursor.fetchone()
                    else:
                        next_value = trends_cursor.fetchone()

            values.append(value)
        return values[::-1]

    def b2mb(self, value):
        return value / 1024 / 1024

    def _get_history(self, item_key, hostid, table, start_timestamp, end_timestamp):
        """
        Execute query to zabbix db to get item values from history
        """
        query = (
            'SELECT clock time, %(value_path)s value '
            'FROM %(table)s history, items '
            'WHERE history.itemid = items.itemid '
            'AND items.key_ = "%(item_key)s" '
            'AND items.hostid = %(hostid)s '
            'AND clock > %(start_timestamp)s '
            'AND clock < %(end_timestamp)s '
            'ORDER BY clock DESC'
        )
        parameters = {
            'table': table,
            'item_key': item_key,
            'hostid': hostid,
            'start_timestamp': start_timestamp,
            'end_timestamp': end_timestamp,
            'value_path': table.startswith('history') and 'value' or 'value_avg'
        }
        query = query % parameters

        try:
            cursor = self._get_db_connection().cursor()
            cursor.execute(query)
            return cursor
        except DatabaseError as e:
            logger.exception('Can not execute query the Zabbix DB.')
            six.reraise(ZabbixBackendError, e, sys.exc_info()[2])

    def _get_db_connection(self):
        host = self.database_parameters['host']
        port = self.database_parameters['port']
        name = self.database_parameters['name']
        user = self.database_parameters['user']
        password = self.database_parameters['password']

        key = '/'.join([name, host, port])
        if key not in connections.databases:
            connections.databases[key] = {
                'ENGINE': 'django.db.backends.mysql',
                'NAME': name,
                'HOST': host,
                'PORT': port,
                'USER': user,
                'PASSWORD': password
            }
        return connections[key]


# TODO: remove dummy backend
class ZabbixDummyBackend(ZabbixBaseBackend):
    pass
