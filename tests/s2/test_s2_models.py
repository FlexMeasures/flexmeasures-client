from flexmeasures_client.s2.utils import get_unique_id
from flexmeasures_client.s2.wrapper import S2Wrapper


def test_simple_model():
    wrapped_message = {
        "message": {
            "message_id": get_unique_id(),
            "resource_id": get_unique_id(),
            "roles": [{"role": "ENERGY_STORAGE", "commodity": "ELECTRICITY"}],
            "instruction_processing_delay": 1.0,
            "available_control_types": ["FILL_RATE_BASED_CONTROL", "NO_SELECTION"],
            "provides_forecast": True,
            "provides_power_measurement_types": ["ELECTRIC.POWER.3_PHASE_SYMMETRIC"],
            "message_type": "ResourceManagerDetails",
        },
        "metadata": {"dt": "2023-01-01T00:00:00"},
    }

    S2Wrapper.validate(wrapped_message)

    wrapped_message_2 = {
        "message": {
            "message_id": get_unique_id(),
            "message_type": "Handshake",
            "role": "CEM",
        },
        "metadata": {"dt": "2024-01-01T00:00:00"},
    }

    S2Wrapper.validate(wrapped_message_2)
