"""Canonical feeder-truth and command value mappings for HA settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from protocol import (
    AgingType,
    BowlMode,
    MotionDetectionRange,
    MotionDetectionSensitivity,
    NightVision,
    Resolution,
    SoundDetectionSensitivity,
    VideoRecordMode,
    PercentageInt,
)


@dataclass(frozen=True)
class TruthProjection:
    field: str
    topic: str
    converter: Callable[[object], object]


@dataclass(frozen=True)
class SettingCommandSpec:
    topic: str
    control: str
    state_field: str
    parser: Callable[[object], object]
    expected: Callable[[object], object]
    publisher: Callable[[object, object], object]


def enabled_to_bool(value: object) -> bool:
    return {"enabled": True, "disabled": False}[str(value).lower()]


def bool_to_enabled(value: bool) -> str:
    return "enabled" if value else "disabled"


def int_to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return bool(int(value))


def aging_to_wire(value: object) -> str:
    return {
        "always_active": AgingType.NON_SCHEDULED_ENABLED.name,
        "scheduled": AgingType.SCHEDULED_ENABLED.name,
    }[str(value).lower()]


def aging_to_semantic(value: AgingType) -> str:
    return (
        "scheduled"
        if value == AgingType.SCHEDULED_ENABLED
        else "always_active"
    )


def resolution_to_wire(value: object) -> str:
    return {"720p": Resolution.P720.name, "1080p": Resolution.P1080.name}[
        str(value).lower()
    ]


def night_vision_to_wire(value: object) -> str:
    return {
        "auto": NightVision.AUTOMATIC.name,
        "automatic": NightVision.AUTOMATIC.name,
        "on": NightVision.OPEN.name,
        "off": NightVision.CLOSE.name,
    }[str(value).lower()]


def night_vision_to_semantic(value: NightVision) -> str:
    return {
        NightVision.AUTOMATIC: "auto",
        NightVision.OPEN: "on",
        NightVision.CLOSE: "off",
    }[value]


def recording_type_to_wire(value: object) -> str:
    return {
        "continuous": VideoRecordMode.CONTINUOUS.name,
        "motion_detection": VideoRecordMode.MOTION_DETECTION.name,
    }[str(value).lower()]


def bowl_mode_to_wire(value: object) -> str:
    return {
        "single_bowl": BowlMode.SINGLE_BOWL.name,
        "dual_bowl": BowlMode.DOUBLE_BOWL.name,
        "double_bowl": BowlMode.DOUBLE_BOWL.name,
    }[str(value).lower()]


def enum_name(enum_type):
    return lambda value: enum_type[str(value).upper()].name


def enum_value(enum_type):
    return lambda value: enum_type[str(value)]


def identity(value):
    return value


TRUTH_PROJECTIONS = (
    TruthProjection("feeding_audio_enabled", "audio/enable", enabled_to_bool),
    TruthProjection("camera_switch", "camera/enable", enabled_to_bool),
    TruthProjection("camera_mode", "camera/aging_type", aging_to_wire),
    TruthProjection("camera_resolution", "camera/resolution", resolution_to_wire),
    TruthProjection("night_vision_mode", "camera/night_vision", night_vision_to_wire),
    TruthProjection("video_record_switch", "recording/enable", enabled_to_bool),
    TruthProjection("local_recording_mode", "recording/aging_type", aging_to_wire),
    TruthProjection("local_camera_recording_type", "recording/mode", recording_type_to_wire),
    TruthProjection("sound_switch", "sound/enable", enabled_to_bool),
    TruthProjection("sound_mode", "sound/aging_type", aging_to_wire),
    TruthProjection("volume", "sound/volume", int),
    TruthProjection("light_switch", "button_lights/enable", enabled_to_bool),
    TruthProjection("button_lights_mode", "button_lights/aging_type", aging_to_wire),
    TruthProjection("auto_change_mode", "buttons_auto_lock/enable", int_to_bool),
    TruthProjection("auto_threshold", "buttons_auto_lock/threshold", int),
    TruthProjection("bowl_mode", "food/bowl_mode", bowl_mode_to_wire),
    TruthProjection("feeding_video_recording_enable", "feeding_video/enable", enabled_to_bool),
    TruthProjection("record_scheduled_feedings", "feeding_video/on_feeding_plan_trigger_enable", enabled_to_bool),
    TruthProjection("record_manual_feedings", "feeding_video/on_manual_feeding_trigger_enable", enabled_to_bool),
    TruthProjection("before_feeding_plan_minutes", "feeding_video/time_before_feeding_plan_trigger", int),
    TruthProjection("automatic_recording_minutes", "feeding_video/time_automatic_recording", int),
    TruthProjection("after_manual_feeding_minutes", "feeding_video/time_after_manual_feeding_trigger", int),
    TruthProjection("video_watermark_enable", "feeding_video/watermark", enabled_to_bool),
    TruthProjection("motion_detection_switch", "motion_detection/enable", enabled_to_bool),
    TruthProjection("motion_detection_mode", "motion_detection/aging_type", aging_to_wire),
    TruthProjection("motion_detection_range", "motion_detection/range", enum_name(MotionDetectionRange)),
    TruthProjection("motion_detection_sensitivity", "motion_detection/sensitivity", enum_name(MotionDetectionSensitivity)),
    TruthProjection("sound_detection_switch", "sound_detection/enable", enabled_to_bool),
    TruthProjection("sound_detection_mode", "sound_detection/aging_type", aging_to_wire),
    TruthProjection("sound_detection_sensitivity", "sound_detection/sensitivity", enum_name(SoundDetectionSensitivity)),
    TruthProjection("cloud_video_record_switch", "cloud_video_recording/enable", enabled_to_bool),
)


def parse_mqtt_bool(value: object) -> bool:
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("boolean command must be true or false")


SETTING_COMMANDS = (
    SettingCommandSpec("audio/cmd/enable", "audio.enable", "feeding_audio_enabled", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_audio(enable=value)),
    SettingCommandSpec("camera/cmd/enable", "camera.enable", "camera_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_camera(enable=value)),
    SettingCommandSpec("camera/cmd/aging_type", "camera.mode", "camera_mode", enum_value(AgingType), aging_to_semantic, lambda backend, value: backend.settings_camera(aging_type=value)),
    SettingCommandSpec("camera/cmd/night_vision", "camera.night_vision", "night_vision_mode", enum_value(NightVision), night_vision_to_semantic, lambda backend, value: backend.settings_camera(night_vision=value)),
    SettingCommandSpec("camera/cmd/resolution", "camera.resolution", "camera_resolution", enum_value(Resolution), lambda value: "1080p" if value == Resolution.P1080 else "720p", lambda backend, value: backend.settings_camera(resolution=value)),
    SettingCommandSpec("recording/cmd/enable", "recording.enable", "video_record_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_recording(enable=value)),
    SettingCommandSpec("recording/cmd/aging_type", "recording.mode", "local_recording_mode", enum_value(AgingType), aging_to_semantic, lambda backend, value: backend.settings_recording(aging_type=value)),
    SettingCommandSpec("recording/cmd/mode", "recording.type", "local_camera_recording_type", enum_value(VideoRecordMode), lambda value: "continuous" if value == VideoRecordMode.CONTINUOUS else "motion_detection", lambda backend, value: backend.settings_recording(mode=value)),
    SettingCommandSpec("motion_detection/cmd/enable", "motion_detection.enable", "motion_detection_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_motion_detection(enable=value)),
    SettingCommandSpec("motion_detection/cmd/aging_type", "motion_detection.mode", "motion_detection_mode", enum_value(AgingType), aging_to_semantic, lambda backend, value: backend.settings_motion_detection(aging_type=value)),
    SettingCommandSpec("motion_detection/cmd/range", "motion_detection.range", "motion_detection_range", enum_value(MotionDetectionRange), lambda value: value.name.lower(), lambda backend, value: backend.settings_motion_detection(range_=value)),
    SettingCommandSpec("motion_detection/cmd/sensitivity", "motion_detection.sensitivity", "motion_detection_sensitivity", enum_value(MotionDetectionSensitivity), lambda value: value.name.lower(), lambda backend, value: backend.settings_motion_detection(sensitivity=value)),
    SettingCommandSpec("sound_detection/cmd/enable", "sound_detection.enable", "sound_detection_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_sound_detection(enable=value)),
    SettingCommandSpec("sound_detection/cmd/aging_type", "sound_detection.mode", "sound_detection_mode", enum_value(AgingType), aging_to_semantic, lambda backend, value: backend.settings_sound_detection(aging_type=value)),
    SettingCommandSpec("sound_detection/cmd/sensitivity", "sound_detection.sensitivity", "sound_detection_sensitivity", enum_value(SoundDetectionSensitivity), lambda value: value.name.lower(), lambda backend, value: backend.settings_sound_detection(sensitivity=value)),
    SettingCommandSpec("feeding_video/cmd/enable", "feeding_video.enable", "feeding_video_recording_enable", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_feeding_video(enable=value)),
    SettingCommandSpec("feeding_video/cmd/on_feeding_plan_trigger_enable", "feeding_video.record_scheduled", "record_scheduled_feedings", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_feeding_video(video_on_start_feeding_plan=value)),
    SettingCommandSpec("feeding_video/cmd/on_manual_feeding_trigger_enable", "feeding_video.record_manual", "record_manual_feedings", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_feeding_video(video_after_manual_feeding=value)),
    SettingCommandSpec("feeding_video/cmd/time_before_feeding_plan_trigger", "feeding_video.pre_record", "before_feeding_plan_minutes", int, identity, lambda backend, value: backend.settings_feeding_video(recording_length_before_feeding_plan_time=value)),
    SettingCommandSpec("feeding_video/cmd/time_after_manual_feeding_trigger", "feeding_video.post_record", "after_manual_feeding_minutes", int, identity, lambda backend, value: backend.settings_feeding_video(recording_length_after_manual_feeding_time=value)),
    SettingCommandSpec("feeding_video/cmd/time_automatic_recording", "feeding_video.automatic_duration", "automatic_recording_minutes", int, identity, lambda backend, value: backend.settings_feeding_video(automatic_recording=value)),
    SettingCommandSpec("feeding_video/cmd/watermark", "feeding_video.watermark", "video_watermark_enable", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_feeding_video(video_watermark=value)),
    SettingCommandSpec("cloud_video_recording/cmd/enable", "cloud_video.enable", "cloud_video_record_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_cloud_video_recording(enable=value)),
    SettingCommandSpec("buttons_auto_lock/cmd/enable", "buttons_auto_lock.enable", "auto_change_mode", parse_mqtt_bool, lambda value: int(value), lambda backend, value: backend.settings_buttons_auto_lock(enable=value)),
    SettingCommandSpec("buttons_auto_lock/cmd/threshold", "buttons_auto_lock.threshold", "auto_threshold", int, identity, lambda backend, value: backend.settings_buttons_auto_lock(threshold=value)),
    SettingCommandSpec("sound/cmd/enable", "sound.enable", "sound_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_sound(enable=value)),
    SettingCommandSpec("sound/cmd/aging_type", "sound.mode", "sound_mode", enum_value(AgingType), aging_to_semantic, lambda backend, value: backend.settings_sound(aging_type=value)),
    SettingCommandSpec("sound/cmd/volume", "sound.volume", "volume", int, identity, lambda backend, value: backend.settings_sound(volume=PercentageInt(value))),
    SettingCommandSpec("button_lights/cmd/enable", "button_lights.enable", "light_switch", parse_mqtt_bool, bool_to_enabled, lambda backend, value: backend.settings_button_lights(enable=value)),
    SettingCommandSpec("button_lights/cmd/aging_type", "button_lights.mode", "button_lights_mode", enum_value(AgingType), aging_to_semantic, lambda backend, value: backend.settings_button_lights(aging_type=value)),
    SettingCommandSpec("food/cmd/bowl_mode", "food.bowl_mode", "bowl_mode", enum_value(BowlMode), lambda value: "single_bowl" if value == BowlMode.SINGLE_BOWL else "dual_bowl", lambda backend, value: backend.settings_bowl_mode(mode=value)),
)
