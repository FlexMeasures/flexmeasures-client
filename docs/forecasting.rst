.. _forecasting:

Forecasting
===========

The FlexMeasures Client supports the forecasting API endpoints introduced in
FlexMeasures v0.31.0:

- ``POST /sensors/<id>/forecasts/trigger`` — queue a forecasting job
- ``GET  /sensors/<id>/forecasts/<uuid>``  — poll for results

These are exposed through three client methods:

- :meth:`trigger_forecast` — trigger and return the job UUID
- :meth:`get_forecast`     — poll until results are ready
- :meth:`trigger_and_get_forecast` — convenience wrapper for both

.. note::

    These endpoints require a FlexMeasures server of version **0.31.0** or above.


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

    # Step 2 – poll until the job finishes
    forecast = await client.get_forecast(
        sensor_id=1,
        forecast_id=forecast_id,
    )
    print(forecast)


Polling behaviour
-----------------

``get_forecast`` polls the server with a ``GET`` request and returns when the
server responds with HTTP 200.  The polling respects the same client-level
settings as scheduling:

- ``polling_interval`` (default 10 s) — time between retries
- ``polling_timeout`` (default 200 s) — maximum total wait time
- ``max_polling_steps`` (default 10)  — maximum number of poll attempts

Override them at client construction time:

.. code-block:: python

    client = FlexMeasuresClient(
        ...,
        polling_interval=5.0,   # check every 5 seconds
        polling_timeout=300.0,  # wait up to 5 minutes
    )
