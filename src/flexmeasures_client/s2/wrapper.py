from datetime import datetime

from pydantic import BaseModel, Field
from s2python.message import S2Message


class MetaData(BaseModel):
    dt: datetime


class S2Wrapper(BaseModel):
    message: S2Message = Field(discriminator="message_type")
    metadata: MetaData
