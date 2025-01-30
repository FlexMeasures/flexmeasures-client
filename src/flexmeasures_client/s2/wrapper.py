from datetime import datetime

from pydantic import BaseModel, Field

try:
    from s2python.message import S2Message
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


class MetaData(BaseModel):
    dt: datetime


class S2Wrapper(BaseModel):
    message: S2Message = Field(discriminator="message_type")
    metadata: MetaData
