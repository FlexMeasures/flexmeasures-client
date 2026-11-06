================
S2 CEM changelog
================

Changes to the client's S2 integration, which runs the FlexMeasures Client as an S2 Customer Energy Manager (CEM),
installed with the ``s2`` extra (see the `CEM documentation <https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/CEM.rst>`_).
It ships with the FlexMeasures Client, so its versions are those of the client;
see the `changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst>`_ for everything else in each release.
Add your entry under **Unreleased** when you open a pull request that changes the S2 integration.

Unreleased
==========

Other changes: see `Unreleased in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#unreleased>`__.

Added
-----

* ``FRBCSimple`` takes an optional ``soc_minima_sensor_id``, ``soc_maxima_sensor_id``, ``usage_forecast_sensor_id`` and ``leakage_behaviour_sensor_id``, and records on them what a resource manager declares about its storage: the fill levels it asks to be kept between, the usage it expects, and what its storage leaks. Without a sensor, that declaration is not recorded, rather than being posted somewhere else [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` takes an ``energy_unit``, which a state of charge and the fill-level minima and maxima around it are all recorded in. It is one of ``J``, ``kJ``, ``MJ``, ``Wh``, ``kWh`` and ``MWh``, and is refused when the handler is made otherwise. A state of charge goes into a flex model in ``kWh`` or ``MWh``, converted from any other of these, since FlexMeasures takes no other [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#260 <https://github.com/FlexMeasures/flexmeasures-client/pull/260>`__]
* ``FRBCSimple`` takes a ``hard_bound_factor``, defaulting to 1.5, which sets how far above the declared fill-level range the hard upper bound of a schedule sits. The bound is widened further where the state of charge a schedule starts from is above it, and a factor below 1 is refused, since FlexMeasures clips the band into the hard bounds and a bound inside the declared range would narrow the band to something the resource manager never asked for [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``FRBCSimple`` takes an optional ``charging_efficiency_sensor_id``, records on it what the actuator's operation modes say about converting drawn power into stored energy, and asks for a schedule against it as ``charging-efficiency``. The ratio between a mode's fill rate and the power it draws is a heat pump's coefficient of performance, and without it the scheduler plans the storage as if an actuator converted power into stored energy one for one, so a plan reaches a fill-level target at a power the actuator cannot deliver it at. FlexMeasures takes one for the whole device, so where a mode converts differently at different fill levels, the element that applies where the schedule starts answers, and where the modes differ, the lowest goes out, and the choice is logged. A mode that draws power while the storage does not fill, such as a standby or circulation mode, states none, rather than an efficiency of zero that would tell the scheduler no power could fill the storage at all [`#253 <https://github.com/FlexMeasures/flexmeasures-client/pull/253>`__], [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* ``fill_rate_in_watts()`` and ``operation_mode_conversion_efficiency()``, which state a fill rate in the watts S2 states a power range in, and take the ratio between the two. A fill rate is per second of whatever unit a fill level is recorded in, so a kilowatt hour per second is 3.6 million watts, and an energy unit neither knows is refused rather than converted by a factor that is wrong by thousands [`#253 <https://github.com/FlexMeasures/flexmeasures-client/pull/253>`__]
* ``FRBCSimple`` takes ``schedule_attempts``, defaulting to five, and ``schedule_retry_interval``, defaulting to two seconds, which bound how often a schedule is asked for again and how long between two attempts. One attempt is what the handler did before, where any failure cost the storage its instruction for that window [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__]
* ``FRBCSimple`` takes an optional ``production_price_sensor_id``, for a site where feeding back is priced differently from drawing, as under a feed-in tariff. Without it, both directions are priced by ``price_sensor_id``, as before [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* ``FRBCSimple`` takes ``relax_site_capacity``: ``True`` lets a schedule breach the site's connection capacity at a price, for a site whose capacity is a target set by others, and ``False`` keeps it a hard limit. Unset, it is left to FlexMeasures, which since v1.0 relaxes it by default, so a home whose connection is a physical limit has to pass ``False`` [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* An FRBC handler logs the instructions of a batch once, at debug level, by operation-mode name [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* ``flexmeasures_client.s2.clock``, with a ``WallClock`` and a ``SimulatedClock``, and ``CEM(clock=...)``: the time a CEM and every handler it registers go by, timers and the moment a state of charge is known as of included [`#264 <https://github.com/FlexMeasures/flexmeasures-client/pull/264>`__, `#268 <https://github.com/FlexMeasures/flexmeasures-client/pull/268>`__]
* ``CEM(accept_simulation_metadata=True)``, which takes a co-simulation's messages wrapped with the simulated time (``S2Wrapper``) and sets its clock from them. Off by default, a wrapped message is refused as invalid. The example server turns it on with ``S2_ACCEPT_SIMULATION_METADATA`` [`#264 <https://github.com/FlexMeasures/flexmeasures-client/pull/264>`__]
* ``flexmeasures_client.s2.site``: ``FRBC_SITE_SENSORS``, ``ensure_site`` and ``FRBCSiteConfigurator``, which gives each resource manager a FlexMeasures site of its own, named after it, and an FRBC handler wired to it [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* ``CEM(site_configurator=...)``, which sets a resource manager's site up once its details arrive, answering its messages with a temporary error until then. The example server does so with ``S2_SITE_PER_RESOURCE_MANAGER`` [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* ``examples/S2-co-simulation``, a FlexMeasures stack with one CEM per simulated home, for a co-simulation whose resource managers connect over websockets [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__, `#267 <https://github.com/FlexMeasures/flexmeasures-client/pull/267>`__]
* ``flexmeasures_client.s2.measurements.MeasurementRecorder`` and ``CEM(measurement_recorder=...)``: where a resource manager's measured power is recorded, one per server so that a reconnecting resource manager is not recorded twice [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]

Changed
-------

* ``FRBCSimple`` plans one schedule at a time, for the newest request: a storage status arriving while a schedule is planned waits for it, and is dropped where a newer one arrives meanwhile, and a request out of date makes no further attempt after a failed one. Each status used to start a solve of its own beside the one in flight, so the newest is now planned after the attempt in flight rather than beside it [`#269 <https://github.com/FlexMeasures/flexmeasures-client/pull/269>`__]
* An FRBC handler no longer turns a schedule into instructions where a newer storage status arrived while it was being planned, since sending would drop them anyway [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* ``FRBC.trigger_schedule()`` takes the number of the request it answers as a keyword argument, so a subclass that overrides it has to accept that argument [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBC.send_storage_status()`` likewise takes the number of the planning request it answers, since a storage status is now what asks for a schedule [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* ``FRBCSimple`` takes a ``resolution``, defaulting to fifteen minutes, which a schedule's start is lined up with [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* ``FRBCSimple`` asks for a schedule within the power ranges the actuator declares: a ``consumption-capacity`` and a ``production-capacity`` from the widest range it states, and each distinct range as one of the ``operation-modes``, except where the ranges together cover exactly what the two capacities allow, without a gap, and so say nothing new while each costs the solver a binary per step. Modes S2 reserves for abnormal conditions are left out of both, and are never instructed into, since a resource manager refuses an instruction into one that is not marked abnormal; an actuator with no other mode is not planned for, with a warning. The scheduler then plans only power values the actuator can run at, instead of a fractional power that a resource manager has to round into a full-on block, overshooting the capacity its site is held to. An actuator that cannot produce at all is given a production capacity of zero, where before its storage could be planned as if it were a battery feeding the grid [`#248 <https://github.com/FlexMeasures/flexmeasures-client/pull/248>`__, `#272 <https://github.com/FlexMeasures/flexmeasures-client/pull/272>`__]
* The ``operation-modes`` field is asked for only of a FlexMeasures at v1.0.0 or newer, which is the release that added it. An older server is asked for a schedule without the power bands, and told so in the log, since a flex model naming a field the server does not know is refused outright and leaves the resource manager with no schedule at all [`#248 <https://github.com/FlexMeasures/flexmeasures-client/pull/248>`__]
* ``FRBCSimple`` asks for a schedule against what the resource manager declared about its storage: the fill levels it wants to be kept between as ``soc-minima`` and ``soc-maxima``, the usage it expects as ``soc-usage``, and what its storage leaks as ``storage-efficiency``, each only where a sensor holds it. Recording those declarations changed nothing about the schedule that came back until the flex model named them [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``fm_schedule_to_instructions()`` takes the fill levels its own running fill level is held between, so a caller that asked for a schedule against other bounds than the declared fill-level range is answered with the operation modes for the storage states the scheduler actually planned. Without them the declared range is used, as before [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``FRBCSimple`` asks for the declared fill-level range as a band whose breach is priced, with ``relax-soc-constraints`` in the flex context, sent with each request as ``soc-minima`` and ``soc-maxima`` over the whole window, the latest fill-level target profile applying where it covers a step, held inside the range, and clipped at zero, since FlexMeasures takes no negative minima. A newer target profile replaces the one before it, as S2 means it to, and the declared range applies again beyond its end. Against a FlexMeasures older than v0.26.0, which cannot relax the band, the band is left out, and the log says so. The hard ``soc-min`` and ``soc-max`` become wide bounds at zero and half again above the range, the top widened to take in a state of charge above it, and a storage reported below zero is planned from zero, with a warning, since FlexMeasures refuses a negative ``soc-min``. A hard bound at the declared range made states a storage really reaches infeasible, such as free-floating above the top of its range or being left below the bottom of it by a previous schedule, and an infeasible job is answered with no schedule at all, or, against a FlexMeasures older than v1.0.0, by a fallback scheduler that ignores the band. A negative usage forecast, a gain FlexMeasures cannot relax, is recorded as no usage, with a warning that counts the steps [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#271 <https://github.com/FlexMeasures/flexmeasures-client/pull/271>`__]
* ``FRBCSimple`` posts a state of charge in ``kWh`` rather than ``MWh``, since S2 is a residential protocol and the example server's own state-of-charge sensor is in kilowatt hours. A state-of-charge sensor in another energy unit still holds the right values, since FlexMeasures converts what is posted into the sensor's own unit [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` asks for a schedule again where an attempt failed in a way another attempt could answer differently. A scheduling job can fail on a transient of its own, from a worker crashing out of its solver to a pandas error over the windows two schedules overlap in, and FlexMeasures refuses a trigger outright where the state of charge it would plan from has not landed yet. Unanswered, such a failure leaves the resource manager polling for an instruction that never comes, since asking for a schedule runs as a task whose exception is logged and dropped. A scheduling job no worker picked up in time is asked about once more, a server that could not be reached or answered with a 5xx is asked again, and so is a bare HTTP 400 from a FlexMeasures older than v0.33.0, which reports a failed job no other way. A cause that fails identically on every attempt is never asked about again: a scheduler colliding with its own belief, or an infeasible problem. Giving up is logged as an error that says the storage is left without an instruction [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` says so, as an error, where a schedule was planned by a fallback scheduler rather than the one it asked for. FlexMeasures answered an infeasible problem that way until v1.0.0 retired it, and an older server either serves that fallback job in place of the failed one or redirects to it, which this client follows, so either way the substitute arrives looking like the schedule that was asked for. It is a coarse charging policy rather than a plan, and it records no state of charge for a later window to be planned from [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__]
* The example resource manager is a heat pump keeping a hot-water buffer warm: it modulates from 400 to 1500 W at a COP of 4 while the buffer is cold and 3 once it is warm, declares its leakage, a day of hot-water use and a comfort profile, and simulates its buffer from S2's own definitions, following each instruction when it is due and reporting its fill level every ``S2_EXAMPLE_STATUS_INTERVAL`` seconds. It follows only the latest plan, honouring the revocations the example server now sends, and tells the CEM where each instruction stands, so that only what it has not yet carried out is revoked, and sends its leakage, usage forecast and comfort profile again every ``S2_EXAMPLE_FORECAST_INTERVAL`` seconds. The example server records each declaration on a sensor of its own, plans from now, seeds its demo prices again every 12 hours, and sets the site's graphs to show the buffer's fill level within its comfort band, and what fills and draws it [`#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#262 <https://github.com/FlexMeasures/flexmeasures-client/pull/262>`__]
* An FRBC handler refuses a system description of more than one actuator with ``INVALID_CONTENT`` and a diagnostic label, since every plan is made for one, where before it acknowledged the description and then failed after each solve, in a task nobody awaited. A description whose actuator declares timers or transitions is taken with a warning that they are not honoured, and so is each timer the first time an ``FRBC.TimerStatus`` names it; every status is now kept. ``FRBCSimple`` plans with the usage forecast sensor only where the resource manager says it provides a usage forecast, or has sent one regardless [`#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` plans from a storage status even where recording its state of charge failed, which is logged, since the schedule starts from the level the status reports, sent inline; before, the failed post cancelled the planning it preceded. A charging or storage efficiency whose ingestion could not be confirmed is no longer remembered as recorded, so it is posted again for the next plan rather than never [`#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]

Removed
-------

* ``flexmeasures_client.s2.cem.get_commodity_unit``, which stated power in kW where S2 states it in W [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* ``op_mode_compute_factor``, which worked a factor back out of a fill rate and returned zero wherever an element's fill rate is one value; the factor now comes with the operation mode chosen, from its power range [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]

Fixed
-----

* A CEM records measured power by the moment it was measured, as one average per step of its sensor's resolution (15 minutes on the example's sites, where it was five), once the step is over by its clock, with the first measurement after that, and in watts, as S2 states it. It binned measurements by its own now, which dropped every measurement a resource manager sent after the fact, as a co-simulation's does, labelled watts as kilowatts, and kept its samples in a buffer shared by every CEM in the process. Recording happens in the background, retried a few times where it fails [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* A CEM without a sensor for a resource manager's measured power no longer records it on sensor 357, which was whichever sensor of whichever site on the server had that id, and answered it as recorded [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* A message of the control type a CEM is selecting, arriving before the resource manager confirmed the selection, is taken as that confirmation, since a resource manager sends a control type's messages only once it works in it. A CEM used to stay without a control type, answering each such message with a temporary error that the resource manager retried, as a co-simulation's reconnecting resource managers showed. A message of any other control type activates nothing [`#263 <https://github.com/FlexMeasures/flexmeasures-client/pull/263>`__]
* Closing a control-type handler cancels the work it still has in flight, recording included, and the example server's session ends with its connection. A resource manager that reconnected used to be served by two sessions planning for it at once [`#263 <https://github.com/FlexMeasures/flexmeasures-client/pull/263>`__]
* A leakage behaviour is recorded as a storage efficiency each time a schedule is asked for, over the window the schedule covers and as a share of the fill level it starts from, or of the middle of the storage's range where it starts below that, and named in the flex model only where it was recorded. It was recorded once, on arrival, over the hours after the message and as a share of the top of the range, so planning went on without it once those hours had passed, and understated the loss at lower fill levels. FlexMeasures takes a storage efficiency as a share of the stock where S2 states a rate, so the loss is exact only at that one fill level [`#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* A resolution boundary is found on the clock of the moment being floored, in the offset it carries, as FlexMeasures floors a series posted with it, and in the repeated hour of an autumn DST change it is the later boundary not after the moment, where it used to be taken for the first pass, an hour or more earlier, so a schedule could start in the past [`#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* On a state-of-charge sensor that records instants, a state of charge is recorded as a reading at an instant, where it was recorded over the minute after it, which FlexMeasures took for a forecast. On a sensor with a resolution, which refuses an instant, it is still recorded over a minute [`#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FillRateBasedControlTUNES`` posts its usage forecast per hour, which is what a value labelled MW means to FlexMeasures, where it posted a quarter-hour amount that FlexMeasures planned as a quarter of the real draw. It reads it back in the resource manager's own units, per second, dividing out the fill-level scale it was posted with, and plans its instructions with its latest leakage behaviour [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``FRBCSimple`` refuses a ``resolution`` or ``schedule_duration`` that is not positive, and a ``schedule_duration`` that is not a whole number of ``resolution`` steps, since FlexMeasures answers with whole steps only, and such a schedule would be read at the wrong step [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* Translating a schedule into instructions takes every rate per second, as S2 states it. It advanced its running fill level by a fill rate per second times a step in hours, so the level moved 3600 times too little, and every step of a schedule was answered with the operation mode for the fill level the schedule started from. Leakage now lowers that level, from the element of the latest ``FRBC.LeakageBehaviour`` that applies, where it used to be read from two fields S2 does not have and so never counted, and so does usage, from the latest ``FRBC.UsageForecast``. Each step is followed exactly, as a resource manager follows an instruction: a fill level crossing into another element, or a change in usage within a step, is taken at the moment it happens, and a level that reaches a boundary its far side would push it back from stays at that boundary [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A schedule is read at the resolution FlexMeasures answers in, its duration over its number of values, rather than as quarter hours whatever it was, which made a schedule for an hourly or a five-minute power sensor raise, so that no instruction went out [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A new instruction goes out wherever the operation mode or its factor changes, including at a constant power where the fill level passes into another element of the mode, rather than only where the power changes. A difference in the factor of a millionth or less, which is the noise of converting a schedule from MW to W, is not a new instruction [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* An instruction asks for the factor that draws the power the schedule asks for, taken from the operation mode's power range. It was worked back out of the fill rate, which asked for the bottom of the power range wherever the fill rate is one value and the power a range [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A leakage behaviour is recorded as a storage efficiency compounded per second, as S2 states the rate, where it was compounded per hour, which declared a buffer losing about 7% of its heat over twelve hours as holding it to within 0.002% [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* An actuator's power and fill rate are recorded as covering one step of the handler's ``resolution``, rather than a quarter hour whatever it plans on [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``FillRateBasedControlTUNES`` plans, and records its conversion efficiencies, when an operation mode has no diagnostic label, which S2 makes optional. It looked for ``THP`` or ``NES`` inside a label that may be absent, so one unlabelled mode stopped planning, and the recording of the efficiencies of every mode listed after it [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]
* ``get_validation_error_summary`` is removed: it iterated a pydantic ``ValidationError``, which is not iterable, so it raised on any error it was given, and nothing called it [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]
* A leakage behaviour is recorded as a storage efficiency at the rate of the element that applies at the latest fill level, or of its leakiest element where no fill level is known yet, where it took the last one listed, which S2 does not require to be the top one. Translating a schedule into instructions likewise no longer depends on the order a mode lists its elements in [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* ``FillRateBasedControlTUNES`` records an ``FRBC.LeakageBehaviour`` instead of answering it with ``PERMANENT_ERROR``. It read its fill-level scale from a misspelt attribute, and the error that raised was taken for a failed post, so no leakage behaviour was ever recorded [`#255 <https://github.com/FlexMeasures/flexmeasures-client/pull/255>`__]
* ``FRBCSimple`` records a second actuator status for the same moment as superseding the first, rather than colliding with it. FlexMeasures keys a belief on its sensor, its source, the event and the moment it was formed, and two statuses inside one resolution step milliseconds apart were refused with a duplicate key whose belief time had lost its sub-second part, losing the later one. A second apart is unambiguous, and the later status supersedes the earlier, which is what a resource manager reporting twice in a second means. A resource manager sends a status for each instruction it applies, and this handler sends several at once, so the power that survived was the one the actuator had already left behind. Statuses about different steps may interleave, so the spacing is kept per step [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* A measurement that cannot be recorded is logged with the value, the unit, the sensor and the moment it was for, rather than disappearing, whether FlexMeasures refused it, the server could not be reached, or the unit it would have been posted in is one nothing converts into. The post runs in a task nobody awaits, so until now a refused measurement left the power sensor holding a schedule and nothing else, with the reason visible only as a dead task in the log [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` converts an actuator's power into the unit it posts in, rather than labelling a value S2 states in watts with whatever ``power_unit`` says. A deployment recording its power sensor in ``kW`` had 883.7 W posted as 883.7 kW, which FlexMeasures stored as the thousandfold it claimed to be; the default of ``W`` needed no conversion and was unaffected [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* The example server creates its power sensor with ``consumption_is_positive``, as S2 states power, so that an actuator's measured power and the schedule planned for it point the same way on the sensor that holds both [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* The example resource manager answers an instruction: it acknowledges it, and reports the transition it asks for when the instruction's ``execution_time`` arrives, rather than printing it and carrying on. Answering on arrival, which is what the resource managers written against this example do, reports a mode the device is not in yet, so a customer energy manager records a measurement of a power nothing is drawing: two instructions sent together, hours apart in execution time, were answered within milliseconds of each other and contradicted one another [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* Importing either S2 example script no longer runs it, so they can be read, imported and tested like any other module [`#251 <https://github.com/FlexMeasures/flexmeasures-client/issues/251>`__]
* ``FRBCSimple`` posts an actuator's power in the sign the power sensor records consumption in, read from the sensor's ``consumption_is_positive`` attribute, rather than in S2's own convention of positive for consumption. FlexMeasures defaults a plain power sensor to the other convention and flips a schedule's sign on the way in, so a measurement of the actuator consuming 883.7 W sat opposite the plan to consume it, which reads as a device doing the reverse of what it was asked. A sensor that cannot be read is posted to as S2 states it, and the reason logged [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` asks for a schedule in watts, which is what S2 states an operation mode's power ranges in, rather than in whatever unit the power sensor happens to be recorded in. S2 states an operation mode's power ranges in watts, and the example server creates its power sensor in ``kW``, so every scheduled power was read as a thousandth of itself: it then sat nearest the operation mode that draws nothing, and the resource manager was instructed to idle through a schedule that asked it to run. The handler already stated the unit when posting an actuator's power; it just never said which one it wanted back [`#247 <https://github.com/FlexMeasures/flexmeasures-client/pull/247>`__]
* A state of charge is posted in the unit the handler records fill levels in, instead of a hardcoded ``MWh``, which stopped FlexMeasures converting it: the example server creates its state-of-charge sensor in ``kWh``, so a fill level of 10 was being recorded as 10,000. A schedule is asked for in that same unit, so the bounds the scheduler plans against and the measurements it plans from agree [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A state of charge, and the whole flex-model a schedule is asked for with, are scaled by ``fill_level_scale`` like every other fill level the handler records. The scale is applied where the data reaches FlexMeasures and nowhere else: translating a schedule back into instructions keeps the resource manager's own units, since it matches fill levels against the operation modes stated in them [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A fill rate is recorded as a rate of fill level, in ``<energy_unit>/s``, instead of in the power unit. S2 states both an actuator's fill rate and a usage forecast as fill level per second, which for a heat buffer is not electricity at all, and which even for a battery differs from kilowatts by the seconds in an hour [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A fill-level target profile is posted from the resolution boundary its translation lined it up with, rather than from the moment the resource manager sent it, which shifted every value by the difference [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* An ``FRBCSimple`` handler accepts an ``FRBC.FillLevelTargetProfile``, an ``FRBC.UsageForecast`` and an ``FRBC.LeakageBehaviour`` instead of raising ``NotImplementedError`` for each. The CEM dispatches these as tasks nobody awaits, so every connection by a resource manager that sends all three logged three "Task exception was never retrieved" tracebacks, and recorded none of what they declared [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A fill-level target profile is held inside the fill-level range the latest system description declared, so a target reaching past either end of it, such as a night setback below the range, is recorded as the range allows rather than as asked [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* The example CEM server answers an S2 handshake at once, instead of after configuring its site with FlexMeasures, which took longer than the timeout a resource manager waits for that answer [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__]
* The example CEM server seeds its demo prices from the current day, instead of from a date hardcoded when it was written, and the example resource manager dates its system description now, so that the window it schedules for has prices. Before both, the two had agreed only by both being hardcoded to the same day [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* An FRBC handler asks for a schedule when a storage status reports a new state of charge, and when a system description arrives after the latest status and differs from the one before it, its validity included, so a resource manager is planned for whichever of the two it sends first, and whether or not its fill level moves [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* A schedule is asked for from a resolution boundary, instead of from whichever moment a resource manager happened to send its system description, which left FlexMeasures without prices for the window, and from the next resolution boundary rather than the start of the step under way, which a description days old made it slide into the past, and which, planned from the fill level reported mid-step, counted on the whole step where only part was left, so each replan within it asked for less power than the step needed [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* An FRBC handler ignores a re-sent system description whose content is unchanged, instead of storing it again [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* An FRBC handler answers only the newest schedule request, instead of letting every request in a backlog complete its own round trip and send its own instruction batch [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* An FRBC handler sends an instruction only where it changes what the actuator does, which holds at the sending boundary for every producer of instructions [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* The instruction-status history is keyed by a string, as its type declares, instead of by the ``UUID`` that a no-op ``cast`` let through, which made every lookup miss [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBCSimple`` reports the power of the operation mode the actuator is actually in, taken from the power range of the element that applies at the latest fill level, and posts it to the power sensor [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* A control-type handler can log before it is registered, and one without its own ``close()`` no longer breaks CEM teardown [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__]

Added
-----

* An FRBC handler tracks the instruction batch the resource manager is working from, and ``send_revocations`` makes it revoke the instructions of a batch it replaces that are still pending [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``ControlTypeHandler.send_message()``, which handlers use instead of putting messages on the CEM's sending queue themselves [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBCSimple`` takes an optional ``fill_rate_sensor_id``, and records the actuator's fill rate there instead of discarding it [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__]

v0.9.0 | 2026-04-21
===================

Other changes: see `v0.9.0 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v090--2026-04-21>`__.

Changed
-------

* Respond to a ``Handshake`` with a ``ReceptionStatus``, too [`#187 <https://github.com/FlexMeasures/flexmeasures-client/pull/187>`__]
* Handle timezones with ``zoneinfo`` instead of ``pytz`` [`#192 <https://github.com/FlexMeasures/flexmeasures-client/pull/192>`__]
* The example WebSocket server logs through the CEM's logger [`#178 <https://github.com/FlexMeasures/flexmeasures-client/pull/178>`__]

Fixed
-----

* Fix the CEM's ``get_message`` under Pydantic 1 [`#176 <https://github.com/FlexMeasures/flexmeasures-client/pull/176>`__]

v0.8.1 | 2026-02-07
===================

Other changes: see `v0.8.1 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v081--2026-02-07>`__.

Added
-----

* Documentation for running the client as a CEM [`#158 <https://github.com/FlexMeasures/flexmeasures-client/pull/158>`__]

v0.8.0 | 2026-01-14
===================

Other changes: see `v0.8.0 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v080--2026-01-14>`__.

Changed
-------

* Update the CEM [`#155 <https://github.com/FlexMeasures/flexmeasures-client/pull/155>`__]

v0.6.0 | 2025-07-09
===================

Other changes: see `v0.6.0 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v060--2025-07-09>`__.

Fixed
-----

* Add missing type annotations [`#122 <https://github.com/FlexMeasures/flexmeasures-client/pull/122>`__]

v0.5.6 | 2025-05-26
===================

Fixed
-----

* Assume a relevant power unit [`#116 <https://github.com/FlexMeasures/flexmeasures-client/pull/116>`__]

v0.5.4 | 2025-05-23
===================

Changed
-------

* Log a warning instead of failing when a power measurement cannot be posted [`#115 <https://github.com/FlexMeasures/flexmeasures-client/pull/115>`__]

v0.5.3 | 2025-05-16
===================

Fixed
-----

* Internal fixes to the TUNES control type.

v0.5.1 | 2025-05-16
===================

Fixed
-----

* Schedule every 15 minutes and send data every 5 minutes.

v0.5.0 | 2025-05-16
===================

Added
-----

* The TUNES control type scales fill levels and fill rates to internal units, and picks the most efficient valid operation mode [`#113 <https://github.com/FlexMeasures/flexmeasures-client/pull/113>`__]

Fixed
-----

* Send a reception status when a message fails validation [`#113 <https://github.com/FlexMeasures/flexmeasures-client/pull/113>`__]

v0.4.7 | 2025-04-23
===================

Added
-----

* The TUNES control type processes instructions [`#112 <https://github.com/FlexMeasures/flexmeasures-client/pull/112>`__]

v0.4.5 | 2025-03-25
===================

Added
-----

* Send the active actuator to a sensor, to track the source of actuator status messages [`#111 <https://github.com/FlexMeasures/flexmeasures-client/pull/111>`__]

v0.4.4 | 2025-03-18
===================

Added
-----

* Limit how often measurements are sent [`#108 <https://github.com/FlexMeasures/flexmeasures-client/pull/108>`__]

v0.4.3 | 2025-02-18
===================

Added
-----

* Activate the TUNES control type with the resource manager's details [`#107 <https://github.com/FlexMeasures/flexmeasures-client/pull/107>`__]

v0.4.2 | 2025-02-17
===================

Fixed
-----

* Units in the TUNES control type [`#105 <https://github.com/FlexMeasures/flexmeasures-client/pull/105>`__]
* Check handshake versions with semantic versioning [`#106 <https://github.com/FlexMeasures/flexmeasures-client/pull/106>`__]

v0.4.1 | 2025-02-06
===================

Fixed
-----

* The CEM version.

v0.4.0 | 2025-02-06
===================

Changed
-------

* Select an S2 version compatible with the TNO resource manager [`#103 <https://github.com/FlexMeasures/flexmeasures-client/pull/103>`__]

v0.3.3 | 2025-01-30
===================

Other changes: see `v0.3.3 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v033--2025-01-30>`__.

Changed
-------

* **Breaking:** S2 support is an optional extra, so install ``flexmeasures-client[s2]`` to use it [`#102 <https://github.com/FlexMeasures/flexmeasures-client/pull/102>`__]

v0.3.1 | 2025-01-24
===================

Changed
-------

* Raise the minimum version of s2-python [`#100 <https://github.com/FlexMeasures/flexmeasures-client/pull/100>`__]

v0.3.0 | 2025-01-23
===================

Added
-----

* The ``FillRateBasedControlTUNES`` control type [`#83 <https://github.com/FlexMeasures/flexmeasures-client/pull/83>`__]

Changed
-------

* Support Pydantic 2 [`#99 <https://github.com/FlexMeasures/flexmeasures-client/pull/99>`__]

v0.2.5 | 2024-11-12
===================

Changed
-------

* Pin s2-python to a release that uses Pydantic 1, which Home Assistant still requires [`#89 <https://github.com/FlexMeasures/flexmeasures-client/pull/89>`__]

v0.2.0 | 2024-03-04
===================

Other changes: see `v0.2.0 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v020--2024-03-04>`__.

Changed
-------

* Use the official s2-python library [`#76 <https://github.com/FlexMeasures/flexmeasures-client/pull/76>`__]

v0.1.8 | 2023-09-01
===================

Fixed
-----

* Initialise the control type handlers when creating the CEM [`#61 <https://github.com/FlexMeasures/flexmeasures-client/pull/61>`__]

v0.1.7 | 2023-08-31
===================

Changed
-------

* Revert the migration to Pydantic 2, for Home Assistant [`#60 <https://github.com/FlexMeasures/flexmeasures-client/pull/60>`__]

v0.1.6 | 2023-08-31
===================

Other changes: see `v0.1.6 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v016--2023-08-31>`__.

Added
-----

* Expose the control type as a public property [`#59 <https://github.com/FlexMeasures/flexmeasures-client/pull/59>`__]

Changed
-------

* Migrate to Pydantic 2 [`#57 <https://github.com/FlexMeasures/flexmeasures-client/pull/57>`__]

v0.1.5 | 2023-08-16
===================

Other changes: see `v0.1.5 in the changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG.rst#v015--2023-08-16>`__.

Added
-----

* FRBC support for actuator status messages [`#56 <https://github.com/FlexMeasures/flexmeasures-client/pull/56>`__]

v0.1.2 | 2023-05-30
===================

Added
-----

* Handle S2 messages as a CEM [`#29 <https://github.com/FlexMeasures/flexmeasures-client/pull/29>`__]
