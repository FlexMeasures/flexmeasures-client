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

.. note:: The tutorial still uses the CLI for reporting. In future versions, we might make reporting available via the API, as well.


Set up your environment
========================

To run the HEMS example (``HEMS_setup.py``), you'll need an environment in which both ``flexmeasures`` (the server) and ``flexmeasures-client`` is installed.

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
- Create an organisation account and a user with the ``account-admin`` role:

.. code-block:: bash

    flexmeasures add account --name "HEMS tutorial"
    flexmeasures add user --username hems-admin --email hems-admin@example.com \
        --account 2 --roles account-admin

Replace ``2`` with the account ID printed by the first command. The tutorial
creates all assets and sensors in this organisation account. It does not create
public assets, so a site-wide ``admin`` role is not required.

- Update the credentials in the ``examples/HEMS/const.py`` script accordingly.


Run the tutorial script
=======================

Before running the tutorial, make sure to update the connection details and other relevant settings (e.g., host, port, credentials) in examples/HEMS/const.py to match your local FlexMeasures setup.
Open three terminals. In the first terminal, run the server:

.. code-block:: bash

    flexmeasures run

In the second terminal, run a flexmeasures worker that listens to both the scheduling and forecasting queues:

.. code-block:: bash

    flexmeasures jobs run-worker --queue "forecasting|scheduling"

Note: you can run the same command in two terminals (2 workers), to speed up the computation!

In the third terminal, go to the HEMS directory:

.. code-block:: bash

    cd examples/HEMS

.. note::
   For the time being, report generation (see :ref:`hems-tutorial` note above) shells out to a ``flexmeasures`` CLI process, which by default is expected on ``PATH`` and configured against the same database as the server. If your FlexMeasures server runs elsewhere (e.g. inside a Docker Compose service), point report generation at it instead via two environment variables:

   - ``FLEXMEASURES_CLI_CMD``: the command used to invoke the CLI
   - ``FLEXMEASURES_CLI_CONFIG_DIR``: the directory the CLI process sees the ``examples/HEMS/configs/`` files at, if different from their local path
   
   Here are steps if you use FlexMeasures' docker-compose:
   - ``export FLEXMEASURES_CLI_CMD="docker compose -f full/path/to/docker-compose.yml exec -T server flexmeasures"``.
   - Add this mount in docker-compose.yml under server.volumes, and restart it: ``- /full/path/to/flexmeasures-client/examples/HEMS/configs:/app/hems-configs:ro``
   - ``export FLEXMEASURES_CLI_CONFIG_DIR="/app/hems-configs"``

Another caveat is rate-limiting. Since v1.0, FlexMeasures only allows a limited number of schedule and forecasts per 5 minute interval.
Either give your account a generous plan (see the docs), or simply set ``FLEXMEASURES_MODE="play"`` and restart the server. 
If you use docker-compose, you could do that like this:

.. code-block:: bash

    sudo chown -R "$(id -u):$(id -g)" /full/path/to/flexmeasures-instance
    printf 'FLEXMEASURES_MODE = "play"\n' > /full/path/to/flexmeasures-instance/flexmeasures.cfg
    docker compose restart name-of-flexmeasures-server-container

Now run the client script using the `/examples/HEMS` folder as the current working directory:

.. code-block:: bash
    
    python3 HEMS_setup.py


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
