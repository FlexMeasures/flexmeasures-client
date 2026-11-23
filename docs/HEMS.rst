.. _hems-tutorial:

HEMS tutorial
-------------

We wrote a complete tutorial with the client*, which sets up a HEMS from scratch (from nothing but a FlexMeasures account).

- It creates the whole structure - with PV, battery and a heat pump.
- It loads two weeks of historical data, plus the measurements of the simulated days, and creates forecasts through the forecasting API.
- It goes through two days in 4h steps, scheduling all flexible assets.

This is the resulting dashboard:

.. image:: https://raw.githubusercontent.com/FlexMeasures/screenshots/main/HEMS-tutorial-dashboard.png
    :align: center
|

.. note:: The tutorial talks to FlexMeasures over the API only, including for reporting. That requires a FlexMeasures server of version 1.1.0 or above, and a worker listening on the ``reporting`` queue.


Set up your environment
========================

To run the HEMS example (``HEMS_setup.py``), you'll need an environment in which both ``flexmeasures`` (the server) and ``flexmeasures-client`` is installed.
The example requires FlexMeasures 1.1.0 or newer, since it triggers reports over the API.

We use `uv <https://docs.astral.sh/uv/>`_ to manage dependencies. First, `install uv <https://docs.astral.sh/uv/getting-started/installation/>`_.

From the ``flexmeasures-client`` repository, install the client and the FlexMeasures server:

.. code-block:: bash

    uv sync
    uv add git+https://github.com/flexmeasures/flexmeasures.git@main

Or, alternatively, to install released versions into a fresh project:

.. code-block:: bash

    uv init my-hems && cd my-hems
    uv add flexmeasures-client flexmeasures


Next steps:

- Follow instructions to set up flexmeasures (fresh database, etc).
- Create an account and a user with the ``account-admin`` role. The quickest way
  is FlexMeasures' toy account, which creates both in one step:

.. code-block:: bash

    flexmeasures add toy-account

The toy account also adds a few unrelated demo assets (``toy-building`` and its
children). The tutorial ignores them and never deletes them.

To use your own account instead, create it and give its user the
``account-admin`` role:

.. code-block:: bash

    flexmeasures add account --name "HEMS tutorial"
    flexmeasures add user --username hems-admin --email hems-admin@example.com \
        --account 2 --roles account-admin

Replace ``2`` with the account ID printed by the first command.

Either way, the tutorial creates all assets and sensors in that one account. It
does not create public assets, so a site-wide ``admin`` role is not required.


Run the tutorial script
=======================

Before running the tutorial, update the connection details and other relevant
settings in ``examples/HEMS/const.py``. Specify the host without an ``http://``
or ``https://`` prefix, and set ``ssl = True`` when connecting over HTTPS. For
example:

.. code-block:: python

    host = "127.0.0.1:5000"
    ssl = False

For an HTTPS deployment, use its host name and set ``ssl = True``.

PV is inflexible by default: all available production is delivered and any
surplus is treated as grid feed-in. Set ``PV_MODE = "curtailable"`` when the PV
gateway can reduce production, for example at a site whose grid-production
capacity is zero. In that mode the simulated gateway treats the PV schedule as
a maximum setpoint; it can reduce available production but cannot increase it.
Recreate an existing tutorial structure after changing this setting so its flex
context and PV sensors match the selected mode.

The PV chart distinguishes available production, delivered production,
self-consumption, grid feed-in, and curtailment. The reporter calculates the
latter two after realization as ``max(delivered PV - local load, 0)`` and
``max(available PV - delivered PV, 0)``. Consequently, the daily
self-consumption percentage uses delivered rather than merely available PV as
its denominator.

Open three terminals. In the first terminal, run the server:

.. code-block:: bash

    flexmeasures run

In the second terminal, run a flexmeasures worker that listens to the
forecasting, scheduling, ingestion, and reporting queues:

.. code-block:: bash

    flexmeasures jobs run-worker --queue "forecasting|scheduling|ingestion|reporting"

Note: you can run the same command in two terminals (2 workers), to speed up the computation!

In the third terminal, go to the HEMS directory:

.. code-block:: bash

    cd examples/HEMS

