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


Reacting to surprises with reaction rules
-----------------------------------------

A schedule is optimal given a forecast of everything else behind the meter, and that forecast is wrong within minutes.
A FlexMeasures server that computes reaction rules serves, with every schedule, one rule per slot and device:
how to react to a surprise at the meter, and within which band that reaction keeps the rest of the plan feasible.
The controller evaluates the rule locally, at meter speed, without a new scheduling request.
All quantities below carry the sign convention of the schedule values: consumption is positive,
so at the connection import is positive and export is negative.

Opt in with ``react: true`` on the flex-model of each device that is to react:

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
The schedule then carries a ``reaction`` list next to its ``values``, with one entry per value,
in the same unit as the values:

.. code-block:: python

    {
        "start": "2026-11-30T00:00:00+01:00",
        "duration": "P1D",
        "unit": "kW",
        "values": [4.4, ...],
        "reaction": [{"reference": -0.4, "react": 1, "band": [[1.4, 7.4]]}, ...],
    }

Per slot, ``reference`` is the uncontrolled power the plan assumed: the forecast of everything behind the meter that is not a reacting device.
``react`` is the share of the surprise the device absorbs: 0 (follow the plan) or 1 (absorb it one to one) in this version,
with the shares in between reserved for a later version.
``band`` lists the power ranges within which deviating keeps the device's own constraints satisfiable:
its power capacity, its operation modes (see below) and the state of charge it must still reach.
Every side of a range is a number, because the planner always knows the device's power capacity.
A schedule served without rules has no ``reaction`` key and reads exactly as before.
When ``get_schedule`` converts the unit itself (for servers older than v0.32.0), it converts the rules along with the values.

A single reacting device
~~~~~~~~~~~~~~~~~~~~~~~~

With one reacting device, the surprise is what the meter shows beyond the plan:
the net power at the connection, minus the device's realized power, minus the reference.
``flexmeasures_client.reaction`` parses the rules and evaluates them, with no state and no I/O:

.. code-block:: python

    from flexmeasures_client.reaction import ReactionSchedule

    plan = ReactionSchedule.from_schedule(schedule)
    if not plan.has_rules:
        print("The server served no reaction rules.")

    # At meter speed:
    now = ...
    slot = plan.slot(now)
    surprise = (meter_power - charger_power) - plan.rules[slot].reference
    setpoint = plan.react_at(now, residual=surprise)

The setpoint is ``clip(p - react * surprise, band)``, with ``p`` the slot's schedule value.
When the house behaves as forecast, the surprise is zero and the setpoint is the plan.

Devices with operation modes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A charger that delivers nothing between zero and 1.4 kW in either direction declares that in its flex-model,
so that neither the plan nor a band lands in that dead zone:

.. code-block:: python

    flex_model = {
        "power-capacity": "7.4 kW",
        "react": True,
        "operation-modes": [
            {"production-range": ["1.4 kW", "7.4 kW"]},
            {"consumption-range": ["0 kW", "0 kW"]},
            {"consumption-range": ["1.4 kW", "7.4 kW"]},
        ],
    }

Its band then has one range per operation mode, ``[[-7.4, -1.4], [0, 0], [1.4, 7.4]]`` in kW.
A continuous answer that falls in a hole between two ranges becomes the nearest edge, and on a tie the edge on the side of ``p``:

.. code-block:: python

    from flexmeasures_client.reaction import ReactionRule

    rule = ReactionRule(reference=-0.4, react=1, band=[(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)])
    rule.react_to(p=2.0, residual=1.2)  # 0.8 kW lies in the dead zone, nearer to 1.4: returns 1.4
    rule.react_to(p=2.0, residual=1.4)  # 0.6 kW, nearer to 0: returns 0.0
    rule.react_to(p=2.0, residual=1.3)  # 0.7 kW, a tie: the edge on the side of p, 1.4

Rounding with hysteresis, holding minimum power through a short surprise, deadbands on commands and dwell times
are left to the device drivers in the controller.

Several reacting devices
~~~~~~~~~~~~~~~~~~~~~~~~

