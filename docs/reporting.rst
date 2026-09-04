.. _reporting:

Reporting
=========

The FlexMeasures Client supports the report trigger endpoint introduced in
FlexMeasures v1.1.0:

- ``POST /assets/<id>/reports/trigger`` — queue a one-off reporting job
- ``GET  /jobs/<uuid>``                 — poll a background job

These are exposed through four client methods:

- :meth:`trigger_report` — trigger and return the job UUID
- :meth:`get_job_status` — look a job up once
- :meth:`wait_for_job`   — poll a job until it reaches a terminal state
- :meth:`trigger_and_await_report` — convenience wrapper for triggering and waiting

.. note::

    These endpoints require a FlexMeasures server of version **1.1.0** or above,
    running a worker on the ``reporting`` queue::

        flexmeasures jobs run-worker --queue reporting


Basic example
-------------

Compute a report over yesterday, writing its result to a sensor:

.. code-block:: python

    import asyncio
    from flexmeasures_client import FlexMeasuresClient

    async def main():
        client = FlexMeasuresClient(
            host="localhost:5000",
            ssl=False,
            email="user@example.com",
            password="password",
        )

        await client.trigger_and_await_report(
            asset_id=3,
            reporter="AggregatorReporter",
            config={"method": "sum"},
            parameters={
                "input": [{"sensor": 1}, {"sensor": 2}],
                "output": [{"sensor": 4}],
                "start": "2026-08-17T00:00:00+02:00",
                "end": "2026-08-18T00:00:00+02:00",
            },
        )

        # Reports write to their output sensors, so read the result back
        values = await client.get_sensor_data(
            sensor_id=4,
            start="2026-08-17T00:00:00+02:00",
            duration="P1D",
            unit="MW",
            resolution="PT15M",
        )
        print(values)

        await client.close()

    asyncio.run(main())


Which asset to trigger against
------------------------------

Every output sensor has to belong to the asset in the URL or one of its
descendants, and the caller needs to be able to read the input sensors and to
record data on the output sensors.  So a report writing to sensors of one site
is triggered against that site, and a report aggregating several sites into a
community sensor is triggered against the community.


Step-by-step usage
-------------------

Trigger and wait separately to handle the job UUID yourself:

.. code-block:: python

    # Step 1 – enqueue the reporting job
    job_id = await client.trigger_report(
        asset_id=3,
        reporter="AggregatorReporter",
        config={"method": "sum"},
        parameters=parameters,
    )
    print(f"Job queued: {job_id}")

    # Step 2 – look it up whenever you like
    job = await client.get_job_status(job_id)
    print(job["status"])  # QUEUED, STARTED, FINISHED, FAILED, ...

    # Step 3 – or block until it reaches a terminal state
    job = await client.wait_for_job(job_id)


Polling behaviour
-----------------

``wait_for_job`` polls ``GET /jobs/<uuid>`` until the job reaches a terminal
state.  Waits back off exponentially, so short jobs are picked up quickly
without hammering the server on long ones:

- ``polling_interval`` (default 2 s)      — delay before a repeated status check
- ``max_polling_interval`` (default 30 s) — cap on the backing-off wait
- ``timeout`` (default 600 s)             — total budget for the job to finish

.. code-block:: python

    job = await client.wait_for_job(
        job_id,
        timeout=3600.0,           # allow an hour for a heavy report
        max_polling_interval=60.0,
    )


Error handling
--------------

Jobs that end badly raise rather than returning a status:

- :class:`JobFailedError` — the job ended as ``FAILED``, ``STOPPED`` or
  ``CANCELED``.  The message carries the server's own message and, where the
  worker stored one, its traceback.
- :class:`JobTimeoutError` — the job did not finish within ``timeout``.  The
  message names the last status seen, which distinguishes a report stuck in
  ``QUEUED`` (no worker on the ``reporting`` queue) from one still ``STARTED``.

.. code-block:: python

    from flexmeasures_client.exceptions import JobFailedError, JobTimeoutError

    try:
        await client.trigger_and_await_report(...)
    except JobFailedError as exception:
        print(f"The report did not compute: {exception}")
    except JobTimeoutError as exception:
        print(f"The report is taking too long: {exception}")

A request the server rejects outright — unknown reporter, malformed
parameters, an output sensor outside the asset's subtree — raises ``ValueError``
from :meth:`trigger_report`, before any job is queued.

The job endpoint uses HTTP 202 for jobs still in progress and HTTP 422 for a
failed job. :meth:`get_job_status` returns both responses as status dictionaries
after one request; :meth:`wait_for_job` is responsible for polling and turning
unsuccessful terminal states into :class:`JobFailedError`.
