# flake8: noqa

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Extra, Field

from flexmeasures_client.s2.python_s2_protocol.common.schemas import (
    ID,
    Commodity,
    Duration,
    NumberRange,
    PowerRange,
    Timer,
    Transition,
)


class FRBCFillLevelTargetProfileElement(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    duration: Duration = Field(..., description="The duration of the element.")
    fill_level_range: NumberRange = Field(
        ...,
        description="The target range in which the fill_level must be for the time period during which the element is active. The start of the range must be smaller or equal to the end of the range. The CEM must take best-effort actions to proactively achieve this target.",
    )


class FRBCLeakageBehaviourElement(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    fill_level_range: NumberRange = Field(
        ...,
        description="The fill level range for which this FRBC.LeakageBehaviourElement applies. The start of the range must be less than the end of the range.",
    )
    leakage_rate: float = Field(
        ...,
        description="Indicates how fast the momentary fill level will decrease per second due to leakage within the given range of the fill level.",
    )


class FRBCOperationModeElement(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    fill_level_range: NumberRange = Field(
        ...,
        description="The range of the fill level for which this FRBC.OperationModeElement applies. The start of the NumberRange shall be smaller than the end of the NumberRange.",
    )
    fill_rate: NumberRange = Field(
        ...,
        description="Indicates the change in fill_level per second. The lower_boundary of the NumberRange is associated with an operation_mode_factor of 0, the upper_boundary is associated with an operation_mode_factor of 1. ",
    )
    power_ranges: List[PowerRange] = Field(
        ...,
        description="The power produced or consumed by this operation mode. The start of each PowerRange is associated with an operation_mode_factor of 0, the end is associated with an operation_mode_factor of 1. In the array there must be at least one PowerRange, and at most one PowerRange per CommodityQuantity.",
        max_items=10,
        min_items=1,
    )
    running_costs: Optional[NumberRange] = Field(
        None,
        description="Additional costs per second (e.g. wear, services) associated with this operation mode in the currency defined by the ResourceManagerDetails, excluding the commodity cost. The range is expressing uncertainty and is not linked to the operation_mode_factor.",
    )


class FRBCOperationMode(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    id: ID = Field(
        ...,
        description="ID of the FRBC.OperationMode. Must be unique in the scope of the FRBC.ActuatorDescription in which it is used.",
    )
    diagnostic_label: Optional[str] = Field(
        None,
        description="Human readable name/description of the FRBC.OperationMode. This element is only intended for diagnostic purposes and not for HMI applications.",
    )
    elements: List[FRBCOperationModeElement] = Field(
        ...,
        description="List of FRBC.OperationModeElements, which describe the properties of this FRBC.OperationMode depending on the fill_level. The fill_level_ranges of the items in the Array must be contiguous.",
        max_items=100,
        min_items=1,
    )
    abnormal_condition_only: bool = Field(
        ...,
        description="Indicates if this FRBC.OperationMode may only be used during an abnormal condition",
    )


class FRBCActuatorDescription(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    id: ID = Field(
        ...,
        description="ID of the Actuator. Must be unique in the scope of the Resource Manager, for at least the duration of the session between Resource Manager and CEM.",
    )
    diagnostic_label: Optional[str] = Field(
        None,
        description="Human readable name/description for the actuator. This element is only intended for diagnostic purposes and not for HMI applications.",
    )
    supported_commodities: List[Commodity] = Field(
        ..., description="List of all supported Commodities.", max_items=4, min_items=1
    )
    operation_modes: List[FRBCOperationMode] = Field(
        ...,
        description="Provided FRBC.OperationModes associated with this actuator",
        max_items=100,
        min_items=1,
    )
    transitions: List[Transition] = Field(
        ...,
        description="Possible transitions between FRBC.OperationModes associated with this actuator.",
        max_items=1000,
        min_items=0,
    )
    timers: List[Timer] = Field(
        ...,
        description="List of Timers associated with this actuator",
        max_items=1000,
        min_items=0,
    )


class FRBCStorageDescription(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    diagnostic_label: Optional[str] = Field(
        None,
        description="Human readable name/description of the storage (e.g. hot water buffer or battery). This element is only intended for diagnostic purposes and not for HMI applications.",
    )
    fill_level_label: Optional[str] = Field(
        None,
        description="Human readable description of the (physical) units associated with the fill_level (e.g. degrees Celsius or percentage state of charge). This element is only intended for diagnostic purposes and not for HMI applications.",
    )
    provides_leakage_behaviour: bool = Field(
        ...,
        description="Indicates whether the Storage could provide details of power leakage behaviour through the FRBC.LeakageBehaviour.",
    )
    provides_fill_level_target_profile: bool = Field(
        ...,
        description="Indicates whether the Storage could provide a target profile for the fill level through the FRBC.FillLevelTargetProfile.",
    )
    provides_usage_forecast: bool = Field(
        ...,
        description="Indicates whether the Storage could provide a UsageForecast through the FRBC.UsageForecast.",
    )
    fill_level_range: NumberRange = Field(
        ...,
        description="The range in which the fill_level should remain. It is expected of the CEM to keep the fill_level within this range. When the fill_level is not within this range, the Resource Manager can ignore instructions from the CEM (except during abnormal conditions). ",
    )


class FRBCUsageForecastElement(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    duration: Duration = Field(
        ..., description="Indicator for how long the given usage_rate is valid."
    )
    usage_rate_upper_limit: Optional[float] = Field(
        None,
        description="The upper limit of the range with a 100\xa0% probability that the usage rate is within that range.",
    )
    usage_rate_upper_95PPR: Optional[float] = Field(
        None,
        description="The upper limit of the range with a 95\xa0% probability that the usage rate is within that range. ",
    )
    usage_rate_upper_68PPR: Optional[float] = Field(
        None,
        description="The upper limit of the range with a 68\xa0% probability that the usage rate is within that range",
    )
    usage_rate_expected: float = Field(
        ...,
        description="The most likely value for the usage rate; the expected increase or decrease of the fill_level per second",
    )
    usage_rate_lower_68PPR: Optional[float] = Field(
        None,
        description="The lower limit of the range with a 68\xa0% probability that the usage rate is within that range",
    )
    usage_rate_lower_95PPR: Optional[float] = Field(
        None,
        description="The lower limit of the range with a 95\xa0% probability that the usage rate is within that range",
    )
    usage_rate_lower_limit: Optional[float] = Field(
        None,
        description="The lower limit of the range with a 100\xa0% probability that the usage rate is within that range",
    )
