from const import (
    BATTERY_CONFIG,
    EV_CONFIG,
    HEATING_CONFIG,
    battery_name,
    evse1_name,
    evse2_name,
    heating_name,
    latitude,
    longitude,
    price_market_name,
    pv_name,
    weather_station_name,
)
from flexmeasures_client import FlexMeasuresClient


async def get_or_create_asset(
    client: FlexMeasuresClient,
    *,
    name: str,
    account_id: int,
    generic_asset_type_id: int,
    parent_asset_id: int | None = None,
) -> dict:
    """Return one exact asset or create it in the requested hierarchy position."""
    assets = await client.get_assets(
        account_id=account_id,
        fields=["id", "name", "account_id", "parent_asset_id"],
        parse_json_fields=False,
    )
    matches = [
        asset
        for asset in assets
        if asset.get("name") == name
        and asset.get("account_id") == account_id
        and asset.get("parent_asset_id") == parent_asset_id
    ]
    if len(matches) > 1:
        raise LookupError(
            f"Expected at most one asset named '{name}' under parent "
            f"{parent_asset_id}, found {len(matches)}."
        )
    if matches:
        print(f"Reusing asset '{name}' with ID {matches[0]['id']}")
        return matches[0]
    return await client.add_asset(
        name=name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=generic_asset_type_id,
        account_id=account_id,
        parent_asset_id=parent_asset_id,
    )


async def get_or_create_sensor(
    client: FlexMeasuresClient,
    *,
    name: str,
    event_resolution: str,
    unit: str,
    generic_asset_id: int,
    timezone: str | None = "Europe/Amsterdam",
    attributes: dict | None = None,
) -> dict:
    """Return one exact sensor on an asset or create the missing sensor."""
    sensors = await client.get_sensors(
        asset_id=generic_asset_id, parse_json_fields=False
    )
    # FlexMeasures 0.33 may include sensors on descendant assets here.
    matches = [
        sensor
        for sensor in sensors
        if sensor.get("name") == name
        and sensor.get("generic_asset_id") == generic_asset_id
    ]
    if len(matches) > 1:
        raise LookupError(
            f"Expected at most one sensor named '{name}' on asset "
            f"{generic_asset_id}, found {len(matches)}."
        )
    if matches:
        print(f"Reusing sensor '{name}' with ID {matches[0]['id']}")
        return matches[0]
    return await client.add_sensor(
        name=name,
        event_resolution=event_resolution,
        unit=unit,
        generic_asset_id=generic_asset_id,
        timezone=timezone,
        attributes=attributes,
    )


async def get_or_create_price_sensor(client: FlexMeasuresClient):
    """Get or create an account-owned price sensor (1h, EUR/kWh).

    Returns the price sensor for use in flex-context.
    """
    print("Getting or creating price sensor...")
    # Get the client account id
    account = await client.get_account()
    account_id = account["id"]
    print(f"Account ID: {account_id}")
    price_market_asset = await get_or_create_asset(
        client,
        name=price_market_name,
        account_id=account_id,
        generic_asset_type_id=8,
    )
    price_sensor = await get_or_create_sensor(
        client,
        name="electricity-price",
        event_resolution="PT1H",
        unit="EUR/kWh",
        generic_asset_id=price_market_asset["id"],
    )

    print(f"Price sensor ID: {price_sensor['id']}")
    return price_sensor


async def get_or_create_weather_station(client: FlexMeasuresClient):
    """Get or create an account-owned weather station and its sensors."""
    print("Getting or creating weather station...")
    # Get the client account id
    account = await client.get_account()
    account_id = account["id"]
    print(f"Account ID: {account_id}")
    weather_asset = await get_or_create_asset(
        client,
        name=weather_station_name,
        account_id=account_id,
        generic_asset_type_id=7,
    )
    irradiation_sensor = await get_or_create_sensor(
        client,
        name="irradiation",
        event_resolution="PT1H",
        unit="W/m²",
        generic_asset_id=weather_asset["id"],
    )
    cloud_coverage_sensor = await get_or_create_sensor(
        client,
        name="cloud-coverage",
        event_resolution="PT1H",
        unit="%",
        generic_asset_id=weather_asset["id"],
    )

    print(f"Created weather station with ID: {weather_asset['id']}")
    return weather_asset, irradiation_sensor, cloud_coverage_sensor


