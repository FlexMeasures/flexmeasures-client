.. _hems-tutorial:

HEMS tutorial
-------------

We wrote a complete tutorial with the client*, which sets up a HEMS from scratch (from nothing but a FlexMeasures account).

- It creates the whole structure - with PV, battery and a heat pump.
- It loads two weeks of historical data and creates forecasts through the forecasting API.
- It goes through one week in 4h steps, forecasting and scheduling all flexible assets.

This is the resulting dashboard:

.. image:: https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/_static/HEMS-tutorial-dashboard.png
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

This is what ``examples/HEMS/const.py`` expects out of the box, so you can run
the tutorial without editing it:

.. code-block:: python

    usr = "toy-user@flexmeasures.io"
    pwd = "toy-password"

The toy account also adds a few unrelated demo assets (``toy-building`` and its
children). The tutorial ignores them and never deletes them.

To use your own account instead, create it and give its user the
``account-admin`` role:

.. code-block:: bash

    flexmeasures add account --name "HEMS tutorial"
    flexmeasures add user --username hems-admin --email hems-admin@example.com \
        --account 2 --roles account-admin

Replace ``2`` with the account ID printed by the first command, and update
``usr`` and ``pwd`` in ``examples/HEMS/const.py`` to match.

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

Now run the client script using the `/examples/HEMS` folder as the current working directory:

.. code-block:: bash

    python3 HEMS_setup.py

Rerunning or resuming the tutorial
==================================

The setup script records completed phases in a namespaced attribute on the
community asset. If a tracked community already exists, the script shows which
phases are complete and offers four choices:

- ``y`` recreates the HEMS assets. This deletes their sensors, IDs, and data,
  including the HEMS energy market and weather station, before creating
  replacements with new IDs. You must confirm this by typing ``RECREATE``.
- ``w`` preserves the asset and sensor structure and IDs, but permanently
  deletes all HEMS time-series data before restarting at data upload. This
  includes uploads, forecasts, schedules, simulated measurements, and report
  outputs. You must confirm this by typing ``WIPE``.
- ``n`` (the default) preserves everything and resumes at the first unfinished
  phase. Completed phases are skipped.
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

    python3 HEMS_cleanup.py

The script shows the matching top-level assets and asks for confirmation. It
deletes the community asset, the energy market, and the weather station. Asset
deletion also removes their child assets, sensors, and time-series data.

.. warning::
   Deletion is permanent. The energy market and weather station are separate
   top-level assets; do not continue if other systems in the account share them.
   The configured user needs the ``account-admin`` role to delete assets.
