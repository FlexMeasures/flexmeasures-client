.. _cem-tutorial:

CEM tutorial
-------------

There are two ways to run the FlexMeasures Client as a local S2 Customer Energy Manager (CEM) using WebSocket communication.

Docker Compose
--------------

This is the easiest way to run the CEM alongside a full FlexMeasures server stack.

First, start the FlexMeasures server from the ``flexmeasures`` folder:

.. code-block:: bash

    docker compose up

This starts:

- Web and worker servers (FlexMeasures)
- Database server (Postgres)
- Queue server (Redis)
- Mail server (MailHog)

Once the server is running, start the CEM from this (``flexmeasures-client``) folder:

.. code-block:: bash

    docker compose up

The CEM runs with host networking, so it can reach the FlexMeasures server at ``http://localhost:5000``.

TODO: fix networking.

You can now point your Resource Managers (RMs) to ``http://localhost:8080/ws``.

Local development
------------------

If you want to run the CEM locally for development purposes, you can do the following:

First, follow the instructions in the FlexMeasures repository to set up the server.

Then, in the folder containing this repository, run:

.. code-block:: bash

    uv sync --extra s2

Then point your Resource Managers (RMs) to ``http://localhost:8080/ws`` and run:

.. code-block:: bash

    uv run src/flexmeasures_client/s2/script/websockets_server.py

We also included a ``docker-compose.override.yaml`` that can be used to set up the CEM including the FlexMeasures server, creating a fully self-hosted HEMS.
Assuming your ``flexmeasures`` and ``flexmeasures-client`` repo folders are located side by side, run this from your flexmeasures folder:

.. code-block:: bash

    docker compose \
      -f docker-compose.yml \
      -f ../flexmeasures-client/docker-compose.override.yml \
      up


This creates the following containers for the CEM:

- a WebSocket server (FlexMeasures Client)
- web and worker servers (FlexMeasures)
- a database server (Postgres)
- a queue server (Redis)
- a mail server (MailHog)

To test, run the included example RM:

.. code-block:: bash

    uv run src/flexmeasures_client/s2/script/websockets_client.py

For full access via the UI, create an admin user for the Docker Toy Account (here, we assume it has ID 1):

.. code-block:: bash

    docker exec -it flexmeasures-server-1 bash
    flexmeasures show accounts
    flexmeasures add user --roles admin  --account 1 --email <email> --username <username>

Disclaimer
==========

The `S2 Protocol <https://s2standard.org/>`_ integration is still under active development. Please, beware that the logic and interfaces can change.
