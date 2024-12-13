import asyncio

from flexmeasures_client.client import FlexMeasuresClient

client = FlexMeasuresClient(
    email="admin@admin.com",
    password="admin",
    host="localhost:5000",
)


async def my_script():
    await client.post_measurements(
        sensor_id=2,
        start="2023-05-14T00:00:00+02:00",
        duration="PT24H",
        unit="EUR/MWh",
        values=[
            10,
            11,
            12,
            15,
            18,
            17,
            10.5,
            9,
            9.5,
            9,
            8.5,
            10,
            8,
            5,
            4,
            4,
            5.5,
            8,
            12,
            13,
            14,
            12.5,
            10,
            7,
        ],
    )


asyncio.run(my_script())
