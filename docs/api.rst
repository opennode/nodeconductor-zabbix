Services
========

List services
-------------
To get a list of services, run GET against **/api/zabbix/** as authenticated user.

Create a Zabbix service
-----------------------

To create a new Zabbix service, issue a POST with service details to **/api/zabbix/** as a customer owner.

Request parameters:

 - name - service name,
 - customer - URL of service customer,
 - settings - URL of Zabbix settings, if not defined - new settings will be created from server parameters,
 - dummy - is service dummy.

The following rules for generation of the service settings are used:

 - backend_url - Zabbix API URL (e.g. http://example.com/zabbix/api_jsonrpc.php);
 - username - Zabbix user username (e.g. admin);
 - password - Zabbix user password (e.g. zabbix);
 - host_group_name - Zabbix group name for registered hosts (default: "waldur");
 - interface_parameters - default parameters for hosts interface. (default: {"dns": "", "ip": "0.0.0.0", "main": 1, "port": "10050", "type": 1, "useip": 1});
 - templates_names - List of Zabbix hosts templates. (default: ["Waldur"]);
 - database_parameters - Zabbix database parameters. (default: {"host": "localhost", "port": "3306", "name": "zabbix", "user": "admin", "password": ""})


Example of a request:

.. code-block:: http

    POST /api/zabbix/ HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com

    {
        "name": "My Zabbix"
        "customer": "http://example.com/api/customers/2aadad6a4b764661add14dfdda26b373/",
        "backend_url": "http://example.com/zabbix/api_jsonrpc.php",
        "username": "admin",
        "password": "zabbix"
    }


Service-project links
=====================

Create and delete link
----------------------
In order to be able to provision Zabbix resources, it must first be linked to a project. To do that,
POST a connection between project and a service to **/api/zabbix-service-project-link/** as staff user or customer
owner. For example,

.. code-block:: http

    POST /api/zabbix-service-project-link/ HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com

    {
        "project": "http://example.com/api/projects/e5f973af2eb14d2d8c38d62bcbaccb33/",
        "service": "http://example.com/api/zabbix/b0e8a4cbd47c4f9ca01642b7ec033db4/"
    }

To remove a link, issue DELETE to url of the corresponding connection as staff user or customer owner.


List links
----------
To get a list of connections between a project and a Zabbix service, run GET against
**/api/zabbix-service-project-link/** as authenticated user. Note that a user can only see connections of a project
where a user has a role.


Hosts
=====

Create host
-----------
A new Zabbix host can be created by users with project administrator role, customer owner role or with
staff privilege (is_staff=True). To create a host, client must issue POST request to **/api/zabbix-hosts/** with
parameters:

 - name - host name;
 - service_project_link - url of service-project-link;
 - visible_name - host visible name (optional if scope is defined);
 - scope - optional url of related object, for example of OpenStack instance;
 - description - host description (optional);
 - interface_parameters - host interface parameters (optional);
 - host_group_name - host group name (optional);
 - templates - list of template urls (optional).

For optional fields, such as interface_parameters, host_group_name, templates if value is not specified in request, default value will be taken from service settings.

Example of a valid request:

.. code-block:: http

    POST /api/zabbix-hosts/ HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com

    {
        "name": "test host",
        "visible_name": "test host",
        "description": "sample description",
        "service_project_link": "http://example.com/api/zabbix-service-project-link/1/",
        "templates": [
            {
                "url": "http://example.com/api/zabbix-templates/99771937d38d41ceba3352b99e01b00b/"
            }
        ]
    }


Get host
--------

To get host data - issue GET request against **/api/zabbix-hosts/<host_uuid>/**.

Example rendering of the host object:

.. code-block:: javascript

    {
        "url": "http://example.com/api/zabbix-hosts/c2c29036f6e441908e5f7ca0f2441431/",
        "uuid": "c2c29036f6e441908e5f7ca0f2441431",
        "name": "a851fa75-5599-467b-be11-3d15858e8673",
        "description": "",
        "start_time": null,
        "service": "http://example.com/api/zabbix/1ffaa994d8424b6e9a512ad967ad428c/",
        "service_name": "My Zabbix",
        "service_uuid": "1ffaa994d8424b6e9a512ad967ad428c",
        "project": "http://example.com/api/projects/8dc8f34f27ef4a4f916184ab71e178e3/",
        "project_name": "Default",
        "project_uuid": "8dc8f34f27ef4a4f916184ab71e178e3",
        "customer": "http://example.com/api/customers/7313b71bd1cc421ea297dcb982e40260/",
        "customer_name": "Alice",
        "customer_native_name": "",
        "customer_abbreviation": "",
        "project_groups": [],
        "tags": [],
        "error_message": "",
        "resource_type": "Zabbix.Host",
        "state": "Online",
        "created": "2015-10-16T11:18:59.596Z",
        "backend_id": "2535",
        "visible_name": "a851fa75-5599-467b-be11-3d15858e8673",
        "interface_parameters": "{u'ip': u'0.0.0.0', u'useip': 1, u'dns': u'', u'main': 1, u'type': 1, u'port': u'10050'}",
        "host_group_name": "waldur",
        "scope": null,
        "templates": [
            {
                "url": "http://example.com/api/zabbix-templates/99771937d38d41ceba3352b99e01b00b/",
                "uuid": "99771937d38d41ceba3352b99e01b00b",
                "name": "Template Waldur Instance",
                "items": [
                    {
                        "name": "Host name of zabbix_agentd running",
                        "key": "agent.hostname"
                    },
                    {
                        "name": "Agent ping",
                        "key": "agent.ping"
                    },
                    {
                        "name": "Version of zabbix_agent(d) running",
                        "key": "agent.version"
                    }
                ]
            }
        ],
        "agreed_sla": 91.5,
        "actual_sla": 100.0
    }


Delete host
-----------

To delete host - issue DELETE request against **/api/zabbix-hosts/<host_uuid>/**.


Host statistics
---------------

URL: **/api/zabbix-hosts/<host_uuid>/items_history/**

Request should specify datetime points and items. There are two ways to define datetime points for historical data.

1. Send *?point=<timestamp>* parameter that can list. Response will contain historical data for each given point in the
   same order.
2. Send *?start=<timestamp>*, *?end=<timestamp>*, *?points_count=<integer>* parameters.
   Result will contain <points_count> points from <start> to <end>.

Also you should specify one or more name of host template items, for example 'openstack.instance.cpu_util'

Response is list of datapoint, each of which is dictionary with following fields:
 - 'point' - timestamp;
 - 'value' - values are converted from bytes to megabytes, if possible;
 - 'item' - name of host template item.

Example response:

.. code-block:: javascript

    [
        {
            "point": 1441935000,
            "value": 0.1393,
            "item": "openstack.instance.cpu_util"
        },
        {
            "point": 1442163600,
            "value": 10.2583,
            "item": "openstack.instance.cpu_util"
        },
        {
            "point": 1442392200,
            "value": 20.3725,
            "item": "openstack.instance.cpu_util"
        },
        {
            "point": 1442620800,
            "value": 30.3426,
            "item": "openstack.instance.cpu_util"
        },
        {
            "point": 1442849400,
            "value": 40.3353,
            "item": "openstack.instance.cpu_util"
        },
        {
            "point": 1443078000,
            "value": 50.3574,
            "item": "openstack.instance.cpu_util"
        }
    ]


Aggregated host statistics
--------------------------

URL: **/api/zabbix-hosts/aggregated_items_history/**

Request should specify host filtering parameters, datetime points, and items.
Host filtering parameters are the same as for */api/resources/* endpoint.
Input/output format is the same as for **/api/zabbix-hosts/<host_uuid>/items_history/** endpoint.

Example request and response:

.. code-block:: http

    GET /api/zabbix-hosts/aggregated_items_history/?point=1436094582&point=1443078000&customer_uuid=7313b71bd1cc421ea297dcb982e40260&item=openstack.instance.cpu_util HTTP/1.1
    Content-Type: application/json
    Accept: application/json
    Authorization: Token c84d653b9ec92c6cbac41c706593e66f567a7fa4
    Host: example.com

    [
        {
            "point": 1436094582,
            "item": "openstack.instance.cpu_util",
            "value": 40.3353
        },
        {
            "point": 1443078000,
            "item": "openstack.instance.cpu_util",
            "value": 50.3574
        }
    ]


IT services and SLA calculation
===============================
The status of `IT Service <https://www.zabbix.com/documentation/2.0/manual/it_services/>`_
is affected by the status of its trigger.

List triggers
-------------
Triggers are available as Zabbix service properties under */api/zabbix-triggers/* endpoint.
You may filter triggers by template by passing its ID as GET query parameter.

.. code-block:: javascript

    [
        {
            "url": "http://example.com/api/zabbix-triggers/3e19dc77279d42ccb6c2e21f2a2f6ced/",
            "uuid": "3e19dc77279d42ccb6c2e21f2a2f6ced",
            "name": "Host name of zabbix_agentd was changed on {HOST.NAME}",
            "template": "http://example.com/api/zabbix-templates/8780ebf60ac448c4a3d083f0c71106ff/"
        }
    ]

List IT services
----------------
IT services are available as Zabbix service properties under */api/zabbix-itservices/* endpoint.

.. code-block:: javascript

 
   {
       "url": "http://example.com/api/zabbix-itservices/db075c3c8d494f5886fc0f6686390624/",
       "uuid": "db075c3c8d494f5886fc0f6686390624",
       "name": "example-it-service",
       "description": "",
       "start_time": null,
       "service": "http://example.com/api/zabbix/18931f568b344b3fbc8d048cbe806ff6/",
       "service_name": "TST Zabbix",
       "service_uuid": "18931f568b344b3fbc8d048cbe806ff6",
       "project": "http://example.com/api/projects/f43171f9374442b78ce7e842effea0aa/",
       "project_name": "TST PaaS project",
       "project_uuid": "f43171f9374442b78ce7e842effea0aa",
       "customer": "http://example.com/api/customers/691f62f8d89e44d6a69d02b3b5334f7c/",
       "customer_name": "TST Paas customer",
       "customer_native_name": "",
       "customer_abbreviation": "",
       "project_groups": [],
       "tags": [],
       "error_message": "",
       "resource_type": "Zabbix.ITService",
       "state": "Online",
       "created": "2016-02-22T06:56:37.393Z",
       "backend_id": "1590",
       "access_url": null,
       "host": "http://example.com/api/zabbix-hosts/f8e46835e4654410915bd24c2f784876/",
       "algorithm": "problem, if at least one child has a problem",
       "sort_order": 1,
       "agreed_sla": "99.0000",
       "actual_sla": 100.0,
       "trigger": "http://example.com/api/zabbix-triggers/765b979ec9b34038b1b214f6be2bb0b5/",
       "trigger_name": "PostgreSQL is not available",
       "is_main": true
   }
 

SLA periods
-----------

IT services list is displaying current SLAs for each of the items.
By default, SLA period is set to the current month. To change the period pass it as a query argument:

- ?period=YYYY-MM - return a list with SLAs for a given month
- ?period=YYYY - return a list with SLAs for a given year

If SLA for the given period is not known or not present, it will be shown as **null** in the response.

SLA events
----------

IT service SLAs are connected with occurrences of events. To get a list of such events issue a GET request to
*/zabbix-itservices/<service_uuid>/events/*. Optionally period can be supplied using the format defined above.

The output contains a list of states and timestamps when the state was reached. The list is sorted in descending order
by the timestamp.

Example output:

.. code-block:: javascript

    [
        {
            "timestamp": 1418043540,
            "state": "U"
        },
        {
            "timestamp": 1417928550,
            "state": "D"
        },
        {
            "timestamp": 1417928490,
            "state": "U"
        }
    ]
