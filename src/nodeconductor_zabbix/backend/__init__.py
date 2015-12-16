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
from nodeconductor.structure import ServiceBackend, ServiceBackendError
from ..import models


logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)



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
    DEFAULT_INTERFACE_PARAMTERS = {
        'dns': '',
        'ip': '0.0.0.0',
        'main': 1,
        'port': '10050',
        'type': 1,
        'useip': 1
    }

    TREND_DELAY_SECONDS = 60 * 60 # One hour
    HISTORY_DELAY_SECONDS = 15 * 60

    def __init__(self, settings):
        self.settings = settings
        self.options = settings.options or {}
        self.host_group_name = self.options.get('host_group_name', self.DEFAULT_HOST_GROUP_NAME)
        self.templates_names = self.options.get('templates_names', self.DEFAULT_TEMPLATES_NAMES)
        self.interface_parameters = self.options.get('interface_parameters', self.DEFAULT_INTERFACE_PARAMTERS)

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
        if 'interface_parameters' in self.options and self.options['interface_parameters']:
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
            zabbix_items = self.api.item.get(output=['itemid', 'key_'], templateids=template.backend_id)
            zabbix_items_ids = set([i['itemid'] for i in zabbix_items])
            # Delete stale template items
            template.items.exclude(backend_id__in=zabbix_items_ids).delete()
            # Update or create zabbix items
            for zabbix_item in zabbix_items:
                defaults = {'name': zabbix_item['key_']}
                nc_item, created = template.items.get_or_create(
                    backend_id=zabbix_item['itemid'], defaults=defaults)
                if not created and (nc_item.name != zabbix_item['key_'] or nc_item.template != template):
                    nc_item.name = zabbix_item['name']
                    nc_item.template = template
                    nc_item.save()
        except pyzabbix.ZabbixAPIException as e:
            raise ZabbixBackendError('Cannot pull template items for template %s. Exception: %s' % (template.name, e))
        else:
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

    def _get_template_id(self, template_name):
        try:
            return self.api.template.get(filter={'name': 'Template NodeConductor Instance'})[0]['templateid']
        except (IndexError, KeyError, pyzabbix.ZabbixAPIException) as e:
            raise ZabbixBackendError('Cannot get template with name "%s". Exception: %s' % (template_name, e))

    def _get_host_unique_name(self, host):
        return host.uuid.hex

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

    def _get_api(self, backend_url, username, password):
        unsafe_session = QuietSession()
        unsafe_session.verify = False

        api = pyzabbix.ZabbixAPI(server=backend_url, session=unsafe_session)
        api.login(username, password)
        return api

    def get_item_stats(self, hostid, item_key, start_timestamp, end_timestamp, segments_count):
        item = self._get_item(item_key, hostid)
        if item['value_type'] == '0':
            # Floating value
            history_table = 'history'
            trend_table = 'trends'
        elif item['value_type'] == '3':
            # Integer value
            history_table = 'history_uint'
            trend_table = 'trends_uint'
        else:
            raise ZabbixBackendError('Cannot get statistics for non-numerical item %s', item_key)
        convert_to_mb = item['units'] == 'B'

        history_retention_days = int(item['history'])
        history_delay_seconds = int(item['delay']) or self.HISTORY_DELAY_SECONDS
        trend_delay_seconds = self.TREND_DELAY_SECONDS

        itemid = item['itemid']
        trends_start_date = datetime_to_timestamp(timezone.now() - timedelta(days=history_retention_days))

        history_cursor = self._get_history(
            itemid, history_table, start_timestamp - history_delay_seconds, end_timestamp)
        trends_cursor = self._get_history(
            itemid, trend_table, start_timestamp - trend_delay_seconds, end_timestamp)

        interval = ((end_timestamp - start_timestamp) / segments_count)
        points = range(end_timestamp, start_timestamp - interval, -interval)

        segment_list = []
        if points[1] > trends_start_date:
            next_value = history_cursor.fetchone()
        else:
            next_value = trends_cursor.fetchone()

        for end, start in zip(points[:-1], points[1:]):
            segment = {'from': start, 'to': end}
            if start > trends_start_date:
                interval = history_delay_seconds
            else:
                interval = trend_delay_seconds

            while True:
                if next_value is None:
                    break
                time, value = next_value
                if convert_to_mb:
                    value /= 1.0 * 1024 * 1024

                if time <= end:
                    if end - time < interval or time > start:
                        segment['value'] = value
                    break
                else:
                    if start > trends_start_date:
                        next_value = history_cursor.fetchone()
                    else:
                        next_value = trends_cursor.fetchone()

            segment_list.append(segment)
        return segment_list

    def _get_item(self, item_key, hostid):
        """
        Find item metadata by it's key and hostid
        """
        try:
            return self.api.item.get(search={'key_': item_key},
                                     hostids=hostid,
                                     limit=1,
                                     output='extend')[0]
        except (pyzabbix.ZabbixAPIException, RequestException, IndexError) as e:
            raise ZabbixBackendError(
                'Cannot get item: %s. Exception: %s' % (item_key, e))

    def _get_history(self, itemid, table, start_timestamp, end_timestamp):
        """
        Execute query to zabbix db to get item values from history
        """
        query = (
            'SELECT clock time, %(value_path)s value '
            'FROM %(table)s '
            'WHERE itemid = %(itemid)s '
            'AND clock > %(start_timestamp)s '
            'AND clock < %(end_timestamp)s '
            'ORDER BY clock DESC'
        )
        parameters = {
            'table': table,
            'itemid': itemid,
            'start_timestamp': start_timestamp,
            'end_timestamp': end_timestamp,
            'value_path': table.startswith('history') and 'value' or 'value_avg'
        }
        query = query % parameters

        try:
            cursor = connections['zabbix'].cursor()
            cursor.execute(query)
            return cursor
        except DatabaseError as e:
            logger.exception('Can not execute query the Zabbix DB.')
            six.reraise(ZabbixBackendError, e, sys.exc_info()[2])


# TODO: remove dummy backend
class ZabbixDummyBackend(ZabbixBaseBackend):
    pass
