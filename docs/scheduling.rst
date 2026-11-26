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

Per slot, ``reference`` is the uncontrolled power the plan is balanced against: usually the forecast of everything behind the meter that is not a reacting device,
and where the operation modes make the plan miss the kink, the balance point itself (see the worked example below).
``react`` is the share of the deviation from that balance point the device absorbs: 0 (follow the plan) or 1 (absorb it one to one) in this version,
with the shares in between reserved for a later version.
``band`` lists the power ranges within which deviating keeps the device's own constraints satisfiable:
its power capacity, its operation modes (see below) and the state of charge it must still reach.
Every side of a range is a number, because the planner always knows the device's power capacity.
A schedule served without rules has no ``reaction`` key and reads exactly as before.
When ``get_schedule`` converts the unit itself (for servers older than v0.32.0), it converts the rules along with the values.

A single reacting device
~~~~~~~~~~~~~~~~~~~~~~~~

With one reacting device, the residual it is given is the deviation from the balance point:
the net power at the connection, minus the device's realized power, minus the reference.
``flexmeasures_client.reaction`` parses the rules and evaluates them with pure functions, with no state and no I/O.
Next to them it offers three controller-side helpers that hold the little state a live loop needs between samples,
``ExitGate`` (the current excursion outside the band), ``Smoother`` (the window of recent samples) and ``ModePolicy`` (the operation mode the device is in),
which the sections below introduce in turn:

.. code-block:: python

    from flexmeasures_client.reaction import ReactionSchedule

    plan = ReactionSchedule.from_schedule(schedule)
    if not plan.has_rules:
        print("The server served no reaction rules.")

    # At meter speed:
    now = ...
    slot = plan.slot(now)
    deviation = (meter_power - charger_power) - plan.rules[slot].reference
    setpoint = plan.react_at(now, residual=deviation)

The setpoint is ``clip(p - react * deviation, band)``, with ``p`` the slot's schedule value.
When the house behaves as forecast and the plan sits on its balance point, the deviation is zero and the setpoint is the plan.

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
A continuous answer that falls in a hole between two ranges becomes the edge of that hole on the side of the mode the device is in,
which at the start of a slot is the mode that contains ``p``; a value beyond the outermost edge becomes that edge:

.. code-block:: python

    from flexmeasures_client.reaction import ReactionRule

    rule = ReactionRule(reference=-0.4, react=1, band=[(-7.4, -1.4), (0.0, 0.0), (1.4, 7.4)])
    rule.react_to(p=2.0, residual=1.2)  # 0.8 kW lies in the dead zone below the charging mode: returns 1.4
    rule.react_to(p=2.0, residual=1.4)  # 0.6 kW, nearer to 0, but still this side of the dead zone: 1.4
    rule.react_to(p=2.0, residual=2.5)  # -0.5 kW lies in the next hole down; its edge on the charging side is idle: 0.0
    rule.react_to(p=2.0, residual=1.2, current=(0.0, 0.0))  # the same 0.8 kW from idle: 0.0

Rounding to the mode's side rather than to the nearest edge keeps a device from idling on a deviation it was meant to absorb.
Take a charger with the modes idle and 1.4 to 7.4 kW, a forecast surplus of 0.5 kW behind the meter, a plan of 1.4 kW,
and a reference of -1.4 kW: the modes make the plan miss the kink, so the server serves the balance point rather than the forecast.
When the house behaves as forecast, the deviation from that balance point is 0.9 kW, expected and non-zero;
the rule's continuous answer is 0.5 kW, in the hole, and the rounding brings the charger back to its plan of 1.4 kW where the nearest edge would have idled it.
1.0 kW of extra sun makes it charge at 1.5 kW.

Which mode the device is in is state, and switching modes is the slow, wearing transition on a real charger,
so :class:`flexmeasures_client.reaction.ModePolicy` keeps that state next to the pure function.
``start_slot(p, band)`` puts the device in the mode that contains the plan; ``observe(when, raw)`` takes the rule's continuous answer
(``p - react * residual``) and returns the setpoint, rounded relative to the current mode.
By default the policy never switches within a slot: it holds the plan's mode, and every raw value outside that mode,
in a hole, inside another mode or beyond the band, is clipped to the mode's nearer edge (1.4 kW for a charging car asked to idle or to discharge).
Crossing to idle is a mode change like any other, and happens only through a switch.
With a ``dwell``, the policy switches once the raw value has lain inside another mode, at least ``margin`` past the hole, for that long;
switching back obeys the same dwell and margin.
A raw value counts as inside idle when it lies on idle or beyond it, seen from the current mode, and the margin does not apply to idle,
since there is nothing to be past; so when a cloud lasts, a charging car goes idle after the dwell.
A lasting deviation the policy will not follow leaves a residual the device does not absorb;
that is what the exit gate below is for, which the policy leaves unchanged.
Deadbands on commands and the device's own settle time are left to the device drivers in the controller.

