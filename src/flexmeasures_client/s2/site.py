"""Set up a FlexMeasures site for an S2 resource manager.

A site consists of an asset, its sensors and its graphs, plus a handler wired to those sensors.
The example server keeps one site for whichever resource manager connects (see ensure_site).
A CEM can also serve many resource managers, as in a co-simulation of a community.
Such a CEM uses FRBCSiteConfigurator to give each resource manager its own site, named after it.
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
from flexmeasures_client.exceptions import ContentTypeError
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class SiteSensor:
    """The settings for creating a site's sensor: its unit, its resolution, and any attributes."""

    unit: str
    resolution: str
    attributes: dict = field(default_factory=dict)


#: The sensors an FRBC site needs, by name.
#: Their units are the units the CEM records in: fill levels in kWh, and fill rates, usage and leakage in W, which S2 states per second.
#: The names are a contract with any reader of the site, such as a co-simulation.
#: A co-simulation looks up "price", "production price", "power", "state of charge" and "measured-power" by name.
FRBC_SITE_SENSORS: dict[str, SiteSensor] = {
    "price": SiteSensor("EUR/kWh", "PT15M"),
    "production price": SiteSensor("EUR/kWh", "PT15M"),
    # In S2, consumed power is positive.
    # This sensor holds both an actuator's power and the schedule planned for it.
    # Without this attribute, FlexMeasures treats production as positive on a power sensor.
    # It would then flip the sign of an incoming schedule, so the power and the schedule would point in opposite directions.
    "power": SiteSensor("kW", "PT15M", {"consumption_is_positive": True}),
    # The power the resource manager measures its site drawing, kept separate from the schedule on "power".
    "measured-power": SiteSensor("kW", "PT15M", {"consumption_is_positive": True}),
    "state of charge": SiteSensor("kWh", "PT0M"),
    "RM discharge": SiteSensor("dimensionless", "PT15M"),
    "fill rate": SiteSensor("W", "PT15M"),
    "soc minima": SiteSensor("kWh", "PT15M"),
    "soc maxima": SiteSensor("kWh", "PT15M"),
    "usage forecast": SiteSensor("W", "PT15M"),
    # The leakage rate the resource manager declares, as it is planned with, in the same unit as the usage forecast.
    "leakage": SiteSensor("W", "PT15M"),
    "charging efficiency": SiteSensor("dimensionless", "PT15M"),
}

#: The sensor a site needs for the handler to correct a usage forecast that no plan can absorb (see FRBCSimple's usage_correction_sensor_id).
#: It holds the rate of fill level the correction adds to the stock: positive where it gives back usage, negative where it takes away a gain.
#: Kept apart from FRBC_SITE_SENSORS, since a site only gets it where the deployment asks for a correction.
USAGE_CORRECTION_SENSORS: dict[str, SiteSensor] = {
    "usage correction": SiteSensor("kW", "PT15M", {"consumption_is_positive": True}),
}


