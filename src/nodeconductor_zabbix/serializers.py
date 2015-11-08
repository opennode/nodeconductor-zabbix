from rest_framework import serializers

from nodeconductor.structure import serializers as structure_serializers
from nodeconductor.structure import SupportedServices
from . import models


class ServiceSerializer(structure_serializers.BaseServiceSerializer):

    SERVICE_TYPE = SupportedServices.Types.Zabbix
    SERVICE_ACCOUNT_FIELDS = {
        'backend_url': 'Zabbix API URL (e.g. http://example.com/zabbix/api_jsonrpc.php)',
        'username': 'Zabbix user username (e.g. admin)',
        'password': 'Zabbix user password (e.g. zabbix)',
    }
    SERVICE_ACCOUNT_EXTRA_FIELDS = {
        'host_group_name': 'Zabbix host group name for registered hosts. (default: "nodeconductor")',
        'interface_parameters': 'Default parameters for hosts interface (will be used if interface is not specified in '
                                'host). (default: {"dns": "", "ip": "0.0.0.0", "main": 1, "port": "10050", "type": 1, '
                                '"useip": 1})',
        'templates_names': 'List of Zabbix hosts templates. (default: ["nodeconductor"])',
    }

    class Meta(structure_serializers.BaseServiceSerializer.Meta):
        model = models.ZabbixService
        view_name = 'zabbix-detail'


class ServiceProjectLinkSerializer(structure_serializers.BaseServiceProjectLinkSerializer):

    class Meta(structure_serializers.BaseServiceProjectLinkSerializer.Meta):
        model = models.ZabbixServiceProjectLink
        view_name = 'zabbix-spl-detail'
        extra_kwargs = {
            'service': {'lookup_field': 'uuid', 'view_name': 'zabbix-detail'},
        }


class HostSerializer(structure_serializers.BaseResourceSerializer):
    service = serializers.HyperlinkedRelatedField(
        source='service_project_link.service',
        view_name='zabbix-detail',
        read_only=True,
        lookup_field='uuid')

    service_project_link = serializers.HyperlinkedRelatedField(
        view_name='zabbix-spl-detail',
        queryset=models.ZabbixServiceProjectLink.objects.all(),
        write_only=True)

    class Meta(structure_serializers.BaseResourceSerializer.Meta):
        model = models.Host
        view_name = 'zabbix-hosts-detail'
        fields = structure_serializers.BaseResourceSerializer.Meta.fields + (
            'visible_name', 'interface_parameters', 'host_group_name')

    def validate(self, attrs):
        if self.instance is not None:
            for name, value in attrs.items():
                setattr(self.instance, name, value)
            self.instance.clean()
        else:
            instance = models.Host(**attrs)
            instance.clean()
        return attrs
