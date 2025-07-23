"""
Only do one thing: update the flex model of an asset
"""

import asyncio

from flexmeasures_client import FlexMeasuresClient

usr = "xxxxxxxxxxxxxxxx"
pwd = "xxxxxxxxxxxxxxxx"
asset_id = 1


async def main():
    client = FlexMeasuresClient(email=usr, password=pwd)

    asset = await client.update_asset(
        asset_id=asset_id,
        updates={
            "flex_model": {"prefer-charging-sooner": False, "soc-min": "1001 kWh"}
        },
    )

    print(asset)

    await client.close()


asyncio.run(main())
