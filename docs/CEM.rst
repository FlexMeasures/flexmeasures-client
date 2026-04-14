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

To test, run the included example RM:

.. code-block:: bash

    uv run src/flexmeasures_client/s2/script/websockets_client.py

Disclaimer
==========

The `S2 Protocol <https://s2standard.org/>`_ integration is still under active development. Please, beware that the logic and interfaces can change.