async def create_site_asset(
    client: FlexMeasuresClient,
    account_id: int,
    price_sensor_id: int,
    site_name: str,
    site_asset_id: int,
):
    """Create Site asset with consumption and energy costs KPI sensors."""
    print("Creating Site asset...")

    # Create site asset (generic_asset_type_id=6 for building)
    site_asset = await get_or_create_asset(
        client,
        name=site_name,
        parent_asset_id=site_asset_id,
        generic_asset_type_id=6,
        account_id=account_id,
    )

    # Create general consumption sensor (15min resolution, kW)
    consumption_sensor = await get_or_create_sensor(
        client,
        name="electricity-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create energy costs KPI sensor (1D resolution, EUR)
    energy_costs_sensor = await get_or_create_sensor(
        client,
        name="energy-costs-kpi",
        event_resolution="P1D",
        unit="EUR",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create aggregate power sensor for the site
    aggregate_sensor = await get_or_create_sensor(
        client,
        name="electricity-aggregate",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create max production capacity sensor for the site
    max_production_sensor = await get_or_create_sensor(
        client,
        name="max-production-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create max consumption capacity sensor for the site
    max_consumption_sensor = await get_or_create_sensor(
        client,
        name="max-consumption-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create site-peak-consumption-price sensor (15min resolution, EUR/kW)
    site_peak_consumption_price_sensor = await get_or_create_sensor(
        client,
        name="site-peak-consumption-price",
        event_resolution="PT15M",
        unit="EUR/kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create site-peak-production-price sensor (15min resolution, EUR/kW)
    site_peak_production_price_sensor = await get_or_create_sensor(
        client,
        name="site-peak-production-price",
        event_resolution="PT15M",
        unit="EUR/kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create self-consumption sensor for the site
    self_consumption_sensor = await get_or_create_sensor(
        client,
        name="self-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create total energy costs sensor for the site
    total_energy_costs_sensor = await get_or_create_sensor(
        client,
        name="total-energy-costs",
        event_resolution="PT15M",
        unit="EUR",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create daily total energy costs sensor for the site
    daily_total_energy_costs_sensor = await get_or_create_sensor(
        client,
        name="daily-total-energy-costs",
        event_resolution="P1D",
        unit="EUR",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create daily share of self-consumption sensor for the site
    daily_share_of_self_consumption_sensor = await get_or_create_sensor(
        client,
        name="daily-share-of-self-consumption",
        event_resolution="P1D",
        unit="%",
        generic_asset_id=site_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created site asset with ID: {site_asset['id']}")
    return (
        site_asset,
        consumption_sensor,
        energy_costs_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
        site_peak_consumption_price_sensor,
        site_peak_production_price_sensor,
        total_energy_costs_sensor,
        daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor,
    )


async def create_pv_asset(
    client: FlexMeasuresClient, account_id: int, site_asset_id: int, pv_name: str
):
    """Create PV asset as a child of the site, and give it a production sensor."""
    print("Creating PV asset...")

    # Create PV asset (generic_asset_type_id=1 for solar/PV)
    pv_asset = await get_or_create_asset(
        client,
        name=pv_name,
        generic_asset_type_id=1,
        account_id=account_id,
        parent_asset_id=site_asset_id,
    )

    # Create production sensor (15min, kW)
    pv_production_sensor = await get_or_create_sensor(
        client,
        name="electricity-production",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=pv_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create power sensor (15min, kW)
    pv_power_sensor = await get_or_create_sensor(
        client,
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=pv_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created PV asset with ID: {pv_asset['id']}")
    return pv_asset, pv_production_sensor, pv_power_sensor


async def create_battery_asset(
    client: FlexMeasuresClient,
    account_id: int,
    site_asset_id: int,
    battery_name: str,
):
    """Create battery asset as a child of the site, and give it power and SoC sensors + settings."""
    print("Creating battery asset...")

    # Create battery asset (generic_asset_type_id=5 for battery)
    battery_asset = await get_or_create_asset(
        client,
        name=battery_name,
        generic_asset_type_id=5,
        account_id=account_id,
        parent_asset_id=site_asset_id,
    )

    # Create power sensor (15min, kW)
    battery_power_sensor = await get_or_create_sensor(
        client,
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=battery_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (0min, kWh)
    battery_soc_sensor = await get_or_create_sensor(
        client,
        name="state-of-charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=battery_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Store battery settings in flex_model attribute (attributes["flex_model"])
    print("Updating battery asset with flex_model settings...")
    capacity = BATTERY_CONFIG["capacity_kwh"]
    attributes_flex_model = {
        "soc_at_start": capacity * BATTERY_CONFIG["soc_at_start_percent"],
    }

    flex_model = {
        "soc-max": f"{capacity * BATTERY_CONFIG['max_soc_percent']} kWh",  # Use operational max (e.g., 90% of physical capacity)
        "soc-min": f"{capacity * BATTERY_CONFIG['min_soc_percent']} kWh",
        "roundtrip-efficiency": BATTERY_CONFIG["roundtrip_efficiency"],
        "power-capacity": f"{BATTERY_CONFIG['power_capacity_kw']}kW",
        "state-of-charge": {"sensor": battery_soc_sensor["id"]},
    }

    # Store soc_at_start in attributes["flex_model"] for now, as it's not supported yet in asset flex_model field
    await client.update_asset(
        asset_id=battery_asset["id"],
        updates={
            "flex_model": flex_model,
            "attributes": {"flex_model": attributes_flex_model},
        },
    )

    print(f"Created battery asset with ID: {battery_asset['id']}")
    return battery_asset, battery_power_sensor, battery_soc_sensor


async def create_evse_asset(
    client: FlexMeasuresClient, account_id: int, site_asset_id: int, evse_name: str
):
    """Create EVSE asset as a child of the site, and give it power and SoC sensors + settings."""
    print(f"Creating EVSE asset: {evse_name}...")

    # Create EVSE asset - using generic type 4 for one-way EVSE based on the codebase search
    # Note: We'll use a basic asset type since one-way_evse might not be available by default
    evse_asset = await get_or_create_asset(
        client,
        name=evse_name,
        generic_asset_type_id=4,
        account_id=account_id,
        parent_asset_id=site_asset_id,
    )

    # Create power sensor (15min, kW)
    evse_power_sensor = await get_or_create_sensor(
        client,
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (instantaneous, kWh)
    evse_soc_sensor = await get_or_create_sensor(
        client,
        name="state-of-charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create soc-min sensor (15min, kWh)
    evse_soc_min_sensor = await get_or_create_sensor(
        client,
        name="soc-min",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create soc-max sensor (15min, kWh)
    evse_soc_max_sensor = await get_or_create_sensor(
        client,
        name="soc-max",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Store EVSE settings in flex_model attribute
    print(f"Updating {evse_name} asset with flex_model settings...")
    capacity = EV_CONFIG["default_capacity_kwh"]
    attributes_flex_model = {
        "soc_at_start": capacity * EV_CONFIG["min_soc_percent"],  # Start at minimum SoC
    }

    flex_model = {
        "soc-max": f"{capacity} kWh",  # Allow operational max to be different from physical capacity
        "soc-min": f"{capacity * EV_CONFIG['min_soc_percent']} kWh",
        "roundtrip-efficiency": EV_CONFIG["roundtrip_efficiency"],
        "power-capacity": f"{EV_CONFIG['default_power_capacity_kw']}kW",  # Total power capacity
        "production-capacity": "0kW",  # Charging only, no V2G capability
        "state-of-charge": {"sensor": evse_soc_sensor["id"]},
    }

    # Configure graph displays as requested
    sensors_to_show = [
        {
            "title": "State of charge",
            "sensors": [
                evse_soc_sensor["id"],
                evse_soc_min_sensor["id"],
                evse_soc_max_sensor["id"],
            ],
        },
        {
            "title": "Power",
            "sensors": [
                evse_power_sensor["id"],
            ],
        },
    ]

    # Store soc_at_start in attributes["flex_model"] for now, as it's not supported yet in asset flex_model field
    await client.update_asset(
        asset_id=evse_asset["id"],
        updates={
            "flex_model": flex_model,
            "attributes": {
                "flex_model": attributes_flex_model,
                "sensors_to_show": sensors_to_show,
            },
        },
    )

    print(f"Created EVSE asset {evse_name} with ID: {evse_asset['id']}")
    return (
        evse_asset,
        evse_power_sensor,
        evse_soc_sensor,
        evse_soc_min_sensor,
        evse_soc_max_sensor,
    )


async def create_heating_asset(
    client: FlexMeasuresClient,
    account_id: int,
    site_asset_id: int,
    heating_name: str,
    latitude: float,
    longitude: float,
):
    """Create heating asset as a child of the site, and give it temperature, power & energy sensors + settings."""
    print(f"Creating heating asset: {heating_name}...")

    # Create heating asset (generic asset type id = 5 if heating not defined in DB)
    heating_asset = await get_or_create_asset(
        client,
        name=heating_name,
        generic_asset_type_id=5,
        account_id=account_id,
        parent_asset_id=site_asset_id,
    )

    # Power sensors (15min, kW)
    heating_power_sensor = await get_or_create_sensor(
        client,
        name="power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Soc usage sensor (15min, kW)
    heating_soc_usage_sensor = await get_or_create_sensor(
        client,
        name="soc-usage",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # State of Charge sensors (15min, kWh)
    heating_soc_sensor = await get_or_create_sensor(
        client,
        name="state of charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )
    heating_min_soc_sensor = await get_or_create_sensor(
        client,
        name="min SoC",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )
    heating_max_soc_sensor = await get_or_create_sensor(
        client,
        name="max SoC",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # COP (Coefficient of Performance)
    heating_COP = await get_or_create_sensor(
        client,
        name="COP",
        event_resolution="PT15M",
        unit="%",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )

    capacity = HEATING_CONFIG["capacity_kwh"]

    flex_model = {
        "soc-max": f"{capacity} kWh",
        "soc-min": f"{capacity * HEATING_CONFIG['min_soc_percent']} kWh",
        "soc-usage": [{"sensor": heating_soc_usage_sensor["id"]}],
        "charging-efficiency": f"{HEATING_CONFIG['charging_efficiency']*100} %",
        "consumption-capacity": "5 kW",
        "production-capacity": "0 kW",
        "storage-efficiency": f"{HEATING_CONFIG['storage_efficiency']*100} %",
        "power-capacity": f"{HEATING_CONFIG['power_capacity_kw']}kW",
        "state-of-charge": {"sensor": heating_soc_sensor["id"]},
    }

    # === Configure graph displays ===
    sensors_to_show = [
        {
            "title": "State of Charge",
            "sensors": [
                heating_soc_sensor["id"],
                heating_min_soc_sensor["id"],
                heating_max_soc_sensor["id"],
            ],
        },
        {
            "title": "Power and heat",
            "sensors": [
                heating_power_sensor["id"],
                heating_soc_usage_sensor["id"],
            ],
        },
    ]

    # === Update asset with all attributes ===
    await client.update_asset(
        asset_id=heating_asset["id"],
        updates={
            "flex_model": flex_model,
            "sensors_to_show": sensors_to_show,
        },
    )

    print(f"Created heating asset '{heating_name}' with ID: {heating_asset['id']}")
    return (
        heating_asset,
        heating_power_sensor,
        heating_soc_usage_sensor,
        heating_soc_sensor,
        heating_min_soc_sensor,
        heating_max_soc_sensor,
        heating_COP,
    )


async def configure_site_flex_context(
    client: FlexMeasuresClient,
    site_asset,
    price_sensor,
    consumption_sensor,
    pv_production_sensor,
    battery_power_sensor,
    max_consumption_sensor,
    max_production_sensor,
    site_peak_consumption_price_sensor,
    site_peak_production_price_sensor,
    aggregate_sensor,
):
    """Configure the site asset with comprehensive flex-context."""
    print("Configuring site flex-context...")

    # Create flex context with all required settings
    flex_context = {
        # Price sensor reference (new format)
        "consumption-price": {"sensor": price_sensor["id"]},
        # Consumption capacity limit (not typically needed for private homes, but including as requested)
        # Calculated using a smaller connection category: 3 x 25 A at 230 V
        "site-consumption-capacity": {
            "sensor": max_consumption_sensor["id"]
        },  # Relaxed constraint for residential
        "site-production-capacity": {
            "sensor": max_production_sensor["id"]
        },  # Relaxed constraint for residential
        "site-power-capacity": "20 kVA",
        # Enable soft constraints for SoC minima (this makes soc-minima soft constraints instead of hard)
        "relax-soc-constraints": True,
        "relax-site-capacity-constraints": True,
        "site-peak-consumption-price": {
            "sensor": site_peak_consumption_price_sensor["id"]
        },
        "site-peak-production-price": {
            "sensor": site_peak_production_price_sensor["id"]
        },
        "site-peak-consumption": "(30/2 - 1) kW",  # i.e. the community capacity fairly shared amongst the sites, minus 1 to hedge against forecast errors
        # Configure breach prices for soft constraints
        # Energy price units (match electricity-price sensor): EUR/kWh
        # Moderate penalty for not meeting soc-minima (allows some flexibility)
        # "soc-minima-breach-price": "100000 EUR/kWh",  # Lower penalty for soft constraint
        # "soc-maxima-breach-price": "100000 EUR/kWh",  # Higher penalty for safety limits
        # Capacity price units (for power capacity constraints): EUR/MW
        # "site-consumption-breach-price": "100000000 EUR/MW",
        # "site-production-breach-price": "10000000 EUR/MW",
        # "consumption-breach-price": "1000 EUR/MW",
        # "production-breach-price": "1000 EUR/MW",
        # Add inflexible devices as requested
        "inflexible-device-sensors": [
            consumption_sensor["id"],  # General consumption
        ],
        "aggregate-power": {"sensor": aggregate_sensor["id"]},
    }

    # Update site asset with flex-context
    await client.update_asset(
        asset_id=site_asset["id"], updates={"flex_context": flex_context}
    )

    print("Site flex-context configured successfully")


async def configure_site_dashboard(
    client: FlexMeasuresClient,
    site_asset,
    consumption_sensor,
    pv_production_sensor,
    pv_power_sensor,
    battery_power_sensor,
    battery_soc_sensor,
    evse1_power_sensor,
    evse2_power_sensor,
    heating_power_sensor,
    heating_soc_sensor,
    aggregate_sensor,
    self_consumption_sensor,
    max_production_sensor,
    max_consumption_sensor,
    price_sensor,
    total_energy_costs_sensor,
    daily_total_energy_costs_sensor,
    daily_share_of_self_consumption_sensor,
):
    """Configure sensors_to_show for site asset graphs."""
    print("Configuring sensors to show...")

    # Configure graph displays as requested
    sensors_to_show = [
        {
            "title": "Power flow by type",
            "sensors": [
                consumption_sensor["id"],
                pv_production_sensor["id"],
                battery_power_sensor["id"],
                evse1_power_sensor["id"],
                heating_power_sensor["id"],
                # evse2_power_sensor["id"],  # Just showing one now to avoid cluttering the chart
            ],
        },
        {
            "title": "Solar self-consumption",
            "sensors": [
                self_consumption_sensor["id"],
                pv_production_sensor["id"],
                pv_power_sensor["id"],
            ],
        },
        {
            "title": "Prices",
            "sensors": [
                price_sensor["id"],
            ],
        },
        {
            "title": "Energy costs",
            "sensors": [
                total_energy_costs_sensor["id"],
            ],
        },
        {
            "title": "Storages SoC",
            "sensors": [battery_soc_sensor["id"], heating_soc_sensor["id"]],
        },
        {
            "title": "Site capacity",
            "sensors": [
                aggregate_sensor["id"],
                max_consumption_sensor["id"],
                max_production_sensor["id"],
            ],
        },
    ]

    sensors_to_show_as_kpis = [
        {
            "title": "Daily costs",
            "sensor": daily_total_energy_costs_sensor["id"],
            "function": "sum",
        },
        {
            "title": "Self-consumption",
            "sensor": daily_share_of_self_consumption_sensor["id"],
            "function": "mean",
        },
    ]

    # Update site asset with sensors_to_show
    await client.update_asset(
        asset_id=site_asset["id"],
        updates={
            "sensors_to_show": sensors_to_show,
            "sensors_to_show_as_kpis": sensors_to_show_as_kpis,
        },
    )

    print("Sensors to show configured successfully")


async def create_sites_assets_and_sensors(
    client: FlexMeasuresClient,
    account: dict,
    community_asset_id: int,
    site_index: int,
    site_names: list[str],
    price_sensor: dict,
):
    """
    Create a site asset with its associated sensors and linked assets (PV, battery, EVSEs, and weather station),
    then configure the site's flex context and dashboard.
    """
    account_id = account["id"]

    print("Creating site asset with PV and battery sensors")
    (
        site_asset,
        consumption_sensor,
        energy_costs_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
        site_peak_consumption_price_sensor,
        site_peak_production_price_sensor,
        total_energy_costs_sensor,
        daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor,
    ) = await create_site_asset(
        client=client,
        account_id=account_id,
        price_sensor_id=price_sensor["id"],
        site_asset_id=community_asset_id,
        site_name=site_names[site_index - 1],
    )
    print(f"Site asset ID: {site_asset['id']}")
    print(f"Consumption sensor ID: {consumption_sensor['id']}")
    print(f"Energy costs sensor ID: {energy_costs_sensor['id']}")
    print(f"Aggregate sensor ID: {aggregate_sensor['id']}")
    print(f"Max production sensor ID: {max_production_sensor['id']}")
    print(f"Max consumption sensor ID: {max_consumption_sensor['id']}")
    print(f"Self-consumption sensor ID: {self_consumption_sensor['id']}")
    print("Creating PV asset with production sensor")
    pv_asset, pv_production_sensor, pv_power_sensor = await create_pv_asset(
        client, account_id, site_asset["id"], pv_name=f"{pv_name} {site_index}"
    )
    print(f"PV asset ID: {pv_asset['id']}")
    print(f"PV production sensor ID: {pv_production_sensor['id']}")
    print(f"PV power sensor ID: {pv_power_sensor['id']}")
    print("Creating battery asset with power and SoC sensors")
    battery_asset, battery_power_sensor, battery_soc_sensor = (
        await create_battery_asset(
            client=client,
            account_id=account_id,
            site_asset_id=site_asset["id"],
            battery_name=f"{battery_name} {site_index}",
        )
    )
    print(f"Battery asset ID: {battery_asset['id']}")
    print(f"Battery power sensor ID: {battery_power_sensor['id']}")
    print(f"Battery SoC sensor ID: {battery_soc_sensor['id']}")

    # Create EVSE assets (2 connectors for one charge point)
    print("Creating EVSE assets with power and SoC sensors")
    (
        evse1_asset,
        evse1_power_sensor,
        evse1_soc_sensor,
        evse1_soc_min_sensor,
        evse1_soc_max_sensor,
    ) = await create_evse_asset(
        client=client,
        account_id=account_id,
        site_asset_id=site_asset["id"],
        evse_name=f"{evse1_name} {site_index}",
    )
    print(f"EVSE 1 asset ID: {evse1_asset['id']}")
    print(f"EVSE 1 power sensor ID: {evse1_power_sensor['id']}")
    print(f"EVSE 1 SoC sensor ID: {evse1_soc_sensor['id']}")

    (
        evse2_asset,
        evse2_power_sensor,
        evse2_soc_sensor,
        evse2_soc_min_sensor,
        evse2_soc_max_sensor,
    ) = await create_evse_asset(
        client=client,
        account_id=account_id,
        site_asset_id=site_asset["id"],
        evse_name=f"{evse2_name} {site_index}",
    )
    print(f"EVSE 2 asset ID: {evse2_asset['id']}")
    print(f"EVSE 2 power sensor ID: {evse2_power_sensor['id']}")
    print(f"EVSE 2 SoC sensor ID: {evse2_soc_sensor['id']}")

    # create heating asset
    print("Creating heating asset with temperature, power & energy sensors")
    (
        heating_asset,
        heating_power_sensor,
        heating_soc_usage_sensor,
        heating_soc_sensor,
        heating_min_soc_sensor,
        heating_max_soc_sensor,
        heating_COP,
    ) = await create_heating_asset(
        client=client,
        account_id=account_id,
        site_asset_id=site_asset["id"],
        heating_name=f"{heating_name} {site_index}",
        latitude=latitude,
        longitude=longitude,
    )
    print(f"Heating asset ID: {heating_asset['id']}")
    print(f"Heating power sensor ID: {heating_power_sensor['id']}")
    print(f"Heating SoC usage sensor ID: {heating_soc_usage_sensor['id']}")
    print(f"Heating SoC sensor ID: {heating_soc_sensor['id']}")
    print(f"Heating min SoC sensor ID: {heating_min_soc_sensor['id']}")
    print(f"Heating max SoC sensor ID: {heating_max_soc_sensor['id']}")
    print(f"Heating COP sensor ID: {heating_COP['id']}")

    print("Configuring site flex-context ...")
    await configure_site_flex_context(
        client=client,
        site_asset=site_asset,
        price_sensor=price_sensor,
        consumption_sensor=consumption_sensor,
        pv_production_sensor=pv_production_sensor,
        battery_power_sensor=battery_power_sensor,
        max_consumption_sensor=max_consumption_sensor,
        max_production_sensor=max_production_sensor,
        site_peak_consumption_price_sensor=site_peak_consumption_price_sensor,
        site_peak_production_price_sensor=site_peak_production_price_sensor,
        aggregate_sensor=aggregate_sensor,
    )
    print("Configuring site dashboard ...")
    await configure_site_dashboard(
        client=client,
        site_asset=site_asset,
        consumption_sensor=consumption_sensor,
        pv_production_sensor=pv_production_sensor,
        pv_power_sensor=pv_power_sensor,
        battery_power_sensor=battery_power_sensor,
        battery_soc_sensor=battery_soc_sensor,
        evse1_power_sensor=evse1_power_sensor,
        evse2_power_sensor=evse2_power_sensor,
        heating_power_sensor=heating_power_sensor,
        heating_soc_sensor=heating_soc_sensor,
        aggregate_sensor=aggregate_sensor,
        self_consumption_sensor=self_consumption_sensor,
        max_production_sensor=max_production_sensor,
        max_consumption_sensor=max_consumption_sensor,
        price_sensor=price_sensor,
        total_energy_costs_sensor=total_energy_costs_sensor,
        daily_total_energy_costs_sensor=daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor=daily_share_of_self_consumption_sensor,
    )


async def create_community_asset(
    client: FlexMeasuresClient,
    account: dict,
    community_name: str,
    site_names: list[str],
    community_asset: dict | None = None,
):
    """Create or complete the HEMS asset structure without replacing existing IDs."""
    # Get account id
    account_id = account["id"]
    print("Getting or creating price market asset and associated price sensor")
    price_sensor = await get_or_create_price_sensor(client=client)

    print("Getting or creating weather station and its sensors")
    weather_asset, irradiation_sensor, cloud_coverage_sensor = (
        await get_or_create_weather_station(client=client)
    )
    print(f"Weather station asset ID: {weather_asset['id']}")
    print(f"Irradiation sensor ID: {irradiation_sensor['id']}")
    print(f"Cloud coverage sensor ID: {cloud_coverage_sensor['id']}")
    print("Creating community asset...")
    if community_asset is None:
        community_asset = await get_or_create_asset(
            client,
            name=community_name,
            generic_asset_type_id=6,
            account_id=account_id,
        )
    elif community_asset.get("parent_asset_id") is not None:
        raise ValueError("The HEMS community asset must be a top-level asset.")

    # Create site power capacity sensor (15min resolution, kW)
    site_power_capacity_sensor = await get_or_create_sensor(
        client,
        name="site-power-capacity",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=community_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create site power sensor (15min resolution, kW)
    # this is used to store aggregate assets power measurements
    site_power_sensor = await get_or_create_sensor(  # noqa: F841
        client,
        name="power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=community_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create flex context with all required settings
    flex_context = {
        "site-power-capacity": {"sensor": site_power_capacity_sensor["id"]},
    }
    print(f"Community asset ID: {community_asset['id']}")

    # Update site asset with flex-context
    await client.update_asset(
        asset_id=community_asset["id"], updates={"flex_context": flex_context}
    )
    for i in range(len(site_names)):
        await create_sites_assets_and_sensors(
            client=client,
            account=account,
            community_asset_id=community_asset["id"],
            site_index=i + 1,
            site_names=site_names,
            price_sensor=price_sensor,
        )
    return community_asset
