from __future__ import annotations

import functools
import json
from collections import deque
from dataclasses import dataclass
from typing import Callable, Coroutine, Dict, Type

import pydantic
from s2python.common import ReceptionStatus, ReceptionStatusValues, RevokeObject

from flexmeasures_client.s2.utils import (
    SizeLimitOrderedDict,
    get_message_id,
    get_validation_error_summary,
)


@dataclass
class Tag:
    message_type: str


def register(schema: Type[pydantic.BaseModel]) -> Callable:
    """
    Adds a tag with the message_type to the function that decorates.
    Moreover, it validates and converts the dict representation
    of the message into its pydantic class equivalent, running the
    data validation.
    """

    def wrapper(func: Callable) -> Callable:
        # add tags to func the wrapper
        @functools.wraps(func)
        def wrap(*args, **kwargs):
            try:
                incoming_message = schema(**args[1])
                self = args[0]

                # TODO: implement function __hash__ in ID that returns
                # the value of __root__, this way we would be able to use
                # the ID as key directly
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
                    subject_message_id=str(incoming_message.message_id),
                    diagnostic_label=get_validation_error_summary(e),
                    status=ReceptionStatusValues.INVALID_DATA,
                )  # TODO: Discuss status

        message_type = schema.__fields__.get("message_type").default
        setattr(wrap, "_tag", Tag(message_type))

        return wrap

    return wrapper


class Handler:
    message_handlers: Dict[str, Callable]

    outgoing_messages: SizeLimitOrderedDict
    incoming_messages: SizeLimitOrderedDict

    objects_revoked: deque

    success_callbacks: Dict[str, Callable]
    failure_callbacks: Dict[str, Callable]

    outgoing_messages_status: SizeLimitOrderedDict

    def __init__(self, max_size: int = 100) -> None:
        """
        Handler
        This class does the following:
            - Create a namespace where the entities (CEM, ControlTypes, ...)
            can store state variables without colliding.
            - Routes the incoming messages to functions (handlers) decorated
             with @register to process the different message types.

        Upon creation, this class will discover the class methods tagged with
        the @register decorator and store them in self.message_handlers indexed
        by the message_type.

        Inherit from this class to define new control type handlers.
        """
        self.message_handlers = dict()

        self.outgoing_messages = SizeLimitOrderedDict(max_size=max_size)
        self.incoming_messages = SizeLimitOrderedDict(max_size=max_size)

        self.success_callbacks = dict()
        self.failure_callbacks = dict()

        self.objects_revoked = deque(maxlen=max_size)

        self.outgoing_messages_status = SizeLimitOrderedDict(max_size=max_size)

        self.discover()

    def is_revoked(self, message_id: str) -> bool:
        return message_id in self.objects_revoked

    def revoke_message(self, message_id: str) -> None:
        self.objects_revoked.append(message_id)

    def discover(self):
        """
        Discovers which class method hasn't been tagged by the decorator
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

    def register_success_callbacks(self, message_id: str, callback: Callable, **kwargs):
        """
        Stores a callback into the success callback store.
        Callbacks will be called if ReceptionStatus.satus = ReceptionStatusValues.OK
        """
        self.register_callback(self.success_callbacks, message_id, callback, **kwargs)

    def register_failure_callbacks(self, message_id: str, callback: Callable, **kwargs):
        """
        Stores a callback into the failure callback store.
        Callbacks will be called if ReceptionStatus.satus != ReceptionStatusValues.OK
        """
        self.register_callback(self.failure_callbacks, message_id, callback, **kwargs)

    def supports_message(self, message: Dict | pydantic.BaseModel | str) -> bool:
        """
        Checks if a message is supported by the Handler.
        """

        # TODO: handle a message that has not `message_type`
        message_type = ""

        if isinstance(message, pydantic.BaseModel):
            message_type = message.message_type
        elif isinstance(message, dict):
            message_type = message.get("message_type", "")
        elif isinstance(message, str):
            message_type = json.loads(message).get("message_type")

        return message_type in self.message_handlers

    def handle_message(self, message: pydantic.BaseModel | str | Dict) -> Coroutine:
        """
        Calls the handler linked to the message_type and converts the output
        to a serialized dict, i.e, it converts all the inner objects to Python
        basic types (dict, list, int, str, ...).

        :returns serialized_output_message:
        """

        if isinstance(message, pydantic.BaseModel):
            message = json.loads(message.json())

        if isinstance(message, str):
            message = json.loads(message)

        output_message = self.message_handlers[message.get("message_type")](message)

        return output_message

    @register(ReceptionStatus)
    def handle_response_status(self, message: ReceptionStatus):
        """
        If defined, it calls the success_callbacks or failure_callbacks depending
        on the status of the ReceptionStatus.

        By default, the handlers will use this handler for the messages of
        type ReceptionStatus.
        """

        callback_store = None

        # save acknowledgement status code
        # TODO: implement function __hash__ in ID that returns the value of __root__
        self.outgoing_messages_status[str(message.subject_message_id)] = message.status

        # choose which callback to call, depending on the ReceptionStatus value
        if message.status == ReceptionStatusValues.OK:
            callback_store = self.success_callbacks
        else:
            callback_store = self.failure_callbacks

        # pop callback from callback_store and run it, if there exists one
        if callback := callback_store.pop(str(message.subject_message_id), None):
            callback()

        # delete success callback related to this message
        if callback is None and (message.status != ReceptionStatusValues.OK):
            self.success_callbacks.pop(str(message.subject_message_id), None)

    @register(RevokeObject)
    def handle_revoke_object(self, message: RevokeObject):
        """
        Stores the revoked object ID into the objects_revoked list
        """

        self.objects_revoked.append(message.object_id)

        return ReceptionStatus(
            subject_message_id=str(message.message_id), status=ReceptionStatusValues.OK
        )
