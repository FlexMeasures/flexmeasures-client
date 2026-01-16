.. _cem-tutorial:

CEM tutorial
-------------

To run the FlexMeasures Client as a local S2 Customer Energy Manager (CEM) using WebSocket communication:

.. code-block:: bash

    pip install flexmeasures-client[s2]

Then point your Resource Managers (RMs) to ``http://localhost:8080/ws`` and run:

.. code-block:: bash

    python3 flexmeasures_client/s2/scripts/websockets_server.py

We also included a ``docker-compose.yaml`` that can be used to set up the CEM including the FlexMeasures server, creating a fully self-hosted HEMS.
Assuming your ``flexmeasures`` and ``flexmeasures-client`` repo folders are located side by side, run this from your flexmeasures folder:

.. code-block:: bash

    docker compose \
      -f docker-compose.yml \
      -f ../flexmeasures-client/docker-compose.yml \
      up


This creates the following containers for the CEM:

- a WebSocket server (FlexMeasures Client)
- web and worker servers (FlexMeasures)
- a database server (Postgres)
- a queue server (Redis)
- a mail server (MailHog)

To test, run the included example RM:

.. code-block:: bash

    python3 flexmeasures_client/s2/script/websockets_client.py

Disclaimer
==========

The `S2 Protocol <https://s2standard.org/>`_ integration is still under active development. Please, beware that the logic and interfaces can change.


Run Demo
=========

Run the following commands in the flexmeasures folder to create a toy-account and an admin user:

.. code-block:: bash

    flexmeasures add toy-account
    flexmeasures add user --username admin --account-id 1 --email admin@mycompany.io --roles admin

Launch server:

.. code-block:: bash

    flexmeasures run

To load the data, run the following command in the flexmeasures-client repository:

.. code-block:: bash

    python src/flexmeasures_client/s2/script/demo_setup.py

Start the S2 server:

.. code-block:: bash

    python src/flexmeasures_client/s2/script/websockets_server.py

In a separate window, start the S2 Client:

.. code-block:: bash

    python src/flexmeasures_client/s2/script/websockets_client.py
