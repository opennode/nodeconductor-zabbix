from __future__ import unicode_literals

from django.apps import AppConfig
from django_fsm import signals as fsm_signals


class ZabbixConfig(AppConfig):
    name = 'nodeconductor_zabbix'
    verbose_name = "NodeConductor Zabbix"
    service_name = 'Zabbix'

    def ready(self):
        from nodeconductor.structure import SupportedServices, models as structure_models
        # structure
        from .backend import ZabbixBackend
        SupportedServices.register_backend(ZabbixBackend)

        # templates
        from nodeconductor.template import TemplateRegistry
        from nodeconductor_zabbix import template
        TemplateRegistry.register(template.HostProvisionTemplateForm)
        TemplateRegistry.register(template.ITServiceProvisionTemplateForm)
        TemplateRegistry.register(template.ZabbixServiceCreationTemplateForm)

        from . import handlers
        for index, resource_model in enumerate(structure_models.ResourceMixin.get_all_models()):

            fsm_signals.post_transition.connect(
                handlers.delete_hosts_on_scope_deletion,
                sender=resource_model,
                dispatch_uid='nodeconductor_zabbix.handlers.delete_hosts_on_scope_deletion_%s_%s' % (
                    index, resource_model.__name__)
            )
