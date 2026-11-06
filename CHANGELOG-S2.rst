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

* ``FRBCSimple`` takes an optional ``soc_minima_sensor_id``, ``soc_maxima_sensor_id``, ``usage_forecast_sensor_id`` and ``leakage_behaviour_sensor_id``, and records on them what a resource manager declares about its storage, for it to be seen and planned with. A fill-level target profile is recorded on the minima and maxima sensors, only where both are given, on the handler's ``resolution`` steps from the step its start falls in, and held inside the declared fill-level range: a target reaching past either end of it, such as a night setback below the range, is recorded as the range allows, and a target band lying wholly outside the range is recorded at the nearest end of it, with a warning. Where one of the two bounds cannot be recorded, that is logged, and the other is still recorded. A usage forecast is recorded in ``<energy_unit>/s``, a negative expected usage as none, with a warning, and a leakage behaviour is recorded as a storage efficiency each time a schedule is asked for (see Changed). Without a sensor, that declaration is not recorded [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#271 <https://github.com/FlexMeasures/flexmeasures-client/pull/271>`__, `#274 <https://github.com/FlexMeasures/flexmeasures-client/pull/274>`__]
* ``FRBCSimple`` takes an ``energy_unit``, defaulting to ``kWh``, which a state of charge, the fill-level band around it, a fill rate and a usage forecast (the latter two per second) are all recorded in. It is one of ``J``, ``kJ``, ``MJ``, ``Wh``, ``kWh`` and ``MWh``, and is refused when the handler is made otherwise. A state of charge goes into a flex model in ``kWh`` or ``MWh``, converted from any other of these, since FlexMeasures takes no other [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#260 <https://github.com/FlexMeasures/flexmeasures-client/pull/260>`__]
* ``FRBCSimple`` takes a ``fill_level_scale``, defaulting to 1, which every fill level and fill rate it sends to FlexMeasures is multiplied by: the state of charge, the flex model, the fill-level band, the fill rate, the usage forecast and the storage efficiency. Translating a schedule back into instructions keeps the resource manager's own units, since it matches fill levels against the operation modes stated in them. A scale of zero or below is refused. It also takes a ``power_unit``, defaulting to ``W``, which an actuator's power is converted into and labelled with when it is posted [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` takes a ``hard_bound_factor``, defaulting to 1.5, which sets how far above the declared fill-level range the hard upper bound of a schedule sits. The bound is widened further where the state of charge a schedule starts from is above it, and a factor below 1 is refused, since FlexMeasures clips the band into the hard bounds and a bound inside the declared range would narrow the band to something the resource manager never asked for [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``FRBCSimple`` takes an optional ``charging_efficiency_sensor_id``, records on it what the actuator's operation modes say about converting drawn power into stored energy, and asks for a schedule against it as ``charging-efficiency``. The ratio between a mode's fill rate and the power it draws is a heat pump's coefficient of performance, and without it the scheduler plans the storage as if an actuator converted power into stored energy one for one, so a plan reaches a fill-level target at a power the actuator cannot deliver it at. FlexMeasures takes one for the whole device, so where a mode converts differently at different fill levels, the element that applies where the schedule starts answers, and where the modes differ, the lowest goes out, and the choice is logged. A mode that draws power while the storage does not fill, such as a standby or circulation mode, states none, rather than an efficiency of zero that would tell the scheduler no power could fill the storage at all, and a mode reserved for abnormal conditions is not counted. It is recorded once per window, and recorded again for the next schedule where FlexMeasures did not confirm storing it; where it cannot be recorded, the schedule is asked for without it, and the log says so [`#253 <https://github.com/FlexMeasures/flexmeasures-client/pull/253>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#272 <https://github.com/FlexMeasures/flexmeasures-client/pull/272>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``fill_rate_in_watts()`` and ``operation_mode_conversion_efficiency()``, which state a fill rate in the watts S2 states a power range in, and take the ratio between the two at a given fill level. A fill rate is per second of whatever unit a fill level is recorded in, so a kilowatt hour per second is 3.6 million watts, and an energy unit neither knows is refused rather than converted by a factor that is wrong by thousands [`#253 <https://github.com/FlexMeasures/flexmeasures-client/pull/253>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* ``FRBCSimple`` takes ``schedule_attempts``, defaulting to five, and ``schedule_retry_interval``, defaulting to two seconds, which bound how often a schedule is asked for and how long between two attempts; fewer than one attempt is refused. v0.9.0 asked once, so any failure cost the storage its instruction for that window, while the resource manager kept polling for it. A scheduling job that failed inside the server is asked for again, unless its cause fails identically on every attempt, as a scheduler colliding with its own belief or an infeasible problem does. So is a server that could not be reached or answered with a 5xx, and a bare HTTP 400 from a FlexMeasures older than v0.33.0, which reports a failed job no other way. A job no worker picked up in time is asked about once more. Giving up is logged as an error that says the storage is left without an instruction [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` takes an optional ``production_price_sensor_id``, for a site where feeding back is priced differently from drawing, as under a feed-in tariff. Without it, both directions are priced by ``price_sensor_id``, as before [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* ``FRBCSimple`` takes ``relax_site_capacity``: ``True`` lets a schedule breach the site's connection capacity at a price, for a site whose capacity is a target set by others, and ``False`` keeps it a hard limit. Unset, it is left to FlexMeasures, which since v1.0 relaxes it by default, so a home whose connection is a physical limit has to pass ``False`` [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* An FRBC handler logs the instructions of a batch once, at debug level, by operation-mode name [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* An FRBC handler tracks the instruction batch the resource manager is working from, and, with its ``send_revocations`` attribute set to ``True``, revokes the instructions of a batch it replaces that are still pending, that is, with no status reported yet, or reported as ``NEW`` or ``ACCEPTED``. Off by default, since a resource manager is not required to honour revocations [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBC.filter_instruction_transitions()``, ``FRBC.send_instruction_batch()`` and ``FRBC.system_description_fingerprint()``, which a handler uses to drop instructions that repeat their predecessor, to send a batch that replaces the one before it (dropped where a newer request arrived meanwhile), and to tell a changed system description from a re-sent one [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``ControlTypeHandler.send_message()``, which handlers use instead of putting messages on the CEM's sending queue themselves [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBCSimple`` takes an optional ``fill_rate_sensor_id``, and records the actuator's fill rate there, as a rate of fill level in ``<energy_unit>/s`` rather than in a power unit, since S2 states a fill rate as fill level per second, which for a heat buffer is not electricity at all [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``flexmeasures_client.s2.clock``, with a ``WallClock`` and a ``SimulatedClock``, and ``CEM(clock=...)``: the time a CEM and every handler it registers go by, timers and the moment a state of charge is known as of included [`#264 <https://github.com/FlexMeasures/flexmeasures-client/pull/264>`__, `#268 <https://github.com/FlexMeasures/flexmeasures-client/pull/268>`__]
* ``CEM(accept_simulation_metadata=True)``, which takes a co-simulation's messages wrapped with the simulated time (``S2Wrapper``) and sets its clock from them. Off by default, a wrapped message is refused as invalid. The example server turns it on with ``S2_ACCEPT_SIMULATION_METADATA`` [`#264 <https://github.com/FlexMeasures/flexmeasures-client/pull/264>`__]
* ``flexmeasures_client.s2.site``: ``FRBC_SITE_SENSORS``, ``ensure_site`` and ``FRBCSiteConfigurator``, which gives each resource manager a FlexMeasures site of its own, named after it, and an FRBC handler wired to it [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* ``CEM(site_configurator=...)``, which sets a resource manager's site up once its details arrive, answering its messages with a temporary error until then. The example server does so with ``S2_SITE_PER_RESOURCE_MANAGER`` [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* ``examples/S2-co-simulation``, a FlexMeasures stack with one CEM per simulated home, for a co-simulation whose resource managers connect over websockets [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__, `#267 <https://github.com/FlexMeasures/flexmeasures-client/pull/267>`__]
* ``flexmeasures_client.s2.measurements.MeasurementRecorder`` and ``CEM(measurement_recorder=...)``: where a resource manager's measured power is recorded, one per server so that a reconnecting resource manager is not recorded twice [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]

Changed
-------

* Breaking for subclasses: ``FRBC.trigger_schedule()`` and ``FRBC.send_storage_status()`` each take a ``request_number`` keyword argument, the number of the planning request they answer, so a subclass that overrides either has to accept it [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__, `#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* Breaking: in ``flexmeasures_client.s2.control_types.FRBC.utils``, ``compute_next_fill_level()`` is now ``compute_next_fill_level(fill_level, fill_rate, step, leakage_behaviour=None, usage_rate=0.0)``, with the step a ``timedelta`` and every rate per second, where it took ``(fill_level, fill_rate, leakage_behaviour, deltaT, usage)`` with ``deltaT`` in hours; and ``power_to_fill_rate_with_metrics()`` returns six values, the operation-mode factor added, instead of five, as do the tuples ``explain_choice()`` takes [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``fm_schedule_to_instructions()`` takes three new keyword arguments. ``fill_level_bounds`` are the fill levels its own running fill level is held between, so a caller that asked for a schedule against other bounds than the declared fill-level range is answered with the operation modes for the storage states the scheduler actually planned; without them the declared range is used, as before. ``leakage_behaviour`` and ``usage_forecast`` lower that running fill level as the storage leaks and is drawn from [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* An FRBC handler plans one schedule at a time, for the newest request only. In v0.9.0 every system description started a solve of its own, and every solve sent its own instruction batch, however many newer ones had arrived meanwhile. Now a request arriving while a schedule is planned waits for it, and is dropped where a newer one arrives meanwhile; a request out of date makes no further attempt after a failed one, is not turned into instructions, and sends no batch [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__, `#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__, `#269 <https://github.com/FlexMeasures/flexmeasures-client/pull/269>`__]
* An FRBC handler asks for a schedule on every storage status, since the state of charge it reports is what a schedule starts from, and on a system description that arrives after the latest status and differs from the one before it, its validity included. So a resource manager is planned for whichever of the two it sends first, and whether or not its fill level moves. v0.9.0 planned on system descriptions only, every one of them, so a resource manager re-sending an unchanged description was planned for again each time: now such a description is acknowledged and otherwise ignored. A state of charge that cannot be recorded is logged, and planned from all the same [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` takes a ``resolution``, defaulting to fifteen minutes: the step a schedule is planned on and its start lined up with, and the step a fill-level target profile, a usage forecast and a storage efficiency are recorded on, and an actuator's power and fill rate are recorded as covering. A ``resolution`` or ``schedule_duration`` that is not positive is refused, and so is a ``schedule_duration`` that is not a whole number of ``resolution`` steps, since FlexMeasures answers with whole steps only. In v0.9.0, whatever the power sensor's resolution, a schedule was read as quarter hours, and an actuator's status recorded as covering one [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A schedule starts at a resolution boundary: the one at or before the system description's ``valid_from`` plus ``valid_from_shift``, or, where that is in the past, the next boundary after now. ``valid_from_shift`` still defaults to one day. v0.9.0 started a schedule at ``valid_from`` plus the shift, unaligned, which left FlexMeasures without prices for a window off its steps. A boundary is found on the clock of the moment being floored, in the offset it carries, as FlexMeasures floors a series posted with it, and in the repeated hour of an autumn DST change it is the later boundary not after the moment [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FRBCSimple`` asks for a schedule within the power ranges the actuator declares: a ``consumption-capacity`` and a ``production-capacity`` from the widest range it states, and, from a FlexMeasures at v1.0.0 or newer, which added the field, each distinct range as one of the ``operation-modes``, except where the ranges together cover exactly what the two capacities allow, without a gap, and so say nothing new while each costs the solver a binary per step. An older server is asked without the power bands, and the log says so, since a flex model naming a field the server does not know is refused outright. The scheduler then plans only power values the actuator can run at, instead of a fractional power that a resource manager has to round into a full-on block, overshooting the capacity its site is held to. An actuator that cannot produce at all is given a production capacity of zero, where before its storage could be planned as if it were a battery feeding the grid. Modes S2 reserves for abnormal conditions are left out of both, and are never instructed into, since a resource manager refuses an instruction into one that is not marked abnormal; an actuator with no other mode is not planned for, with a warning [`#248 <https://github.com/FlexMeasures/flexmeasures-client/pull/248>`__, `#272 <https://github.com/FlexMeasures/flexmeasures-client/pull/272>`__]
* ``FRBCSimple`` holds a storage to its declared fill-level range as a band whose breach is priced, rather than as hard bounds, and plans against what the resource manager declared about its storage. The band goes out with each request as ``soc-minima`` and ``soc-maxima`` over the whole window, with ``relax-soc-constraints`` in the flex context: the declared range, with the latest fill-level target profile, held inside the range, applying where it covers a step, and clipped at zero, since FlexMeasures takes no negative minima. A newer target profile replaces the one before it, as S2 means it to. It goes out with or without the band sensors, which only record it. The hard ``soc-min`` and ``soc-max`` are now zero and ``hard_bound_factor`` times the top of the range, widened to take in a state of charge above it, where v0.9.0 held the storage hard to the declared range: a state a storage really reaches, such as free-floating above the top of its range or being left below the bottom of it, was infeasible, and answered with no schedule at all, or by a fallback scheduler. A storage reported below zero is planned from zero, with a warning, since FlexMeasures refuses a negative ``soc-min``. Against a FlexMeasures older than v0.26.0, which cannot relax the band, the band is left out, with a note in the log, so the storage is planned anywhere between the wide hard bounds. The usage forecast is named as ``soc-usage`` where its sensor is given and the resource manager says it provides one, or has sent one regardless. A leakage behaviour is recorded as a ``storage-efficiency`` each time a schedule is asked for, over the window the schedule covers, from the element that applies at the fill level it starts from, as a share of that fill level or of the middle of the storage's range where it starts below that, and named only where FlexMeasures confirmed storing it. FlexMeasures takes a storage efficiency as a share of the stock where S2 states a rate, so the loss is exact only at that one fill level [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#271 <https://github.com/FlexMeasures/flexmeasures-client/pull/271>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` posts a state of charge in its ``energy_unit``, ``kWh`` by default, and states the flex model's ``soc-unit`` to match (``kWh`` where ``energy_unit`` is neither ``kWh`` nor ``MWh``), where v0.9.0 labelled every state of charge, and the ``soc-unit`` of every schedule, ``MWh``, whatever unit the resource manager reported in: against the example server's ``kWh`` sensor, a fill level of 10 was recorded as 10,000. A resource manager that reports in megawatt hours now needs ``energy_unit="MWh"`` [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` records an actuator's power on the power sensor, and no longer writes anything to ``rm_discharge_sensor_id``, which stays a required argument. v0.9.0 posted minus the fill rate of the first-listed operation mode, labelled ``MWh``, onto that discharge sensor. Now it posts the power of the operation mode the actuator reports it is in, from the power range of the element that applies at the latest fill level, interpolated by the reported factor, as covering one ``resolution`` step. It is posted in the sign the power sensor records consumption in, read from the sensor's ``consumption_is_positive`` attribute (as S2 states it where the sensor cannot be read, with the reason logged), and converted into ``power_unit``. A second status for the same step supersedes the first rather than colliding with it, and a power that cannot be recorded is logged with its value, unit, sensor and moment [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FRBCSimple`` says so, as an error, where a schedule was planned by a fallback scheduler rather than the one it asked for. FlexMeasures answered an infeasible problem that way until v1.0.0 retired it, and an older server either serves that fallback job in place of the failed one or redirects to it, which this client follows, so either way the substitute arrives looking like the schedule that was asked for, though it is a coarse charging policy rather than a plan [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__]
* An FRBC handler refuses a system description of more than one actuator with ``INVALID_CONTENT`` and a diagnostic label, since every plan is made for one, where v0.9.0 acknowledged it and planned for the first actuator alone. A description whose actuator declares timers, or transitions that rule out a switch between its operation modes, is taken with a warning that they are not honoured; transitions allowing every switch, as an on/off device declaring both of its switches does, rule out nothing and are taken without one, and so is each timer the first time an ``FRBC.TimerStatus`` names it; every timer status is now kept [`#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* The example resource manager is a heat pump keeping a hot-water buffer warm: it modulates from 400 to 1500 W at a COP of 4 while the buffer is cold and 3 once it is warm, declares its leakage, a day of hot-water use and a comfort profile, and simulates its buffer from S2's own definitions. It acknowledges each instruction, and reports the transition it asks for when the instruction's ``execution_time`` arrives, rather than printing it and carrying on; reports its fill level every ``S2_EXAMPLE_STATUS_INTERVAL`` seconds; follows only the latest plan, honouring the revocations the example server now sends, and tells the CEM where each instruction stands, so that only what it has not yet carried out is revoked; and sends its leakage, usage forecast and comfort profile again every ``S2_EXAMPLE_FORECAST_INTERVAL`` seconds. The example server records each declaration on a sensor of its own, creates its power sensor with ``consumption_is_positive``, as S2 states power, so that measured and scheduled power point the same way, seeds its demo prices from the current day and again every 12 hours, so that the window it plans for keeps its prices, and sets the site's graphs to show the buffer's fill level within its comfort band, and what fills and draws it [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__, `#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#262 <https://github.com/FlexMeasures/flexmeasures-client/pull/262>`__]

Removed
-------

* ``flexmeasures_client.s2.cem.get_commodity_unit``, which stated power in kW where S2 states it in W [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* ``op_mode_compute_factor``, which worked a factor back out of a fill rate and returned zero wherever an element's fill rate is one value; the factor now comes with the operation mode chosen, from its power range [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``flexmeasures_client.s2.utils.get_validation_error_summary``, which iterated a pydantic ``ValidationError``, which is not iterable, so it raised on any error it was given [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]

Fixed
-----

* A full message history keeps the newest messages: it made room by evicting the newest entry, so it kept a session's first messages and lost every later one but the last. A message set again under the same id becomes the latest [`#274 <https://github.com/FlexMeasures/flexmeasures-client/pull/274>`__]
* ``Handler.is_revoked()`` finds an object the resource manager revoked. The CEM and its handlers stored a revoked object's id as a ``UUID``, so a lookup by its string id always missed [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]
* An FRBC actuator status that arrives before any system description, as one can after a reconnection, is logged and skipped, rather than raising in a background task [`#274 <https://github.com/FlexMeasures/flexmeasures-client/pull/274>`__]
* A CEM records measured power by the moment it was measured, as one average per step of its sensor's resolution (15 minutes on the example's sites, where it was five), once the step is over by its clock, with the first measurement after that, and in watts, as S2 states it. It binned measurements by its own now, which dropped every measurement a resource manager sent after the fact, as a co-simulation's does, labelled watts as kilowatts, and kept its samples in a buffer shared by every CEM in the process. Recording happens in the background, retried a few times where it fails [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* A CEM without a sensor for a resource manager's measured power no longer records an L1 measurement on sensor 357, which was whichever sensor of whichever site on the server had that id, and answered it as recorded [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* A message of the control type a CEM is selecting, arriving before the resource manager confirmed the selection, is taken as that confirmation, since a resource manager sends a control type's messages only once it works in it. A CEM used to stay without a control type, answering each such message with a temporary error that the resource manager retried, as a co-simulation's reconnecting resource managers showed. A message of any other control type activates nothing [`#263 <https://github.com/FlexMeasures/flexmeasures-client/pull/263>`__]
* Closing a control-type handler cancels the work it still has in flight, recording included, and the example server's session ends with its connection, where a session used to keep working after its resource manager had disconnected [`#263 <https://github.com/FlexMeasures/flexmeasures-client/pull/263>`__]
* A control-type handler can log before it is registered, and one without its own ``close()`` no longer breaks CEM teardown [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__]
* An ``FRBCSimple`` handler accepts an ``FRBC.FillLevelTargetProfile``, an ``FRBC.UsageForecast`` and an ``FRBC.LeakageBehaviour`` instead of raising ``NotImplementedError`` for each. The CEM dispatches these as tasks nobody awaits, so every connection by a resource manager that sends all three logged three "Task exception was never retrieved" tracebacks [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` asks for a schedule in watts, which is what S2 states an operation mode's power ranges in, rather than in whatever unit the power sensor happens to be recorded in. The example server creates its power sensor in ``kW``, so every scheduled power was read as a thousandth of itself: it then sat nearest the operation mode that draws nothing, and the resource manager was instructed to idle through a schedule that asked it to run [`#247 <https://github.com/FlexMeasures/flexmeasures-client/pull/247>`__]
* On a state-of-charge sensor that records instants, a state of charge is recorded as a reading at an instant, where it was recorded over the minute after it, which FlexMeasures took for a forecast. On a sensor with a resolution, which refuses an instant, it is still recorded over a minute [`#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* Translating a schedule into instructions takes every rate per second, as S2 states it. It advanced its running fill level by a fill rate per second times a step in hours, so the level moved 3600 times too little, and every step of a schedule was answered with the operation mode for the fill level the schedule started from. Leakage now lowers that level, from the element of the latest ``FRBC.LeakageBehaviour`` that applies, where it used to be read from two fields S2 does not have and so never counted, and so does usage, from the latest ``FRBC.UsageForecast``. Each step is followed exactly, as a resource manager follows an instruction: a fill level crossing into another element, or a change in usage within a step, is taken at the moment it happens, and a level that reaches a boundary its far side would push it back from stays at that boundary [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* Translating a schedule into instructions no longer depends on the order a mode lists its elements in: where two elements share a boundary, the one that applies answers, not the first listed [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* A schedule is read at the resolution FlexMeasures answers in, its duration over its number of values, rather than as quarter hours whatever it was, which made a schedule for an hourly or a five-minute power sensor raise, so that no instruction went out [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A new instruction goes out wherever the operation mode or its factor changes, including at a constant power where the fill level passes into another element of the mode, and only there, rather than wherever the power changes. A difference in the factor of a millionth or less, which is the noise of converting a schedule from MW to W, is not a new instruction [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* An instruction asks for the factor that draws the power the schedule asks for, taken from the operation mode's power range. It was worked back out of the fill rate, which asked for the bottom of the power range wherever the fill rate is one value and the power a range [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``leakage_behaviour_to_storage_efficiency()`` compounds a leakage rate per second, as S2 states it, where it compounded it per hour, which declared a buffer losing about 7% of its heat over twelve hours as holding it to within 0.002%. It takes the rate of the element that applies at a new ``fill_level`` keyword argument, or of the leakiest element where none is given, where it took the last one listed, which S2 does not require to be the top one; and a new ``share_of`` keyword argument sets the fill level the loss is a share of, which is otherwise the top of the highest element's range [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FillRateBasedControlTUNES`` posts its usage forecast per hour, which is what a value labelled MW means to FlexMeasures, where it posted a quarter-hour amount that FlexMeasures planned as a quarter of the real draw. It reads it back in the resource manager's own units, per second, dividing out the fill-level scale it was posted with, and plans its instructions with its latest leakage behaviour [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``FillRateBasedControlTUNES`` plans, and records its conversion efficiencies, when an operation mode has no diagnostic label, which S2 makes optional. It looked for ``THP`` or ``NES`` inside a label that may be absent, so one unlabelled mode stopped planning, and the recording of the efficiencies of every mode listed after it [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]
* ``FillRateBasedControlTUNES`` records an ``FRBC.LeakageBehaviour`` instead of answering it with ``PERMANENT_ERROR``. It read its fill-level scale from a misspelt attribute, and the error that raised was taken for a failed post, so no leakage behaviour was ever recorded [`#255 <https://github.com/FlexMeasures/flexmeasures-client/pull/255>`__]
* Importing either S2 example script no longer runs it, so they can be read, imported and tested like any other module [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__] [`issue #251 <https://github.com/FlexMeasures/flexmeasures-client/issues/251>`__]
* The example CEM server answers an S2 handshake at once, instead of after configuring its site with FlexMeasures, which took longer than the timeout a resource manager waits for that answer [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__]

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
