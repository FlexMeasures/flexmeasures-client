.. _flexed:

The FLEXED project
==================

FLEXED is a Dutch research project on heating flexibility in homes and its coordination at district level.
The FLEXED home energy management system (HEMS) made use of the following energy management components:

- a resource manager (RM),
- a customer energy manager (CEM) per home, and
- a district-level energy manager that distributes the capacity of a shared grid connection across those homes.

This page records the open-source components used for the simulations in the project.
Each entry below names the released version that carries the code,
or, where that code is not released yet, the branch that carries it as it ran.


Open-source components
----------------------

`FlexMeasures <https://github.com/FlexMeasures/flexmeasures>`_, branch ``feat/soc-end-value`` (Apache-2.0)
   The platform that both energy managers run on.
   It holds forecasts, schedules and measurements, computes device schedules, and exposes everything over an API.
   In FLEXED it carries both layers: the home layer and the district layer.
   The simulations ran a 1.1.0 development build of this branch, which is `pull request #2310 <https://github.com/FlexMeasures/flexmeasures/pull/2310>`_ and still open.

`FlexMeasures Client <https://github.com/FlexMeasures/flexmeasures-client>`_ v0.10.0 (Apache-2.0)
   This package, in two roles.
   As an `S2 Customer Energy Manager <https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/CEM.rst>`_ it speaks S2 over a WebSocket with the resource manager in each home, and translates that home's heat pump and heat buffer into a FlexMeasures flex-model.
   As an API client it is how the simulation driver creates assets, posts measurements and triggers schedules, which is also what the `HEMS tutorial <https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/HEMS.rst>`_ does.
   The simulations ran a development branch (`pull request #188 <https://github.com/FlexMeasures/flexmeasures-client/pull/188>`_).
   That work was finished, reviewed and merged into ``main`` (`#278 <https://github.com/FlexMeasures/flexmeasures-client/pull/278>`_ and `#279 <https://github.com/FlexMeasures/flexmeasures-client/pull/279>`_), and first released in v0.10.0.
   The `S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst>`_ lists what changed along the way.
   The FLEXED resource managers report the fill level of the heat buffer in joules, and S2 states no unit for a fill level.
   So to run the example CEM server with them, set ``S2_FILL_LEVEL_UNIT=J`` (`#296 <https://github.com/FlexMeasures/flexmeasures-client/pull/296>`_); the default, ``kWh``, would make every storage quantity 3.6 million times too large.

`s2-python <https://github.com/flexiblepower/s2-ws-json-python>`_ 0.8.1 (Apache-2.0)
   The message types of the `S2 standard <https://s2standard.org/>`_ for flexibility communication.
   FLEXED uses its FRBC (Fill Rate Based Control) control type, which fits a heat pump charging a thermal buffer.

`timely-beliefs <https://github.com/SeitaBV/timely-beliefs>`_ 4.2.0 (MIT)
   The time series model underneath FlexMeasures.
   Every value keeps its event time, its belief time and its source, which is what lets a simulation replay a past day using only the information that was available at each moment in it.

`Pyomo <https://www.pyomo.org/>`_ 6.8.2 (BSD-3-Clause) and `HiGHS <https://highs.dev/>`_ 1.15.1 (MIT)
   Pyomo states the scheduling problem as a mixed-integer linear program; HiGHS solves it.
   Pyomo is a modelling layer, not a solver.

`PostgreSQL <https://www.postgresql.org/>`_ 17.8 and `Redis <https://redis.io/>`_ 8.6.1
   Data storage and the job queue that scheduling and forecasting jobs run on.


Not part of this release
------------------------

The district-level coordination is a FlexMeasures scheduler plugin, and the simulation harness that drives the homes and the district through a historical period is a separate project.
Neither is open source.
What they rely on, though, is: they are built on the components above, through the plugin and API interfaces that FlexMeasures and this client already document.


Where to start
--------------

- The `CEM tutorial <https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/CEM.rst>`_ sets up the client as an S2 CEM, including a Docker Compose stack with a FlexMeasures server.
- The `HEMS tutorial <https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/HEMS.rst>`_ builds a complete home energy management system from scratch, with PV, a battery and a heat pump.
