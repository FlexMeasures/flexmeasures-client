import random

"""
Settings for the HEMS example script.
"""
# Connection details - UPDATE THESE FOR YOUR SETUP
usr = "admin@admin.com"  # Admin user email
pwd = "admin"  # Admin password
host = "127.0.0.1:5000"  # FlexMeasures host

# Asset and sensor names
building_name = "My Home"
pv_name = "Rooftop PV"
battery_name = "Home Battery"
evse1_name = "EV Connector 1"
evse2_name = "EV Connector 2"
heating_name = "Heat Pump"
weather_station_name = "Local Weather Station"
price_market_name = "Energy Market"

# Location coordinates (Amsterdam as example)
latitude = 52.3676
longitude = 4.9041

# Data configuration
TUTORIAL_START_DATE = "2026-01-01T00:00:00+01:00"
FORECASTING_START = "2026-01-15T00:00:00+01:00"
SCHEDULING_START = "2026-01-15T00:00:00+01:00"
SCHEDULING_END = "2026-01-16T00:00:00+01:00"
SIMULATION_STEP_HOURS = 4
FORECAST_HORIZON_HOURS = 24

random.seed(42)

# Configuration constants
EV_CONFIG = {
    "default_capacity_kwh": 60.0,
    "default_power_capacity_kw": 11.0,
    "min_soc_percent": 0.20,  # 20% minimum SoC
    "roundtrip_efficiency": 0.85,  # 85% efficiency
    "random_trip_probability": 0.10,  # 10% chance per step
    "random_trip_consumption_range": (0.10, 0.20),  # 10-20% consumption
    "driving_consumption_kwh_per_hour": 7.5,  # 15 kWh/100km at 50 km/h average
}

BATTERY_CONFIG = {
    "capacity_kwh": 16.7,
    "power_capacity_kw": 5.0,
    "min_soc_percent": 0.15,  # 15% minimum
    "max_soc_percent": 0.90,  # 90% maximum
    "soc_at_start_percent": 0.50,  # 50% starting
    "roundtrip_efficiency": 0.85,
}

# EV scheduling patterns - 7-day weekly cycle
# Each entry represents: (needs_charging_overnight, departure_time, return_time, target_soc_percent)
# Index 0 = Monday, 1 = Tuesday, ..., 6 = Sunday
EV_WEEKLY_PATTERNS = [
    (False, None, None, 40),  # Monday - Free day, keep at moderate charge
    (True, "07:00", "13:00", 80),  # Tuesday - Work day, need 80% by 7am
    (True, "08:00", "13:00", 80),  # Wednesday - Work day, need 80% by 8am
    (True, "07:00", "13:00", 80),  # Thursday - Work day, need 80% by 7am
    (False, None, None, 60),  # Friday - Free day, charge to 60% for weekend
    (False, None, None, 40),  # Saturday - Free day
    (False, None, None, 40),  # Sunday - Free day
]

HEATING_CONFIG = {
    "capacity_kwh": 15.0,  # energy
    "power_capacity_kw": 5.0,  # power
    "min_soc_percent": 0,  # 0% minimum
    "max_soc_percent": 1,  # 100% maximum
    "soc_at_start_percent": 0.20,  # 20% starting
    "charging_efficiency": 3.0,  # 300% charging efficiency (heat pumps)
    "storage_efficiency": 0.993,  # 99.3% storage efficiency (heat pumps)
}
