from typing import Any


def create_dynamic_storage_flex_model(
    current_soc: float,
    constraints: dict = None,
) -> dict[str, Any]:
    """
    Create a dynamic flex model for scheduling storage devices.

    This function builds only the *ad hoc* part of the flex model, temporary values
    defined at scheduling time for the current context, such as current SoC, SoC minima,
    and SoC usage. Permanent properties of the device (e.g. capacities or efficiencies)
    are defined on the asset's flex_model field.
    """

    flex_model = {
        "soc-at-start": current_soc,
    }

    # Add dynamic constraints if provided
    if constraints:
        if constraints.get("soc_minima"):
            flex_model["soc-minima"] = constraints["soc_minima"]
        if constraints.get("soc_usage"):
            flex_model["soc-usage"] = constraints["soc_usage"]
        if constraints.get("consumption_capacity"):
            flex_model["consumption-capacity"] = constraints["consumption_capacity"]

    return flex_model
