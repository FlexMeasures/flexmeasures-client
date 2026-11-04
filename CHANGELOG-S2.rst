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
* ``FRBCSimple`` takes an ``energy_unit``, which a state of charge and the fill-level minima and maxima around it are all recorded in [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* ``FRBCSimple`` takes a ``hard_bound_factor``, defaulting to 1.5, which sets how far above the declared fill-level range the hard upper bound of a schedule sits. The bound is widened further where the state of charge a schedule starts from is above it, and a factor below 1 is refused, since FlexMeasures clips the band into the hard bounds and a bound inside the declared range would narrow the band to something the resource manager never asked for [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]

Changed
-------

* ``FRBC.trigger_schedule()`` takes the number of the request it answers as a keyword argument, so a subclass that overrides it has to accept that argument [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBC.send_storage_status()`` likewise takes the number of the planning request it answers, since a storage status is now what asks for a schedule [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* ``FRBCSimple`` takes a ``resolution``, defaulting to fifteen minutes, which a schedule's start is lined up with [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* ``FRBCSimple`` asks for a schedule against what the resource manager declared about its storage: the fill levels it wants to be kept between as ``soc-minima`` and ``soc-maxima``, the usage it expects as ``soc-usage``, and what its storage leaks as ``storage-efficiency``, each only where a sensor holds it. Recording those declarations changed nothing about the schedule that came back until the flex model named them [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``fm_schedule_to_instructions()`` takes the fill levels its own running fill level is held between, so a caller that asked for a schedule against other bounds than the declared fill-level range is answered with the operation modes for the storage states the scheduler actually planned. Without them the declared range is used, as before [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``FRBCSimple`` asks for the declared fill-level range as a band whose breach is priced, with ``relax-soc-constraints`` in the flex context, rather than as the hard ``soc-min`` and ``soc-max``, which become wide bounds at zero and half again above the range, each widened to take in a state of charge beyond them. A hard bound at the declared range made states a storage really reaches infeasible, such as free-floating above the top of its range or being left below the bottom of it by a previous schedule, and an infeasible job is answered by the fallback scheduler, which ignores the band altogether [`#245 <https://github.com/FlexMeasures/flexmeasures-client/pull/245>`__]
* ``FRBCSimple`` posts a state of charge in ``kWh`` rather than ``MWh``, since S2 is a residential protocol and the example server's own state-of-charge sensor is in kilowatt hours. A deployment whose state-of-charge sensor is in megawatt hours has to pass ``energy_unit="MWh"`` to keep its readings a thousand times smaller than they would otherwise become [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]

Fixed
-----

* ``FRBCSimple`` records a second actuator status for the same moment as superseding the first, rather than colliding with it. FlexMeasures keys a belief on its sensor, its source, the event and the moment it was formed, and rounds that moment down to the second, so two statuses inside one second were one belief and the later one was refused. A resource manager sends a status for each instruction it applies, and this handler sends several at once, so the power that survived was the one the actuator had already left behind [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* A measurement that cannot be recorded is logged with the value, the unit, the sensor and the moment it was for, rather than disappearing, whether FlexMeasures refused it, the server could not be reached, or the unit it would have been posted in is one nothing converts into. The post runs in a task nobody awaits, so until now a refused measurement left the power sensor holding a schedule and nothing else, with the reason visible only as a dead task in the log [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` converts an actuator's power into the unit it posts in, rather than labelling a value S2 states in watts with whatever ``power_unit`` says. A deployment recording its power sensor in ``kW`` had 883.7 W posted as 883.7 kW, which FlexMeasures stored as the thousandfold it claimed to be; the default of ``W`` needed no conversion and was unaffected [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* The example server creates its power sensor with ``consumption_is_positive``, as S2 states power, so that an actuator's measured power and the schedule planned for it point the same way on the sensor that holds both [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` posts an actuator's power in the sign the power sensor records consumption in, read from the sensor's ``consumption_is_positive`` attribute, rather than in S2's own convention of positive for consumption. FlexMeasures defaults a plain power sensor to the other convention and flips a schedule's sign on the way in, so a measurement of the actuator consuming 883.7 W sat opposite the plan to consume it, which reads as a device doing the reverse of what it was asked. A sensor that cannot be read is posted to as S2 states it, and the reason logged [`#252 <https://github.com/FlexMeasures/flexmeasures-client/pull/252>`__]
* ``FRBCSimple`` asks for a schedule in watts, which is what S2 states an operation mode's power ranges in, rather than in whatever unit the power sensor happens to be recorded in. S2 states an operation mode's power ranges in watts, and the example server creates its power sensor in ``kW``, so every scheduled power was read as a thousandth of itself: it then sat nearest the operation mode that draws nothing, and the resource manager was instructed to idle through a schedule that asked it to run. The handler already stated the unit when posting an actuator's power; it just never said which one it wanted back [`#247 <https://github.com/FlexMeasures/flexmeasures-client/pull/247>`__]
* A state of charge is posted in the unit the handler records fill levels in, instead of a hardcoded ``MWh``, which stopped FlexMeasures converting it: the example server creates its state-of-charge sensor in ``kWh``, so a fill level of 10 was being recorded as 10,000. A schedule is asked for in that same unit, so the bounds the scheduler plans against and the measurements it plans from agree [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A state of charge, and the whole flex-model a schedule is asked for with, are scaled by ``fill_level_scale`` like every other fill level the handler records. The scale is applied where the data reaches FlexMeasures and nowhere else: translating a schedule back into instructions keeps the resource manager's own units, since it matches fill levels against the operation modes stated in them [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A fill rate is recorded as a rate of fill level, in ``<energy_unit>/s``, instead of in the power unit. S2 states both an actuator's fill rate and a usage forecast as fill level per second, which for a heat buffer is not electricity at all, and which even for a battery differs from kilowatts by the seconds in an hour [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A fill-level target profile is posted from the resolution boundary its translation lined it up with, rather than from the moment the resource manager sent it, which shifted every value by the difference [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* An ``FRBCSimple`` handler accepts an ``FRBC.FillLevelTargetProfile``, an ``FRBC.UsageForecast`` and an ``FRBC.LeakageBehaviour`` instead of raising ``NotImplementedError`` for each. The CEM dispatches these as tasks nobody awaits, so every connection by a resource manager that sends all three logged three "Task exception was never retrieved" tracebacks, and recorded none of what they declared [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* A fill-level target profile is held inside the fill-level range the latest system description declared, so a target reaching past either end of it, such as a night setback below the range, is recorded as the range allows rather than as asked [`#244 <https://github.com/FlexMeasures/flexmeasures-client/pull/244>`__]
* The example CEM server answers an S2 handshake at once, instead of after configuring its site with FlexMeasures, which took longer than the timeout a resource manager waits for that answer [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__]
* The example CEM server seeds its demo prices from the current day, instead of from a date hardcoded when it was written, so that the window it schedules for has prices [`#241 <https://github.com/FlexMeasures/flexmeasures-client/pull/241>`__]
* An FRBC handler asks for a schedule when a storage status reports a new state of charge, instead of when a system description arrives, so a resource manager that sends its description before its first status is planned for at all [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* A schedule is asked for from a resolution boundary, instead of from whichever moment a resource manager happened to send its system description, which left FlexMeasures without prices for the window [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* An FRBC handler ignores a re-sent system description whose content is unchanged, instead of storing it again [`#240 <https://github.com/FlexMeasures/flexmeasures-client/pull/240>`__]
* An FRBC handler answers only the newest schedule request, instead of letting every request in a backlog complete its own round trip and send its own instruction batch [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* An FRBC handler sends an instruction only where it changes what the actuator does, which holds at the sending boundary for every producer of instructions [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* The instruction-status history is keyed by a string, as its type declares, instead of by the ``UUID`` that a no-op ``cast`` let through, which made every lookup miss [`#237 <https://github.com/FlexMeasures/flexmeasures-client/pull/237>`__]
* ``FRBCSimple`` reports the power of the operation mode the actuator is actually in, taken from the mode's power range, and posts it to the power sensor [`#236 <https://github.com/FlexMeasures/flexmeasures-client/pull/236>`__]
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
