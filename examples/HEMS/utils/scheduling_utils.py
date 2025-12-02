from typing import Any

from const import EV_CONFIG


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
            # todo: here we remove the last soc_minima constraint and set it up as a soc_usage component instead
            # this is a workaround; we should define the SoC drop during the trip as a soc_usage component straightaway
            soc_usage = constraints["soc_minima"].pop(-1)
            flex_model["soc-minima"] = constraints["soc_minima"]
            soc_usage["value"] = f'{EV_CONFIG["driving_consumption_kwh_per_hour"]} kW'
            # add soc_usage as a component (soc-usage supports a list of usage components)
            flex_model["soc-usage"] = [[soc_usage]]
        if constraints.get("consumption_capacity"):
            flex_model["consumption-capacity"] = constraints["consumption_capacity"]

    return flex_model
