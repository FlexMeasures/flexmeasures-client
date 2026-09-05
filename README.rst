.. raw:: html

   <p align="center">
     <picture>
       <source media="(prefers-color-scheme: dark)" srcset="https://github.com/FlexMeasures/screenshots/blob/main/logo/flexmeasures-horizontal-dark.svg?raw=true">
       <source media="(prefers-color-scheme: light)" srcset="https://github.com/FlexMeasures/screenshots/blob/main/logo/flexmeasures-horizontal-color.svg?raw=true">
       <img alt="FlexMeasures Logo" src="https://github.com/FlexMeasures/screenshots/blob/main/logo/flexmeasures-horizontal-color.svg?raw=true">
     </picture>
   </p>

.. image:: https://img.shields.io/github/license/FlexMeasures/flexmeasures-client?color=blue
    :alt: License
    :target: https://github.com/FlexMeasures/flexmeasures-client/blob/main/LICENSE.txt
.. image:: https://github.com/FlexMeasures/flexmeasures-client/actions/workflows/ci.yml/badge.svg
    :alt: Tests
    :target: https://github.com/FlexMeasures/flexmeasures-client/actions/workflows/ci.yml
.. image:: https://img.shields.io/pypi/v/flexmeasures-client.svg
    :alt: PyPI Version
    :target: https://pypi.python.org/pypi/flexmeasures-client
.. image:: https://img.shields.io/badge/python-3.9+-blue.svg
    :alt: Python 3.9+
    :target: https://www.python.org/downloads/
.. image:: https://img.shields.io/badge/code%20style-black-000000.svg
    :alt: Code style: black
    :target: https://github.com/psf/black
.. image:: https://coveralls.io/repos/github/FlexMeasures/flexmeasures-client/badge.svg
    :alt: Coverage
    :target: https://coveralls.io/github/FlexMeasures/flexmeasures-client

|

===================
FlexMeasures Client
===================


The FlexMeasures Client provides a Python package to connect to a `FlexMeasures <https://github.com/FlexMeasures/flexmeasures>`_ server to manage flexible assets.

The Flexmeasures Client package provides functionality for authentication, asset and sensor management, posting sensor data, and triggering and retrieving schedules from a FlexMeasures instance through the API.

*As the Flexmeasures Client is still in active development and on version 0.x it should be considered in beta.*


Installation
===============


We use `uv <https://docs.astral.sh/uv/>`_ to manage dependencies. First, `install uv <https://docs.astral.sh/uv/getting-started/installation/>`_.

Then add it to your project:

.. code-block:: bash

    uv add flexmeasures-client

The FlexMeasures Client can also run as an `S2 CEM <https://docs.s2standard.org/docs/concepts/common-concepts/>`_. To enable S2 features, you need to install extra requirements:

