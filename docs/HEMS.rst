.. _hems-tutorial:

HEMS tutorial
-------------

We wrote a complete tutorial with the client*, which sets up a HEMS from scratch (from nothing but a FlexMeasures account).

- It creates the whole structure - with PV, battery and a heat pump.
- It loads two weeks of historical data and creates forecasts based on it
- It goes through one week in 4h steps, forecasting and scheduling all flexible assets.

This is the resulting dashboard:

.. image:: https://github.com/FlexMeasures/flexmeasures-client/blob/main/docs/_static/HEMS-tutorial-dashboard.png
    :align: center
|

.. note:: The tutorial still uses the CLI for two things: forecasting and reporting. We are working on those...


Set up your environment
========================

To run the HEMS example (``HEMS_setup.py``), you'll need a virtual environment in which both ``flexmeasures`` (the server) and ``flexmeasures-client`` is installed.

.. code-block:: bash

    python3.12 -m venv venv
    pip install -e .
    pip install git+https://github.com/flexmeasures/flexmeasures.git@main

Or, alternatively, for developers:

.. code-block:: bash

    python3.12 -m venv venv
    pip install flexmeasures-client
    pip install flexmeasures


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

In the second terminal, run a flexmeasures worker for the scheduling jobs:

.. code-block:: bash

    flexmeasures jobs run-worker --queue "scheduling"

In the third terminal, run the client script using the `/examples/HEMS` folder as the current working directory:

.. code-block:: bash

    cd examples/HEMS
    python3 HEMS_setup.py
