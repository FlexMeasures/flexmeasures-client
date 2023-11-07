.. These are examples of badges you might want to add to your README:
   please update the URLs accordingly

    .. image:: https://api.cirrus-ci.com/github/<USER>/flexmeasures-client.svg?branch=main
        :alt: Built Status
        :target: https://cirrus-ci.com/github/<USER>/flexmeasures-client
    .. image:: https://readthedocs.org/projects/flexmeasures-client/badge/?version=latest
        :alt: ReadTheDocs
        :target: https://flexmeasures-client.readthedocs.io/en/stable/
    .. image:: https://img.shields.io/coveralls/github/<USER>/flexmeasures-client/main.svg
        :alt: Coveralls
        :target: https://coveralls.io/r/<USER>/flexmeasures-client
    .. image:: https://img.shields.io/pypi/v/flexmeasures-client.svg
        :alt: PyPI-Server
        :target: https://pypi.org/project/flexmeasures-client/
    .. image:: https://img.shields.io/conda/vn/conda-forge/flexmeasures-client.svg
        :alt: Conda-Forge
        :target: https://anaconda.org/conda-forge/flexmeasures-client
    .. image:: https://pepy.tech/badge/flexmeasures-client/month
        :alt: Monthly Downloads
        :target: https://pepy.tech/project/flexmeasures-client
    .. image:: https://img.shields.io/twitter/url/http/shields.io.svg?style=social&label=Twitter
        :alt: Twitter
        :target: https://twitter.com/flexmeasures-client

.. image:: https://img.shields.io/badge/-PyScaffold-005CA0?logo=pyscaffold
    :alt: Project generated with PyScaffold
    :target: https://pyscaffold.org/
.. image::https://img.shields.io/badge/python-3.9+-blue.svg
    :target: https://www.python.org/downloads/

|

===================
FlexMeasures Client
===================


The FlexMeasures Client provides a python package to connect to a `FlexMeasures <https://github.com/FlexMeasures/flexmeasures>`_ server to manage flexible assets.

The Flexmeasures Client package provides functionality for authentication, posting sensor data, triggering schedules and retrieving schedules from a FlexMeasures instance through the API.

As the Flexmeasures Client is still in active development and on version 0.1 it should be considered in beta.


Getting Started
===============

To get started using the FlexMeasures Client package first an account needs to be registered with a FlexMeasures instance or a local FlexMeasures instance needs to be created. Registring a to a FlexMeasures instance can be done through `Seita BV <https://seita.nl/>`_. To create a local instance of FlexMeasures follow the `FlexMeasures documentation <https://flexmeasures.readthedocs.io/en/latest/index.html>`_.

In this example we are connecting to ``localhost:5000``, To connect to a different host add the host in the initialization of the client.

Install using ``pip``::

    pip install flexmeasures-client

Initialization and Authentication::

    from client import FlexMeasuresClient
    client = FlexMeasuresClient(email="email@email.com", password="pw")

Retrieve available assets and sensors::

    assets = await client.get_assets()
    sensors = await client.get_sensors()

Post a measurement from a sensor::

    await client.post_measurements(
            sensor_id=<sensor_id>, # integer
            start="2023-03-26T10:00+02:00", #iso datetime
            duration="PT6H", # iso timedelta
            values=[1,2,3,4], # list
            unit="kWh",
            entity_address=<sensor_entity_address>, # string
        )

With FlexMeasures a schedule can be requested to optimize at what time the flexible assets can be activated to optimize for price of energy or emissions.

The calculation of the schedule can take some time depending on the complexity of the calculations. A polling function is used to check if a schedule is available after triggering the schedule.

Trigger and retrieve a schedule::

    schedule = await flexmeasures_client.trigger_and_get_schedule(
            sensor_id=<sensor_id>, # int
            start="2023-03-26T10:00+02:00", # iso datetime
            duration="PT12H", # iso timedelta
            flex_context= {"consumption-price-sensor": <consumption_price_sensor_id>, # int},
            flex-model= {
                    "soc-unit": "kWh",
                    "soc-at-start": 50, # soc_units (kWh)
                    "soc-max": 400,
                    "soc-min": 20,
                    "soc-targets": [
                        {"value": 100, "datetime": "2023-03-03T11:00+02:00"}
                    ],
               }
        )

The trigger and get schedule function can also be separated to trigger the schedule first and later retrieve the schedule using the ``schedule_uuid``.

Trigger a schedule::

    schedule_uuid = await flexmeasures_client.trigger_storage_schedule(
            sensor_id=<sensor_id>, # int
            start="2023-03-26T10:00+02:00", # iso datetime
            duration="PT12H", # iso timedelta
            flex_context= {"consumption-price-sensor": <consumption_price_sensor_id>, # int},
            flex-model= {
                    "soc-unit": "kWh",
                    "soc-at-start": 50, # soc_units (kWh)
                    "soc-max": 400,
                    "soc-min": 20,
                    "soc-targets": [
                        {"value": 100, "datetime": "2023-03-03T11:00+02:00"}
                    ],
               }
        )

The ``trigger_storage_schedule`` return a ``schedule_uuid``. This can be used to retrieve the schedule. The client will re-try if until the schedule is available or the ``MAX_POLLING_STEPS`` of ``10`` is reached. Retrieve schedule::

    schedule = await flexmeasures_client.get_schedule(
                sensor_id=<sensor_id>, #int
                schedule_id="<schedule_uuid>", # uuid
                duration="PT45M" # iso timedelta
            )

The schedule returns a Pandas ``DataFrame`` that can be used to regulate the flexible assets.




.. _pyscaffold-notes:

Making Changes & Contributing
=============================

This project uses `pre-commit`_, please make sure to install it before making any
changes::

    pip install pre-commit
    cd flexmeasures-client
    pre-commit install

It is a good idea to update the hooks to the latest version::

    pre-commit autoupdate

Don't forget to tell your contributors to also install and use pre-commit.

.. _pre-commit: https://pre-commit.com/

===================
S2 Protocol
===================

Disclaimer
==========

The `S2 Protocol <https://s2standard.org/>`_ integration is still under active development. Please, beware that the logic and interfaces can change.


Run Demo
=========

Run the following commands in the flexmeasures folder to create a toy-account and an admin user::

    flexmeasures add toy-account
    flexmeasures add user --username admin --account-id 1 --email admin@mycompany.io --roles admin

Launch server::

    flexmeasures run

To load the data, run the following command in the flexmeasures-client repository::

    python src/flexmeasures_client/s2/script/demo_setup.py

Start the S2 server::

    python src/flexmeasures_client/s2/script/websockets_server.py

In a separate window, start the S2 Client::

    python src/flexmeasures_client/s2/script/websockets_client.py

Note
====

This project has been set up using PyScaffold 4.4. For details and usage
information on PyScaffold see https://pyscaffold.org/.
