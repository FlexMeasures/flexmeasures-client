.. _hems-tutorial:

HEMS tutorial
-------------

## Set up your environment

To run the HEMS example (``HEMS_setup.py``), you'll need a virtual environment in which both ``flexmeasures`` (the server) and ``flexmeasures-client`` is installed.

```
python3.12 -m venv venv
pip install -e .
pip install git+https://github.com/flexmeasures/flexmeasures.git@main
```

Or, alternatively, for developers:

```
python3.12 -m venv venv
pip install flexmeasures-client
pip install flexmeasures
```

Next steps:

- Follow instructions to set up flexmeasures (fresh database, etc).
- Create an organisation account and an admin with:

  :code-block:

      flexmeasures add account
      flexmeasures add user --roles admin

- Update the credentials in the ``HEMS_setup.py`` script accordingly.


Run the tutorial script
=======================

Open three terminals. In the first terminal, run the server:

```
flexmeasures run
```

In the second terminal, run a flexmeasures worker for the scheduling jobs:

```
flexmeasures jobs run-worker --queue "scheduling"
```

In the third terminal, run the client script:

```
python3 examples/HEMS_setup.py
```