The devices that react in a slot (``react`` 1) form the chain for that slot, in the order in which the flex-model lists them.
They react in series: instead of the whole surprise, each device is given a residual,
which is the surprise, plus the deviations from plan of the devices outside the chain,
plus the deviations of the devices before it in the chain.
:func:`residuals` computes these from the plans and the realized powers:

.. code-block:: python

    from flexmeasures_client.reaction import residuals

    given = residuals(
        surprise=-3.0,  # 3 kW more export than forecast
        chain=["battery", "charger"],  # the devices with react = 1, in order
        plans={"battery": 0.0, "charger": 4.4, "pv": -5.0},
        realized={"battery": 3.0, "charger": 4.4, "pv": -5.0},
    )
    # given == {"battery": -3.0, "charger": 0.0}: the battery took the whole surprise

Each device then evaluates its own rule with the residual it is given, in place of the surprise.
``examples/reaction_rules.py`` triggers a schedule with these fields, walks a day of slots with a scripted surprise and prints the setpoints;
it exits with status 2 when the server served no rules, so it doubles as a smoke test.

A day after saldering
~~~~~~~~~~~~~~~~~~~~~

Until the end of 2026, Dutch households net exported energy against imported energy (*saldering*),
so a kilowatt-hour is worth the same whichever way it crosses the meter and a schedule that is a little off costs nothing.
From 2027 an exported kilowatt-hour earns the feed-in price, far below the import price and negative in some hours.
A schedule is then only as good as the forecast it was built on, and a household's forecast is wrong within minutes.

``examples/reaction_rules_saldering.py`` simulates one such day for a bidirectional charger, offline:
a forecast of everything behind the meter, the actual with a cloud, a sunny spell and the oven,
the plan a scheduler would make under the forecast, and the reaction rules that would come with it.
The rules are evaluated with ``flexmeasures_client.reaction.react``.
It needs matplotlib for the pictures (``uv sync --group examples``); the numbers print without it.

.. image:: https://raw.githubusercontent.com/FlexMeasures/screenshots/main/flexmeasures-client/reaction-rules-day.png
    :alt: Three panels over one day: the house against its forecast, the net power at the connection with a plain schedule and with reaction rules, and the charger's plan and setpoint.
    :width: 100%

Following the plan blindly, the charger keeps charging at the planned rate through the cloud, so the house imports,
and keeps the planned rate through the sunny spell, so the surplus is exported at a negative price;
in the evening it discharges as planned while the oven pushes the house into the peak price.
With reaction rules the charger charges less during the cloud, more during the sunny spell and discharges harder during the oven,
within its band, and the connection stays where the plan put it.

.. image:: https://raw.githubusercontent.com/FlexMeasures/screenshots/main/flexmeasures-client/reaction-rules-cost.png
    :alt: Four bars with the cost of the same day: under saldering, the plan as expected, a plain schedule after saldering, and reaction rules after saldering.
    :width: 100%

The plain schedule costs about a euro more on this day than the planner expected; reaction rules bring the day back to within a few cents of the plan,
which is also what the same day used to cost under saldering.

A band exit, second by second
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A rule can only absorb what its band allows. When more is left over, the controller asks for a new plan,
and ``examples/reaction_rules_retrigger.py`` shows how, at the meter's sampling rate:
the oven adds 4.5 kW while the car, which must keep energy for the morning, can discharge only 1.5 kW more.
The rule pins the charger to its band edge at once, the residual of 3 kW waits out the dwell (the charger's settle time plus one sample),
one retrigger fires with fresh meter data, nothing else fires while the request is in flight, even when the surprise flaps,
and the new plan arrives a couple of minutes later with a wider band and a plan that closes the residual.

.. image:: https://raw.githubusercontent.com/FlexMeasures/screenshots/main/flexmeasures-client/reaction-rules-retrigger.png
    :alt: Three panels over eight minutes: the surprise, the charger pinned to its band edge until a new plan widens the band, and the residual with the band exit, the dwell, the retrigger and the arrival of the new plan.
    :width: 100%

The timings are placeholders; the controller measures its devices' settle times and sets the dwell from them.


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
