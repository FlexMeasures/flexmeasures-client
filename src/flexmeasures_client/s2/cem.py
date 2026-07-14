from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta
from logging import Logger
from zoneinfo import ZoneInfo
from typing import Dict, Optional

import pandas as pd
import pydantic

try:
    from s2python.common import (
        ControlType,
        Handshake,
        HandshakeResponse,
        PowerMeasurement,
        ReceptionStatus,
        ReceptionStatusValues,
        ResourceManagerDetails,
        RevokeObject,
        SelectControlType,
    )
except ImportError:
    raise ImportError(
        "The 's2-python' package is required for this functionality. "
        "Install it using `pip install flexmeasures-client[s2]`."
    )


from flexmeasures_client.client import FlexMeasuresClient
from flexmeasures_client.s2 import Handler, register
from flexmeasures_client.s2.control_types import ControlTypeHandler
from flexmeasures_client.s2.config_utils import configure_site
from flexmeasures_client.s2.utils import (
    ControlContext,
    get_latest_compatible_version,
    get_reception_status,
    get_unique_id,
)

_LOGGER = logging.getLogger(__name__)


class CEM(Handler):
    __version__ = "0.0.2-beta"

    _resource_manager_details: ResourceManagerDetails

    _control_types_handlers: Dict[ControlType | None, ControlTypeHandler]
    _is_closed = True
    _default_control_type: ControlType | None

    _power_sensors: Dict[
        str, int
    ]  # maps the CommodityQuantity power measurement sensors to FM sensor IDs

    _fm_client: FlexMeasuresClient
    _sending_queue: asyncio.Queue[tuple[pydantic.BaseModel, asyncio.Future]]

    _timers: dict[str, datetime]
    _datastore: dict
    _minimum_measurement_period: pd.Timedelta = pd.Timedelta(minutes=5)

    _power_buffer: defaultdict = defaultdict(
        list
    )  # {commodity_quantity: [(timestamp, value), ...]}

    def __init__(
        self,
        fm_client: FlexMeasuresClient,
        logger: Logger | None = None,
        default_control_type: ControlType | None = None,
        timers: dict[str, datetime] | None = None,
        datastore: dict | None = None,
        power_sensor_id: dict[str, int] | None = None,
        **kwargs,
    ) -> None:
        """
        Customer Energy Manager (CEM)
        """
        # Initialize per-instance control context and handler build tasks BEFORE calling super().__init__()
        # because parent's __init__ calls discover() which accesses control_type property
        self._control = ControlContext()
        self._handler_build_tasks: dict[ControlType, asyncio.Task] = {}

        super(CEM, self).__init__()

        self._fm_client = fm_client
        self._sending_queue = asyncio.Queue()
        self._power_sensors = dict()
        self.power_sensor_id = power_sensor_id
        # The apartment's FlexMeasures asset id (set once mapped), and the id of its
        # flex-context "aggregate-power" sensor (attached by the community runner AFTER
        # this CEM connects, i.e. only resolvable from step 1 onward). Resolved lazily in
        # handle_power_measurement so realized apartment power lands on the aggregate-power
        # sensor too, not only on measured-power (defect 4a: live apartment realizations).
        self._apartment_asset_id: int | None = None
        self._aggregate_power_sensor_id: int | None = None
        self._control_types_handlers = dict()
        self._default_control_type = default_control_type

        if not logger:
            logger = _LOGGER

        self._logger = logger
        self._is_closed = False

        self._timers = timers if timers is not None else {}
        self._datastore = datastore if datastore is not None else {}

    def _is_timer_due(self, name: str) -> bool:
        now = datetime.now()
        due_time = self._timers.get(name, now - self._minimum_measurement_period)
        if due_time <= now:
            # Get total seconds of the period
            period_seconds = self._minimum_measurement_period.total_seconds()

            # Seconds since start of the hour
            seconds_since_hour = now.minute * 60 + now.second + now.microsecond / 1e6

            # Ceil to next multiple of period_seconds
            next_tick_seconds = (
                math.ceil(seconds_since_hour / period_seconds) * period_seconds
            )

            # Compute next due datetime
            next_due = now.replace(minute=0, second=0, microsecond=0) + timedelta(
                seconds=next_tick_seconds
            )
            self._timers[name] = next_due
            return True
        else:
            self._logger.debug(
                f"Timer for {name} is not due until {self._timers[name]}"
            )
            return False

    def supports_control_type(self, control_type: ControlType):
        return control_type in self._resource_manager_details.available_control_types

    async def close(self):
        self._is_closed = True

        for control_type, handler in self._control_types_handlers.items():
            self._logger.debug(f"Closing handler for {control_type}")
            await handler.close()

    def is_closed(self):
        return self._is_closed

    @property
    def control_type(self):
        return self._control.control_type

    def register_control_type(self, control_type_handler: ControlTypeHandler):
        """
        This method registers control types.
        """

        # skip registering if there's a handler already registered for
        # the same control type
        if control_type_handler._control_type in self._control_types_handlers:
            self._logger.debug(
                "Control Type {control_type} already registered. Updating..."
            )

        # add fm_client to control_type handler
        control_type_handler._fm_client = self._fm_client

        # add send_message method so the handler can send messages
        control_type_handler.send_message = self.send_message

        # Add logger
        control_type_handler._logger = self._logger

        # store control_type_handler
        self._control_types_handlers[control_type_handler._control_type] = (
            control_type_handler
        )

        # Mark handler as ready once registered
        self._control.handler_ready[control_type_handler._control_type] = True

    async def handle_message(self, message: Dict | pydantic.BaseModel | str):
        """
        This method handles the incoming messages to the CEM and routes them to their custom handler.
        If a certain control type is active and there's a handler defined in both
        the control type handler and in the CEM, then the one defined in the control type prevails.
        """

        response = None

        if isinstance(message, pydantic.BaseModel):
            message = json.loads(message.json())

        if isinstance(message, str):
            message = json.loads(message)

        # Detect wrapper
        if isinstance(message, dict) and "message" in message and "metadata" in message:
            metadata = message["metadata"]
            message = message["message"]
            self._logger.debug("Received wrapped message")
            self._logger.debug(f"Received message: {message}")
            self._logger.debug(f"Received metadata: {metadata}")
            if "dt" in metadata:
                for control_type in self._control_types_handlers.values():
                    control_type.now = lambda: metadata["dt"]  # type: ignore
                self.now = lambda: metadata["dt"]  # type: ignore
        else:
            self._logger.debug(f"Received: {message}")

        # try to handle the message with the control_type handle
        ct = self._control.control_type
        handler = self._control_types_handlers.get(ct)
        ready = self._control.handler_ready.get(ct, False)

        if (
            handler is not None
            and ready
            and ct not in [ControlType.NO_SELECTION, ControlType.NOT_CONTROLABLE]
            and handler.supports_message(message)
        ):
            response = await handler.handle_message(message)
        else:
            if self.supports_message(message):
                response = await super().handle_message(
                    message
                )  # run Handler.handle_message

        # TODO: handle exceptions of handle message using Exceptions
        if response is None and message.get("message_type") not in ["ReceptionStatus"]:
            # case where none of the handlers support the message type
            response = ReceptionStatus(
                subject_message_id=message.get("message_id"),
                status=ReceptionStatusValues.TEMPORARY_ERROR,
            )

        if response is not None:
            await self.send_message(response)

    def update_control_type(self, control_type: ControlType):
        """
        Callback function that is triggered when we receive
        a confirmation that the message has been received.
        """
        self._control.control_type = control_type

    async def get_message(self) -> tuple[str, asyncio.Future]:
        """Call this function to get the messages to be sent to the RM

        Returns:
            str: message in JSON format
        """

        item = await self._sending_queue.get()

        if not isinstance(item, tuple) or len(item) != 2:
            raise RuntimeError(
                "Invalid item in sending queue. All messages must go through send_message() rather than _sending_queue.put()."
            )

        message, fut = item
        message = message.model_dump(mode="json")

        return message, fut

    async def activate_control_type(
        self, control_type: ControlType
    ) -> Optional[SelectControlType]:
        """
        This method returns a SelectControlType to enable a control type in the RM.
        """

        # check if it's trying to activate the current control_type
        if control_type == self._control.control_type:
            self._logger.debug(f"RM is already in `{control_type}` control type.")
            return None

        # check if the RM supports the control type
        if control_type not in self._resource_manager_details.available_control_types:
            self._logger.debug(f"RM does not support `{control_type}` control type.")
            return None

        # RM initialization succeeded
        if self._control.control_type is not None:
            message_id = get_unique_id()

            # the callback `update_control_type` will be called upon arrival of a
            # ReceptionStatus message with status = ReceptionStatusValues.OK

            # register callback in CEM handler
            if self._control.control_type in [
                ControlType.NOT_CONTROLABLE,
                ControlType.NO_SELECTION,
            ]:
                self.register_success_callbacks(
                    message_id, self.update_control_type, control_type=control_type
                )
            else:  # register callback in control mode handler
                self._control_types_handlers[
                    self._control.control_type
                ].register_success_callbacks(
                    message_id, self.update_control_type, control_type=control_type
                )
            await self.send_message(
                SelectControlType(message_id=message_id, control_type=control_type)
            )
        return None

    @register(Handshake)
    async def handle_handshake(self, message: Handshake):
        # TODO: check the version that the RM is using and send a
        # `selected_protocol_version` that matches the one of the RM
        # TODO: Return a TBD "CloseConnection" message to close the connection
        await self.send_message(get_reception_status(message, ReceptionStatusValues.OK))

        latest_compatible_version = get_latest_compatible_version(
            message.supported_protocol_versions,
            self.__version__,
            self._logger,
        )

        return HandshakeResponse(
            message_id=get_unique_id(),
            selected_protocol_version=str(latest_compatible_version),
        )

    @register(ResourceManagerDetails)
    async def handle_resource_manager_details(self, message: ResourceManagerDetails):
        self._resource_manager_details = message

        # schedule map_resource_to_asset to run soon concurrently
        task = asyncio.create_task(self.map_resource_to_asset(message))
        self._handler_build_tasks[ControlType.FILL_RATE_BASED_CONTROL] = task
        # self.background_tasks.add(
        #     task
        # )  # important to avoid a task disappearing mid-execution.
        # task.add_done_callback(self.background_tasks.discard)

        # await self.map_resource_to_asset(message)

        if (
            not self._control.control_type
        ):  # initializing. TODO: check if sending resource_manager_details
            # resets control type
            self._control.control_type = ControlType.NO_SELECTION

            # Activate default control type if defined
            if self._default_control_type:
                await self.activate_control_type(self._default_control_type)

        return get_reception_status(message)

    async def map_resource_to_asset(self, message):
        """Map S2 resource to FM asset.

        - Creates a new asset if the resource ID does not yet exist in FlexMeasures.
        - Updates the existing asset if the resource details changed.
        - Updates the control type for the resource.
        """
        assets = await self._fm_client.get_assets()
        asset = None
        for ast in assets:
            if ast["external_id"] == message.resource_id:
                asset = ast
        if asset is None:
            # Fall back to matching by name: servers without external_id support
            # cannot persist the resource ID, and RMs generate a fresh resource ID
            # on every connection, so a returning RM would otherwise cause a
            # duplicate-name asset creation attempt.
            for ast in assets:
                if ast["name"] == message.name:
                    asset = ast
        if asset is None:
            self._logger.debug(
                f"HANGDEBUG map_resource_to_asset: no existing asset found for "
                f"{message.name!r}, creating a new one"
            )
            account = await self._fm_client.get_account()
            asset = await self._fm_client.add_asset(
                name=message.name,
                account_id=account["id"],
                generic_asset_type_id=1,
                # parent_asset_id=self._asset_id,
                attributes=json.loads(message.to_json()),
            )
            self._logger.debug(
                f"HANGDEBUG map_resource_to_asset: created asset id={asset['id']}"
            )
        else:
            self._logger.debug(
                f"HANGDEBUG map_resource_to_asset: reusing existing asset "
                f"id={asset['id']} name={asset['name']!r} for {message.name!r}"
            )
        if asset["name"] != message.name:
            await self._fm_client.update_asset(
                asset_id=asset["id"], updates={"name": message.name}
            )
        if asset["attributes"] != message.to_json():
            await self._fm_client.update_asset(
                asset_id=asset["id"],
                updates={"attributes": json.loads(message.to_json())},
            )

        # Reconfigure site
        (
            price_sensor,
            production_price_sensor,
            power_sensor,
            soc_sensor,
            rm_discharge_sensor,
            soc_minima_sensor,
            soc_maxima_sensor,
            usage_forecast_sensor,
            leakage_behaviour_sensor,
            charging_efficiency_sensor,
            measured_power_sensor,
        ) = await configure_site(message.name, self._fm_client)

        # Wire up the apartment's dedicated MEASUREMENT sensor (distinct from the
        # "power" SCHEDULE sensor above) so incoming S2 PowerMeasurements land on
        # their own sensor instead of a hardcoded/wrong one.
        if self.power_sensor_id is None:
            self.power_sensor_id = {}
        self.power_sensor_id["ELECTRIC.POWER.L1"] = measured_power_sensor["id"]

        # Remember the apartment asset so handle_power_measurement can lazily resolve its
        # flex-context "aggregate-power" sensor (attached by the community runner only
        # after this CEM has connected). Resetting the cached sensor id lets a
        # reconnect/reconfigure pick up a freshly-attached aggregate-power sensor.
        self._apartment_asset_id = asset["id"]
        self._aggregate_power_sensor_id = None

        from flexmeasures_client.s2.control_types.FRBC.frbc_simple import FRBCSimple

        frbc = FRBCSimple(
            power_sensor_id=power_sensor["id"],
            price_sensor_id=price_sensor["id"],
            production_price_sensor_id=production_price_sensor["id"],
            soc_sensor_id=soc_sensor["id"],
            rm_discharge_sensor_id=rm_discharge_sensor["id"],
            soc_minima_sensor_id=soc_minima_sensor["id"],
            soc_maxima_sensor_id=soc_maxima_sensor["id"],
            usage_forecast_sensor_id=usage_forecast_sensor["id"],
            leakage_behaviour_sensor_id=leakage_behaviour_sensor["id"],
            charging_efficiency_sensor_id=charging_efficiency_sensor["id"],
        )
        self.register_control_type(frbc)

    @register(PowerMeasurement)
    async def handle_power_measurement(self, message: PowerMeasurement):

        for power_measurement in message.values:
            commodity_quantity = power_measurement.commodity_quantity.value

            if self.power_sensor_id:
                sensor_id = self.power_sensor_id.get(commodity_quantity)
                if sensor_id is None:
                    # TODO: create a new sensor or return ReceptionStatus
                    self._logger.debug(
                        f"No power sensor set up for {commodity_quantity}. Ignoring measurement {power_measurement.value} at {message.measurement_timestamp}."
                    )
                    continue
            else:
                self._logger.debug(
                    f"No power sensor IDs set up. Ignoring measurement {power_measurement.value} at {message.measurement_timestamp}."
                )
                continue

            # Bin to the SIMULATED measurement timestamp (not wall-clock time):
            # this co-sim runs on 2022 simulated time, so datetime.now() would bin
            # measurements onto a meaningless (real-world) event_start.
            measurement_ts = message.measurement_timestamp
            if measurement_ts.tzinfo is None:
                # This co-sim treats naive sim times as Europe/Amsterdam (kept
                # consistent with the community orchestrator, RM and controller,
                # which all localize the same naive sim times as Europe/Amsterdam).
                measurement_ts = measurement_ts.replace(
                    tzinfo=ZoneInfo("Europe/Amsterdam")
                )
            period = self._minimum_measurement_period
            m = period // pd.Timedelta(minutes=1)
            bin_start = measurement_ts.replace(
                second=0, microsecond=0, minute=(measurement_ts.minute // m) * m
            )
            # Belief time = the SIMULATED instant the measurement became known (just
            # after its interval elapses). Without this, FlexMeasures stamps the belief
            # time at wall-clock now (2026), so the UI's horizon view shows realized data
            # "recorded in 2026" instead of at simulation time (defect 4b).
            prior = (bin_start + period).isoformat()

            # Resolve the apartment's flex-context "aggregate-power" sensor lazily: it is
            # attached by the community runner AFTER this CEM connects, so it only exists
            # from step 1 onward. When present, mirror the realized value onto it too, so
            # advancing the sim produces live apartment realizations on the aggregate-power
            # sensor (defect 4a) - not just on the dedicated measured-power sensor. That
            # sensor also carries StorageScheduler SCHEDULE data natively; the realized
            # posts stay distinguishable by their own (CEM/user) source and simulated
            # belief time. Only mirror ELECTRIC.POWER.L1 (the aggregated apartment power).
            aggregate_sensor_id = None
            if commodity_quantity == "ELECTRIC.POWER.L1":
                aggregate_sensor_id = await self._resolve_aggregate_power_sensor_id()

            # Post DIRECTLY, without the wall-clock _is_timer_due throttle or the
            # 5-minute buffered-averaging that the original real-time streaming path
            # used. In this co-simulation the RM sends exactly one already-aggregated
            # apartment-power value per simulated step, and simulated time advances at
            # its own (non-real-time) pace; gating on datetime.now() would suppress
            # almost every post, and re-averaging a single value is a no-op. Each
            # value is simply written at its own simulated event_start.
            target_sensor_ids = [sensor_id]
            if aggregate_sensor_id is not None and aggregate_sensor_id != sensor_id:
                target_sensor_ids.append(aggregate_sensor_id)
            for target_sensor_id in target_sensor_ids:
                try:
                    await self._fm_client.post_sensor_data(
                        target_sensor_id,
                        start=bin_start.isoformat(),
                        duration=period.isoformat(),  # TODO: not specified in S2 Protocol
                        values=[power_measurement.value],
                        # S2 PowerMeasurement values are in Watts (unlike this codebase's
                        # S2 power *ranges*, which carry kW-magnitude values that
                        # get_commodity_unit labels "kW"). Post as W and let FlexMeasures
                        # convert to the sensor's kW unit, so a ~5000 W realized load is
                        # stored as 5 kW, not 5000.
                        unit="W",
                        prior=prior,
                    )
                except Exception as e:  # noqa: B902 - intentional safety net
                    self._logger.debug(
                        f"POSTing power measurement failed with error: {e}"
                    )

        return get_reception_status(message)

    async def _resolve_aggregate_power_sensor_id(self) -> int | None:
        """Lazily resolve the apartment's flex-context "aggregate-power" sensor id.

        The community runner attaches this sensor (and the flex-context key) only after
        the CEM has connected, so it is absent during step 0 and appears from step 1
        onward. We re-read the apartment asset's flex_context until the key is present,
        then cache the id. Returns None (and posts nothing extra) while it is absent.
        """
        if self._aggregate_power_sensor_id is not None:
            return self._aggregate_power_sensor_id
        if self._apartment_asset_id is None:
            return None
        try:
            asset = await self._fm_client.get_asset(
                self._apartment_asset_id, parse_json_fields=True
            )
            flex_context = asset.get("flex_context") or {}
            entry = flex_context.get("aggregate-power")
            if isinstance(entry, dict):
                sensor_id = entry.get("sensor")
                if sensor_id is not None:
                    self._aggregate_power_sensor_id = int(sensor_id)
                    return self._aggregate_power_sensor_id
        except Exception as e:  # noqa: B902 - best-effort, never break measurement posting
            self._logger.debug(
                f"Could not resolve aggregate-power sensor for asset "
                f"{self._apartment_asset_id}: {e}"
            )
        return None

    @register(RevokeObject)
    def handle_revoke_object(self, message: RevokeObject):
        """
        Stores the revoked object ID into the objects_revoked list
        """

        control_types = {
            "FRBC": ControlType.FILL_RATE_BASED_CONTROL,
            "DDBC": ControlType.DEMAND_DRIVEN_BASED_CONTROL,
            "PEBC": ControlType.POWER_ENVELOPE_BASED_CONTROL,
            "OMBC": ControlType.OPERATION_MODE_BASED_CONTROL,
            "PPBC": ControlType.POWER_PROFILE_BASED_CONTROL,
        }

        for initials, control_type in control_types.items():
            if initials in str(message.object_type):
                self._control_types_handlers[control_type].revoke_message(
                    message.object_id
                )

        return get_reception_status(message, ReceptionStatusValues.OK)

    async def send_message(self, message):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._logger.debug(f"Sent: {message}")
        await self._sending_queue.put((message, fut))


def get_commodity_unit(commodity_quantity) -> str:
    if "POWER" in commodity_quantity:
        return "kW"
    if "FLOW_RATE" in commodity_quantity:
        return "m³/h"
    if "TEMPERATURE" in commodity_quantity:
        return "°C"
    return ""
