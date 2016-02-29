import datetime

from rest_framework import status, test
from rest_framework.reverse import reverse

from nodeconductor.core.utils import datetime_to_timestamp
from nodeconductor.monitoring.models import ResourceSla, ResourceItem, ResourceState
from nodeconductor.openstack.tests.factories import InstanceFactory
from nodeconductor.openstack.tests.factories import OpenStackServiceProjectLinkFactory
from nodeconductor.structure.tests.factories import UserFactory
from nodeconductor_zabbix.utils import format_period


class BaseMonitoringTest(test.APITransactionTestCase):
    def setUp(self):
        self.link = OpenStackServiceProjectLinkFactory()
        self.vm1 = InstanceFactory(service_project_link=self.link)
        self.vm2 = InstanceFactory(service_project_link=self.link)
        self.vm3 = InstanceFactory(service_project_link=self.link)
        self.client.force_authenticate(UserFactory(is_staff=True))


class SlaTest(BaseMonitoringTest):
    def setUp(self):
        super(SlaTest, self).setUp()

        today = datetime.date.today()
        period = format_period(today)

        invalid_date = today + datetime.timedelta(days=100)
        invalid_period = format_period(invalid_date)

        ResourceSla.objects.create(scope=self.vm1, period=period, value=90)
        ResourceSla.objects.create(scope=self.vm1, period=invalid_period, value=70)
        ResourceSla.objects.create(scope=self.vm2, period=period, value=80)

    def test_sorting(self):
        response = self.client.get(InstanceFactory.get_list_url(), data={'o': 'actual_sla'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(3, len(response.data))
        self.assertEqual([None, 80, 90], [item['actual_sla'] for item in response.data])

    def test_filtering(self):
        response = self.client.get(InstanceFactory.get_list_url(), data={'actual_sla': 80})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(1, len(response.data))

    def test_actual_sla_serializer(self):
        response = self.client.get(InstanceFactory.get_url(self.vm1))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(90, response.data['actual_sla'])

    def test_empty_monitoring_items_serializer(self):
        response = self.client.get(InstanceFactory.get_url(self.vm2))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(None, response.data['monitoring_items'])


class EventsTest(BaseMonitoringTest):
    def setUp(self):
        super(EventsTest, self).setUp()

        today = datetime.date.today()
        timestamp = datetime_to_timestamp(today)
        period = format_period(today)

        ResourceState.objects.create(scope=self.vm1, period=period, timestamp=timestamp, state=True)
        ResourceState.objects.create(scope=self.vm2, period=period, timestamp=timestamp, state=False)

    def test_scope_filter(self):
        url = reverse('zabbix-event-list')

        vm1_url = InstanceFactory.get_url(self.vm1)
        response = self.client.get(url, data={'scope': vm1_url})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual('U', response.data[0]['state'])

        vm2_url = InstanceFactory.get_url(self.vm2)
        response = self.client.get(url, data={'scope': vm2_url})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual('D', response.data[0]['state'])

    def test_period_filter(self):
        url = reverse('zabbix-event-list')

        today = datetime.date.today()
        invalid_date = today + datetime.timedelta(days=100)
        invalid_period = format_period(invalid_date)

        response = self.client.get(url, data={'period': invalid_period})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(0, len(response.data))


class ItemTest(BaseMonitoringTest):
    def setUp(self):
        super(ItemTest, self).setUp()

        ResourceItem.objects.create(scope=self.vm1, name='application_status', value=1)
        ResourceItem.objects.create(scope=self.vm2, name='application_status', value=0)

        ResourceItem.objects.create(scope=self.vm1, name='ram_usage', value=10)
        ResourceItem.objects.create(scope=self.vm2, name='ram_usage', value=20)

    def test_serializer(self):
        response = self.client.get(InstanceFactory.get_url(self.vm1))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({'application_status': 1, 'ram_usage': 10},
                         response.data['monitoring_items'])

    def test_filter(self):
        response = self.client.get(InstanceFactory.get_list_url(),
                                   data={'monitoring__application_status': 1})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(1, len(response.data))

    def test_sorter(self):
        response = self.client.get(InstanceFactory.get_list_url(),
                                   data={'o': 'monitoring__application_status'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        values = []
        for item in response.data:
            if not item['monitoring_items']:
                values.append(None)
            else:
                values.append(item['monitoring_items']['application_status'])
        self.assertEqual([None, 0, 1], values)
