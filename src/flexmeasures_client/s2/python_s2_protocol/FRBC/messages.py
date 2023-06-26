# flake8: noqa

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Extra, Field, constr

from flexmeasures_client.s2.python_s2_protocol.common.schemas import (
    ID,
    Duration,
    NumberRange,
)
from flexmeasures_client.s2.python_s2_protocol.FRBC.schemas import (
    FRBCActuatorDescription,
    FRBCFillLevelTargetProfileElement,
    FRBCLeakageBehaviourElement,
    FRBCStorageDescription,
    FRBCUsageForecastElement,
)


class FRBCActuatorStatus(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.ActuatorStatus", const=True)
    message_id: ID = Field(..., description="ID of this message")
    actuator_id: ID = Field(
        ..., description="ID of the actuator this messages refers to"
    )
    active_operation_mode_id: ID = Field(
        ..., description="ID of the FRBC.OperationMode that is presently active."
    )
    operation_mode_factor: float = Field(
        ...,
        description="The number indicates the factor with which the FRBC.OperationMode is configured. The factor should be greater than or equal than 0 and less or equal to 1.",
    )
    previous_operation_mode_id: Optional[ID] = Field(
        None,
        description="ID of the FRBC.OperationMode that was active before the present one. This value shall always be provided, unless the active FRBC.OperationMode is the first FRBC.OperationMode the Resource Manager is aware of.",
    )
    transition_timestamp: Optional[datetime] = Field(
        None,
        description="Time at which the transition from the previous FRBC.OperationMode to the active FRBC.OperationMode was initiated. This value shall always be provided, unless the active FRBC.OperationMode is the first FRBC.OperationMode the Resource Manager is aware of.",
    )


class FRBCFillLevelTargetProfile(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.FillLevelTargetProfile", const=True)
    message_id: ID = Field(..., description="ID of this message")
    start_time: datetime = Field(
        ..., description="Time at which the FRBC.FillLevelTargetProfile starts."
    )
    elements: List[FRBCFillLevelTargetProfileElement] = Field(
        ...,
        description="List of different fill levels that have to be targeted within a given duration. There shall be at least one element. Elements must be placed in chronological order.",
        max_items=288,
        min_items=1,
    )


class FRBCInstruction(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.Instruction", const=True)
    message_id: ID = Field(..., description="ID of this message")
    id: ID = Field(
        ...,
        description="ID of the instruction. Must be unique in the scope of the Resource Manager, for at least the duration of the session between Resource Manager and CEM.",
    )
    actuator_id: ID = Field(
        ..., description="ID of the actuator this instruction belongs to."
    )
    operation_mode: ID = Field(
        ..., description="ID of the FRBC.OperationMode that should be activated."
    )
    operation_mode_factor: float = Field(
        ...,
        description="The number indicates the factor with which the FRBC.OperationMode should be configured. The factor should be greater than or equal to 0 and less or equal to 1.",
    )
    execution_time: datetime = Field(
        ..., description="Time when instruction should be executed."
    )
    abnormal_condition: bool = Field(
        ...,
        description="Indicates if this is an instruction during an abnormal condition.",
    )


class FRBCLeakageBehaviour(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.LeakageBehaviour", const=True)
    message_id: ID = Field(..., description="ID of this message")
    valid_from: datetime = Field(
        ...,
        description="Moment this FRBC.LeakageBehaviour starts to be valid. If the FRBC.LeakageBehaviour is immediately valid, the DateTimeStamp should be now or in the past.",
    )
    elements: List[FRBCLeakageBehaviourElement] = Field(
        ...,
        description="List of elements that model the leakage behaviour of the buffer. The fill_level_ranges of the elements must be contiguous.",
        max_items=288,
        min_items=1,
    )


class FRBCStorageStatus(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.StorageStatus", const=True)
    message_id: ID = Field(..., description="ID of this message")
    present_fill_level: float = Field(
        ..., description="Present fill level of the Storage"
    )


class FRBCSystemDescription(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.SystemDescription", const=True)
    message_id: ID = Field(..., description="ID of this message")
    valid_from: datetime = Field(
        ...,
        description="Moment this FRBC.SystemDescription starts to be valid. If the system description is immediately valid, the DateTimeStamp should be now or in the past.",
    )
    actuators: List[FRBCActuatorDescription] = Field(
        ..., description="Details of all Actuators.", max_items=10, min_items=1
    )
    storage: FRBCStorageDescription = Field(..., description="Details of the storage.")


class FRBCTimerStatus(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.TimerStatus", const=True)
    message_id: ID = Field(..., description="ID of this message")
    timer_id: ID = Field(..., description="The ID of the timer this message refers to")
    actuator_id: ID = Field(
        ..., description="The ID of the actuator the timer belongs to"
    )
    finished_at: datetime = Field(
        ...,
        description="Indicates when the Timer will be finished. If the DateTimeStamp is in the future, the timer is not yet finished. If the DateTimeStamp is in the past, the timer is finished. If the timer was never started, the value can be an arbitrary DateTimeStamp in the past.",
    )


class FRBCUsageForecast(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("FRBC.UsageForecast", const=True)
    message_id: ID = Field(..., description="ID of this message")
    start_time: datetime = Field(
        ..., description="Time at which the FRBC.UsageForecast starts."
    )
    elements: List[FRBCUsageForecastElement] = Field(
        ...,
        description="Further elements that model the profile. There shall be at least one element. Elements must be placed in chronological order.",
        max_items=288,
        min_items=1,
    )