.. note::
   Reports are triggered over the API, so the client script needs nothing beyond its API credentials: no local CLI, no database access, and no bind-mount of ``examples/HEMS/configs/`` into the server. Those configuration files are read by the client and posted along with each report request. The server does need a worker on the ``reporting`` queue, as above, or reports will stay queued until the client gives up on them.

Another caveat is rate-limiting. Since v1.0, FlexMeasures only allows a limited number of schedule, forecast and report triggers per 5 minute interval.
Either give your account a generous plan (see the docs), or simply set ``FLEXMEASURES_MODE="play"`` and restart the server.
If you use docker-compose, you could do that like this:

Add ``FLEXMEASURES_MODE = "play"`` to the existing
``/full/path/to/flexmeasures-instance/flexmeasures.cfg`` file without replacing
its other settings, then restart the server container:

.. code-block:: bash

    docker compose restart name-of-flexmeasures-server-container

Now run the client script using the ``examples/HEMS`` folder as the current
working directory. Point ``uv`` at the repository project so the script imports
this checkout's client instead of a possibly older user-site installation:

.. code-block:: bash

    uv run --no-sync --project ../.. python HEMS_setup.py

The script prompts for the account-admin user's email and password when it
first authenticates. The password is not echoed or stored in ``const.py``.
Run it in an interactive terminal; the cleanup script prompts in the same way.

To save a refresh token in your system keyring and avoid entering the password
on subsequent runs, install the optional `keyring package
<https://keyring.readthedocs.io/en/latest/>`_ or run the script with it:

.. code-block:: bash

    uv run --with keyring --no-sync --project ../.. python HEMS_setup.py

Use the same ``--with keyring`` option for ``HEMS_cleanup.py``. After the first
login, the scripts remember the account and look up its refresh token without
prompting for an email. Set ``FLEXMEASURES_HEMS_EMAIL`` to select a different
account; the choice is remembered for that server after a successful login or
refresh. When upgrading from an earlier version, the keyring may supply an
existing account automatically (its choice depends on the backend if several
accounts exist); otherwise enter the email once to select the existing token.
When the server issues or rotates a refresh token, the new token is saved; the
password is not saved. An expired or revoked token requires a new login. This
requires a usable system keyring backend; if none is available,
the scripts continue with interactive login. Older servers that do not issue
refresh tokens require a password each run. Do not run multiple clients with
the same saved refresh token concurrently: rotation can invalidate the token.

Scheduling the community jointly or per building
=================================================

The ``SOLVE_AS_COMMUNITY`` option in ``examples/HEMS/const.py`` defaults to
``True``. In this mode, each scheduling iteration triggers one job on the
community asset. The job includes every building's flexible and inflexible
devices, each building's connection constraints, and the community's shared
``site-power-capacity`` constraint.

Set ``SOLVE_AS_COMMUNITY = False`` to retain independent scheduling jobs for
each building. Those jobs enforce the individual building constraints, but they
cannot enforce the shared community constraint. The script prints a prominent
warning when this mode starts and, after each simulated step, prints a
timestamped annotation for every measured 15-minute interval that exceeded the
community limit.

Synthetic weather forecasts
===========================

PV forecasting currently uses two irradiation sensors on the weather station:

- ``irradiation`` contains measurements and serves as a past regressor.
- ``irradiation-forecast`` contains synthetic forecasts **and a copy of the
  measurements** and serves as a future regressor. The model trains this input
  on its measured history and uses its forecasts for upcoming events.

Observations and forecasts therefore share the future-regressor sensor, while
the past regressor remains a separate sensor. The tutorial publishes a
synthetic 48-hour irradiation forecast every simulation step (four hours by
default, controlled by ``SIMULATION_STEP_HOURS``), including historical forecasts.
Measurements on both sensors are recorded as known only at each hourly event's
end. FlexMeasures v1.1.1 needs these realized values for
the historical part of its future covariates; forecast-only history is discarded
and cannot teach the model the relationship between irradiation and PV output.
Each issuance has its own belief time, so a
rolling PV forecast can only use weather predictions already published then.
Weather forecasts are uploaded once for the shared weather station and ingestion
finishes before PV forecasting starts.

The synthetic weather predictions use the same daily cloud scenarios as the
measurements, with smoothed clouds, timing errors and intensity bias that varies
with the target day. Successive issuances reduce those errors as the lead time
shrinks, while retaining some error even for the nearest events. They provide
useful upcoming weather information without perfect knowledge of future
production. Nighttime irradiation remains
zero. The PV-condition annotations remain display labels, not model inputs.

