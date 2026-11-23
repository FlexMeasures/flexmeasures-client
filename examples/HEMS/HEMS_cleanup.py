"""Delete the assets and data created by the HEMS tutorial."""

import asyncio
import sys

from const import COMMUNITY_NAME, host, ssl
from utils.asset_utils import delete_hems_assets
from utils.auth_utils import configure_refresh_token_storage

from flexmeasures_client import FlexMeasuresClient


async def main() -> None:
    client = FlexMeasuresClient(host=host, ssl=ssl)
    try:
        await configure_refresh_token_storage(client)
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
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCleanup cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
