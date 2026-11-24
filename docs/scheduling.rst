.. _scheduling:

Scheduling
==========

The FlexMeasures Client supports the scheduling API endpoints:

- ``POST /assets/<id>/schedules/trigger`` — queue a scheduling job
- ``GET  /jobs/<uuid>``                   — inspect the job (v0.33.0+)
- ``GET  /sensors/<id>/schedules/<uuid>`` — retrieve a sensor's schedule

These are exposed through three client methods:

- :meth:`trigger_schedule` — trigger and return the schedule UUID
- :meth:`get_schedule` — retrieve one sensor's result, with legacy polling
- :meth:`trigger_and_get_schedule` — trigger, wait, and retrieve

.. note::

    The asset trigger endpoint requires FlexMeasures **v0.27.0** or above and
    the generic job status endpoint is available from **v0.33.0**. Scheduling
    also needs
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

    # Step 2 – wait once for the joint job (FlexMeasures v0.33.0+)
    await client.wait_for_job(schedule_id)

    # Step 3 – retrieve a result for each relevant sensor
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


Responding to surprises with recourse rules
-------------------------------------------

A schedule is optimal given a forecast of everything else behind the meter, and that forecast is wrong within minutes.
A FlexMeasures server that computes recourse rules serves, with every schedule, one rule per slot and device:
how to respond to a surprise at the meter, and within which band that response keeps the rest of the plan feasible.
The controller evaluates the rule locally, at meter speed, without a new scheduling request.

Opt in with ``react: true`` on the flex-model of each device that is to respond:

.. code-block:: python

    schedule = await client.trigger_and_get_schedule(
        sensor_id=8,
        start="2026-11-30T00:00:00+01:00",
        duration="P1D",
        flex_context={
            "consumption-price": {"sensor": 7},
        },
        flex_model={
            "soc-unit": "kWh",
            "soc-at-start": 20,
            "soc-min": 10,
            "soc-max": 60,
            "power-capacity": "7.4 kW",
            "react": True,
        },
        unit="kW",
    )

The client sends the flex-model and flex-context as given, so a field the server adds later needs no client release.
The schedule then carries a ``response`` list next to its ``values``, with one entry per value,
in the same unit as the values:

.. code-block:: python

    {
        "start": "2026-11-30T00:00:00+01:00",
        "duration": "P1D",
        "unit": "kW",
        "values": [4.4, ...],
        "response": [{"reference": -0.4, "react": 1, "band": [[1.4, 7.4]]}, ...],
    }

Per slot, ``reference`` is the uncontrolled power the plan assumed,
``react`` is the share of the surprise the device absorbs, 0 (follow the plan) or 1 (absorb it one to one) in this version,
and ``band`` lists the ranges within which deviating keeps the device's own constraints satisfiable,
with ``null`` for an unbounded side and more than one range for a device with operation modes.
A schedule served without rules has no ``response`` key and reads exactly as before.
When ``get_schedule`` converts the unit itself (for servers older than v0.32.0), it converts the rules along with the values.

``flexmeasures_client.recourse`` parses the rules and evaluates them, with no state and no I/O:

.. code-block:: python

    from flexmeasures_client.recourse import ResponseSchedule, residuals

    plan = ResponseSchedule.from_schedule(schedule)
    if not plan.has_rules:
        print("The server served no recourse rules.")

    # At meter speed: the surprise on the uncontrolled part of the meter is
    # the net power at the connection, minus the devices' realized powers, minus the reference.
    now = ...
    slot = plan.slot(now)
    surprise = (meter_power - charger_power) - plan.rules[slot].reference
    setpoint = plan.respond_at(now, residual=surprise)

The setpoint is ``clip(p - react * residual, band)``, with ``p`` the slot's schedule value.
A continuous answer in a hole between two ranges becomes the nearest edge, and on a tie the edge on the side of ``p``.
With several responding devices, :func:`residuals` walks them in the order in which they respond and returns the residual each device is given:
the surprise, plus the deviations from plan of the devices outside the chain, plus those of the devices before it in the chain.
The chain for a slot is the devices with a nonzero react share, in that order.
Sign convention throughout: meter import positive, device consumption positive.

Rounding with hysteresis, deadbands on commands and dwell times are left to the device drivers in the controller.
``examples/recourse_rules.py`` triggers a schedule with these fields, walks a day of slots with a scripted surprise and prints the setpoints;
it exits with status 2 when the server served no rules, so it doubles as a smoke test.


Waiting, legacy polling, and errors
-----------------------------------

On FlexMeasures v0.33.0 and newer, ``trigger_and_get_schedule`` waits through
``GET /jobs/<uuid>`` once, then retrieves the requested sensor result or
results. Its job wait is controlled by the following client defaults:

- ``job_polling_interval`` (default 2 s) — delay before a repeated status check
- ``job_polling_max_interval`` (default 30 s) — maximum delay between checks
- ``job_polling_timeout`` (default 600 s) — total job wait budget

Set these defaults when constructing the client. The convenience method's
``polling_interval``, ``max_polling_interval``, and ``timeout`` arguments can
override them for one schedule:

.. code-block:: python

    client = FlexMeasuresClient(
        ...,
        job_polling_interval=5.0,
        job_polling_timeout=1800.0,
    )

    schedule = await client.trigger_and_get_schedule(
        sensor_id=8,
        start="2026-09-05T08:00:00+02:00",
        duration="PT12H",
        max_polling_interval=60.0,
    )

``get_schedule`` still polls the sensor result endpoint when called directly,
and ``trigger_and_get_schedule`` retains that behaviour for servers older than
v0.33.0. This legacy polling uses the client's general HTTP request settings.
Those settings also apply to authentication, API discovery, asset and sensor
operations, data transfer, trigger calls, result retrieval, and each individual
job-status lookup:

- ``request_timeout`` (default 40 s) — timeout for one HTTP attempt
- ``request_retry_interval`` (default 10 s) — initial wait between attempts
- ``request_retry_timeout`` (default 200 s) — total request/retry budget
- ``max_request_attempts`` (default 10) — maximum attempts in that loop

They do not control the cadence or total lifetime of a background-job wait;
the ``job_polling_*`` settings above do that.

Override them when constructing the client:

.. code-block:: python

    client = FlexMeasuresClient(
        ...,
        request_retry_interval=5.0,
        request_retry_timeout=300.0,
        max_request_attempts=12,
    )

With the jobs API, a job that ends as ``FAILED``, ``STOPPED``, or ``CANCELED``
raises :class:`JobFailedError`; exceeding the job wait budget raises
:class:`JobTimeoutError`. A trigger rejected before a job is queued raises
``ValueError``. Direct and legacy result polling can raise ``ValueError`` or
``ConnectionError``.

Schedule, forecast and report triggers share the server's computation rate
limit.  A server running simulations should use ``FLEXMEASURES_MODE = "play"``;
production deployments can configure the server-wide limit or assign the
account an appropriate plan.
