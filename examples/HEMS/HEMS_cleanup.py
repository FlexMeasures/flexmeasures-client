"""Delete the assets and data created by the HEMS tutorial."""

import asyncio

from const import COMMUNITY_NAME, host, pwd, ssl, usr
from utils.asset_utils import delete_hems_assets

from flexmeasures_client import FlexMeasuresClient


async def main() -> None:
    client = FlexMeasuresClient(email=usr, password=pwd, host=host, ssl=ssl)
    try:
        account = await client.get_account()
        if not account:
            raise RuntimeError("No account found for the configured user.")
        print(f"Connected to account: {account['name']} (ID: {account['id']})")
        await delete_hems_assets(
            client=client,
            account_id=account["id"],
            community_name=COMMUNITY_NAME,
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
