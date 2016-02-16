import datetime
from decimal import Decimal
import logging

from celery import shared_task

from nodeconductor.core.tasks import save_error_message, transition

from .backend import ZabbixBackendError
from .models import Host, SlaHistory, ITService


logger = logging.getLogger(__name__)


@shared_task(name='nodeconductor.zabbix.provision_host')
def provision_host(host_uuid):
    begin_host_provision.apply_async(
        args=(host_uuid,),
        link=set_host_online.si(host_uuid),
        link_error=set_host_erred.si(host_uuid)
    )


@shared_task(name='nodeconductor.zabbix.destroy_host')
def destroy_host(host_uuid):
    begin_host_destroy.apply_async(
        args=(host_uuid,),
        link=delete_host.si(host_uuid),
        link_error=set_host_erred.si(host_uuid),
    )


@shared_task(name='nodeconductor.zabbix.update_visible_name')
def update_visible_name(host_uuid):
    host = Host.objects.get(uuid=host_uuid)
    backend = host.get_backend()
    backend.update_host_visible_name(host)


@shared_task
@transition(Host, 'begin_provisioning')
@save_error_message
def begin_host_provision(host_uuid, transition_entity=None):
    host = transition_entity
    backend = host.get_backend()
    backend.provision_host(host)


@shared_task
@transition(Host, 'begin_deleting')
@save_error_message
def begin_host_destroy(host_uuid, transition_entity=None):
    host = transition_entity
    backend = host.get_backend()
    backend.destroy_host(host)


@shared_task
@transition(Host, 'set_online')
def set_host_online(host_uuid, transition_entity=None):
    pass


@shared_task
@transition(Host, 'set_erred')
def set_host_erred(host_uuid, transition_entity=None):
    pass


@shared_task
def delete_host(host_uuid):
    Host.objects.get(uuid=host_uuid).delete()


@shared_task(name='nodeconductor.zabbix.update_sla')
def update_sla(sla_type):
    if sla_type not in ('yearly', 'monthly'):
        logger.error('Requested unknown SLA type: %s' % sla_type)
        return

    dt = datetime.datetime.now()

    if sla_type == 'yearly':
        period = dt.year
        start_time = int(datetime.datetime.strptime('01/01/%s' % dt.year, '%d/%m/%Y').strftime("%s"))
    else:  # it's a monthly SLA update
        period = '%s-%s' % (dt.year, dt.month)
        month_start = datetime.datetime.strptime('01/%s/%s' % (dt.month, dt.year), '%d/%m/%Y')
        start_time = int(month_start.strftime("%s"))

    end_time = int(dt.strftime("%s"))

    for itservice in ITService.objects.all():
        update_itservice_sla.delay(itservice.pk, period, start_time, end_time)


@shared_task
def update_itservice_sla(itservice_id, period, start_time, end_time):
    logger.debug('Updating SLAs for IT Service %s. Period: %s, start_time: %s, end_time: %s',
                 itservice_id, period, start_time, end_time)

    try:
        itservice = ITService.objects.get(pk=itservice_id)
    except ITService.DoesNotExist:
        logger.warning('Unable to update SLA for IT Service %s, because it is gone', itservice_id)
        return

    backend = itservice.settings.get_backend()

    try:
        current_sla = backend.get_sla(itservice.backend_id, start_time, end_time)
        entry, _ = SlaHistory.objects.get_or_create(itservice=itservice, period=period)
        entry.value = Decimal(current_sla)
        entry.save()

        if itservice.backend_trigger_id:
            # update connected events
            events = backend.get_trigger_events(itservice.backend_trigger_id, start_time, end_time)
            for event in events:
                event_state = 'U' if int(event['value']) == 0 else 'D'
                entry.events.get_or_create(
                    timestamp=int(event['timestamp']),
                    state=event_state
                )

        if itservice.field_name and itservice.host and itservice.host.scope:
            status = None
            try:
                status = backend.get_itservice_status(itservice.backend_id) == '0'
            except Exception:
                pass
            itservice.host.scope.monitoring.update_or_create(
                name=itservice.field_name,
                defaults={'value': status}
            )

    except ZabbixBackendError as e:
        logger.warning('Unable to update SLA for IT Service %s. Reason: %s', itservice.id, e)


@shared_task(name='nodeconductor.zabbix.provision_itservice')
def provision_itservice(itservice_uuid):
    begin_itservice_provision.apply_async(
        args=(itservice_uuid,),
        link=set_itservice_online.si(itservice_uuid),
        link_error=set_itservice_erred.si(itservice_uuid)
    )


@shared_task
@transition(ITService, 'begin_provisioning')
@save_error_message
def begin_itservice_provision(itservice_uuid, transition_entity=None):
    itservice = transition_entity
    backend = itservice.get_backend()
    backend.provision_itservice(itservice)


@shared_task(name='nodeconductor.zabbix.destroy_itservice')
def destroy_itservice(itservice_uuid):
    begin_itservice_destroy.apply_async(
        args=(itservice_uuid,),
        link=delete_itservice.si(itservice_uuid),
        link_error=set_itservice_erred.si(itservice_uuid),
    )


@shared_task
@transition(ITService, 'begin_deleting')
@save_error_message
def begin_itservice_destroy(itservice_uuid, transition_entity=None):
    itservice = transition_entity
    backend = itservice.get_backend()
    backend.delete_service(itservice.backend_id)


@shared_task
def delete_itservice(itservice_uuid):
    ITService.objects.get(uuid=itservice_uuid).delete()


@shared_task
@transition(ITService, 'set_online')
def set_itservice_online(host_uuid, transition_entity=None):
    pass


@shared_task
@transition(ITService, 'set_erred')
def set_itservice_erred(host_uuid, transition_entity=None):
    pass
