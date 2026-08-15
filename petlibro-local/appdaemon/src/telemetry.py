"""Projection of non-authoritative feeder telemetry into Home Assistant."""

from __future__ import annotations

import datetime

from backend import Backend, FoodOutputProgress
from ha_entities import HomeAssistantStatePublisher
from protocol import (
    GrainOutputType,
    PercentageInt,
    PowerMode,
    PowerType,
    SdCardFileSystem,
    SdCardState,
    WifiType,
)


class TelemetryPublisher:
    """Publish operational data that is not persistent feeder truth."""

    def __init__(self, state: HomeAssistantStatePublisher, logger):
        self.state = state
        self.logger = logger

    def register(self, backend: Backend) -> None:
        backend.device_info_listen(self.device_info)
        backend.device_wifi_info_listen(self.wifi_info)
        backend.device_sd_card_info_listen(self.sd_card_info)
        backend.capabilities_listen(self.capabilities)
        backend.state_power_listen(self.power)
        backend.state_food_listen(self.food)
        backend.food_output_log_start_listen(self.food_output_start)
        backend.food_output_log_end_listen(self.food_output_end)
        backend.food_output_progress_listen(self.food_output_progress)

    def device_info(
        self,
        device_serial: str | None = None,
        product_id: str | None = None,
        uuid: str | None = None,
        hardware_version: str | None = None,
        software_version: str | None = None,
    ) -> None:
        self._publish_non_none({
            "device/serial_number": device_serial,
            "device/product_id": product_id,
            "device/uuid": uuid,
            "device/hardware_version": hardware_version,
            "device/software_version": software_version,
        })

    def wifi_info(
        self,
        mac_address: str | None = None,
        rssi: int | None = None,
        type_: WifiType | None = None,
        ssid: str | None = None,
    ) -> None:
        self._publish_non_none({
            "wifi/mac_address": mac_address,
            "wifi/rssi": rssi,
            "wifi/type": type_,
            "wifi/ssid": ssid,
        })

    def sd_card_info(
        self,
        state: SdCardState | None = None,
        file_system: SdCardFileSystem | None = None,
        total_capacity_mb: int | None = None,
        used_capacity_mb: int | None = None,
    ) -> None:
        self._publish_non_none({
            "sd_card/state": state,
            "sd_card/file_system": file_system,
            "sd_card/total_capacity": total_capacity_mb,
            "sd_card/used_capacity": used_capacity_mb,
        })

    def capabilities(self, values: dict[str, bool | None]) -> None:
        self._publish_non_none(values)

    def power(
        self,
        battery_level: PercentageInt | None = None,
        mode: PowerMode | None = None,
        type_: PowerType | None = None,
    ) -> None:
        self._publish_non_none({
            "power/battery_level": (
                None if battery_level is None else battery_level.value_get()
            ),
            "power/mode": mode,
            "power/type": type_,
        })

    def food(
        self,
        motor_state: int | None = None,
        outlet_blocked: bool | None = None,
        low_fill_level: bool | None = None,
    ) -> None:
        self._publish_non_none({
            "food/motor_state": motor_state,
            "food/outlet_blocked": outlet_blocked,
            "food/low_fill_level": low_fill_level,
        })

    def food_output_start(
        self, grain_output_type: GrainOutputType, grain_num: int
    ) -> None:
        self._publish_non_none({
            "food_output/last_start": datetime.datetime.now().astimezone(),
            "food_output/last_grain_count": grain_num,
            "food_output/last_trigger": grain_output_type,
        })

    def food_output_end(
        self, grain_output_type: GrainOutputType, grain_num: int
    ) -> None:
        self._publish_non_none({
            "food_output/last_end": datetime.datetime.now().astimezone(),
            "food_output/last_grain_count": grain_num,
            "food_output/last_trigger": grain_output_type,
        })

    def food_output_progress(self, progress: FoodOutputProgress) -> None:
        self.state.publish("food_output/progress", progress)

    def _publish_non_none(self, values: dict[str, object | None]) -> None:
        for topic, value in values.items():
            if value is not None:
                self.state.publish(topic, value)
