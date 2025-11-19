.. _hems-tutorial:

HEMS tutorial
-------------

Set up your environment
========================

To run the HEMS example (``HEMS_setup.py``), you'll need a virtual environment in which both ``flexmeasures`` (the server) and ``flexmeasures-client`` is installed.

`
python3.12 -m venv venv
pip install -e .
pip install git+https://github.com/flexmeasures/flexmeasures.git@main
`

Or, alternatively, for developers:

`
python3.12 -m venv venv
pip install flexmeasures-client
pip install flexmeasures
`

Next steps:

- Follow instructions to set up flexmeasures (fresh database, etc).
- Create an organisation account and an admin with:

  :code-block:

      flexmeasures add account
      flexmeasures add user --roles admin

- Update the credentials in the ``examples/HEMS/const.py`` script accordingly.


Run the tutorial script
=======================

Before running the tutorial, make sure to update the connection details and other relevant settings (e.g., host, port, credentials) in examples/HEMS/const.py to match your local FlexMeasures setup.
Open three terminals. In the first terminal, run the server:

`
flexmeasures run
`

In the second terminal, run a flexmeasures worker for the scheduling jobs:

`
flexmeasures jobs run-worker --queue "scheduling"
`

In the third terminal, run the client script using the `/examples/HEMS` folder as the current working directory:

`
cd examples/HEMS
python3 HEMS_setup.py
`
