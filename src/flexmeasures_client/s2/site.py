"""A FlexMeasures site for an S2 resource manager: its asset, its sensors, its graphs, and the handler wired to them.

The example server keeps one site for whichever resource manager connects (see ensure_site).
A CEM serving many resource managers, as a co-simulation of a community does, gives each one a site of its own,
named after it, with FRBCSiteConfigurator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from logging import Logger, getLogger
from typing import Any

try:
    from s2python.common import ResourceManagerDetails
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )

from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class SiteSensor:
    """What a site's sensor is made with: its unit, its resolution, and any attributes."""

    unit: str
    resolution: str
    attributes: dict = field(default_factory=dict)


#: The sensors an FRBC site needs, by name.
#: Their units are the ones the CEM records in: fill levels in kWh, rates of fill level per second, as S2 states them.
#: The names are a contract with whoever reads the site, a co-simulation among them,
#: which looks up "price", "production price", "power", "state of charge" and "measured-power" by name.
FRBC_SITE_SENSORS: dict[str, SiteSensor] = {
    "price": SiteSensor("EUR/kWh", "PT15M"),
    "production price": SiteSensor("EUR/kWh", "PT15M"),
    # S2 states a power as positive when it is consumed,
    # and this sensor holds both an actuator's power and the schedule planned for it.
    # Left unsaid, FlexMeasures reads a power sensor the other way round,
    # and flips a schedule's sign on the way in, which puts the two in opposite directions.
    "power": SiteSensor("kW", "PT15M", {"consumption_is_positive": True}),
    # What the resource manager measures its site drawing, kept apart from the schedule on "power".
    "measured-power": SiteSensor("kW", "PT15M", {"consumption_is_positive": True}),
    "state of charge": SiteSensor("kWh", "PT0M"),
    "RM discharge": SiteSensor("dimensionless", "PT15M"),
    "fill rate": SiteSensor("kWh/s", "PT15M"),
    "soc minima": SiteSensor("kWh", "PT15M"),
    "soc maxima": SiteSensor("kWh", "PT15M"),
    "usage forecast": SiteSensor("kWh/s", "PT15M"),
    "storage efficiency": SiteSensor("%", "PT15M"),
    "charging efficiency": SiteSensor("dimensionless", "PT15M"),
}


def frbc_site_graphs(sensors: dict[str, dict]) -> list[dict]:
    """A site's graphs, top to bottom, as FlexMeasures takes an asset's sensors_to_show.

    In the form with a title and a list of sensors per graph, which every FlexMeasures version reads,
    rather than the newer one with plots, which needs v0.32 or later and draws the same graphs.
    A graph whose sensors the site does not have is left out.
    """
    graphs = [
        ("Prices", ["price", "production price"]),
        ("Power: planned and measured", ["power", "measured-power"]),
        (
            "Buffer: fill level and comfort band",
            ["state of charge", "soc minima", "soc maxima"],
        ),
        (
            "Buffer: filled by the actuator, drawn by usage",
            ["fill rate", "usage forecast"],
        ),
    ]
    shown = []
    for title, names in graphs:
        ids = [sensors[name]["id"] for name in names if name in sensors]
        if ids:
            shown.append({"title": title, "sensors": ids})
    return shown


async def ensure_site(
    fm_client: FlexMeasuresClient,
    name: str,
    *,
    asset_specs: dict[str, Any],
    sensors: dict[str, SiteSensor],
    timezone: str,
    known_asset_id: int | None = None,
) -> tuple[dict, dict[str, dict]]:
    """Find or make the site asset of this name, with its specs and every one of these sensors, and its graphs.

    It is safe to run again: what exists is kept, and what is missing is made.

    :param known_asset_id:  the asset's id where it was found before, which spares a listing of every asset.
    :returns:               the asset, and its sensors by name.
    """
    site_asset = None
    if known_asset_id is not None:
        try:
            candidate = await fm_client.get_asset(
                known_asset_id, parse_json_fields=True
            )
        except (ValueError, ConnectionError):
            candidate = None
        if candidate is not None and candidate.get("name") == name:
            site_asset = candidate
    if site_asset is None:
        for asset in await fm_client.get_assets(parse_json_fields=True):
            if asset["name"] == name:
                site_asset = asset
                break
    if site_asset is None:
        account = await fm_client.get_account()
        if account is None:
            raise ValueError(
                f"The user belongs to no organisation, so there is nowhere to create the site {name!r}."
            )
        site_asset = await fm_client.add_asset(
            name=name, account_id=account["id"], **asset_specs
        )
    else:
        await fm_client.update_asset(
            site_asset["id"], asset_specs, parse_json_fields=True
        )

    existing = {sensor["name"]: sensor for sensor in site_asset.get("sensors", [])}
    made: dict[str, dict] = {}
    for sensor_name, spec in sensors.items():
        made[sensor_name] = existing.get(sensor_name) or await fm_client.add_sensor(
            name=sensor_name,
            event_resolution=spec.resolution,
            unit=spec.unit,
            generic_asset_id=site_asset["id"],
            timezone=timezone,
            **({"attributes": spec.attributes} if spec.attributes else {}),
        )

    # Set on every run, so changes made to the graphs in the UI do not outlive a restart.
    await fm_client.update_asset(
        site_asset["id"],
        {"sensors_to_show": frbc_site_graphs(made)},
        parse_json_fields=True,
    )
    return site_asset, made