def frbc_site_graphs(sensors: dict[str, dict]) -> list[dict]:
    """Return a site's graphs, top to bottom, in the format of an asset's sensors_to_show in FlexMeasures.

    Each graph has a title and a list of sensors.
    Every FlexMeasures version reads this format.
    The newer format with plots draws the same graphs, but needs FlexMeasures v0.32 or later.
    A graph is left out if the site has none of its sensors.
    """
    # Keep the titles from #188, because a co-simulation's controller adds to the graph titled "Power".
    graphs = [
        ("Prices", ["price", "production price"]),
        ("Power", ["power", "measured-power"]),
        ("State of charge", ["state of charge", "soc minima", "soc maxima"]),
        ("Fill rate and usage", ["fill rate", "usage forecast"]),
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
    reset_graphs: bool = False,
) -> tuple[dict, dict[str, dict]]:
    """Find or create the site asset with this name, with its specs, all of these sensors, and its graphs.

    Running this again is safe: existing parts are kept, and missing parts are created.
    A flex model in the specs is merged into the asset's existing flex model, because others write to it too.
    For example, a co-simulation's controller sets the value of the asset's stock at the end.
    Graphs are only set if the asset has none, because others add to them too.
    Pass reset_graphs to set them anyway.

    :param known_asset_id:  The asset's id from an earlier lookup, which avoids listing every asset.
    :param reset_graphs:    Whether to set the graphs even if the asset already has some.
    :returns:               The asset, and its sensors by name.
    """
    site_asset = None
    if known_asset_id is not None:
        try:
            candidate = await fm_client.get_asset(
                known_asset_id, parse_json_fields=True
            )
        except (ValueError, ConnectionError, ContentTypeError):
            candidate = None
        if candidate is not None and candidate.get("name") == name:
            site_asset = candidate
    if site_asset is None:
        # Pick the newest asset with that name, as a co-simulation does too.
        named = [
            asset
            for asset in await fm_client.get_assets(parse_json_fields=True)
            if asset["name"] == name
        ]
        site_asset = max(named, key=lambda asset: asset["id"]) if named else None
    created = site_asset is None
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
        specs = dict(asset_specs)
        if isinstance(specs.get("flex_model"), dict) and isinstance(
            site_asset.get("flex_model"), dict
        ):
            specs["flex_model"] = {**site_asset["flex_model"], **specs["flex_model"]}
        await fm_client.update_asset(site_asset["id"], specs, parse_json_fields=True)

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

    if created or reset_graphs or not site_asset.get("sensors_to_show"):
        await fm_client.update_asset(
            site_asset["id"],
            {"sensors_to_show": frbc_site_graphs(made)},
            parse_json_fields=True,
        )
    return site_asset, made


@dataclass
class ConfiguredSite:
    """What a CEM needs to serve one resource manager: its handler, and the sensors to record its measured power on."""

    handler: FRBCSimple
    #: Per commodity quantity, the sensor to record measured power on, in the format of the CEM's power_sensor_id.
    power_sensor_ids: dict[str, int]
    asset_id: int


class FRBCSiteConfigurator:
    """Give each resource manager its own site, named after it, and an FRBC handler wired to the site's sensors.

    The server creates one configurator and hands it to each CEM it creates (see the CEM's site_configurator).
    The configurator remembers each site's asset id.
    So when a resource manager reconnects, its site is found by that id, without listing every asset on the server.
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
        correct_usage: bool = False,
        logger: Logger | None = None,
    ) -> None:
        """
        :param asset_type_id:           The FlexMeasures asset type to create each site with.
        :param timezone:                The timezone of each site's sensors, and of its handler.
        :param asset_specs:             Other specs to create or update each site asset with, such as a flex model.
        :param sensors:                 The sensors each site gets; FRBC_SITE_SENSORS by default.
        :param power_sensor_attributes: Extra attributes for the "power" sensor that depend on the device,
                                        such as that it cannot produce.
        :param handler_options:         Keyword arguments for each FRBCSimple, other than its sensors.
                                        By default, it plans from now over 24 hours, because a co-simulation steps through days.
        :param send_revocations:        Whether a newer plan revokes the remainder of the previous plan.
        :param correct_usage:           Whether each site gets a sensor to correct a usage forecast that no plan can absorb.
                                        With a correction, the handler also sets the breach prices of the fill-level band and the site's capacity,
                                        relative to the market, rather than leaving them to FlexMeasures (see FRBCSimple's usage_correction_sensor_id).
        """
        self._fm_client = fm_client
        self._timezone = timezone
        self._asset_specs = {
            "generic_asset_type_id": asset_type_id,
            **(asset_specs or {}),
        }
        sensors = dict(sensors if sensors is not None else FRBC_SITE_SENSORS)
        if correct_usage:
            sensors.update(USAGE_CORRECTION_SENSORS)
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
            leakage_behaviour_sensor_id=sensor_id("leakage"),
            charging_efficiency_sensor_id=sensor_id("charging efficiency"),
            usage_correction_sensor_id=sensor_id("usage correction"),
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