.. code-block:: bash

    uv add flexmeasures-client[s2]


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

    await client.post_sensor_data(
        sensor_id=1,
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
It sets up an asset and sensor (checking if they exist first), and then sends data to it using `post_sensor_data()`.


Scheduling
===========


With FlexMeasures a schedule can be requested to optimize at what time the flexible assets can be activated to optimize for price of energy or emissions.

The calculation of a schedule can take some time. On FlexMeasures v0.33.0 and
newer, the convenience method waits on the generic job-status endpoint before
retrieving the schedule values. It falls back to result-endpoint polling on
older servers.

Trigger and retrieve a schedule for multiple devices:

.. code-block:: python

    schedules = await client.trigger_and_get_schedule(
        asset_id=3,
        start="2026-09-05T08:00+02:00",
        duration="PT12H",  # ISO duration
        flex_context={
            "consumption-price": {"sensor": 7},
        },
        flex_model=[
            # Example flex-model for an electric truck at a regular Charge Point
            {
                "sensor": 8,
                "power-capacity": "22 kVA",
                "production-capacity": "0 kW",
                "soc-at-start": "50 kWh",
                "soc-max": "400 kWh",
                "soc-min": "20 kWh",
                "soc-targets": [
                    {"value": "100 kWh", "datetime": "2026-09-05T18:00+02:00"},
                ],
            },
            # Example flex-model for curtailable solar panels
            {
                "sensor": 9,
                "power-capacity": "20 kVA",
                "consumption-capacity": "0 kW",
                "production-capacity": {"sensor": 9},
            },
        ],
    )

For triggering and retrieving a schedule for a single device, simply limit the flex-model to list a single device.
Alternatively, use a single-device flex-model (no list) and move the device's power sensor ID out of the flex-model and use it as the sensor ID in the call to ``trigger_and_get_schedule`` (and leave out the asset ID).

.. code-block:: python

    schedule = await client.trigger_and_get_schedule(
        sensor_id=8,
        start="2026-09-05T08:00+02:00",
        duration="PT12H",  # ISO duration
        flex_context={
            "consumption-price": {"sensor": 7},
        },
        flex_model={
            "soc-at-start": "50 kWh",
            "soc-max": "400 kWh",
            "soc-min": "20 kWh",
            "soc-targets": [
                {"value": "100 kWh", "datetime": "2026-09-05T18:00+02:00"},
            ],
        },
    )

The trigger and get schedule function can also be separated to trigger the schedule first and later retrieve the schedule using the ``schedule_uuid``.

Trigger a schedule:

.. code-block:: python

    schedule_uuid = await client.trigger_schedule(
        **kwargs,  # same kwargs as previous example
    )

The ``trigger_schedule`` method returns a ``schedule_uuid``.
On FlexMeasures v0.33.0 and newer, wait for the job once before retrieving one
or more sensor results:

.. code-block:: python

    await client.wait_for_job(schedule_uuid)

    schedule = await client.get_schedule(
        sensor_id=8,
        schedule_id=schedule_uuid,
        duration="PT45M",  # ISO duration
    )

For the complete scheduling API, including multi-device results, job timeouts,
and compatibility with older servers, see :doc:`scheduling`.


Forecasting
===========

Trigger a forecast for a sensor and wait for the result:

.. code-block:: python

    forecast = await client.trigger_and_get_forecast(
        sensor_id=1,
        duration="PT24H",  # ISO duration – how far ahead to forecast
    )
    # Returns e.g. {"values": [1.2, 1.5, ...], "start": "...", "duration": "PT24H", "unit": "kW"}

On FlexMeasures v0.33.0 and newer, the client polls the generic job endpoint
until the forecasting job is complete, then retrieves its values. For more
advanced options (training window, regressors, forecast frequency, etc.) see
:doc:`forecasting`.


Development
==============

We use `uv <https://docs.astral.sh/uv/>`_ to manage dependencies. First, `install uv <https://docs.astral.sh/uv/getting-started/installation/>`_.

To install the package with all development and testing dependencies:

.. code-block:: bash

    uv sync --group dev --group test

Moreover, if you need to work on S2 features, you need to install extra dependencies:

.. code-block:: bash

    uv sync --extra s2 --group dev --group test

.. note::

   If you prefer shorter commands during interactive development, you can activate the virtual environment (``source .venv/bin/activate``, or ``.venv\\Scripts\\activate`` on Windows) or run commands directly with ``uv run <command>``.




.. _pyscaffold-notes:


Making Changes & Contributing
=============================

.. note: Read more details in CONTRIBUTING.rst

Install the project locally (creating a virtual environment automatically):

.. code-block:: bash

    uv sync


Running tests locally is crucial as well:

.. code-block:: bash

    uv run poe test

For S2 features:

.. code-block:: bash

    uv sync --extra s2 --group test
    uv run poe test-s2

This project uses `pre-commit`_, please make sure to install it before making any
changes:

.. code-block:: bash

    uv tool install pre-commit
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

The FlexMeasures Client comes with a tutorial for creating a Home Energy Management System (HEMS) `See the Usage docs <docs/HEMS.rst>`_.


===================
S2 CEM
===================

The FlexMeasures Client can also be run as a local S2 Customer Energy Manager (CEM) using WebSocket communication. `See here for the docs <docs/CEM.rst>`_, which includes a docker-compose stack.