@dataclass
class ConfiguredSite:
    """What a CEM needs to serve one resource manager: its handler, and where its measured power goes."""

    handler: FRBCSimple
    #: The sensor each commodity quantity's measured power is recorded on, as the CEM's power_sensor_id takes it.
    power_sensor_ids: dict[str, int]
    asset_id: int


class FRBCSiteConfigurator:
    """Gives each resource manager a site of its own, named after it, and an FRBC handler wired to its sensors.

    Made once per server and handed to each CEM it makes (see CEM's site_configurator),
    so that a resource manager that reconnects finds its site by the id remembered here,
    rather than by a listing of every asset on the server.
    """

    def __init__(
        self,
        fm_client: FlexMeasuresClient,
        *,
        asset_type_id: int,
        timezone: str = "UTC",
        asset_specs: dict[str, Any] | None = None,
        sensors: dict[str, SiteSensor] | None = None,
        power_sensor_attributes: dict[str, Any] | None = None,
        handler_options: dict[str, Any] | None = None,
        send_revocations: bool = True,
        logger: Logger | None = None,
    ) -> None:
        """
        :param asset_type_id:           the FlexMeasures asset type each site is made with.
        :param timezone:                the time zone of each site's sensors, and of its handler.
        :param asset_specs:             anything else each site asset is made or updated with, such as a flex model.
        :param sensors:                 the sensors each site gets; FRBC_SITE_SENSORS by default.
        :param power_sensor_attributes: attributes added to the "power" sensor's, which a device's nature decides,
                                        such as that it cannot produce.
        :param handler_options:         keyword arguments for each FRBCSimple beyond its sensors.
                                        By default it plans from now, over 24 hours, as a co-simulation steps through days.
        :param send_revocations:        whether a newer plan revokes what is left of the one before.
        """
        self._fm_client = fm_client
        self._timezone = timezone
        self._asset_specs = {
            "generic_asset_type_id": asset_type_id,
            **(asset_specs or {}),
        }
        sensors = dict(sensors if sensors is not None else FRBC_SITE_SENSORS)
        if power_sensor_attributes:
            power = sensors["power"]
            sensors["power"] = SiteSensor(
                power.unit,
                power.resolution,
                {**power.attributes, **power_sensor_attributes},
            )
        self._sensors = sensors
        self._handler_options = {
            "valid_from_shift": timedelta(0),
            "schedule_duration": timedelta(hours=24),
            **(handler_options or {}),
        }
        self._send_revocations = send_revocations
        self._asset_ids: dict[str, int] = {}
        self._logger = logger or _LOGGER

    async def __call__(self, details: ResourceManagerDetails) -> ConfiguredSite:
        name = details.name
        if not name:
            raise ValueError(
                "A resource manager without a name cannot be given a site of its own, which is found by its name."
            )
        asset, sensors = await ensure_site(
            self._fm_client,
            name,
            asset_specs=self._asset_specs,
            sensors=self._sensors,
            timezone=self._timezone,
            known_asset_id=self._asset_ids.get(name),
        )
        self._asset_ids[name] = asset["id"]
        self._logger.info("Resource manager %r has site %s.", name, asset["id"])

        def sensor_id(sensor_name: str) -> int | None:
            sensor = sensors.get(sensor_name)
            return sensor["id"] if sensor else None

        handler = FRBCSimple(
            power_sensor_id=sensors["power"]["id"],
            price_sensor_id=sensors["price"]["id"],
            production_price_sensor_id=sensor_id("production price"),
            soc_sensor_id=sensors["state of charge"]["id"],
            rm_discharge_sensor_id=sensors["RM discharge"]["id"],
            fill_rate_sensor_id=sensor_id("fill rate"),
            soc_minima_sensor_id=sensor_id("soc minima"),
            soc_maxima_sensor_id=sensor_id("soc maxima"),
            usage_forecast_sensor_id=sensor_id("usage forecast"),
            leakage_behaviour_sensor_id=sensor_id("storage efficiency"),
            charging_efficiency_sensor_id=sensor_id("charging efficiency"),
            timezone=self._timezone,
            **self._handler_options,
        )
        handler.send_revocations = self._send_revocations
        measured = sensor_id("measured-power")
        return ConfiguredSite(
            handler=handler,
            power_sensor_ids={"ELECTRIC.POWER.L1": measured} if measured else {},
            asset_id=asset["id"],
        )
