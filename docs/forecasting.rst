.. _forecasting:

Forecasting
===========

The FlexMeasures Client supports the forecasting API endpoints introduced in
FlexMeasures v0.31.0:

- ``POST /sensors/<id>/forecasts/trigger`` — queue a forecasting job
- ``GET  /jobs/<uuid>``                    — inspect the job (v0.33.0+)
- ``GET  /sensors/<id>/forecasts/<uuid>``  — retrieve the result

These are exposed through three client methods:

- :meth:`trigger_forecast` — trigger and return the job UUID
- :meth:`get_forecast` — retrieve results, with legacy result polling when needed
- :meth:`trigger_and_get_forecast` — trigger, wait, and retrieve

.. note::

    Forecasting requires a FlexMeasures server of version **0.31.0** or above.
    The generic job status endpoint is available from **v0.33.0**.


Basic example
-------------

Forecast the next 24 hours for a sensor, using server-side defaults for the
training window:

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

        forecast = await client.trigger_and_get_forecast(
            sensor_id=1,
            duration="PT24H",
        )
        print(forecast)
        # e.g. {"values": [1.2, 1.5, 1.8, ...], "start": "...", "duration": "PT24H", "unit": "kW"}

        await client.close()

    asyncio.run(main())


Specifying a forecast window
-----------------------------

Use ``start`` and ``end`` (or ``start`` and ``duration``) to define the exact
period to forecast:

.. code-block:: python

    forecast = await client.trigger_and_get_forecast(
        sensor_id=1,
        start="2025-01-15T00:00:00+01:00",
        end="2025-01-17T00:00:00+01:00",
    )


Controlling the training window
---------------------------------

Pass training parameters inside a nested structure via the ``train_start``,
``train_period``, and ``retrain_frequency`` keyword arguments:

.. code-block:: python

    forecast = await client.trigger_and_get_forecast(
        sensor_id=1,
        start="2025-01-15T00:00:00+01:00",
        duration="PT48H",
        # Training configuration
        train_start="2025-01-01T00:00:00+01:00",  # historical data start
        train_period="P14D",                        # use 14 days of history
        retrain_frequency="PT24H",                  # retrain every 24 h
    )


Using regressors
----------------

You can improve forecast accuracy by supplying regressor sensor IDs:

.. code-block:: python

    forecast = await client.trigger_and_get_forecast(
        sensor_id=1,
        duration="PT24H",
        # Sensors whose *forecasts* matter (e.g. weather forecasts)
        future_regressors=[10, 11],
        # Sensors whose *measurements* matter (e.g. price history)
        past_regressors=[20],
    )


Step-by-step usage
-------------------

Trigger and retrieve separately to handle the job UUID yourself:

.. code-block:: python

    # Step 1 – enqueue the forecasting job
    forecast_id = await client.trigger_forecast(
        sensor_id=1,
        start="2025-01-15T00:00:00+01:00",
        end="2025-01-17T00:00:00+01:00",
    )
    print(f"Job queued: {forecast_id}")

    # Step 2 – wait for the job itself to finish (FlexMeasures v0.33.0+)
    await client.wait_for_job(forecast_id)

    # Step 3 – retrieve the forecast values
    forecast = await client.get_forecast(
        sensor_id=1,
        forecast_id=forecast_id,
    )
    print(forecast)


Waiting and legacy polling
--------------------------

On FlexMeasures v0.33.0 and newer, ``trigger_and_get_forecast`` waits through
``GET /jobs/<uuid>`` and fetches the forecast values only after the job has
finished.  Its job wait uses exponential backoff and accepts the same options
as :meth:`wait_for_job`:

- ``polling_interval`` (default 2 s) — delay before a repeated status check
- ``max_polling_interval`` (default 30 s) — maximum delay between checks
- ``timeout`` (default 600 s) — total job wait budget

For example:

.. code-block:: python

    forecast = await client.trigger_and_get_forecast(
        sensor_id=1,
        duration="PT24H",
        timeout=1800.0,
        max_polling_interval=60.0,
    )

``get_forecast`` still polls the result endpoint when it is called directly,
and ``trigger_and_get_forecast`` retains that behaviour for servers older than
v0.33.0.  This legacy polling is controlled by client-level settings:

- ``polling_interval`` (default 10 s) — initial wait between retries
- ``polling_timeout`` (default 200 s) — maximum total wait time
- ``max_polling_steps`` (default 10)  — maximum number of poll attempts

Configure those settings at client construction time:

.. code-block:: python

    client = FlexMeasuresClient(
        ...,
        polling_interval=5.0,   # check every 5 seconds
        polling_timeout=300.0,  # wait up to 5 minutes
    )
