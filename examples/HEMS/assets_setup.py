from const import (
    BATTERY_CONFIG,
    EV_CONFIG,
    HEATING_CONFIG,
    battery_name,
    building_name,
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


async def create_public_price_sensor(client: FlexMeasuresClient):
    """Create a public price sensor (1h, EUR/kWh).

    Returns the price sensor for use in flex-context.
    """
    print("Creating public price sensor...")
    # Get the client account id
    account = await client.get_account()
    account_id = account["id"]
    print(f"Account ID: {account_id}")
    # Create public market asset (no account_id for public assets)
    # Generic asset type 8 is typically used for market/price assets
    price_market_asset = await client.add_asset(
        name=price_market_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=8,  # Transmission zone  A grid regulated & balanced as a whole, usually a national grid.
        account_id=account_id,
    )

    # Create price sensor with 1-hour resolution
    price_sensor = await client.add_sensor(
        name="electricity-price",
        event_resolution="PT1H",
        unit="EUR/kWh",
        generic_asset_id=price_market_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created public price sensor with ID: {price_sensor['id']}")
    return price_sensor


async def create_weather_station(client: FlexMeasuresClient):
    """Create a public weather station with irradiation and cloud coverage sensors."""
    print("Creating weather station...")
    # Get the client account id
    account = await client.get_account()
    account_id = account["id"]
    print(f"Account ID: {account_id}")
    # Create public weather station asset
    # Generic asset type 7 (process) used for weather stations since no dedicated type exists
    weather_asset = await client.add_asset(
        name=weather_station_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=7,  # Process asset type (for weather station)
        account_id=account_id,  # Public account ID
    )

    # Create irradiation sensor (1H, W/m²)
    irradiation_sensor = await client.add_sensor(
        name="irradiation",
        event_resolution="PT1H",
        unit="W/m²",
        generic_asset_id=weather_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create cloud coverage sensor (1H, %)
    cloud_coverage_sensor = await client.add_sensor(
        name="cloud-coverage",
        event_resolution="PT1H",
        unit="%",
        generic_asset_id=weather_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created weather station with ID: {weather_asset['id']}")
    return weather_asset, irradiation_sensor, cloud_coverage_sensor


async def create_building_asset(
    client: FlexMeasuresClient, account_id: int, price_sensor_id: int
):
    """Create building asset with consumption and energy costs KPI sensors."""
    print("Creating building asset...")

    # Create building asset (generic_asset_type_id=6 for building)
    building_asset = await client.add_asset(
        name=building_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=6,  # Building asset type
        account_id=account_id,
    )

    # Create general consumption sensor (15min resolution, kW)
    consumption_sensor = await client.add_sensor(
        name="electricity-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create energy costs KPI sensor (1D resolution, EUR)
    energy_costs_sensor = await client.add_sensor(
        name="energy-costs-kpi",
        event_resolution="P1D",
        unit="EUR",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create aggregate power sensor for the building
    aggregate_sensor = await client.add_sensor(
        name="electricity-aggregate",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create max production capacity sensor for the building
    max_production_sensor = await client.add_sensor(
        name="max-production-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create max consumption capacity sensor for the building
    max_consumption_sensor = await client.add_sensor(
        name="max-consumption-capacity",
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create self-consumption sensor for the building
    self_consumption_sensor = await client.add_sensor(
        name="self-consumption",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create total energy costs sensor for the building
    total_energy_costs_sensor = await client.add_sensor(
        name="total-energy-costs",
        event_resolution="PT15M",
        unit="EUR",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create daily total energy costs sensor for the building
    daily_total_energy_costs_sensor = await client.add_sensor(
        name="daily-total-energy-costs",
        event_resolution="P1D",
        unit="EUR",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create daily share of self-consumption sensor for the building
    daily_share_of_self_consumption_sensor = await client.add_sensor(
        name="daily-share-of-self-consumption",
        event_resolution="P1D",
        unit="%",
        generic_asset_id=building_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created building asset with ID: {building_asset['id']}")
    return (
        building_asset,
        consumption_sensor,
        energy_costs_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
        total_energy_costs_sensor,
        daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor,
    )


async def create_pv_asset(
    client: FlexMeasuresClient, account_id: int, building_asset_id: int
):
    """Create PV asset as child of building with production sensor."""
    print("Creating PV asset...")

    # Create PV asset (generic_asset_type_id=1 for solar/PV)
    pv_asset = await client.add_asset(
        name=pv_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=1,  # Solar/PV asset type
        account_id=account_id,
        parent_asset_id=building_asset_id,  # Child of building
    )

    # Create production sensor (15min, kW)
    pv_production_sensor = await client.add_sensor(
        name="electricity-production",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=pv_asset["id"],
        timezone="Europe/Amsterdam",
    )

    print(f"Created PV asset with ID: {pv_asset['id']}")
    return pv_asset, pv_production_sensor


async def create_battery_asset(
    client: FlexMeasuresClient, account_id: int, building_asset_id: int
):
    """Create battery asset as child of building with power and SoC sensors + settings."""
    print("Creating battery asset...")

    # Create battery asset (generic_asset_type_id=5 for battery)
    battery_asset = await client.add_asset(
        name=battery_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=5,  # Battery asset type
        account_id=account_id,
        parent_asset_id=building_asset_id,  # Child of building
    )

    # Create power sensor (15min, kW)
    battery_power_sensor = await client.add_sensor(
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=battery_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (0min, kWh)
    battery_soc_sensor = await client.add_sensor(
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
    client: FlexMeasuresClient, account_id: int, building_asset_id: int, evse_name: str
):
    """Create EVSE asset as child of building with power and SoC sensors + settings."""
    print(f"Creating EVSE asset: {evse_name}...")

    # Create EVSE asset - using generic type 4 for one-way EVSE based on the codebase search
    # Note: We'll use a basic asset type since one-way_evse might not be available by default
    evse_asset = await client.add_asset(
        name=evse_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=4,  # Using a generic type, could be EVSE specific if available
        account_id=account_id,
        parent_asset_id=building_asset_id,  # Child of building
    )

    # Create power sensor (15min, kW)
    evse_power_sensor = await client.add_sensor(
        name="electricity-power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Create state-of-charge sensor (instantaneous, kWh)
    evse_soc_sensor = await client.add_sensor(
        name="state-of-charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create soc-min sensor (15min, kWh)
    evse_soc_min_sensor = await client.add_sensor(
        name="soc-min",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=evse_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # Create soc-max sensor (15min, kWh)
    evse_soc_max_sensor = await client.add_sensor(
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
    building_asset_id: int,
    heating_name: str,
    latitude: float,
    longitude: float,
):
    """Create heating asset (child of building) with temperature, power & energy sensors + settings."""
    print(f"Creating heating asset: {heating_name}...")

    # Create heating asset (generic asset type id = 5 if heating not defined in DB)
    heating_asset = await client.add_asset(
        name=heating_name,
        latitude=latitude,
        longitude=longitude,
        generic_asset_type_id=5,  # Using battery type as placeholder for heating asset
        account_id=account_id,
        parent_asset_id=building_asset_id,
    )

    # Power sensors (15min, kW)
    heating_power_sensor = await client.add_sensor(
        name="power",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # Soc usage sensor (15min, kW)
    heating_soc_usage_sensor = await client.add_sensor(
        name="soc-usage",
        event_resolution="PT15M",
        unit="kW",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
        attributes=dict(consumption_is_positive=True),
    )

    # State of Charge sensors (15min, kWh)
    heating_soc_sensor = await client.add_sensor(
        name="state of charge",
        event_resolution="PT0M",
        unit="kWh",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )
    heating_min_soc_sensor = await client.add_sensor(
        name="min SoC",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )
    heating_max_soc_sensor = await client.add_sensor(
        name="max SoC",
        event_resolution="PT15M",
        unit="kWh",
        generic_asset_id=heating_asset["id"],
        timezone="Europe/Amsterdam",
    )

    # COP (Coefficient of Performance)
    heating_COP = await client.add_sensor(
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


async def configure_building_flex_context(
    client: FlexMeasuresClient,
    building_asset,
    price_sensor,
    consumption_sensor,
    pv_production_sensor,
    battery_power_sensor,
    max_consumption_sensor,
    max_production_sensor,
    aggregate_sensor,
):
    """Configure building asset with comprehensive flex-context."""
    print("Configuring building flex-context...")

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
        "site-peak-consumption-price": "26 EUR/MW",
        "site-peak-consumption": "0 kW",
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
    }

    # Update building asset with flex-context
    await client.update_asset(
        asset_id=building_asset["id"], updates={"flex_context": flex_context}
    )

    print("Building flex-context configured successfully")


async def configure_building_dashboard(
    client: FlexMeasuresClient,
    building_asset,
    consumption_sensor,
    pv_production_sensor,
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
    """Configure sensors_to_show for building asset graphs."""
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
            "sensors": [self_consumption_sensor["id"], pv_production_sensor["id"]],
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

    # Update building asset with sensors_to_show
    await client.update_asset(
        asset_id=building_asset["id"],
        updates={
            "sensors_to_show": sensors_to_show,
            "sensors_to_show_as_kpis": sensors_to_show_as_kpis,
        },
    )

    print("Sensors to show configured successfully")


async def create_building_assets_and_sensors(client: FlexMeasuresClient, account: dict):
    """
    Create a building asset with its associated sensors and linked assets (PV, battery, EVSEs, and weather station),
    then configure the building's flex context and dashboard.
    """
    account_id = account["id"]
    print("Creating price market asset and associated price sensor")
    price_sensor = await create_public_price_sensor(client)
    print("Creating building asset with PV and battery sensors")
    (
        building_asset,
        consumption_sensor,
        energy_costs_sensor,
        aggregate_sensor,
        self_consumption_sensor,
        max_production_sensor,
        max_consumption_sensor,
        total_energy_costs_sensor,
        daily_total_energy_costs_sensor,
        daily_share_of_self_consumption_sensor,
    ) = await create_building_asset(client, account_id, price_sensor["id"])
    print(f"Building asset ID: {building_asset['id']}")
    print(f"Consumption sensor ID: {consumption_sensor['id']}")
    print(f"Energy costs sensor ID: {energy_costs_sensor['id']}")
    print(f"Aggregate sensor ID: {aggregate_sensor['id']}")
    print(f"Max production sensor ID: {max_production_sensor['id']}")
    print(f"Max consumption sensor ID: {max_consumption_sensor['id']}")
    print(f"Self-consumption sensor ID: {self_consumption_sensor['id']}")
    print("Creating PV asset with production sensor")
    pv_asset, pv_production_sensor = await create_pv_asset(
        client, account_id, building_asset["id"]
    )
    print(f"PV asset ID: {pv_asset['id']}")
    print(f"PV production sensor ID: {pv_production_sensor['id']}")
    print("Creating battery asset with power and SoC sensors")
    battery_asset, battery_power_sensor, battery_soc_sensor = (
        await create_battery_asset(client, account_id, building_asset["id"])
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
    ) = await create_evse_asset(client, account_id, building_asset["id"], evse1_name)
    print(f"EVSE 1 asset ID: {evse1_asset['id']}")
    print(f"EVSE 1 power sensor ID: {evse1_power_sensor['id']}")
    print(f"EVSE 1 SoC sensor ID: {evse1_soc_sensor['id']}")

    (
        evse2_asset,
        evse2_power_sensor,
        evse2_soc_sensor,
        evse2_soc_min_sensor,
        evse2_soc_max_sensor,
    ) = await create_evse_asset(client, account_id, building_asset["id"], evse2_name)
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
        client,
        account_id,
        building_asset["id"],
        heating_name,
        latitude,
        longitude,
    )
    print(f"Heating asset ID: {heating_asset['id']}")
    print(f"Heating power sensor ID: {heating_power_sensor['id']}")
    print(f"Heating SoC usage sensor ID: {heating_soc_usage_sensor['id']}")
    print(f"Heating SoC sensor ID: {heating_soc_sensor['id']}")
    print(f"Heating min SoC sensor ID: {heating_min_soc_sensor['id']}")
    print(f"Heating max SoC sensor ID: {heating_max_soc_sensor['id']}")
    print(f"Heating COP sensor ID: {heating_COP['id']}")

    print("Creating weather station with irradiation and cloud coverage sensors")
    weather_asset, irradiation_sensor, cloud_coverage_sensor = (
        await create_weather_station(client)
    )
    print(f"Weather station asset ID: {weather_asset['id']}")
    print(f"Irradiation sensor ID: {irradiation_sensor['id']}")
    print(f"Cloud coverage sensor ID: {cloud_coverage_sensor['id']}")
    print("Configuring building flex-context ...")
    await configure_building_flex_context(
        client=client,
        building_asset=building_asset,
        price_sensor=price_sensor,
        consumption_sensor=consumption_sensor,
        pv_production_sensor=pv_production_sensor,
        battery_power_sensor=battery_power_sensor,
        max_consumption_sensor=max_consumption_sensor,
        max_production_sensor=max_production_sensor,
        aggregate_sensor=aggregate_sensor,
    )
    print("Configuring building dashboard ...")
    await configure_building_dashboard(
        client=client,
        building_asset=building_asset,
        consumption_sensor=consumption_sensor,
        pv_production_sensor=pv_production_sensor,
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
