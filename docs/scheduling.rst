.. _scheduling:

Scheduling
==========

The FlexMeasures Client supports the scheduling API endpoints:

- ``POST /assets/<id>/schedules/trigger`` — queue a scheduling job
- ``GET  /sensors/<id>/schedules/<uuid>`` — poll for a sensor's schedule

These are exposed through three client methods:

- :meth:`trigger_schedule` — trigger and return the schedule UUID
- :meth:`get_schedule` — poll until a sensor's schedule is ready
- :meth:`trigger_and_get_schedule` — convenience wrapper for both

.. note::

    The asset trigger endpoint requires FlexMeasures **v0.27.0** or above and
    a worker listening on the ``scheduling`` queue::

        flexmeasures jobs run-worker --queue scheduling


Basic example
-------------

Schedule a single storage device against an electricity price.  ``sensor_id``
is the device's power sensor; the client resolves the asset that owns it and
adds the sensor to the flex model sent to the asset endpoint:

.. code-block:: python

    import asyncio
    from flexmeasures_client import FlexMeasuresClient

    async def main():
        client = FlexMeasuresClient(
            host="localhost:5000",
            ssl=False,
            email="user@example.com",
            password="password",
        )

        schedule = await client.trigger_and_get_schedule(
            sensor_id=8,
            start="2026-09-05T08:00:00+02:00",
            duration="PT12H",
            flex_context={
                "consumption-price": {"sensor": 7},
            },
            flex_model={
                "soc-unit": "kWh",
                "soc-at-start": 50,
                "soc-min": 10,
                "soc-max": 100,
                "power-capacity": "20 kW",
                "soc-targets": [
                    {
                        "value": 80,
                        "datetime": "2026-09-05T18:00:00+02:00",
                    },
                ],
            },
            unit="kW",
        )
        print(schedule)
        # e.g. {"values": [...], "start": "...", "duration": "PT12H", "unit": "kW"}

        await client.close()

    asyncio.run(main())


Scheduling multiple devices
---------------------------

Pass the ID of the common parent asset and one flex-model entry per power
sensor to optimize several devices together.  Every referenced sensor must
belong to that asset or one of its descendants:

.. code-block:: python

    schedules = await client.trigger_and_get_schedule(
        asset_id=3,
        start="2026-09-05T08:00:00+02:00",
        duration="PT12H",
        flex_context={
            "consumption-price": {"sensor": 7},
        },
        flex_model=[
            {
                "sensor": 8,
                "soc-unit": "kWh",
                "soc-at-start": 50,
                "soc-min": 10,
                "soc-max": 100,
                "power-capacity": "20 kW",
            },
            {
                "sensor": 9,
                "consumption-capacity": "0 kW",
                "production-capacity": {"sensor": 9},
            },
        ],
        unit="kW",
    )

    for schedule in schedules:
        print(schedule["sensor"], schedule["values"])

The convenience method returns one schedule dictionary for ``sensor_id`` and
a list of dictionaries for ``asset_id``.  Each item in the latter is tagged
with its ``sensor`` ID.


Using stored flexibility
------------------------

Flex context and flex models may already be configured on the server's asset
tree.  The ``flex_context`` and ``flex_model`` arguments can be omitted to use
that configuration, or supplied to add to or override it for one request.

When using ``asset_id``, :meth:`trigger_and_get_schedule` needs a ``flex_model``
list to know which sensor schedules to retrieve.  If you rely entirely on
stored flex models, trigger first and then retrieve the known power sensors
explicitly, as shown below.


Step-by-step usage
------------------

Trigger and retrieve separately to keep the schedule UUID or to fetch several
output sensors, such as both power and state of charge:

.. code-block:: python

    # Step 1 – enqueue one joint scheduling job
    schedule_id = await client.trigger_schedule(
        asset_id=3,
        start="2026-09-05T08:00:00+02:00",
        duration="PT12H",
        flex_model=[{"sensor": 8}, {"sensor": 9}],
    )
    print(f"Job queued: {schedule_id}")

    # Step 2 – retrieve a result for each relevant sensor
    battery_power = await client.get_schedule(
        sensor_id=8,
        schedule_id=schedule_id,
        duration="PT12H",
        unit="kW",
    )
    battery_soc = await client.get_schedule(
        sensor_id=10,
        schedule_id=schedule_id,
        duration="PT12H",
        unit="kWh",
    )


Belief time and repeated scheduling
-----------------------------------

Set ``prior`` to restrict the scheduler to sensor data recorded before that
time.  Supplying it also asks the server to create a new job rather than reuse
a cached schedule for the same window:

.. code-block:: python

    schedule = await client.trigger_and_get_schedule(
        sensor_id=8,
        start="2026-09-05T08:00:00+02:00",
        duration="PT12H",
        prior="2026-09-05T07:55:00+02:00",
    )

This is useful for rolling or simulated scheduling, where each run represents
a new information horizon.


Selecting a custom scheduler
----------------------------

Pass ``scheduler`` together with ``asset_id`` to select a server-side custom
scheduler:

.. code-block:: python

    schedule_id = await client.trigger_schedule(
        asset_id=3,
        start="2026-09-05T08:00:00+02:00",
        duration="PT12H",
        scheduler="MyCustomScheduler",
    )

The client implements this by updating the asset's ``custom-scheduler``
attribute before triggering.  This is a persistent asset update, not merely a
field on this scheduling request.


Units
-----

Pass ``unit`` to :meth:`get_schedule` or :meth:`trigger_and_get_schedule` to
request the desired output unit.  FlexMeasures v0.32.0 and newer perform this
conversion server-side.  For older servers, the client converts between
``W``, ``kW`` and ``MW`` locally; other client-side conversions raise
``NotImplementedError``.


Polling and errors
------------------

``get_schedule`` polls the sensor schedule endpoint until the result is ready.
Polling uses exponential backoff and is controlled by these client settings:

- ``polling_interval`` (default 10 s) — initial wait between attempts
- ``polling_timeout`` (default 200 s) — maximum total wait
- ``max_polling_steps`` (default 10) — maximum number of attempts

Override them when constructing the client:

.. code-block:: python

    client = FlexMeasuresClient(
        ...,
        polling_interval=5.0,
        polling_timeout=300.0,
        max_polling_steps=12,
    )

A scheduling job rejected or reported as failed by the server raises
``ValueError`` with the server's message.  Connection and polling timeouts
raise ``ConnectionError``.

Schedule, forecast and report triggers share the server's computation rate
limit.  A server running simulations should use ``FLEXMEASURES_MODE = "play"``;
production deployments can configure the server-wide limit or assign the
account an appropriate plan.
