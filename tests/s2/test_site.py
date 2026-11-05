"""A site of its own for each resource manager, and a CEM that waits for it."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from s2python.common import ControlType, ReceptionStatus, ReceptionStatusValues

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.cem import CEM
from flexmeasures_client.s2.site import (
    FRBC_SITE_SENSORS,
    FRBCSiteConfigurator,
    SiteSensor,
    ensure_site,
    frbc_site_graphs,
)


def _flexmeasures(existing: list[dict] | None = None) -> AsyncMock:
    fm = AsyncMock(FlexMeasuresClient)
    fm.get_account = AsyncMock(return_value={"id": 1})
    fm.get_assets = AsyncMock(return_value=existing or [])
    fm.add_asset = AsyncMock(side_effect=lambda **kw: {"id": 50, "sensors": [], **kw})
    fm.made = {}

    def add_sensor(**kw):
        sensor = {"id": 100 + len(fm.made), **kw}
        fm.made[kw["name"]] = sensor
        return sensor

    fm.add_sensor = AsyncMock(side_effect=add_sensor)
    return fm


@pytest.fixture
def named_details(resource_manager_details):
    return resource_manager_details.model_copy(update={"name": "home A"})


@pytest.mark.asyncio
async def test_a_site_is_made_with_every_sensor_and_its_graphs():
    fm = _flexmeasures()
    asset, sensors = await ensure_site(
        fm,
        "home A",
        asset_specs={"generic_asset_type_id": 6},
        sensors=FRBC_SITE_SENSORS,
        timezone="UTC",
    )
    assert asset["id"] == 50
    assert set(sensors) == set(FRBC_SITE_SENSORS)
    made = {c.kwargs["name"]: c.kwargs for c in fm.add_sensor.await_args_list}
    assert made["power"]["attributes"] == {"consumption_is_positive": True}
    assert made["state of charge"]["event_resolution"] == "PT0M"
    graphs = fm.update_asset.await_args.args[1]["sensors_to_show"]
    assert graphs == frbc_site_graphs(sensors)


@pytest.mark.asyncio
async def test_an_existing_site_keeps_its_sensors_and_gets_the_missing_ones():
    fm = _flexmeasures(
        [
            {"id": 6, "name": "home B", "sensors": []},
            {"id": 7, "name": "home A", "sensors": [{"id": 70, "name": "price"}]},
        ]
    )
    asset, sensors = await ensure_site(
        fm,
        "home A",
        asset_specs={"generic_asset_type_id": 6},
        sensors=FRBC_SITE_SENSORS,
        timezone="UTC",
    )
    assert asset["id"] == 7
    assert sensors["price"]["id"] == 70
    assert "price" not in {c.kwargs["name"] for c in fm.add_sensor.await_args_list}
    fm.add_asset.assert_not_awaited()
    specs_update = fm.update_asset.await_args_list[0]
    assert specs_update.args == (7, {"generic_asset_type_id": 6})


@pytest.mark.asyncio
async def test_a_known_site_is_fetched_by_id_rather_than_found_in_a_listing():
    """A resource manager reconnects every round of a co-simulation; a listing of every asset each time is slow."""
    fm = _flexmeasures()
    fm.get_asset = AsyncMock(return_value={"id": 7, "name": "home A", "sensors": []})
    await ensure_site(
        fm,
        "home A",
        asset_specs={},
        sensors={"price": SiteSensor("EUR/kWh", "PT15M")},
        timezone="UTC",
        known_asset_id=7,
    )
    fm.get_assets.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_known_id_that_now_names_another_asset_is_looked_up_again():
    fm = _flexmeasures([{"id": 9, "name": "home A", "sensors": []}])
    fm.get_asset = AsyncMock(
        return_value={"id": 7, "name": "someone else", "sensors": []}
    )
    asset, _ = await ensure_site(
        fm, "home A", asset_specs={}, sensors={}, timezone="UTC", known_asset_id=7
    )
    assert asset["id"] == 9


@pytest.mark.asyncio
async def test_a_site_needs_an_organisation_to_be_made_in():
    fm = _flexmeasures()
    fm.get_account = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="no organisation"):
        await ensure_site(fm, "home A", asset_specs={}, sensors={}, timezone="UTC")


def test_a_graph_without_sensors_on_the_site_is_left_out():
    graphs = frbc_site_graphs({"price": {"id": 1}, "power": {"id": 2}})
    assert graphs == [
        {"title": "Prices", "sensors": [1]},
        {"title": "Power: planned and measured", "sensors": [2]},
    ]


@pytest.mark.asyncio
async def test_each_resource_manager_gets_a_site_named_after_it_and_a_handler_wired_to_it(
    named_details,
):
    fm = _flexmeasures()
    configurator = FRBCSiteConfigurator(
        fm, asset_type_id=6, power_sensor_attributes={"is_strictly_non_positive": True}
    )
    site = await configurator(named_details)
    assert fm.add_asset.await_args.kwargs["name"] == "home A"
    made = fm.made
    assert made["power"]["attributes"] == {
        "consumption_is_positive": True,
        "is_strictly_non_positive": True,
    }
    handler = site.handler
    assert handler._power_sensor_id == made["power"]["id"]
    assert handler._price_sensor_id == made["price"]["id"]
    assert handler._production_price_sensor_id == made["production price"]["id"]
    assert handler._soc_sensor_id == made["state of charge"]["id"]
    assert handler._valid_from_shift == timedelta(0)
    assert handler._schedule_duration == timedelta(hours=24)
    assert handler.send_revocations is True
    assert site.power_sensor_ids == {"ELECTRIC.POWER.L1": made["measured-power"]["id"]}


@pytest.mark.asyncio
async def test_a_reconnecting_resource_manager_finds_its_site_by_id(named_details):
    fm = _flexmeasures()
    configurator = FRBCSiteConfigurator(fm, asset_type_id=6)
    await configurator(named_details)
    fm.get_asset = AsyncMock(
        return_value={"id": 50, "name": "home A", "sensors": list(fm.made.values())}
    )
    fm.get_assets.reset_mock()
    await configurator(named_details)
    fm.get_assets.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_resource_manager_without_a_name_gets_no_site(resource_manager_details):
    configurator = FRBCSiteConfigurator(_flexmeasures(), asset_type_id=6)
    with pytest.raises(ValueError, match="without a name"):
        await configurator(resource_manager_details.model_copy(update={"name": None}))


# --- The CEM, while the site is being configured ---


async def _details_then_selection(cem, rm_handshake, resource_manager_details):
    await cem.handle_message(rm_handshake)
    await cem.handle_message(resource_manager_details)
    await cem.activate_control_type(ControlType.FILL_RATE_BASED_CONTROL)
    messages = []
    while not cem._sending_queue.empty():
        messages.append(await cem.get_message())
    return [m for m in messages if m["message_type"] == "SelectControlType"][0]


@pytest.mark.asyncio
async def test_messages_are_answered_with_a_temporary_error_until_the_site_is_configured(
    rm_handshake, named_details, frbc_system_description
):
    """The resource manager retries a temporary error, so it is served once its handler exists."""
    release = asyncio.Event()
    fm = _flexmeasures()
    configurator = FRBCSiteConfigurator(fm, asset_type_id=6)

    async def slow(details):
        await release.wait()
        return await configurator(details)

    cem = CEM(fm_client=fm, site_configurator=slow)
    selection = await _details_then_selection(cem, rm_handshake, named_details)
    await cem.handle_message(
        ReceptionStatus(
            subject_message_id=selection["message_id"], status=ReceptionStatusValues.OK
        )
    )
    assert cem.control_type == ControlType.FILL_RATE_BASED_CONTROL

    await cem.handle_message(frbc_system_description)
    assert (await cem.get_message())[
        "status"
    ] == ReceptionStatusValues.TEMPORARY_ERROR.value

    release.set()
    await cem._configuring
    assert ControlType.FILL_RATE_BASED_CONTROL in cem._control_types_handlers
    assert cem.power_sensor_id and "ELECTRIC.POWER.L1" in cem.power_sensor_id
    await cem.handle_message(frbc_system_description)
    answer = await cem.get_message()
    assert answer["status"] == ReceptionStatusValues.OK.value


@pytest.mark.asyncio
async def test_a_site_that_cannot_be_configured_leaves_the_resource_manager_retrying(
    rm_handshake, resource_manager_details, caplog
):
    async def failing(details):
        raise ConnectionError("FlexMeasures is down")

    cem = CEM(fm_client=_flexmeasures(), site_configurator=failing)
    await cem.handle_message(rm_handshake)
    await cem.handle_message(resource_manager_details)
    await cem._configuring
    assert cem._control_types_handlers == {}
    assert "Could not configure a site" in caplog.text


@pytest.mark.asyncio
async def test_closing_the_cem_stops_a_site_being_configured(
    rm_handshake, resource_manager_details
):
    async def forever(details):
        await asyncio.sleep(3600)

    cem = CEM(fm_client=_flexmeasures(), site_configurator=forever)
    await cem.handle_message(rm_handshake)
    await cem.handle_message(resource_manager_details)
    configuring = cem._configuring
    await cem.close()
    await asyncio.wait([configuring], timeout=1)
    assert configuring.cancelled()


@pytest.mark.asyncio
async def test_a_known_site_that_cannot_be_fetched_is_looked_up_again():
    """Deleted since, say: the listing finds it by name, or it is made again."""
    fm = _flexmeasures([{"id": 9, "name": "home A", "sensors": []}])
    fm.get_asset = AsyncMock(side_effect=ValueError("404"))
    asset, _ = await ensure_site(
        fm, "home A", asset_specs={}, sensors={}, timezone="UTC", known_asset_id=7
    )
    assert asset["id"] == 9
