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
- Create an organisation account and an admin with:

.. code-block:: bash

    flexmeasures add account
    flexmeasures add user --roles admin

- Update the credentials in the ``examples/HEMS/const.py`` script accordingly.


Run the tutorial script
=======================

Before running the tutorial, make sure to update the connection details and other relevant settings (e.g., host, port, credentials) in examples/HEMS/const.py to match your local FlexMeasures setup.
Open three terminals. In the first terminal, run the server:

.. code-block:: bash

    flexmeasures run

In the second terminal, run a flexmeasures worker that listens to the scheduling, forecasting and reporting queues:

.. code-block:: bash

    flexmeasures jobs run-worker --queue "forecasting|scheduling|reporting"

Note: you can run the same command in two terminals (2 workers), to speed up the computation!

In the third terminal, run the client script using the `/examples/HEMS` folder as the current working directory:

.. code-block:: bash

    cd examples/HEMS
    python3 HEMS_setup.py

.. note::
   Reports are triggered over the API, so the client script needs nothing beyond its API credentials: no local CLI, no database access, and no bind-mount of ``examples/HEMS/configs/`` into the server. Those configuration files are read by the client and posted along with each report request. The server does need a worker on the ``reporting`` queue, as above, or reports will stay queued until the client gives up on them.
