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


The FlexMeasures Client provides a Python package to connect to a `FlexMeasures <https://github.com/FlexMeasures/flexmeasures>`_ server to manage flexible assets.

The Flexmeasures Client package provides functionality for authentication, asset and sensor management, posting sensor data, and triggering and retrieving schedules from a FlexMeasures instance through the API.

*As the Flexmeasures Client is still in active development and on version 0.x it should be considered in beta.*


Installation
===============


Install using ``pip``:

.. code-block:: bash

    pip install flexmeasures-client

To enable S2 features, you need to install extra requirements:

.. code-block:: bash

    pip install flexmeasures-client[s2]


Initialization and authentication
==================================

To get started with the FlexMeasures Client, first an account needs to be registered with a FlexMeasures instance.
To create a local instance of FlexMeasures, follow the `FlexMeasures documentation <https://flexmeasures.readthedocs.io/en/latest/index.html>`_.
Registering to a hosted FlexMeasures instance instead can be done through `Seita BV <https://seita.nl/>`_.

In these examples we show how to set up the client to connect to either ``http://localhost:5000`` or ``https://ems.seita.energy``. To connect to a different host, adapt the host in the initialization of the client.

   .. code-block:: python

    from flexmeasures_client import FlexMeasuresClient

    async def main():
        client = FlexMeasuresClient(host="localhost:5000", ssl=False, email="email@email.com", password="pw")
        client = FlexMeasuresClient(host="ems.seita.energy", ssl=True, email="email@email.com", password="pw")


Retrieving available info
==========================

Retrieve user and account:

.. code-block:: python

   user = await client.get_user()
   account = await client.get_account()

The data will be returned as a dictionary.

Retrieve available assets and sensors:

.. code-block:: python

    assets = await client.get_assets()
    sensors = await client.get_sensors()

The data will be returned as (lists of) dictionaries.

.. note:: For `get_assets()` as well as `get_sensors()`, you can use various parameters which the API endpoints also support.


Sending data
=================

Post a measurement from a sensor:

.. code-block:: python

    await client.post_measurements(
        sensor_id=<sensor_id>,  # integer
        start="2023-03-26T10:00+02:00",  # ISO datetime
        duration="PT6H",  # ISO duration
        values=[1, 2, 3, 4],  # list
        unit="kWh",
    )


Here is a small but complete FlexMeasures Client script, which simply updates the flex context of an asset:

.. code-block:: python

    import asyncio

    from flexmeasures_client import FlexMeasuresClient

    usr = "xxxxxxxxxxxxxxxx"
    pwd = "xxxxxxxxxxxxxxxx"
    asset_id = 1


    async def main():
        client = FlexMeasuresClient(email=usr, password=pwd)

        asset = await client.update_asset(
            asset_id=asset_id,
            updates={
                "flex_context": {
                    "site-consumption-capacity": "110 kW",
                    "relax-constraints": True
                }
            },
        )

        print(asset)

        await client.close()


    asyncio.run(main())


For a slightly larger self-contained script, see `this script for sending data <examples/send_data_to_asset.py>`_.
It sets up an asset and sensor (checking if they exist first), and then sends data to it using `post_measurements()`.


Scheduling
===========


With FlexMeasures a schedule can be requested to optimize at what time the flexible assets can be activated to optimize for price of energy or emissions.

The calculation of the schedule can take some time depending on the complexity of the calculations. A polling function is used to check if a schedule is available after triggering the schedule.

Trigger and retrieve a schedule for multiple devices:

.. code-block:: python

    schedule = await flexmeasures_client.trigger_and_get_schedule(
        asset_id=<asset_id>,  # the asset ID (int) of the asset that all relevant power sensors belong to (or live under, in case of a tree-like asset structure)
        start="2023-03-26T10:00+02:00",  # ISO datetime
        duration="PT12H",  # ISO duration
        flex_context={
            "consumption-price": {"sensor": <consumption_price_sensor_id>},  # int
        },
        flex-model=[
            # Example flex-model for an electric truck at a regular Charge Point
            {
                "sensor": <power_sensor_id>,  # int
                "power-capacity": "22 kVA",
                "production-capacity": "0 kW",
                "soc-at-start": "50 kWh",
                "soc-max": "400 kWh",
                "soc-min": "20 kWh",
                "soc-targets": [
                    {"value": "100 kWh", "datetime": "2023-03-03T11:00+02:00"},
                ],
            },
            # Example flex-model for curtailable solar panels
            {
                "sensor": <another_power_sensor_id>,  # int
                "power-capacity": "20 kVA",
                "consumption-capacity": "0 kW",
                "production-capacity": {"sensor": <another_power_sensor_id>},  # int
            },
        ],
    )

