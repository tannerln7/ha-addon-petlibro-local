import json
from pathlib import Path
import socket
import struct
import subprocess
import time
from urllib import request

import pytest

from settings_map import SETTING_COMMANDS


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_SOURCE = REPO_ROOT / "feeder-state-agent" / "plaf203_state_agent.c"
TOKEN = "synthetic-agent-test-token"


def _fnv64(data, initial=14695981039346656037):
    value = initial
    for byte in data:
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return value


def _settings_revision(state):
    ranges = (
        (0x09, 1),
        (0x10, 4),
        (0x15, 8),
        (0x21, 8),
        (0x2C, 100),
        (0x91, 17),
        (0xA3, 20),
        (0xB8, 17),
        (0xCA, 13),
        (0xE7, 1),
    )
    revision_input = bytearray()
    for offset, width in ranges:
        revision_input.extend((offset, width))
        revision_input.extend(state[offset : offset + width])
    return f"fnv64:{_fnv64(revision_input):016x}"


@pytest.fixture(scope="session")
def state_agent_binary(tmp_path_factory):
    binary = tmp_path_factory.mktemp("state-agent-build") / "plaf203-state-agent"
    subprocess.run(
        [
            "cc",
            "-Os",
            "-Wall",
            "-Wextra",
            "-std=c99",
            "-o",
            str(binary),
            str(AGENT_SOURCE),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def _state_bytes(size=236):
    state = bytearray(size)
    if size >= 0xE8:
        state[0x09] = 0
        state[0x10] = 50
        state[0x11] = 1
        state[0x12] = 20
        state[0x15] = 1
        state[0x16] = 1
        state[0x21] = 1
        state[0x22] = 1
        state[0x91] = 1
        struct.pack_into("<I", state, 0x92, 1)
        struct.pack_into("<I", state, 0x9A, 1)
        struct.pack_into("<I", state, 0x9E, 1)
        state[0xA3] = 1
        struct.pack_into("<I", state, 0xA4, 1)
        struct.pack_into("<I", state, 0xA8, 1)
        state[0xB8] = 1
        struct.pack_into("<I", state, 0xB9, 1)
        struct.pack_into("<I", state, 0xC1, 7)
        struct.pack_into("<I", state, 0xC5, 1)
        state[0xCA] = 1
        struct.pack_into("<I", state, 0xCB, 1)
        struct.pack_into("<I", state, 0xD3, 80)
    return state


def _plan_record(
    *,
    plan_id=1,
    minute=0,
    hour=11,
    one_shot=0,
    days=(1, 2, 3, 4, 5, 6, 7),
    enable_audio=1,
    audio_times=2,
    portions=10,
    execution_state=0,
    sync_time=0,
    skip_end_time=0,
    opaque=b"\x00" * 10,
):
    record = bytearray(47)
    struct.pack_into("<I", record, 0x00, plan_id)
    record[0x04] = minute
    record[0x05] = hour
    record[0x06] = one_shot
    record[0x07 : 0x07 + len(days)] = bytes(days)
    record[0x0E] = enable_audio
    record[0x0F] = audio_times
    record[0x10] = portions
    struct.pack_into("<I", record, 0x11, execution_state)
    struct.pack_into("<Q", record, 0x15, sync_time)
    struct.pack_into("<Q", record, 0x1D, skip_end_time)
    record[0x25:0x2F] = opaque
    return record


def _feed_phase(
    *,
    plan_id,
    finished,
    retried,
    phase_status,
    type_raw,
    actual,
    expected,
    exec_time,
    error_code=0,
):
    record = bytearray(31)
    struct.pack_into("<I", record, 0x00, plan_id)
    record[0x08] = finished
    record[0x09] = retried
    record[0x0A] = phase_status
    record[0x0B] = type_raw
    record[0x0C] = actual
    record[0x0D] = expected
    struct.pack_into("<Q", record, 0x0E, exec_time)
    struct.pack_into("<I", record, 0x1A, error_code)
    return record


def _write_snapshot(root, *, state=None, plans=(), feed=None, head=0, tail=0):
    (root / "attr").mkdir(parents=True)
    (root / "feed_plan").mkdir(parents=True)
    (root / "rtc").mkdir(parents=True)
    (root / "attr" / "state.bin").write_bytes(
        bytes(_state_bytes() if state is None else state)
    )
    (root / "feed_plan" / "index.bin").write_bytes(bytes([len(plans)]))
    (root / "feed_plan" / "plan.bin").write_bytes(b"".join(plans))
    (root / "feed_plan" / "rec_index_head.bin").write_bytes(bytes([head]))
    (root / "feed_plan" / "rec_index_tail.bin").write_bytes(bytes([tail]))
    (root / "feed_plan" / "err_rec_index_head.bin").write_bytes(b"\xff")
    (root / "feed_plan" / "err_rec_index_tail.bin").write_bytes(b"\xff")
    (root / "feed_plan" / "feed_rec.bin").write_bytes(
        bytes(4743) if feed is None else bytes(feed)
    )
    (root / "rtc" / "rtc_time.bin").write_bytes(b"\x00" * 8)


class RunningAgent:
    def __init__(self, binary, root):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process = subprocess.Popen(
            [
                str(binary),
                "--root",
                str(root),
                "--listen",
                f"127.0.0.1:{self.port}",
                "--token",
                TOKEN,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                self.get("/health")
                break
            except OSError:
                time.sleep(0.02)
        else:
            self.close()
            raise AssertionError("state agent did not start")

    def get(self, path):
        req = request.Request(
            self.base_url + path,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with request.urlopen(req, timeout=1) as response:
            return json.load(response)

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_state_decoder_uses_persistent_switches_and_correct_authority_classes(
    state_agent_binary, tmp_path
):
    state = _state_bytes()
    state[0x0E] = 3
    state[0x14] = 0
    state[0x15] = 1
    state[0x20] = 0
    state[0x21] = 1
    state[0x90] = 0
    state[0x91] = 1
    state[0xA2] = 0
    state[0xA3] = 1
    state[0xB7] = 0
    state[0xB8] = 1
    state[0xC9] = 0
    state[0xCA] = 1
    _write_snapshot(tmp_path, state=state)

    with RunningAgent(state_agent_binary, tmp_path) as agent:
        core = agent.get("/v1/core?raw=1")

    settings = core["settings"]
    assert settings["motor_state_raw"] == 3
    assert "sound_enable_or_mode" not in settings
    assert settings["sound_effective_cached"] == "disabled"
    assert settings["sound_switch"] == "enabled"
    assert settings["light_effective_cached"] == "disabled"
    assert settings["light_switch"] == "enabled"
    assert settings["camera_effective_cached"] == "disabled"
    assert settings["camera_switch"] == "enabled"
    assert settings["video_record_effective_cached"] == "disabled"
    assert settings["video_record_switch"] == "enabled"
    assert settings["motion_detection_effective_cached"] == "disabled"
    assert settings["motion_detection_switch"] == "enabled"
    assert settings["sound_detection_effective_cached"] == "disabled"
    assert settings["sound_detection_switch"] == "enabled"
    assert "sound_switch" in core["setting_classes"]["persistent"]
    assert "sound_effective_cached" in core["setting_classes"]["effective_cached"]
    assert "motor_state_raw" in core["setting_classes"]["runtime"]


@pytest.mark.parametrize(
    ("raw", "kind", "enabled"),
    [(0, "disabled", "disabled"), (1, "builtin", "enabled"), (2, "custom", "enabled")],
)
def test_feeding_audio_subtypes_remain_distinguishable(
    state_agent_binary, tmp_path, raw, kind, enabled
):
    state = _state_bytes()
    state[0x13] = raw
    _write_snapshot(tmp_path, state=state)
    with RunningAgent(state_agent_binary, tmp_path) as agent:
        settings = agent.get("/v1/core")["settings"]
    assert settings["feeding_audio_type"] == kind
    assert settings["feeding_audio_enabled"] == enabled


def test_unaligned_u32_fields_use_all_four_bytes_and_minutes_are_raw(
    state_agent_binary, tmp_path
):
    state = _state_bytes()
    for offset in (0x92, 0x9A, 0x9E, 0xA4, 0xA8, 0xB9, 0xC1, 0xC5, 0xCB, 0xD3):
        struct.pack_into("<I", state, offset, 0x01000001 | offset)
    state[0xB3:0xB6] = bytes([17, 29, 43])
    _write_snapshot(tmp_path, state=state)

    with RunningAgent(state_agent_binary, tmp_path) as agent:
        core = agent.get("/v1/core?raw=1")

    raw = core["settings_raw"]
    assert raw["camera_mode_u32le_0x92"] == struct.unpack_from("<I", state, 0x92)[0]
    assert raw["resolution_u32le_0x9a"] == struct.unpack_from("<I", state, 0x9A)[0]
    assert raw["night_vision_u32le_0x9e"] == struct.unpack_from("<I", state, 0x9E)[0]
    assert raw["video_record_mode_u32le_0xa4"] == struct.unpack_from("<I", state, 0xA4)[0]
    assert raw["video_record_schedule_mode_u32le_0xa8"] == struct.unpack_from("<I", state, 0xA8)[0]
    assert raw["motion_detection_mode_u32le_0xb9"] == struct.unpack_from("<I", state, 0xB9)[0]
    assert raw["motion_sensitivity_u32le_0xc1"] == struct.unpack_from("<I", state, 0xC1)[0]
    assert raw["motion_range_u32le_0xc5"] == struct.unpack_from("<I", state, 0xC5)[0]
    assert raw["sound_detection_mode_u32le_0xcb"] == struct.unpack_from("<I", state, 0xCB)[0]
    assert raw["sound_detection_sensitivity_u32le_0xd3"] == struct.unpack_from("<I", state, 0xD3)[0]
    assert core["settings"]["before_feeding_plan_minutes"] == 17
    assert core["settings"]["automatic_recording_minutes"] == 29
    assert core["settings"]["after_manual_feeding_minutes"] == 43


def test_core_decode_and_revision_are_derived_from_the_same_raw_snapshot(
    state_agent_binary, tmp_path
):
    state = _state_bytes()
    state[0x10] = 73
    state[0x21] = 0
    struct.pack_into("<I", state, 0xA4, 2)
    _write_snapshot(tmp_path, state=state)

    with RunningAgent(state_agent_binary, tmp_path) as agent:
        core = agent.get("/v1/core?raw=1")

    raw_state = bytes.fromhex(core["raw"]["attr/state.bin"]["hex"])
    assert raw_state == bytes(state)
    assert core["settings"]["volume"] == raw_state[0x10]
    assert core["settings"]["sound_switch"] == "disabled"
    assert core["settings"]["local_camera_recording_type"] == "motion_detection"
    assert core["revisions"]["settings_rev"] == _settings_revision(raw_state)

    persistent = set(core["setting_classes"]["persistent"])
    assert {spec.state_field for spec in SETTING_COMMANDS} <= persistent


@pytest.mark.parametrize("size", [235, 237])
def test_state_bin_requires_exact_236_byte_snapshot(state_agent_binary, tmp_path, size):
    _write_snapshot(tmp_path, state=_state_bytes(size))
    with RunningAgent(state_agent_binary, tmp_path) as agent:
        health = agent.get("/health")
        core = agent.get("/v1/core")
    assert health["ok"] is False
    assert health["state_decode"]["actual_size"] == size
    assert core["ok"] is False
    assert core["expected_size"] == 236


def test_plan_decoder_models_full_47_byte_record(state_agent_binary, tmp_path):
    plan = _plan_record(
        plan_id=0x01020304,
        minute=37,
        hour=22,
        one_shot=1,
        days=(),
        enable_audio=1,
        audio_times=4,
        portions=13,
        execution_state=0xA1B2C3D4,
        sync_time=0x0102030405060708,
        skip_end_time=0x1112131415161718,
        opaque=bytes(range(10)),
    )
    _write_snapshot(tmp_path, plans=(plan,))

    with RunningAgent(state_agent_binary, tmp_path) as agent:
        core = agent.get("/v1/core?raw=1")

    decoded = core["plans"]["semantic_records"][0]
    assert core["plans"]["record_size"] == 47
    assert decoded["id"] == 0x01020304
    assert decoded["one_shot"] is True
    assert decoded["execution_state"] == 0xA1B2C3D4
    assert decoded["sync_time"] == 0x0102030405060708
    assert decoded["skip_end_time"] == 0x1112131415161718
    assert decoded["opaque_hex"].replace(" ", "") == bytes(range(10)).hex()


def test_feed_event_queue_uses_51_slots_three_phases_u64_and_wraparound(
    state_agent_binary, tmp_path
):
    feed = bytearray(4743)
    for order, slot_index in enumerate((50, 0)):
        for phase in range(3):
            record = _feed_phase(
                plan_id=0x01020304 + order,
                finished=phase == 1,
                retried=phase == 2,
                phase_status=8 + phase,
                type_raw=phase + 1,
                actual=9 + phase,
                expected=12,
                exec_time=0x0102030405060708 + phase,
                error_code=0xAABBCCDD if phase == 2 else 0,
            )
            start = slot_index * 93 + phase * 31
            feed[start : start + 31] = record
    _write_snapshot(tmp_path, feed=feed, head=50, tail=1)

    with RunningAgent(state_agent_binary, tmp_path) as agent:
        result = agent.get("/v1/feed-events?raw=1")

    assert result["semantics"] == "pending_outbound_events_not_history"
    assert [event["slot_index"] for event in result["events"]] == [50, 0]
    phases = result["events"][0]["phases"]
    assert [phase["phase"] for phase in phases] == [
        "GRAIN_START",
        "GRAIN_END",
        "GRAIN_BLOCKING",
    ]
    assert phases[0]["exec_time"] == 0x0102030405060708
    assert [phase["type"] for phase in phases] == [
        "scheduled",
        "remote_manual",
        "local_button_manual",
    ]
    assert phases[2]["error_code"] == 0xAABBCCDD
