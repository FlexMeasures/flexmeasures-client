from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import Callable, Dict, List

import pydantic
from python_s2_protocol.common.messages import (
    ReceptionStatus,
    ReceptionStatusValues,
    RevokeObject,
)

from flexmeasures_client.s2.utils import (
    SizeLimitOrderedDict,
    get_message_id,
    get_validation_error_summary,
)


@dataclass
class Tag:
    message_type: str


def register(schema: pydantic.BaseModel, message_type: str) -> Callable:
    """
    Adds a tag with the message_type that the function that decorates
    handles. Moreover, it validates and converts the dict represenation
    of the message into its pydantic class equivalent, running the
    data validation.

    TODO: use `message_type = schema.__fields__["message_type"].default`
    to infer message_type when not given
    """

    def wrapper(func: Callable) -> Callable:
        # add tags to func the wrapper
        @functools.wraps(func)
        def wrap(*args, **kwargs):
            try:
                incoming_message = schema(**args[1])
                self = args[0]

                # TODO: implement function __hash__ in ID that returns
                # the value of __root__
                self.incoming_messages[
                    get_message_id(incoming_message)
                ] = incoming_message

                outgoing_message = func(self, incoming_message)

                self.outgoing_messages[
                    get_message_id(outgoing_message)
                ] = outgoing_message

                return outgoing_message

            except pydantic.ValidationError as e:
                return ReceptionStatus(
                    subject_message_id=incoming_message.message_id,
                    diagnostic_label=get_validation_error_summary(e),
                    status=ReceptionStatusValues.INVALID_DATA,
                )  # TODO: Discuss status

        setattr(wrap, "_tag", Tag(message_type))

        return wrap

    return wrapper


class Handler:
    message_handlers: Dict[str, Callable]

    outgoing_messages: SizeLimitOrderedDict
    incoming_messages: SizeLimitOrderedDict

    objects_revoked: List[str]

    success_callback: Dict[str, Callable]
    failiure_callback: Dict[str, Callable]

    outgoing_messages_status: SizeLimitOrderedDict

    def __init__(self, max_size=100) -> None:
        """
        Handler
        Upon creation, this class will discover the functions tagged with
        the @register decorator and store them in self.message_handlers indexed
        by the message_type.
        """
        self.message_handlers = dict()

        self.outgoing_messages = SizeLimitOrderedDict(max_size=max_size)
        self.incoming_messages = SizeLimitOrderedDict(max_size=max_size)

        self.success_callback = dict()
        self.failiure_callback = dict()

        self.objects_revoked = list()

        self.outgoing_messages_status = SizeLimitOrderedDict(max_size=max_size)

        self.discovery()

    def discovery(self):
        """
        Discovers which class method haven been tagged by the decorator
        @register
        """
        for method in dir(self):
            if not method.startswith("__") and hasattr(getattr(self, method), "_tag"):
                tag = getattr(getattr(self, method), "_tag")
                self.message_handlers[tag.message_type] = getattr(self, method)

    def register_callback(
        self, callback_store: dict, message_id: str, callback: Callable, **kwargs
    ):
        """
        Stores a callback into a callback store indexed by the message.
        """
        callback_store[message_id] = functools.partial(callback, **kwargs)

    def register_success_callback(self, message_id: str, callback: Callable, **kwargs):
        """
        Stores a callback into the success callback store.
        Callbacks will be called if ReceptionStatus.satus = ReceptionStatusValues.OK
        """
        self.register_callback(self.success_callback, message_id, callback, **kwargs)

    def register_failiure_callback(self, message_id: str, callback: Callable, **kwargs):
        """
        Stores a callback into the failiure callback store.
        Callbacks will be called if ReceptionStatus.satus != ReceptionStatusValues.OK
        """
        self.register_callback(self.failiure_callback, message_id, callback, **kwargs)

    def supports_message(self, message: Dict | pydantic.BaseModel) -> bool:
        """
        Checks if a message is supported by the Handler.
        """

        # TODO: handle a message that has not `message_type`
        message_type = ""

        if isinstance(message, pydantic.BaseModel):
            message_type = message.message_type
        else:
            message_type = message.get("message_type")

        return message_type in self.message_handlers

    def handle_message(self, message: pydantic.BaseModel) -> dict:
        """
        Calls the handler linked to the message_type and converts the output
        to a serilizd dict, i.e, it converts all the inner objects to Python
        basic types (dict, list, int, str, ...).

        :returns serialized_output_message:
        """

        output_message = self.message_handlers[message.get("message_type")](message)

        # Pending for pydantic V2 to implemen model.model_dump(mode="json") in
        # PR #1409 (https://github.com/pydantic/pydantic/issues/1409)
        if output_message:  # e.g of return None is handling ReceptionStatus
            output_message = json.loads(output_message.json())

        return output_message

    @register(ReceptionStatus, "ReceptionStatus")
    def handle_response_status(self, message: ReceptionStatus):
        """
        If defined, it calls the success_callback or failiure_callback depeding
        on the status of the ReceptionStatus.

        By default, the handlers will use this handler for the messages of
        type ReceptionStatus.
        """

        callback_store = None

        # saving ACK status code
        # TODO: implement function __hash__ in ID that returns the value of __root__
        self.outgoing_messages_status[
            message.subject_message_id.__root__
        ] = message.status

        # choose which callback to call, depending on the ReceptionSatatus value
        if message.status == ReceptionStatusValues.OK:
            callback_store = self.success_callback
        else:
            callback_store = self.failiure_callback

        # pop callback from callback_store and run it, if there exists one
        if callback := callback_store.pop(message.subject_message_id.__root__):
            callback()

    @register(RevokeObject, "RevokeObject")
    def handle_revoke_object(self, message: RevokeObject):
        """
        Stores the revoked object ID into the objects_revoked list
        """

        self.objects_revoked.append(message.object_id.__root__)

        return ReceptionStatus(
            subject_message_id=message.message_id, status=ReceptionStatusValues.OK
        )
