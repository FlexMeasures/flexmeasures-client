from zoneinfo import ZoneInfo

from flexmeasures_client.s2 import Handler
from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple


def test_handler_uses_zoneinfo_timezone():
    handler = Handler(timezone="Europe/Amsterdam")

    assert handler.now().tzinfo == ZoneInfo("Europe/Amsterdam")


def test_frbc_simple_now_uses_zoneinfo_timezone():
    frbc = FRBCSimple(
        power_sensor_id=1,
        soc_sensor_id=2,
        rm_discharge_sensor_id=3,
        price_sensor_id=4,
        timezone="Europe/Amsterdam",
    )

    assert frbc.now().tzinfo == ZoneInfo("Europe/Amsterdam")
