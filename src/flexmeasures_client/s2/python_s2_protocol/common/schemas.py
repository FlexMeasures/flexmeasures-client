# flake8: noqa

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Extra, Field, conint, constr


class ID(BaseModel):
    class Config:
        validate_assignment = True

    __root__: constr(regex=r"[a-zA-Z0-9\-_:]{2,64}") = Field(
        ..., description="An identifier expressed as a UUID", title="ID"
    )


class Duration(BaseModel):
    class Config:
        validate_assignment = True

    __root__: conint(ge=0) = Field(
        ..., description="Duration in milliseconds", title="Duration"
    )


class NumberRange(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    start_of_range: float = Field(
        ..., description="Number that defines the start of the range"
    )
    end_of_range: float = Field(
        ..., description="Number that defines the end of the range"
    )


class Commodity(Enum):
    GAS = "GAS"
    HEAT = "HEAT"
    ELECTRICITY = "ELECTRICITY"
    OIL = "OIL"


class CommodityQuantity(Enum):
    ELECTRIC_POWER_L1 = "ELECTRIC.POWER.L1"
    ELECTRIC_POWER_L2 = "ELECTRIC.POWER.L2"
    ELECTRIC_POWER_L3 = "ELECTRIC.POWER.L3"
    ELECTRIC_POWER_3_PHASE_SYMMETRIC = "ELECTRIC.POWER.3_PHASE_SYMMETRIC"
    NATURAL_GAS_FLOW_RATE = "NATURAL_GAS.FLOW_RATE"
    HYDROGEN_FLOW_RATE = "HYDROGEN.FLOW_RATE"
    HEAT_TEMPERATURE = "HEAT.TEMPERATURE"
    HEAT_FLOW_RATE = "HEAT.FLOW_RATE"
    HEAT_THERMAL_POWER = "HEAT.THERMAL_POWER"
    OIL_FLOW_RATE = "OIL.FLOW_RATE"


class Timer(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    id: ID = Field(
        ...,
        description="ID of the Timer. Must be unique in the scope of the OMBC.SystemDescription, FRBC.ActuatorDescription or DDBC.ActuatorDescription in which it is used.",
    )
    diagnostic_label: Optional[str] = Field(
        None,
        description="Human readable name/description of the Timer. This element is only intended for diagnostic purposes and not for HMI applications.",
    )
    duration: Duration = Field(
        ...,
        description="The time it takes for the Timer to finish after it has been started",
    )


class Transition(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    id: ID = Field(
        ...,
        description="ID of the Transition. Must be unique in the scope of the OMBC.SystemDescription, FRBC.ActuatorDescription or DDBC.ActuatorDescription in which it is used.",
    )
    from_: ID = Field(
        ...,
        alias="from",
        description="ID of the OperationMode (exact type differs per ControlType) that should be switched from.",
    )
    to: ID = Field(
        ...,
        description="ID of the OperationMode (exact type differs per ControlType) that will be switched to.",
    )
    start_timers: List[ID] = Field(
        ...,
        description="List of IDs of Timers that will be (re)started when this transition is initiated",
        max_items=1000,
        min_items=0,
    )
    blocking_timers: List[ID] = Field(
        ...,
        description="List of IDs of Timers that block this Transition from initiating while at least one of these Timers is not yet finished",
        max_items=1000,
        min_items=0,
    )
    transition_costs: Optional[float] = Field(
        None,
        description="Absolute costs for going through this Transition in the currency as described in the ResourceManagerDetails.",
    )
    transition_duration: Optional[Duration] = Field(
        None,
        description="Indicates the time between the initiation of this Transition, and the time at which the device behaves according to the Operation Mode which is defined in the ‘to’ data element. When no value is provided it is assumed the transition duration is negligible.",
    )
    abnormal_condition_only: bool = Field(
        ...,
        description="Indicates if this Transition may only be used during an abnormal condition (see Clause )",
    )


class PowerRange(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    start_of_range: float = Field(
        ..., description="Power value that defines the start of the range."
    )
    end_of_range: float = Field(
        ..., description="Power value that defines the end of the range."
    )
    commodity_quantity: CommodityQuantity = Field(
        ..., description="The power quantity the values refer to"
    )


class EnergyManagementRole(Enum):
    CEM = "CEM"
    RM = "RM"


class InstructionStatus(Enum):
    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    ABORTED = "ABORTED"


class PowerForecastValue(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    value_upper_limit: Optional[float] = Field(
        None,
        description="The upper boundary of the range with 100\xa0% certainty the power value is in it",
    )
    value_upper_95PPR: Optional[float] = Field(
        None,
        description="The upper boundary of the range with 95\xa0% certainty the power value is in it",
    )
    value_upper_68PPR: Optional[float] = Field(
        None,
        description="The upper boundary of the range with 68\xa0% certainty the power value is in it",
    )
    value_expected: float = Field(..., description="The expected power value.")
    value_lower_68PPR: Optional[float] = Field(
        None,
        description="The lower boundary of the range with 68\xa0% certainty the power value is in it",
    )
    value_lower_95PPR: Optional[float] = Field(
        None,
        description="The lower boundary of the range with 95\xa0% certainty the power value is in it",
    )
    value_lower_limit: Optional[float] = Field(
        None,
        description="The lower boundary of the range with 100\xa0% certainty the power value is in it",
    )
    commodity_quantity: CommodityQuantity = Field(
        ..., description="The power quantity the value refers to"
    )


class PowerForecastElement(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    duration: Duration = Field(..., description="Duration of the PowerForecastElement")
    power_values: List[PowerForecastValue] = Field(
        ...,
        description="The values of power that are expected for the given period of time. There shall be at least one PowerForecastValue, and at most one PowerForecastValue per CommodityQuantity.",
        max_items=10,
        min_items=1,
    )


class PowerValue(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    commodity_quantity: CommodityQuantity = Field(
        ..., description="The power quantity the value refers to"
    )
    value: float = Field(
        ...,
        description="Power value expressed in the unit associated with the CommodityQuantity",
    )


class ReceptionStatusValues(Enum):
    INVALID_DATA = "INVALID_DATA"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    INVALID_CONTENT = "INVALID_CONTENT"
    TEMPORARY_ERROR = "TEMPORARY_ERROR"
    PERMANENT_ERROR = "PERMANENT_ERROR"
    OK = "OK"


class ControlType(Enum):
    POWER_ENVELOPE_BASED_CONTROL = "POWER_ENVELOPE_BASED_CONTROL"
    POWER_PROFILE_BASED_CONTROL = "POWER_PROFILE_BASED_CONTROL"
    OPERATION_MODE_BASED_CONTROL = "OPERATION_MODE_BASED_CONTROL"
    FILL_RATE_BASED_CONTROL = "FILL_RATE_BASED_CONTROL"
    DEMAND_DRIVEN_BASED_CONTROL = "DEMAND_DRIVEN_BASED_CONTROL"
    NOT_CONTROLABLE = "NOT_CONTROLABLE"
    NO_SELECTION = "NO_SELECTION"


class Currency(Enum):
    AED = "AED"
    ANG = "ANG"
    AUD = "AUD"
    CHE = "CHE"
    CHF = "CHF"
    CHW = "CHW"
    EUR = "EUR"
    GBP = "GBP"
    LBP = "LBP"
    LKR = "LKR"
    LRD = "LRD"
    LSL = "LSL"
    LYD = "LYD"
    MAD = "MAD"
    MDL = "MDL"
    MGA = "MGA"
    MKD = "MKD"
    MMK = "MMK"
    MNT = "MNT"
    MOP = "MOP"
    MRO = "MRO"
    MUR = "MUR"
    MVR = "MVR"
    MWK = "MWK"
    MXN = "MXN"
    MXV = "MXV"
    MYR = "MYR"
    MZN = "MZN"
    NAD = "NAD"
    NGN = "NGN"
    NIO = "NIO"
    NOK = "NOK"
    NPR = "NPR"
    NZD = "NZD"
    OMR = "OMR"
    PAB = "PAB"
    PEN = "PEN"
    PGK = "PGK"
    PHP = "PHP"
    PKR = "PKR"
    PLN = "PLN"
    PYG = "PYG"
    QAR = "QAR"
    RON = "RON"
    RSD = "RSD"
    RUB = "RUB"
    RWF = "RWF"
    SAR = "SAR"
    SBD = "SBD"
    SCR = "SCR"
    SDG = "SDG"
    SEK = "SEK"
    SGD = "SGD"
    SHP = "SHP"
    SLL = "SLL"
    SOS = "SOS"
    SRD = "SRD"
    SSP = "SSP"
    STD = "STD"
    SYP = "SYP"
    SZL = "SZL"
    THB = "THB"
    TJS = "TJS"
    TMT = "TMT"
    TND = "TND"
    TOP = "TOP"
    TRY = "TRY"
    TTD = "TTD"
    TWD = "TWD"
    TZS = "TZS"
    UAH = "UAH"
    UGX = "UGX"
    USD = "USD"
    USN = "USN"
    UYI = "UYI"
    UYU = "UYU"
    UZS = "UZS"
    VEF = "VEF"
    VND = "VND"
    VUV = "VUV"
    WST = "WST"
    XAG = "XAG"
    XAU = "XAU"
    XBA = "XBA"
    XBB = "XBB"
    XBC = "XBC"
    XBD = "XBD"
    XCD = "XCD"
    XOF = "XOF"
    XPD = "XPD"
    XPF = "XPF"
    XPT = "XPT"
    XSU = "XSU"
    XTS = "XTS"
    XUA = "XUA"
    XXX = "XXX"
    YER = "YER"
    ZAR = "ZAR"
    ZMW = "ZMW"
    ZWL = "ZWL"


class RoleType(Enum):
    ENERGY_PRODUCER = "ENERGY_PRODUCER"
    ENERGY_CONSUMER = "ENERGY_CONSUMER"
    ENERGY_STORAGE = "ENERGY_STORAGE"


class Role(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    role: RoleType = Field(
        ..., description="Role type of the Resource Manager for the given commodity"
    )
    commodity: Commodity = Field(..., description="Commodity the role refers to.")


class RevokableObjects(Enum):
    PEBC_PowerConstraints = "PEBC.PowerConstraints"
    PEBC_EnergyConstraint = "PEBC.EnergyConstraint"
    PEBC_Instruction = "PEBC.Instruction"
    PPBC_PowerProfileDefinition = "PPBC.PowerProfileDefinition"
    PPBC_ScheduleInstruction = "PPBC.ScheduleInstruction"
    PPBC_StartInterruptionInstruction = "PPBC.StartInterruptionInstruction"
    PPBC_EndInterruptionInstruction = "PPBC.EndInterruptionInstruction"
    OMBC_SystemDescription = "OMBC.SystemDescription"
    OMBC_Instruction = "OMBC.Instruction"
    FRBC_SystemDescription = "FRBC.SystemDescription"
    FRBC_Instruction = "FRBC.Instruction"
    DDBC_SystemDescription = "DDBC.SystemDescription"
    DDBC_Instruction = "DDBC.Instruction"


class SessionRequestType(Enum):
    RECONNECT = "RECONNECT"
    TERMINATE = "TERMINATE"