Resuming an older tutorial setup adds the forecast sensor and reruns forecasting,
scheduling and reporting while preserving asset IDs and uploaded measurements.

Reading the results
===================

The tutorial uploads the measured consumption, PV production and heat usage for the whole period up front, including the simulated days.
Each measurement is recorded as known at the end of its event,
so a forecast or schedule made during the simulation only uses the measurements that would have been available at that moment.
Measured data is never used before it is "known".

This means that a graph of the simulated days shows measurements (solid lines) and forecasts (dotted lines) on top of each other.
To see what was known as the simulation progressed, press the replay button on a site's graphs page (or hit **p**, or **n** to step one hour at a time).
Replay only shows data that had been recorded by the time on the replay clock,
so measurements and executed schedules (left of the replay line) fill in behind the forecasts and planned schedules (dotted and dashed lines, right of it).
Prices and capacity limits stay visible for the whole period, because they were uploaded as known in advance.

.. image:: https://raw.githubusercontent.com/FlexMeasures/screenshots/main/HEMS-tutorial-replay.png
    :align: center


Rerunning or resuming the tutorial
==================================

The setup script records completed phases in a namespaced attribute on the
community asset.

The tutorial version is ``WORKFLOW_VERSION`` in
``examples/HEMS/utils/workflow_utils.py`` (separate from the server version).
It is derived from ``WORKFLOW_UPGRADES``, which records each version's changes
and the phases it invalidates. Both the preview and the applied upgrade use
only entries newer than the stored version.
The script displays it at startup and compares it with
``attributes.hems_tutorial.workflow-version`` on the community asset.
Before offering the choices below, it previews any required upgrade and lists
which completed phases will rerun. After applying an upgrade it displays the
updated version and phase status.

If a tracked community already exists, the script offers four choices:

- ``y`` recreates the HEMS assets. This deletes their sensors, IDs, and data,
  including the HEMS energy market and weather station, before creating
  replacements with new IDs. You must confirm this by typing ``RECREATE``.
- ``w`` preserves the asset and sensor structure and IDs, but permanently
  deletes all HEMS time-series data and sensor annotations before restarting at
  data upload. This
  includes uploads, forecasts, schedules, simulated measurements, and report
  outputs, plus annotations such as PV conditions and EV target labels.
  Shared annotations are removed from each recorded HEMS sensor; annotations
  linked outside those sensors remain there. This requires FlexMeasures v1.1.0
  or above. You must confirm this by typing ``WIPE``.
- ``n`` (the default) keeps the setup and its IDs and measurements, upgrades an
  older tutorial version if needed, then runs the pending phases shown above.
  Completed phases unaffected by the upgrade are skipped. An upgrade can rerun
  forecasting, scheduling and reporting even for a previously completed tutorial.
- ``q`` exits without changing the setup.

If an earlier data wipe was interrupted, normal resume is disabled because
some sensors may already be empty while others still contain old data. The
script instead offers to continue the wipe, recreate the setup, or exit.

If asset creation was interrupted before the setup marker was saved, the
script offers to complete missing assets and sensors while preserving existing
IDs, recreate the setup, or exit. For an older setup with different site names,
it also offers to keep those names or rename the sites to the names configured
in ``const.py``.

The workflow marker stores the sensor IDs in the HEMS structure when the marker
is created, so a data wipe remains limited to that recorded set. If an existing
setup predates workflow markers, safe resume is unavailable because its
completed phases are unknown. Choose the offered repair option to preserve IDs
and complete missing structure, or choose recreation to replace the setup.


Delete the tutorial assets and data
===================================

To remove the HEMS setup from the configured account, run the cleanup script
from the same directory:

.. code-block:: bash

    uv run --no-sync --project ../.. python HEMS_cleanup.py

The script shows the matching top-level assets and asks for confirmation. It
deletes the community asset, the energy market, and the weather station. Asset
deletion also removes their child assets, sensors, and time-series data.

.. warning::
   Deletion is permanent. The energy market and weather station are separate
   top-level assets; do not continue if other systems in the account share them.
   The configured user needs the ``account-admin`` role to delete assets.