Several reacting devices
~~~~~~~~~~~~~~~~~~~~~~~~

The devices that react in a slot (``react`` 1) form the chain for that slot, in the order in which the flex-model lists them.
They react in series: instead of the whole deviation at the meter, each device is given a residual,
which is that deviation, plus the departures from plan of the devices outside the chain,
plus the departures of the devices before it in the chain.
:func:`residuals` computes these from the plans and the realized powers:

.. code-block:: python

    from flexmeasures_client.reaction import residuals

    given = residuals(
        deviation=-3.0,  # 3 kW more export than forecast
        chain=["battery", "charger"],  # the devices with react = 1, in order
        plans={"battery": 0.0, "charger": 4.4, "pv": -5.0},
        realized={"battery": 3.0, "charger": 4.4, "pv": -5.0},
    )
    # given == {"battery": -3.0, "charger": 0.0}: the battery took the whole deviation

Each device then evaluates its own rule with the residual it is given, in place of the deviation.
``examples/reaction_rules.py`` triggers a schedule with these fields and walks the day sample by sample through the local loop,
with the real pieces: the deviation at the meter goes through the smoother, the rule gives the setpoint, what the device could not absorb is the residual,
and the exit gate says when a retrigger is due, which the probe only marks (after ``--turnaround`` seconds the gate is told the plan arrived and the same rules continue).
It prints the setpoint per slot, every band exit with the energy past the band, and a summary with the counts;
it exits with status 2 when the server served no rules, so it doubles as a smoke test.
The loop's knobs are options with the classes' defaults (``--window``, ``--median``, ``--dwell``, ``--min-energy``, ``--tolerance``),
and a recorded deviation replaces the scripted one with ``--deviation-csv`` (``--surprise-csv`` still works), a CSV with a header and the columns ``time`` (ISO 8601 with an offset)
and ``deviation`` (or ``surprise``; in ``--deviation-unit``, kW by default), walked in time order against the slot each sample falls in; samples outside the schedule are skipped and counted.
So a day's recording can be replayed against a real schedule with different knobs, to see how many exits and retriggers each setting gives.

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

A rule can only absorb what its band allows. When more is left over for long enough, the controller asks for a new plan,
and ``examples/reaction_rules_retrigger.py`` shows how, at the meter's sampling rate.
The car must keep energy for the morning, so it can discharge only 1.5 kW more than planned.
First the kettle adds 3 kW for two minutes: the rule pins the charger to its band edge at once and 1.5 kW are left over,
but two minutes of that is 50 Wh, a few cents, and not worth a scheduling run, so nothing fires.
Then the oven adds 4.5 kW and stays: 3 kW are left over, and after the gate's dwell and 100 Wh past the band one retrigger fires with fresh meter data.
Nothing else fires while the request is in flight, even when the oven's element cycles off for a moment,
and the new plan arrives half a minute later with a wider band and a plan that closes the residual.

.. image:: https://raw.githubusercontent.com/FlexMeasures/screenshots/main/flexmeasures-client/reaction-rules-retrigger.png
    :alt: Four panels over seven minutes: the surprise with a kettle and an oven, the charger pinned to its band edge until a new plan widens the band, the residual, and the energy past the band with the threshold at which the retrigger fires.
    :width: 100%

The gate is :class:`flexmeasures_client.reaction.ExitGate`, the one stateful piece next to the pure functions:
it is fed every sample's residual and says when a retrigger is due, needing both a dwell (the slowest commanded device's settle time plus one sample,
so a residual only counts once every device has had the chance to land) and a minimum energy past the band;
after ``request_sent()`` nothing fires until ``plan_arrived()``.
Its thresholds are parameters of the gate, not settings of the client, because the controller that runs the loop owns them
and measures the settle times they rest on.
The gate sees what the rule and the mode policy left over: a deviation the device will not follow into another mode shows up here as a residual.
Next to the gate, :class:`flexmeasures_client.reaction.Smoother` takes the noise out of the per-sample deviation before it reaches the rule:
a median over a few samples kills one-second spikes and a mean over a window of a few seconds takes out the jitter of a meter and a charger polled at different moments,
at the price of about half a window of lag. Smooth the deviation and feed the smoothed residual to the gate; never smooth a fuse guard. The timings in the picture are placeholders, except the scheduling round trip,
which takes 20 to 30 seconds in practice.

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
