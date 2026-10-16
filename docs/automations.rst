.. _automations:

Automations
===========

Automations are recurring forecasting, scheduling, reporting or plugin-defined
tasks, defined on an asset and run by the FlexMeasures server on a cron
schedule. The FlexMeasures Client supports the automation endpoints introduced
in FlexMeasures v1.1.0:

- ``GET    /assets/<id>/automations``               — list automations
- ``GET    /assets/<id>/automations/<id>``          — automation details
- ``POST   /assets/<id>/automations``               — create an automation
- ``PATCH  /assets/<id>/automations/<id>``          — update an automation
- ``DELETE /assets/<id>/automations/<id>``          — delete an automation
- ``POST   /assets/<id>/automations/<id>/trigger``  — run an automation once now

These are exposed through the following client methods:

- :meth:`get_automations` — list the automations of an asset and, by default, of the assets below it
- :meth:`get_automation` — the details of one automation, including its sensors and run statistics
- :meth:`add_automation` — create an automation
- :meth:`update_automation` — change its name, recurrence, timezone or activation status
- :meth:`delete_automation` — delete an automation
- :meth:`trigger_automation` — run an automation once now, and return its job UUID
- :meth:`trigger_automation_and_wait` — convenience wrapper for triggering and waiting

.. note::

    These endpoints require a FlexMeasures server of version **1.1.0** or above,
    with workers on the queues the automations queue their jobs on.


Basic example
-------------

Forecast a sensor every day at 06:00, try the automation out once, and switch it off again:

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

        automation = await client.add_automation(
            asset_id=3,
            name="Day-ahead PV forecasts",
            cron="0 6 * * *",
            timezone="Europe/Amsterdam",
            parameters={"sensor": 2092},
            active=False,
        )

        # Run it once, and wait for its job to finish
        await client.trigger_automation_and_wait(
            asset_id=3, automation_id=automation["id"]
        )

        # Let it run every day from now on
        await client.update_automation(3, automation["id"], active=True)

        for automation in await client.get_automations(asset_id=3):
            print(automation["name"], automation["recurrence-description"])

        await client.close()

    asyncio.run(main())


Timing each run
---------------

An automation runs again and again, so its parameters cannot fix a moment in
time: a ``start``, ``end`` or ``prior`` is refused. Say instead how the period
each run covers relates to the time the run was due, with two of
``start-offset``, ``end-offset`` and ``duration``. The offsets are chains of
comma-separated Pandas offsets, plus ``DB`` (day begin) and ``HB`` (hour begin):

.. code-block:: python

    # Every day at noon, schedule the whole of the next day
    await client.add_automation(
        asset_id=3,
        name="Day-ahead schedules",
        cron="0 12 * * *",
        timezone="Europe/Amsterdam",
        automation_type="scheduling",
        parameters={"start-offset": "1D,DB", "duration": "P1D"},
    )


Updating an automation
----------------------

Pass the fields to update as a dictionary, as keyword arguments, or both:

.. code-block:: python

    await client.update_automation(3, 5, active=False)
    await client.update_automation(3, 5, {"cron": "0 7 * * *"}, timezone="UTC")

Field names are passed on without checking them, so fields that a newer server
makes editable can be updated without upgrading the client. Keyword names are
converted to the API's kebab-case (``depends_on`` becomes ``depends-on``),
while dictionary keys are sent as given.

Changing the recurrence or the timezone, or reactivating the automation, resets
its cursor, so that runs from before the change are not caught up on. Its
other fields cannot be changed; create a new automation instead.


Triggering a run
----------------

:meth:`trigger_automation` runs an automation once, in addition to its
recurring runs, and also works for inactive automations. Its response holds
the UUID of a job to follow, which can be passed to :meth:`wait_for_job`, and
the number of jobs the run queued, in ``n-jobs``:

.. code-block:: python

    response = await client.trigger_automation(asset_id=3, automation_id=5)
    if response["n-jobs"] > 1:
        print("This run queued more jobs than the one we wait for.")
    job = await client.wait_for_job(response["job"])

See :ref:`reporting` for how job polling can be configured, and which errors it raises.


Error handling
--------------

A request the server rejects raises ``ValueError``, with the server's message.
For an automation it refuses to create, that message says which part of the
request it refused, such as the ``parameters`` or the ``config``. An
automation is only found under the asset it is defined on, so asking for it
under another asset raises ``ValueError``, too. A server older than v1.1.0
raises :class:`InsufficientServerVersionError`.
