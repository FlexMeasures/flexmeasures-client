# flake8: noqa

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Extra, Field

from flexmeasures_client.s2.python_s2_protocol.common.schemas import (
    ID,
    CommodityQuantity,
    ControlType,
    Currency,
    Duration,
    EnergyManagementRole,
    InstructionStatus,
    PowerForecastElement,
    PowerValue,
    ReceptionStatusValues,
    RevokableObjects,
    Role,
    SessionRequestType,
)


class Handshake(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("Handshake", const=True)
    message_id: ID = Field(..., description="ID of this message")
    role: EnergyManagementRole = Field(
        ..., description="The role of the sender of this message"
    )
    supported_protocol_versions: Optional[List[str]] = Field(
        None,
        description="Protocol versions supported by the sender of this message. This field is mandatory for the RM, but optional for the CEM.",
        min_items=1,
    )


class HandshakeResponse(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("HandshakeResponse", const=True)
    message_id: ID = Field(..., description="ID of this message")
    selected_protocol_version: str = Field(
        ..., description="The protocol version the CEM selected for this session"
    )


class InstructionStatusUpdate(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("InstructionStatusUpdate", const=True)
    message_id: ID = Field(..., description="ID of this message")
    instruction_id: ID = Field(
        ..., description="ID of this instruction (as provided by the CEM) "
    )
    status_type: InstructionStatus = Field(
        ..., description="Present status of this instruction."
    )
    timestamp: datetime = Field(
        ..., description="Timestamp when status_type has changed the last time."
    )


class PowerForecast(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("PowerForecast", const=True)
    message_id: ID = Field(..., description="ID of this message")
    start_time: datetime = Field(
        ..., description="Start time of time period that is covered by the profile."
    )
    elements: List[PowerForecastElement] = Field(
        ...,
        description="Elements of which this forecast consists. Contains at least one element. Elements must be placed in chronological order.",
        max_items=288,
        min_items=1,
    )


class PowerMeasurement(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("PowerMeasurement", const=True)
    message_id: ID = Field(..., description="ID of this message")
    measurement_timestamp: datetime = Field(
        ..., description="Timestamp when PowerValues were measured."
    )
    values: List[PowerValue] = Field(
        ...,
        description="Array of measured PowerValues. Must contain at least one item and at most one item per ‘commodity_quantity’ (defined inside the PowerValue).",  # noqa: E501
        max_items=10,
        min_items=1,
    )


class ReceptionStatus(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("ReceptionStatus", const=True)
    subject_message_id: ID = Field(
        ..., description="The message this ReceptionStatus refers to"
    )
    status: ReceptionStatusValues = Field(
        ..., description="Enumeration of status values"
    )
    diagnostic_label: Optional[str] = Field(
        None,
        description="Diagnostic label that can be used to provide additional information for debugging. However, not for HMI purposes.",  # noqa: E501
    )


class ResourceManagerDetails(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("ResourceManagerDetails", const=True)
    message_id: ID = Field(..., description="ID of this message")
    resource_id: ID = Field(
        ...,
        description="Identifier of the Resource Manager. Must be unique within the scope of the CEM.",  # noqa: E501
    )
    name: Optional[str] = Field(None, description="Human readable name given by user")
    roles: List[Role] = Field(
        ...,
        description="Each Resource Manager provides one or more energy Roles",
        max_items=3,
        min_items=1,
    )
    manufacturer: Optional[str] = Field(None, description="Name of Manufacturer")
    model: Optional[str] = Field(
        None,
        description="Name of the model of the device (provided by the manufacturer)",
    )
    serial_number: Optional[str] = Field(
        None, description="Serial number of the device (provided by the manufacturer)"
    )
    firmware_version: Optional[str] = Field(
        None,
        description="Version identifier of the firmware used in the device (provided by the manufacturer)",  # noqa: E501
    )
    instruction_processing_delay: Duration = Field(
        ...,
        description="The average time the combination of Resource Manager and HBES/BACS/SASS or (Smart) device needs to process and execute an instruction",  # noqa: E501
    )
    available_control_types: List[ControlType] = Field(
        ...,
        description="The control types supported by this Resource Manager.",
        max_items=5,
        min_items=1,
    )
    currency: Optional[Currency] = Field(
        None,
        description="Currency to be used for all information regarding costs. Mandatory if cost information is published.",  # noqa: E501
    )
    provides_forecast: bool = Field(
        ...,
        description="Indicates whether the ResourceManager is able to provide PowerForecasts",  # noqa: E501
    )
    provides_power_measurement_types: List[CommodityQuantity] = Field(
        ...,
        description="Array of all CommodityQuantities that this Resource Manager can provide measurements for. ",  # noqa: E501
        max_items=10,
        min_items=1,
    )


class RevokeObject(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("RevokeObject", const=True)
    message_id: ID = Field(..., description="ID of this message")
    object_type: RevokableObjects = Field(
        ..., description="The type of object that needs to be revoked"
    )
    object_id: ID = Field(..., description="The ID of object that needs to be revoked")


class SelectControlType(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("SelectControlType", const=True)
    message_id: ID = Field(..., description="ID of this message")
    control_type: ControlType = Field(
        ...,
        description="The ControlType to activate. Must be one of the available ControlTypes as defined in the ResourceManagerDetails",
    )


class SessionRequest(BaseModel):
    class Config:
        extra = Extra.forbid
        validate_assignment = True

    message_type: str = Field("SessionRequest", const=True)
    message_id: ID = Field(..., description="ID of this message")
    request: SessionRequestType = Field(..., description="The type of request")
    diagnostic_label: Optional[str] = Field(
        None,
        description="Optional field for a human readible descirption for debugging purposes",
    )