For triggering and retrieving a schedule for a single device, simply limit the flex-model to list a single device.
Alternatively, use a single-device flex-model (no list) and move the device's power sensor ID out of the flex-model and use it as the sensor ID in the call to ``trigger_and_get_schedule`` (and leave out the asset ID).

.. code-block:: python

    schedule = await flexmeasures_client.trigger_and_get_schedule(
        sensor_id=<sensor_id>,  # int
        start="2023-03-26T10:00+02:00",  # ISO datetime
        duration="PT12H",  # ISO duration
        flex_context={
            "consumption-price": {"sensor": <consumption_price_sensor_id>},  # int
        },
        flex-model={
            "soc-at-start": "50 kWh",
            "soc-max": "400 kWh",
            "soc-min": "20 kWh",
            "soc-targets": [
                {"value": "100 kWh", "datetime": "2023-03-03T11:00+02:00"},
            ],
        },
    )

The trigger and get schedule function can also be separated to trigger the schedule first and later retrieve the schedule using the ``schedule_uuid``.

Trigger a schedule:

.. code-block:: python

    schedule_uuid = await flexmeasures_client.trigger_schedule(
        **kwargs,  # same kwargs as previous example
    )

The ``trigger_schedule`` method returns a ``schedule_uuid``.
This can be used to retrieve the schedule, using:

.. code-block:: python

    schedule = await flexmeasures_client.get_schedule(
        sensor_id=<sensor_id>,  # int
        schedule_id="<schedule_uuid>",  # uuid
        duration="PT45M",  # ISO duration
    )

The client will re-try until the schedule is available or the ``MAX_POLLING_STEPS`` of ``10`` is reached.


Development
==============

If you want to develop this package it's necessary to install testing requirements:

.. code-block:: bash

    pip install -e ".[testing]"

Moreover, if you need to work on S2 features, you need to install extra dependencies:

.. code-block:: bash

    pip install -e ".[s2, testing]"




.. _pyscaffold-notes:


Making Changes & Contributing
=============================

.. note: Read more details in CONTRIBUTING.rst

Install the project locally (in a virtual environment of your choice):

.. code-block:: bash

    pip install -e .


Running tests locally is crucial as well. Staying close to the CI workflow:

.. code-block:: bash

    pip install tox
    tox -e clean,build
    tox -- -rFEx --durations 10 --color yes

For S2 features, you need to add `-e s2` to tox:

.. code-block:: bash

    tox -e s2

This project uses `pre-commit`_, please make sure to install it before making any
changes:

.. code-block:: bash

    pip install pre-commit
    cd flexmeasures-client
    pre-commit install

It is a good idea to update the hooks to the latest version:

.. code-block:: bash

    pre-commit autoupdate

Don't forget to tell your contributors to also install and use pre-commit.

.. _pre-commit: https://pre-commit.com/


New releases on PyPI are made by adding a tag and pushing it:

.. code-block:: bash

    git tag -s -a vX.Y.Z -m "Short summary"
    git push --tags

(of course you need the permissions to do so)

See releases in GitHub Actions at https://github.com/FlexMeasures/flexmeasures-client/deployments/release


===================
HEMS tutorial
===================

`See the Usage docs <docs/HEMS.rst>`_.


===================
S2 Protocol
===================

Disclaimer
==========

The `S2 Protocol <https://s2standard.org/>`_ integration is still under active development. Please, beware that the logic and interfaces can change.


Run Demo
=========

Run the following commands in the flexmeasures folder to create a toy-account and an admin user:

.. code-block:: bash

    flexmeasures add toy-account
    flexmeasures add user --username admin --account-id 1 --email admin@mycompany.io --roles admin

Launch server:

.. code-block:: bash

    flexmeasures run

To load the data, run the following command in the flexmeasures-client repository:

.. code-block:: bash

    python src/flexmeasures_client/s2/script/demo_setup.py

Start the S2 server:

.. code-block:: bash

    python src/flexmeasures_client/s2/script/websockets_server.py

In a separate window, start the S2 Client:

.. code-block:: bash

    python src/flexmeasures_client/s2/script/websockets_client.py
