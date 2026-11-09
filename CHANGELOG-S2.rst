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

* ``FRBCSimple`` can record what a resource manager declares about its storage, so it can be seen and planned with. Give it any of the new ``soc_minima_sensor_id``, ``soc_maxima_sensor_id``, ``usage_forecast_sensor_id`` and ``leakage_behaviour_sensor_id``; a declaration without a sensor is not recorded.

  - A fill-level target profile is recorded on the minima and maxima sensors, if both are given. It is recorded on the handler's ``resolution`` steps, starting from the step its start falls in. It is kept inside the declared fill-level range: a target past either end, such as a night setback below the range, is cut back to the range, and a target band wholly outside the range is recorded at its nearest end, with a warning. If one of the two bounds cannot be recorded, this is logged and the other is still recorded.
  - A usage forecast is recorded in ``<energy_unit>/s``. A negative expected usage, which S2 allows, is recorded as stated. Without a usage correction (see below), a schedule request carries the latest forecast without its negative values, since FlexMeasures would plan them as a gain the storage cannot refuse. A FlexMeasures server before v0.23.0 only plans with what is recorded, so there a negative expected usage is recorded as zero, with a warning [`#286 <https://github.com/FlexMeasures/flexmeasures-client/pull/286>`__].
  - A leakage behaviour is recorded as the leakage rate it states, in ``<energy_unit>/s``, each time a schedule is requested (see Changed) [`#PRNUM <https://github.com/FlexMeasures/flexmeasures-client/pull/PRNUM>`__].

  [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#271 <https://github.com/FlexMeasures/flexmeasures-client/pull/271>`__, `#274 <https://github.com/FlexMeasures/flexmeasures-client/pull/274>`__]
* ``FRBCSimple`` takes an ``energy_unit`` (default ``kWh``). The state of charge, the fill-level band, the fill rate and the usage forecast are recorded in this unit (the last two per second). Allowed units are ``J``, ``kJ``, ``MJ``, ``Wh``, ``kWh`` and ``MWh``; any other unit is refused when the handler is created. In a flex model, the state of charge is stated in ``kWh`` or ``MWh``, converted from the other units, because FlexMeasures accepts no others [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#260 <https://github.com/FlexMeasures/flexmeasures-client/pull/260>`__]
* ``FRBCSimple`` takes a ``fill_level_scale`` (default 1). Every fill level and fill rate sent to FlexMeasures is multiplied by it: the state of charge, the flex model, the fill-level band, the fill rate, the usage forecast and the leakage. Translating a schedule into instructions keeps the resource manager's own units, because it matches fill levels against the operation modes, which are stated in those units. A scale of zero or below is refused. ``FRBCSimple`` also takes a ``power_unit`` (default ``W``), which an actuator's power is converted into and posted in [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` takes a ``hard_bound_factor`` (default 1.5). It sets how far above the declared fill-level range a schedule's hard upper bound lies. If a schedule starts from a state of charge above that bound, the bound is raised to include it. A factor below 1 is refused: FlexMeasures clips the band to the hard bounds, so a hard upper bound inside the declared range would narrow the band to something the resource manager never asked for [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``FRBCSimple`` can take the actuator's charging efficiency into account. Give it the new ``charging_efficiency_sensor_id``: it records there what the operation modes say about converting drawn power into stored energy, and requests a schedule with it as ``charging-efficiency``. For a heat pump, this ratio is its coefficient of performance. Without it, the scheduler assumes power converts into stored energy one to one, and plans to reach fill-level targets at a power the actuator cannot deliver.

  - FlexMeasures takes one efficiency per device. If a mode's efficiency depends on the fill level, the value where the schedule starts is used. If modes differ, the lowest is used, and the choice is logged.
  - A mode that draws power without filling the storage, such as standby or circulation, does not count. Counting it as zero would tell the scheduler that the storage cannot be filled at all. Modes reserved for abnormal conditions do not count either.
  - The efficiency is recorded once per window. It is recorded again for the next schedule if FlexMeasures did not confirm storing it. If it cannot be recorded, the schedule is requested without it, and this is logged.

  [`#253 <https://github.com/FlexMeasures/flexmeasures-client/pull/253>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#272 <https://github.com/FlexMeasures/flexmeasures-client/pull/272>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``fill_rate_in_watts()`` converts a fill rate into watts, the unit of S2 power ranges. ``operation_mode_conversion_efficiency()`` gives the ratio between the two at a given fill level. A fill rate is per second, in the unit fill levels are recorded in, so one kilowatt hour per second is 3.6 million watts. An unknown energy unit is refused, rather than converted with a factor that is off by thousands [`#253 <https://github.com/FlexMeasures/flexmeasures-client/pull/253>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* ``FRBCSimple`` retries a failed schedule request. Two new arguments set the limits: ``schedule_attempts`` (default 5, at least 1) and ``schedule_retry_interval`` (default 2 seconds). In v0.9.0, a single failure left the storage without an instruction for that window, while the resource manager kept polling for one.

  - Retried: a scheduling job that failed on the server, an unreachable server, a 5xx response, and a bare HTTP 400 from a FlexMeasures older than v0.33.0 (its only way to report a failed job).
  - Not retried: failures that would repeat on every attempt, such as an infeasible problem or a scheduler colliding with its own belief.
  - A job that no worker picked up in time is checked once more.
  - Giving up is logged as an error, saying that the storage is left without an instruction.

  [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` takes an optional ``production_price_sensor_id``, for sites where feeding back is priced differently from drawing, such as under a feed-in tariff. Without it, ``price_sensor_id`` prices both directions, as before [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* ``FRBCSimple`` takes ``relax_site_capacity``. With ``True``, a schedule may exceed the site's connection capacity at a price; use this where the capacity is a target set by others. With ``False``, the capacity is a hard limit. If unset, FlexMeasures decides, and since v1.0 it relaxes the capacity by default. So for a home whose connection is a physical limit, pass ``False`` [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* An FRBC handler logs each instruction batch once, at debug level, listing the operation modes by name [`#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__]
* An FRBC handler tracks the instruction batch the resource manager is working from, and can revoke instructions it no longer wants carried out. Set its ``send_revocations`` attribute to ``True``. When a new batch replaces that one, the handler revokes the old batch's pending instructions: those without a reported status, or reported as ``NEW`` or ``ACCEPTED``. This is off by default, because a resource manager does not have to honour revocations [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* New ``FRBC`` methods for handlers. ``filter_instruction_transitions()`` drops instructions that repeat the previous one. ``send_instruction_batch()`` sends a batch that replaces the previous one, unless a newer request has arrived in the meantime. ``system_description_fingerprint()`` tells a changed system description from a re-sent one [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``ControlTypeHandler.send_message()``. Handlers now use it to send messages, instead of putting them on the CEM's sending queue themselves [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBCSimple`` takes an optional ``fill_rate_sensor_id``, and records the actuator's fill rate there. The fill rate is recorded in ``<energy_unit>/s``, not as a power: S2 states a fill rate as fill level per second, which for a heat buffer is not electricity at all [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``flexmeasures_client.s2.clock``, with a ``WallClock`` and a ``SimulatedClock``, and ``CEM(clock=...)``. The clock sets the time for the CEM and for every handler it registers. That includes their timers, and the time a recorded state of charge is known from [`#264 <https://github.com/FlexMeasures/flexmeasures-client/pull/264>`__, `#268 <https://github.com/FlexMeasures/flexmeasures-client/pull/268>`__]
* ``CEM(accept_simulation_metadata=True)`` accepts messages from a co-simulation that are wrapped with the simulated time (``S2Wrapper``), and sets the CEM's clock from them. It is off by default, and then a wrapped message is refused as invalid. The example server turns it on with ``S2_ACCEPT_SIMULATION_METADATA`` [`#264 <https://github.com/FlexMeasures/flexmeasures-client/pull/264>`__]
* ``flexmeasures_client.s2.site``, with ``FRBC_SITE_SENSORS``, ``ensure_site`` and ``FRBCSiteConfigurator``. It gives each resource manager its own FlexMeasures site, named after it, with an FRBC handler connected to that site's sensors [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* ``CEM(site_configurator=...)`` sets up a resource manager's site once its details arrive. Until then, the CEM answers its messages with a temporary error. The example server enables this with ``S2_SITE_PER_RESOURCE_MANAGER`` [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* ``examples/S2-co-simulation``: a FlexMeasures stack with one CEM per simulated home, for co-simulations whose resource managers connect over websockets [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__, `#267 <https://github.com/FlexMeasures/flexmeasures-client/pull/267>`__]
* ``flexmeasures_client.s2.measurements.MeasurementRecorder`` and ``CEM(measurement_recorder=...)`` record a resource manager's measured power. Use one recorder per server, so that a resource manager that reconnects is not recorded twice [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* ``FRBCSimple`` can correct a usage forecast that no plan can absorb, instead of leaving the storage without a plan. Give it the new ``usage_correction_sensor_id``; it needs FlexMeasures v1.1.0 or newer. A forced gain at a full storage, or a draw at an empty one that the actuator cannot keep up with, used to make every plan infeasible.

  - The plan may move each step's expected usage towards zero, never past it, and only where nothing physical keeps the storage within its hard bounds. It is recorded on the new sensor.
  - A negative expected usage is then planned as stated, instead of as zero.
  - The request sets the breach price of the fill-level band relative to the highest market price over the window (``soc_breach_price_factor``, default 10), instead of leaving it to FlexMeasures. The correction is priced above anything else a unit of stock can be worth, including the site's own stored peak and commitment prices, except a breach of the site's capacity. The site's breach prices are kept from the asset, or else set at FlexMeasures' default, which FlexMeasures itself only fills in for a flex context without commodities, so a site keeps to the capacity it is steered with.
  - Where a correction cannot be planned, for example without readable prices, the forecast is planned without its negative values, and the log says why.
  - ``FRBCSiteConfigurator`` takes ``correct_usage`` to give each site the sensor, and the example server does so with ``S2_CORRECT_USAGE``. Both are off by default.

  [`#286 <https://github.com/FlexMeasures/flexmeasures-client/pull/286>`__]

Changed
-------

* Breaking for subclasses: ``FRBC.trigger_schedule()`` and ``FRBC.send_storage_status()`` each take a ``request_number`` keyword argument: the number of the planning request they answer. A subclass that overrides either method has to accept it [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__, `#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* Breaking: two functions in ``flexmeasures_client.s2.control_types.FRBC.utils`` changed signature.

  - ``compute_next_fill_level()`` is now ``compute_next_fill_level(fill_level, fill_rate, step, leakage_behaviour=None, usage_rate=0.0)``. The step is a ``timedelta``, and every rate is per second. Before, it was ``(fill_level, fill_rate, leakage_behaviour, deltaT, usage)``, with ``deltaT`` in hours.
  - ``power_to_fill_rate_with_metrics()`` returns six values instead of five; the operation-mode factor is new. The tuples that ``explain_choice()`` takes changed the same way.

  [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``fm_schedule_to_instructions()`` takes three new keyword arguments. ``fill_level_bounds`` are the bounds of the fill level it tracks while translating. Pass them if the schedule was requested with bounds other than the declared fill-level range, so that each step gets the operation mode for the state the scheduler actually planned. Without them, the declared range is used, as before. ``leakage_behaviour`` and ``usage_forecast`` lower the tracked fill level as the storage leaks and is used [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* An FRBC handler plans one schedule at a time, and only for the newest request. In v0.9.0, every system description started its own solve, and every solve sent its own instruction batch, even when newer ones had arrived. Now a request that arrives during planning waits. It is dropped if an even newer request arrives. A request that is out of date is not retried after a failure, not turned into instructions, and sends no batch [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__, `#261 <https://github.com/FlexMeasures/flexmeasures-client/pull/261>`__, `#269 <https://github.com/FlexMeasures/flexmeasures-client/pull/269>`__]
* An FRBC handler requests a schedule on every storage status, because a schedule starts from the reported state of charge. It also requests one on a system description that arrives after the latest status and differs from the previous one, including in its validity. So a resource manager gets a plan for whichever of the two it sends first, whether or not its fill level changes. In v0.9.0, only system descriptions triggered planning, every one of them, so re-sending an unchanged description triggered a new plan each time. Now such a description is acknowledged and otherwise ignored. If the state of charge cannot be recorded, this is logged, and the schedule is still planned from it [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` takes a ``resolution`` (default 15 minutes). It is the step a schedule is planned on and aligned to. Fill-level target profiles, usage forecasts and storage efficiencies are recorded on this step, and each recorded actuator power and fill rate covers one step. A ``resolution`` or ``schedule_duration`` that is not positive is refused. So is a ``schedule_duration`` that is not a whole number of steps, because FlexMeasures only answers with whole steps. In v0.9.0, a schedule was always read as quarter hours, whatever the power sensor's resolution, and an actuator status was recorded as covering a quarter hour [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A schedule starts on a resolution boundary: the last one at or before the system description's ``valid_from`` plus ``valid_from_shift``, or, if that has passed, the next one after now. ``valid_from_shift`` still defaults to one day. In v0.9.0, a schedule started exactly at ``valid_from`` plus the shift. That moment could lie between steps, which left FlexMeasures without prices for the window. Boundaries are computed in the UTC offset of the moment itself, as FlexMeasures does for a series posted with that offset. In the repeated hour at the end of daylight saving time, the later boundary that is not after the moment is used [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FRBCSimple`` keeps a schedule within the power ranges the actuator declares, so the scheduler only plans powers the actuator can run at. Before, it could plan a fractional power, which the resource manager had to round up to a full-on block, exceeding the site's capacity.

  - The widest declared range sets ``consumption-capacity`` and ``production-capacity``. An actuator that cannot produce gets a production capacity of zero; before, its storage could be planned as if it were a battery feeding the grid.
  - Each distinct range is also sent as one of the ``operation-modes``, if the server runs FlexMeasures v1.0.0 or newer (the release that added this field). This is skipped if the ranges together cover exactly what the capacities allow, without gaps: then they add nothing, and each one would cost the solver a binary variable per step. An older server is asked without the operation modes, and this is logged, because it would refuse a flex model with a field it does not know.
  - Modes that S2 reserves for abnormal conditions are left out of both the capacities and the operation modes, and are never used in instructions: a resource manager refuses an instruction into such a mode if the instruction is not marked as abnormal. An actuator that only has such modes is not planned for, with a warning.

  [`#248 <https://github.com/FlexMeasures/flexmeasures-client/pull/248>`__, `#272 <https://github.com/FlexMeasures/flexmeasures-client/pull/272>`__]
* ``FRBCSimple`` treats the declared fill-level range as a soft band whose breach is priced, instead of as hard bounds. It also plans with what the resource manager declared about its storage.

  - The band is sent with every schedule request as ``soc-minima`` and ``soc-maxima`` over the whole window, with ``relax-soc-constraints`` in the flex context. It is the declared range, narrowed by the latest fill-level target profile wherever that profile covers a step, and clipped at zero, because FlexMeasures accepts no negative minima. A newer target profile replaces the previous one, as S2 intends. The band is sent whether or not the band sensors are given; they only record it.
  - The hard bounds ``soc-min`` and ``soc-max`` are now zero and ``hard_bound_factor`` times the top of the range, raised to include a state of charge above it. In v0.9.0, the declared range itself was the hard bound. So a storage that really was outside it, for example floating above the top or left below the bottom, made the problem infeasible: there was no schedule at all, or one from a fallback scheduler. A storage reported below zero is planned from zero, with a warning, because FlexMeasures refuses a negative ``soc-min``.
  - FlexMeasures older than v0.26.0 cannot relax the band. Then the band is left out, and this is logged, so the storage is planned anywhere between the wide hard bounds.
  - The usage forecast is sent as ``soc-usage`` if its sensor is given and the resource manager provides a forecast (or has sent one anyway).
  - The leakage behaviour is planned as part of the usage: the leakage rate of the fill-level range the storage starts in is added to ``soc-usage`` over the whole window, and sent with the request. S2 states leakage as a rate, so it is not planned as a ``storage-efficiency``, which FlexMeasures takes as a share of the stock, and which would make the storage leak in proportion to how full it is. A flat rate keeps draining an empty storage, as any forced usage does; with a usage correction, the plan can take that away. It needs FlexMeasures v0.23.0 or newer, which takes a usage with the request; an older server is planned without leakage, and this is logged [`#PRNUM <https://github.com/FlexMeasures/flexmeasures-client/pull/PRNUM>`__].

  [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__, `#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#271 <https://github.com/FlexMeasures/flexmeasures-client/pull/271>`__, `#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* ``FRBCSimple`` posts the state of charge in its ``energy_unit`` (``kWh`` by default), and sets the flex model's ``soc-unit`` to match (to ``kWh`` if ``energy_unit`` is neither ``kWh`` nor ``MWh``). In v0.9.0, every state of charge and every schedule's ``soc-unit`` was labelled ``MWh``, whatever unit the resource manager reported in. On the example server's ``kWh`` sensor, a fill level of 10 was recorded as 10,000. A resource manager that reports in megawatt hours now needs ``energy_unit="MWh"`` [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` records the actuator's power on the power sensor. It no longer writes to ``rm_discharge_sensor_id``, which is still a required argument. In v0.9.0, it posted the negative fill rate of the first listed operation mode, labelled ``MWh``, to that discharge sensor.

  - The power is now that of the operation mode the actuator reports being in: taken from the power range that applies at the latest fill level, interpolated by the reported factor, and posted as covering one ``resolution`` step.
  - The sign follows the power sensor's ``consumption_is_positive`` attribute. If the sensor cannot be read, S2's sign is used, and the reason is logged. The power is converted into ``power_unit``.
  - A second status for the same step replaces the first, instead of colliding with it. If the power cannot be recorded, the log gives its value, unit, sensor and time.

  [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__, `#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__, `#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FRBCSimple`` logs an error if a schedule was made by a fallback scheduler instead of the requested one. Until v1.0.0, FlexMeasures answered an infeasible problem that way. An older server either serves the fallback job in place of the failed one, or redirects to it, which this client follows. Either way, the substitute looks like the requested schedule, but it is only a coarse charging policy, not a plan [`#254 <https://github.com/FlexMeasures/flexmeasures-client/pull/254>`__]
* An FRBC handler refuses a system description with more than one actuator, answering ``INVALID_CONTENT`` with a diagnostic label, because every plan is made for one actuator. In v0.9.0, it accepted such a description and planned for the first actuator only.

  - An actuator that declares timers, or transitions that rule out switching between some of its operation modes, is accepted with a warning that these are not honoured. Transitions that allow every switch rule nothing out, so they cause no warning; an on/off device that declares both its switches is an example.
  - The first ``FRBC.TimerStatus`` for each timer also causes the warning. Every timer status is now stored.

  [`#273 <https://github.com/FlexMeasures/flexmeasures-client/pull/273>`__]
* The example resource manager is now a heat pump that keeps a hot-water buffer warm.

  - It modulates from 400 to 1500 W, with a COP of 4 while the buffer is cold and 3 once it is warm. It declares its leakage, a day of hot-water use and a comfort profile, and simulates its buffer using S2's own definitions.
  - It acknowledges each instruction, and reports the transition it asks for when the instruction's ``execution_time`` arrives, instead of printing it and moving on. It reports its fill level every ``S2_EXAMPLE_STATUS_INTERVAL`` seconds.
  - It follows only the latest plan, and honours the revocations the example server now sends. It tells the CEM the status of each instruction, so that only instructions not yet carried out are revoked.
  - It re-sends its leakage, usage forecast and comfort profile every ``S2_EXAMPLE_FORECAST_INTERVAL`` seconds.

  The example server records each declaration on its own sensor. It creates its power sensor with ``consumption_is_positive``, as S2 states power, so that measured and scheduled power have the same sign. It seeds its demo prices from the current day, and again every 12 hours, so that the window it plans for keeps its prices. It sets up the site's graphs to show the buffer's fill level within its comfort band, and what fills and drains it [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__, `#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__, `#262 <https://github.com/FlexMeasures/flexmeasures-client/pull/262>`__]

Removed
-------

* ``flexmeasures_client.s2.cem.get_commodity_unit``. It stated power in kW, while S2 states it in W [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* ``op_mode_compute_factor``. It derived a factor from a fill rate, and returned zero wherever an element has a single fill rate. The factor now comes with the chosen operation mode, from its power range [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``flexmeasures_client.s2.utils.get_validation_error_summary``. It iterated over a pydantic ``ValidationError``, which is not iterable, so it raised on every error [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]

Fixed
-----

* A full message history now keeps the newest messages. It used to make room by evicting the newest entry, so it kept a session's first messages and lost all later ones except the last. A message stored again under the same id now counts as the latest [`#274 <https://github.com/FlexMeasures/flexmeasures-client/pull/274>`__]
* ``Handler.is_revoked()`` now finds objects the resource manager revoked. The CEM and its handlers stored a revoked object's id as a ``UUID``, so a lookup by its string id always missed [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]
* An FRBC actuator status that arrives before any system description is logged and skipped, instead of raising an error in a background task. This can happen after a reconnection [`#274 <https://github.com/FlexMeasures/flexmeasures-client/pull/274>`__]
* A CEM records measured power by the time it was measured. It records one average per step of the sensor's resolution (15 minutes on the example's sites, previously five), in watts, as S2 states it. A step is recorded with the first measurement that arrives after the step has ended by the CEM's clock. Before, measurements were grouped by the CEM's current time, which dropped every measurement sent after the fact, as co-simulations do. They were also labelled as kilowatts instead of watts, and kept in a buffer shared by every CEM in the process. Recording happens in the background, with a few retries if it fails [`#266 <https://github.com/FlexMeasures/flexmeasures-client/pull/266>`__]
* A CEM without a sensor for a resource manager's measured power no longer records L1 measurements on sensor 357. That was whatever sensor had that id on the server, on any site, and the CEM reported the measurement as recorded [`#265 <https://github.com/FlexMeasures/flexmeasures-client/pull/265>`__]
* A CEM that is selecting a control type now takes a message of that control type as confirmation of the selection, because a resource manager only sends such messages once it uses that control type. Before, the CEM stayed without a control type, and answered each such message with a temporary error, which the resource manager retried; co-simulations whose resource managers reconnect showed this. A message of any other control type activates nothing [`#263 <https://github.com/FlexMeasures/flexmeasures-client/pull/263>`__]
* Closing a control-type handler cancels its remaining work, including recording. The example server's session now ends when its connection closes; it used to keep working after the resource manager disconnected [`#263 <https://github.com/FlexMeasures/flexmeasures-client/pull/263>`__]
* A control-type handler can log before it is registered, and a handler without its own ``close()`` no longer breaks the CEM's teardown [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__]
* An ``FRBCSimple`` handler now accepts ``FRBC.FillLevelTargetProfile``, ``FRBC.UsageForecast`` and ``FRBC.LeakageBehaviour`` messages, instead of raising ``NotImplementedError`` for each. The CEM runs these as tasks that nothing awaits, so a resource manager sending all three caused three "Task exception was never retrieved" tracebacks on every connection [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` requests schedules in watts, the unit of S2 operation-mode power ranges, instead of in the power sensor's unit. The example server's power sensor is in ``kW``, so every scheduled power was read as a thousandth of its value. It then matched the operation mode that draws nothing, and the resource manager was told to idle while the schedule asked it to run [`#247 <https://github.com/FlexMeasures/flexmeasures-client/pull/247>`__]
* On a state-of-charge sensor that records instants, a state of charge is now recorded as a reading at an instant. Before, it was recorded as covering the minute after it, which FlexMeasures took for a forecast. On a sensor with a resolution, which refuses instants, it is still recorded over one minute [`#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* Translating a schedule into instructions now treats every rate as per second, as S2 states it.

  - It used to advance the tracked fill level by a fill rate per second times a step in hours, so the level moved 3600 times too little. As a result, every step got the operation mode for the fill level the schedule started from.
  - Leakage now lowers the tracked fill level, using the element of the latest ``FRBC.LeakageBehaviour`` that applies. Before, leakage was read from two fields that S2 does not have, so it was never counted. Usage from the latest ``FRBC.UsageForecast`` now lowers it too.
  - Each step is followed exactly, as a resource manager follows an instruction. If the fill level crosses into another element, or usage changes within a step, that is taken into account at the moment it happens. A level that reaches a boundary, beyond which it would be pushed back, stays at that boundary.

  [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* Translating a schedule into instructions no longer depends on the order in which a mode lists its elements. Where two elements share a boundary, the one that applies is used, not the first one listed [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__]
* A schedule is read at the resolution FlexMeasures answers in: its duration divided by its number of values. It used to be read as quarter hours, so a schedule for an hourly or a five-minute power sensor raised an error, and no instruction was sent [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* A new instruction is sent whenever the operation mode or its factor changes, and only then, instead of whenever the power changes. This includes a constant power at which the fill level moves into another element of the mode. A change in the factor of a millionth or less does not count: that is rounding noise from converting a schedule from MW to W [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* An instruction now asks for the factor that draws the scheduled power, taken from the operation mode's power range. It used to derive the factor from the fill rate, which asked for the bottom of the power range wherever the fill rate is a single value and the power a range [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``leakage_behaviour_to_storage_efficiency()`` compounds a leakage rate per second, as S2 states it. It compounded it per hour, so a buffer that loses about 7% of its heat over twelve hours was described as losing 0.002%.

  - A new ``fill_level`` keyword argument selects the element that applies at that fill level. Without it, the leakiest element is used. Before, it used the last element listed, which S2 does not require to be the top one.
  - A new ``share_of`` keyword argument sets the fill level that the loss is a share of. Without it, that is the top of the highest element's range.

  [`#256 <https://github.com/FlexMeasures/flexmeasures-client/pull/256>`__, `#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__, `#259 <https://github.com/FlexMeasures/flexmeasures-client/pull/259>`__]
* ``FillRateBasedControlTUNES`` posts its usage forecast per hour, which is what a value in MW means to FlexMeasures. It used to post a quarter-hour amount, which FlexMeasures planned as a quarter of the real usage. It reads the forecast back in the resource manager's own units, per second, dividing out the fill-level scale it was posted with, and plans its instructions with its latest leakage behaviour [`#258 <https://github.com/FlexMeasures/flexmeasures-client/pull/258>`__]
* ``FillRateBasedControlTUNES`` now plans, and records its conversion efficiencies, when an operation mode has no diagnostic label, which S2 makes optional. It looked for ``THP`` or ``NES`` in a label that may be missing, so a single unlabelled mode stopped planning, and stopped the efficiencies of all modes listed after it from being recorded [`#257 <https://github.com/FlexMeasures/flexmeasures-client/pull/257>`__]
* ``FillRateBasedControlTUNES`` records an ``FRBC.LeakageBehaviour`` instead of answering it with ``PERMANENT_ERROR``. It read its fill-level scale from a misspelt attribute, and the resulting error was mistaken for a failed post, so no leakage behaviour was ever recorded [`#255 <https://github.com/FlexMeasures/flexmeasures-client/pull/255>`__]
* Importing either S2 example script no longer runs it, so they can be read, imported and tested like any other module [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__] [`issue #251 <https://github.com/FlexMeasures/flexmeasures-client/issues/251>`__]
* The example CEM server answers an S2 handshake immediately. It used to configure its site in FlexMeasures first, which took longer than a resource manager waits for the answer [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__]

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
