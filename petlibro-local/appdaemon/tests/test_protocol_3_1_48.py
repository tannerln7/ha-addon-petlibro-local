import datetime
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = [
    json.loads(line)
    for line in (ROOT / 'tests/fixtures/protocol_3_1_48.sample.jsonl').read_text().splitlines()
]


def captured(cmd, direction, predicate=lambda payload: True):
    return next(
        row for row in FIXTURE
        if row['cmd'] == cmd
        and row['direction'] == direction
        and predicate(row['payload'])
    )


def topic_suffix(row):
    return '/' + '/'.join(row['topic'].split('/')[-2:])


NTP_REQUEST = captured('NTP', 'device_to_server')
NTP_RESPONSE = captured('NTP', 'server_to_device')
NTP_SYNC_REQUEST = captured('NTP_SYNC', 'server_to_device')
NTP_SYNC_RESPONSE = captured('NTP_SYNC', 'device_to_server')
BOOT_REQUEST = captured('DEVICE_START_EVENT', 'device_to_server')
DEVICE_CONFIG_REQUEST = captured('DEVICE_CONFIG_SYNC', 'server_to_device')
DEVICE_CONFIG_RESPONSE = captured('DEVICE_CONFIG_SYNC', 'device_to_server')
GET_PLAN_REQUEST = captured('GET_FEEDING_PLAN_EVENT', 'device_to_server')
GET_PLAN_RESPONSE = captured('GET_FEEDING_PLAN_EVENT', 'server_to_device')
FEEDING_PLAN_REQUEST = captured(
    'FEEDING_PLAN_SERVICE', 'server_to_device', lambda payload: bool(payload['plans']))
FEEDING_PLAN_RESPONSE = captured(
    'FEEDING_PLAN_SERVICE', 'device_to_server', lambda payload: bool(payload['plans']))
MANUAL_FEED_REQUEST = captured('MANUAL_FEEDING_SERVICE', 'server_to_device')
MANUAL_FEED_RESPONSE = captured('MANUAL_FEEDING_SERVICE', 'device_to_server')
GRAIN_REQUEST = captured('GRAIN_OUTPUT_EVENT', 'device_to_server')
GRAIN_RESPONSE = captured('GRAIN_OUTPUT_EVENT', 'server_to_device')
ATTR_PUSH_REQUEST = captured(
    'ATTR_PUSH_EVENT', 'device_to_server', lambda payload: 'enableLight' in payload)
ATTR_PUSH_STORAGE_REQUEST = captured(
    'ATTR_PUSH_EVENT', 'device_to_server',
    lambda payload: 'sdCardFileSystem' in payload and 'bowlMode' in payload)
ATTR_PUSH_RESPONSE = captured('ATTR_PUSH_EVENT', 'server_to_device')
DEVICE_LOG_REQUEST = captured('DEVICE_LOG_REPORT_EVENT', 'device_to_server')
sys.path.insert(0, str(ROOT / 'src'))
import plaf203 as p


class FakeAd:
    def __init__(self):
        self.logs = []
        self.errors = []
        self.scheduled = []
        self.cancelled = []

    def log(self, value):
        self.logs.append(value)

    def error(self, value):
        self.errors.append(value)

    def run_in(self, callback, delay, **kwargs):
        handle = f"timer-{len(self.scheduled) + 1}"
        self.scheduled.append((handle, callback, delay, kwargs))
        return handle

    def cancel_timer(self, handle, silent):
        self.cancelled.append((handle, silent))


class FakeMqtt:
    def __init__(self):
        self.published = []

    def mqtt_publish(self, topic, payload, **kwargs):
        self.published.append((topic, json.loads(payload)))


class ProtocolCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.ad = FakeAd()
        self.mqtt = FakeMqtt()
        self.client = p.Client(self.ad, self.mqtt, 'SERIAL')

    def test_home_assistant_discovery_identity_is_unique_per_serial(self):
        first = p.HomeAssistantDiscoveryMqtt(self.mqtt, 'SERIAL_ONE')
        second = p.HomeAssistantDiscoveryMqtt(self.mqtt, 'SERIAL_TWO')

        self.assertNotEqual(
            first._device_info_get()['identifiers'],
            second._device_info_get()['identifiers'],
        )
        self.assertEqual(
            'homeassistant/sensor/plaf203_SERIAL_ONE/device_uuid/config',
            first._ha_config_topic_base_path_get('sensor', 'device_uuid'),
        )

    def test_resolution_discovery_identifies_feeder_reported_state(self):
        discovery = p.HomeAssistantDiscoveryMqtt(self.mqtt, 'SERIAL')
        discovery.discovery_issue()

        config = next(
            payload for topic, payload in self.mqtt.published
            if topic.endswith('/camera_resolution/config')
        )
        self.assertEqual('Feeder-reported camera resolution', config['name'])

    def test_bowl_mode_discovery_exposes_writable_device_configuration(self):
        discovery = p.HomeAssistantDiscoveryMqtt(self.mqtt, 'SERIAL')
        discovery.discovery_issue()

        config = next(
            payload for topic, payload in self.mqtt.published
            if topic.endswith('/food_bowl_mode/config')
        )
        self.assertEqual('Bowl configuration', config['name'])
        self.assertEqual(
            ['SINGLE_BOWL', 'DOUBLE_BOWL'],
            config['options'],
        )
        self.assertEqual(
            'plaf203/SERIAL/food/cmd/bowl_mode',
            config['command_topic'],
        )
        self.assertEqual(
            'plaf203/SERIAL/food/bowl_mode',
            config['state_topic'],
        )

    def test_ntp_and_ntp_sync_include_capture_dst_schema(self):
        response = NTP_RESPONSE['payload']
        sync_request = NTP_SYNC_REQUEST['payload']
        value = datetime.datetime.fromtimestamp(response['ts'] / 1000, ZoneInfo('America/New_York'))
        timestamp = p.Timestamp(value)

        ntp = p.NtpOut(p.Code.OK, timestamp, False).to_mqtt_payload()
        ntp_sync = p.NtpSyncOut(p.MessageId('sync-id'), timestamp).to_mqtt_payload()
        fields = ('timezone', 'timezoneOffsetSeconds', 'nextDSTOffsetSeconds',
                  'nextDSTTransitionTs', 'secondNextDSTOffsetSeconds',
                  'secondNextDSTTransitionTs')
        for field in fields:
            self.assertEqual(response[field], ntp[field])
            self.assertEqual(response[field], ntp_sync[field])
            self.assertEqual(sync_request[field], ntp_sync[field])

        parsed_ntp = p.NtpIn.from_mqtt_payload(NTP_REQUEST['payload'])
        parsed_sync = p.NtpSyncIn.from_mqtt_payload(NTP_SYNC_RESPONSE['payload'])
        self.assertEqual(NTP_REQUEST['payload']['ts'], parsed_ntp.timestamp.to_timestamp_epoch_ms())
        self.assertEqual(sync_request['msgId'], parsed_sync.message_id.data)

        fixed = p.NtpOut(p.Code.OK, p.Timestamp(value.astimezone(datetime.timezone.utc)), False).to_mqtt_payload()
        self.assertEqual(0, fixed['nextDSTTransitionTs'])
        self.assertEqual(0, fixed['secondNextDSTTransitionTs'])

    def test_boot_ack_preserves_existing_device_endpoints_by_default(self):
        calls = []
        fake_client = types.SimpleNamespace(
            device_start_event_send=lambda message: calls.append(('event', message.to_mqtt_payload())),
            device_config_sync_send=lambda message: calls.append(('service', message.to_mqtt_payload())),
        )
        backend = p.Backend()
        backend.client = fake_client
        backend.persist_feeder_mqtt = False
        backend.tutk_p2p_region = 'REGION_US'
        backend.device_info_callback = None
        backend.device_wifi_info_callback = None
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None

        boot = p.DeviceStartEventIn.from_mqtt_payload(BOOT_REQUEST['payload'])
        backend._device_start_event_cb(boot)

        self.assertEqual(['event'], [channel for channel, _ in calls])
        self.assertEqual(boot.message_id.data, calls[0][1]['msgId'])

    def test_boot_ack_precedes_explicit_feeder_mqtt_persistence(self):
        calls = []
        fake_client = types.SimpleNamespace(
            device_start_event_send=lambda message: calls.append(('event', message.to_mqtt_payload())),
            device_config_sync_send=lambda message: calls.append(('service', message.to_mqtt_payload())),
        )
        backend = p.Backend()
        backend.client = fake_client
        backend.logger = p.PetlibroLogger(self.ad, "petlibro.backend", "debug")
        backend.persist_feeder_mqtt = True
        backend.feeder_mqtt_host = 'mqtt.example.test'
        backend.feeder_mqtt_port = 1883
        backend.feeder_https_addr = 'api.example.test'
        backend.tutk_p2p_region = 'REGION_US'
        backend.device_info_callback = None
        backend.device_wifi_info_callback = None
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None

        boot = p.DeviceStartEventIn.from_mqtt_payload(BOOT_REQUEST['payload'])
        backend._device_start_event_cb(boot)

        self.assertEqual(['event', 'service'], [channel for channel, _ in calls])
        self.assertEqual(boot.message_id.data, calls[0][1]['msgId'])
        self.assertEqual([{'host': 'mqtt.example.test', 'port': 1883}], calls[1][1]['mqttAddr'])
        self.assertEqual('api.example.test', calls[1][1]['httpsAddr'])
        self.assertEqual(
            calls[1][1]['msgId'], backend.device_config_sync_pending_message_id
        )

    def test_device_config_sync_omits_unspecified_https_override(self):
        payload = p.DeviceConfigSyncOut.create(
            mqtt_addr=[p.MqttAddr('mqtt.example.test', 1883)],
            https_addr=None,
            tutk_p2p_region='REGION_US',
        ).to_mqtt_payload()

        self.assertNotIn('httpsAddr', payload)

    def test_storage_uses_entity_exists_before_creating_first_install_state(self):
        class StorageAD:
            def __init__(self):
                self.created = []

            def set_namespace(self, _namespace):
                return None

            def entity_exists(self, name, **_kwargs):
                return False

            def get_state(self, *_args, **_kwargs):
                raise AssertionError("get_state must not be used as an existence check")

            def set_state(self, name, **kwargs):
                self.created.append((name, kwargs))

        ad = StorageAD()
        storage = p.Storage(ad, 'plaf203', 'SERIAL')
        storage.initialize()
        self.assertEqual(2, len(ad.created))
        self.assertTrue(all(
            created[1]["check_existence"] is False
            for created in ad.created
        ))

    def _heartbeat_backend(self, food_plans):
        calls = []
        client = types.SimpleNamespace(
            get_config_send=lambda message: calls.append(
                ('get_config', message.to_mqtt_payload())
            ),
            attr_get_service_send=lambda message: calls.append(
                ('attr_get', message.to_mqtt_payload())
            ),
            feeding_plan_service_send=lambda message: calls.append(
                ('feeding_plan', message.to_mqtt_payload())
            ),
            ntp_sync_send=lambda message: calls.append(
                ('ntp_sync', message.to_mqtt_payload())
            ),
        )
        backend = p.Backend()
        backend.ad = self.ad
        backend.logger = p.PetlibroLogger(self.ad, "petlibro.backend", "debug")
        backend.client = client
        backend.device_serial = 'SERIAL'
        backend.food_plans = food_plans
        backend.last_heartbeat_count = 0
        backend.is_online = False
        backend.went_online_callback = None
        backend.went_offline_callback = None
        backend.ntp_sync_status_callback = None
        backend.ntp_sync_pending_message_id = None
        backend.ntp_sync_timeout_handle = None
        backend.device_info_callback = None
        backend.device_wifi_info_callback = None
        backend.heartbeat_watchdog = types.SimpleNamespace(
            reset=lambda: calls.append(('watchdog_reset', None))
        )
        return backend, calls

    def test_initial_heartbeat_drift_starts_one_correction_without_false_failure(self):
        backend, calls = self._heartbeat_backend(p.FoodPlans.create_empty())
        statuses = []
        backend.ntp_sync_status_callback = statuses.append
        heartbeat_timestamp = p.Timestamp(
            datetime.datetime(2020, 1, 2, tzinfo=datetime.timezone.utc)
        )
        heartbeat = p.HeartbeatIn(
            heartbeat_timestamp, 1, -50, p.WifiType.TYPE_0
        )
        now = p.Timestamp(datetime.datetime(2026, 8, 11, tzinfo=datetime.timezone.utc))

        with patch.object(p.Timestamp, 'now', return_value=now):
            backend._heartbeat_cb(heartbeat)

        self.assertEqual([], statuses)
        self.assertEqual([], self.ad.errors)
        self.assertFalse(
            any("device NTP synchronization failed" in message for message in self.ad.logs)
        )
        self.assertEqual(1, len([name for name, _payload in calls if name == 'ntp_sync']))
        backend._device_timestamp_sync_drift_check_and_adjust(heartbeat_timestamp)
        self.assertEqual(1, len([name for name, _payload in calls if name == 'ntp_sync']))
        self.assertTrue(backend.is_online)
        self.assertIn(('watchdog_reset', None), calls)

    def test_ntp_correction_reports_failure_only_after_bad_ack_or_timeout(self):
        backend, calls = self._heartbeat_backend(p.FoodPlans.create_empty())
        statuses = []
        backend.ntp_sync_status_callback = statuses.append
        stale = p.Timestamp(datetime.datetime(2020, 1, 2, tzinfo=datetime.timezone.utc))
        now = p.Timestamp(datetime.datetime(2026, 8, 11, tzinfo=datetime.timezone.utc))

        with patch.object(p.Timestamp, 'now', return_value=now):
            self.assertTrue(backend._request_ntp_sync())
            message_id = backend.ntp_sync_pending_message_id
            backend._ntp_sync_cb(
                p.NtpSyncIn(p.MessageId(message_id), stale, p.Code.OK)
            )

        self.assertEqual([False], statuses)
        self.assertIsNone(backend.ntp_sync_pending_message_id)
        self.assertTrue(
            any("phase=acknowledgement" in message for message in self.ad.logs)
        )

        statuses.clear()
        with patch.object(p.Timestamp, 'now', return_value=now):
            self.assertTrue(backend._request_ntp_sync())
        message_id = backend.ntp_sync_pending_message_id
        backend._ntp_sync_timeout({"message_id": message_id})
        self.assertEqual([False], statuses)
        self.assertTrue(any("phase=timeout" in message for message in self.ad.logs))

    def test_heartbeat_offline_online_transitions_are_idempotent(self):
        backend, _calls = self._heartbeat_backend(p.FoodPlans.create_empty())
        online = []
        offline = []
        backend.went_online_callback = lambda: online.append(True)
        backend.went_offline_callback = lambda: offline.append(True)

        backend._heartbeat_cb(
            p.HeartbeatIn(p.Timestamp.now(), 5, -50, p.WifiType.TYPE_0)
        )
        backend._heartbeat_watchdog_trigger()
        backend._heartbeat_watchdog_trigger()
        backend._heartbeat_cb(
            p.HeartbeatIn(p.Timestamp.now(), 1, -50, p.WifiType.TYPE_0)
        )

        self.assertEqual([True, True], online)
        self.assertEqual([True], offline)

    def test_first_heartbeat_does_not_push_unconfigured_empty_plan(self):
        backend, calls = self._heartbeat_backend(p.FoodPlans.create_empty())
        heartbeat = p.HeartbeatIn(p.Timestamp.now(), 1, -50, p.WifiType.TYPE_0)

        backend._heartbeat_cb(heartbeat)

        self.assertNotIn('feeding_plan', [name for name, _payload in calls])
        self.assertTrue(
            any(
                "automatic feeding-plan sync skipped" in message
                for message in self.ad.logs
            )
        )

    def test_explicit_plan_update_sends_non_empty_plan(self):
        backend, calls = self._heartbeat_backend(p.FoodPlans.create_empty())
        plan = p.FoodPlan(
            1,
            p.HourMinTimestamp(datetime.time(19, 0)),
            p.WeekdaySchedule.create(p.Weekday.MONDAY),
            False,
            1,
            3,
        )

        backend.food_plans_set(p.FoodPlans.create(plan))

        feeding_calls = [payload for name, payload in calls if name == 'feeding_plan']
        self.assertEqual(1, len(feeding_calls))
        self.assertEqual(1, feeding_calls[0]['plans'][0]['planId'])

    def test_explicit_empty_plan_update_can_clear_schedule(self):
        backend, calls = self._heartbeat_backend(p.FoodPlans.create_empty())

        backend.food_plans_set(p.FoodPlans.create_empty())

        feeding_calls = [payload for name, payload in calls if name == 'feeding_plan']
        self.assertEqual([[]], [payload['plans'] for payload in feeding_calls])

    def test_device_config_response_parser_and_service_post_dispatch(self):
        request = DEVICE_CONFIG_REQUEST['payload']
        outgoing = p.DeviceConfigSyncOut(
            p.MessageId(request['msgId']), p.Timestamp.from_timestamp_epoch_ms(request['ts']),
            [p.MqttAddr(**address) for address in request['mqttAddr']],
            request['httpsAddr'], request['tutkP2pRegion'])
        self.client.device_config_sync_send(outgoing)
        self.assertTrue(self.mqtt.published[0][0].endswith(topic_suffix(DEVICE_CONFIG_REQUEST)))
        self.assertEqual(request, self.mqtt.published[0][1])

        response = DEVICE_CONFIG_RESPONSE['payload']
        parsed = p.DeviceConfigSyncIn.from_mqtt_payload(response)
        self.assertEqual(response['msgId'], parsed.message_id.data)
        self.assertEqual(p.Code.OK, parsed.code)

        received = []
        self.client.device_config_sync_listen(received.append)
        self.client._mqtt_recv_service_cb('', {'payload': json.dumps(response)}, {})
        self.assertEqual(response['msgId'], received[0].message_id.data)

    def test_sparse_resolution_push_dispatches_only_camera_state(self):
        calls = []
        backend = p.Backend()
        for callback_name in (
            'settings_audio_callback',
            'settings_camera_callback',
            'settings_recording_callback',
            'settings_motion_detection_callback',
            'settings_sound_detection_callback',
            'settings_cloud_video_recording_callback',
            'settings_sound_callback',
            'settings_button_lights_callback',
            'state_power_callback',
            'state_food_callback',
            'device_sd_card_info_callback',
            'settings_feeding_video_callback',
            'settings_buttons_auto_lock_callback',
            'settings_bowl_mode_callback',
        ):
            setattr(
                backend,
                callback_name,
                lambda _name=callback_name, **kwargs: calls.append((_name, kwargs)),
            )
        acknowledgements = []
        backend.client = types.SimpleNamespace(
            attr_push_event_send=acknowledgements.append
        )
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None

        sparse = p.AttrPushEventIn.from_mqtt_payload({
            'msgId': 'resolution-only',
            'ts': p.Timestamp.now().to_timestamp_epoch_ms(),
            'resolution': 'P720',
        })
        backend._attr_push_event_cb(sparse)

        self.assertEqual(1, len(calls))
        self.assertEqual('settings_camera_callback', calls[0][0])
        self.assertEqual(p.Resolution.P720, calls[0][1]['resolution'])
        self.assertEqual(1, len(acknowledgements))

    def test_sparse_food_push_preserves_absent_boolean_fields(self):
        food_states = []
        backend = p.Backend()
        for callback_name in (
            'settings_audio_callback',
            'settings_camera_callback',
            'settings_recording_callback',
            'settings_motion_detection_callback',
            'settings_sound_detection_callback',
            'settings_cloud_video_recording_callback',
            'settings_sound_callback',
            'settings_button_lights_callback',
            'state_power_callback',
            'device_sd_card_info_callback',
            'settings_feeding_video_callback',
            'settings_buttons_auto_lock_callback',
            'settings_bowl_mode_callback',
        ):
            setattr(backend, callback_name, None)
        backend.state_food_callback = lambda **kwargs: food_states.append(kwargs)
        backend.client = types.SimpleNamespace(attr_push_event_send=lambda message: None)
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None

        sparse = p.AttrPushEventIn.from_mqtt_payload({
            'msgId': 'motor-only',
            'ts': p.Timestamp.now().to_timestamp_epoch_ms(),
            'motorState': 2,
        })
        backend._attr_push_event_cb(sparse)

        self.assertEqual([{
            'motor_state': 2,
            'outlet_blocked': None,
            'low_fill_level': None,
        }], food_states)

    def test_sparse_bowl_mode_push_dispatches_only_bowl_configuration(self):
        bowl_modes = []
        backend = p.Backend()
        for callback_name in (
            'settings_audio_callback',
            'settings_camera_callback',
            'settings_recording_callback',
            'settings_motion_detection_callback',
            'settings_sound_detection_callback',
            'settings_cloud_video_recording_callback',
            'settings_sound_callback',
            'settings_button_lights_callback',
            'state_power_callback',
            'state_food_callback',
            'device_sd_card_info_callback',
            'settings_feeding_video_callback',
            'settings_buttons_auto_lock_callback',
        ):
            setattr(backend, callback_name, None)
        backend.settings_bowl_mode_callback = (
            lambda **kwargs: bowl_modes.append(kwargs)
        )
        backend.client = types.SimpleNamespace(attr_push_event_send=lambda message: None)
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None

        sparse = p.AttrPushEventIn.from_mqtt_payload({
            'msgId': 'bowl-only',
            'ts': p.Timestamp.now().to_timestamp_epoch_ms(),
            'bowlMode': 'SINGLE_BOWL',
        })
        backend._attr_push_event_cb(sparse)

        self.assertEqual([{'mode': p.BowlMode.SINGLE_BOWL}], bowl_modes)

    def test_bowl_mode_command_serializes_attr_set_without_food_quantity(self):
        sent = []
        backend = p.Backend()
        backend.client = types.SimpleNamespace(attr_set_service_send=sent.append)

        backend.settings_bowl_mode(p.BowlMode.DOUBLE_BOWL)

        payload = sent[0].to_mqtt_payload()
        self.assertEqual('DOUBLE_BOWL', payload['bowlMode'])
        self.assertNotIn('grainNum', payload)

    def test_bowl_mode_mqtt_command_reaches_backend(self):
        selected = []
        controller = types.SimpleNamespace(
            backend=types.SimpleNamespace(
                settings_bowl_mode=lambda **kwargs: selected.append(kwargs)
            )
        )

        p.Plaf203._mqtt_cmd_food_bowl_mode_cb(
            controller,
            '',
            {'payload': 'DOUBLE_BOWL'},
            {},
        )

        self.assertEqual([{'mode': p.BowlMode.DOUBLE_BOWL}], selected)

    def test_dual_bowl_wire_alias_normalizes_to_double_bowl(self):
        parsed = p.AttrPushEventIn.from_mqtt_payload({
            'msgId': 'bowl-alias',
            'ts': p.Timestamp.now().to_timestamp_epoch_ms(),
            'bowlMode': 'DUAL_BOWL',
        })

        self.assertEqual(p.BowlMode.DOUBLE_BOWL, parsed.bowl_mode)

    def test_invalid_feeding_plan_json_logs_position_without_payload(self):
        controller = types.SimpleNamespace(
            logger=p.PetlibroLogger(self.ad, "petlibro.controller", "debug"),
        )
        invalid = '{"minute":01}'

        p.Plaf203._mqtt_cmd_food_plans(
            controller, '', {'payload': invalid}, {}
        )

        message = self.ad.logs[-1]
        self.assertIn('invalid feeding-plan JSON ignored', message)
        self.assertIn('line=1', message)
        self.assertIn('column=12', message)
        self.assertIn('payload_length=13', message)
        self.assertNotIn(invalid, message)

    def test_device_config_acknowledgement_requires_matching_pending_request(self):
        backend = p.Backend()
        backend.logger = p.PetlibroLogger(self.ad, "petlibro.backend", "debug")
        backend.persist_feeder_mqtt = True
        backend.feeder_mqtt_host = 'mqtt.example.test'
        backend.feeder_mqtt_port = 1883
        backend.device_config_sync_pending_message_id = 'expected-config-id'
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None
        errors = []
        backend._error_report = errors.append

        backend._device_config_sync_cb(p.DeviceConfigSyncIn(
            p.MessageId('stale-config-id'), p.Timestamp.now(), p.Code.OK
        ))
        self.assertEqual(
            'expected-config-id', backend.device_config_sync_pending_message_id
        )
        self.assertFalse(any(
            'persistence acknowledged' in message for message in self.ad.logs
        ))

        backend._device_config_sync_cb(p.DeviceConfigSyncIn(
            p.MessageId('expected-config-id'), p.Timestamp.now(), p.Code.OK
        ))
        self.assertIsNone(backend.device_config_sync_pending_message_id)
        self.assertTrue(any(
            'persistence acknowledged' in message for message in self.ad.logs
        ))
        self.assertEqual([], errors)

    def test_get_plan_and_grain_ack_use_event_sub_and_echo_fields(self):
        get_request = p.GetFeedingPlanEventIn.from_mqtt_payload(GET_PLAN_REQUEST['payload'])
        get_response = p.GetFeedingPlanEventOut(
            get_request.message_id, p.Timestamp.now(), p.Code.OK, [])
        self.client.get_feeding_plan_event_send(get_response)

        grain_request = p.GrainOutputEventIn.from_mqtt_payload(GRAIN_REQUEST['payload'])
        grain_response = p.GrainOutputEventOut.create(
            message_id=grain_request.message_id, code=p.Code.OK, exec_step=grain_request.exec_step)
        self.client.grain_output_event_send(grain_response)

        self.assertTrue(self.mqtt.published[0][0].endswith(topic_suffix(GET_PLAN_RESPONSE)))
        self.assertEqual(get_request.message_id.data, self.mqtt.published[0][1]['msgId'])
        self.assertTrue(self.mqtt.published[1][0].endswith(topic_suffix(GRAIN_RESPONSE)))
        self.assertEqual('GRAIN_START', self.mqtt.published[1][1]['execStep'])

    def test_feeding_plan_round_trip_and_empty_replacement(self):
        response = p.FeedingPlanServiceIn.from_mqtt_payload(FEEDING_PLAN_RESPONSE['payload'])
        self.assertIsInstance(response.plans[0].sync_time, p.Timestamp)

        request = FEEDING_PLAN_REQUEST['payload']['plans'][0]
        plan = p.FeedingPlanOut(
            request['planId'], p.HourMinTimestamp.from_mqtt_payload_value(request['executionTime']),
            p.WeekdaySchedule.create(*[p.Weekday(day) for day in request['repeatDay']]),
            request['enableAudio'], request['audioTimes'], request['grainNum'],
            p.Timestamp.from_timestamp_epoch_ms(request['syncTime']))
        payload = p.FeedingPlanServiceOut.create([plan]).to_mqtt_payload()
        self.assertNotIn('skipEndTime', payload['plans'][0])
        self.assertEqual([], p.FeedingPlanServiceOut.create([]).to_mqtt_payload()['plans'])

    def test_manual_feed_shape_matches_capture(self):
        request = MANUAL_FEED_REQUEST['payload']
        message = p.ManualFeedingServiceOut(
            p.MessageId(request['msgId']), p.Timestamp.from_timestamp_epoch_ms(request['ts']), request['grainNum'])
        self.assertEqual(request, message.to_mqtt_payload())
        parsed = p.ManualFeedingServiceIn.from_mqtt_payload(MANUAL_FEED_RESPONSE['payload'])
        self.assertEqual(p.Code.OK, parsed.code)

    def test_attr_push_capture_fields_ack_and_serializers(self):
        request = ATTR_PUSH_REQUEST['payload']
        parsed = p.AttrPushEventIn.from_mqtt_payload(request)
        self.assertFalse(parsed.disable_hardware_button)
        self.assertTrue(parsed.enable_light)

        storage = p.AttrPushEventIn.from_mqtt_payload(ATTR_PUSH_STORAGE_REQUEST['payload'])
        self.assertEqual(p.BowlMode.SINGLE_BOWL, storage.bowl_mode)
        self.assertEqual(p.SdCardFileSystem.UNKNOWN, storage.sd_card_file_system)

        self.client.attr_push_event_send(p.AttrPushEventOut.create(
            message_id=parsed.message_id, code=p.Code.OK))
        self.assertTrue(self.mqtt.published[0][0].endswith(topic_suffix(ATTR_PUSH_RESPONSE)))

        set_payload = p.AttrSetServiceOut.create(
            enable_audio=True,
            light_aging_type=p.AgingType.NON_SCHEDULED_ENABLED,
            bowl_mode=p.BowlMode.SINGLE_BOWL,
        ).to_mqtt_payload()
        self.assertIs(set_payload['enableAudio'], True)
        self.assertEqual(1, set_payload['lightAgingType'])
        self.assertEqual('SINGLE_BOWL', set_payload['bowlMode'])
        json.dumps(set_payload)

    def test_device_log_is_parsed_and_ignored_without_ack(self):
        request = DEVICE_LOG_REQUEST['payload']
        self.client._mqtt_recv_event_cb('', {'payload': json.dumps(request)}, {})
        self.assertEqual([], self.mqtt.published)
        self.assertEqual([], self.ad.errors)
        self.assertEqual([], self.ad.logs)

        self.client.logger.level = "trace"
        self.client._mqtt_recv_event_cb('', {'payload': json.dumps(request)}, {})
        self.assertIn('ignored device log report', self.ad.logs[-1])

    def test_adjacent_runtime_name_fixes(self):
        self.assertEqual(23, p.HourMinTimestamp.from_mqtt_payload_value('23:00').time.hour)
        message = p.ServerConfigPushOut(p.MessageId('id'), p.Timestamp.now(), '10')
        self.client.server_config_push_send(message)
        self.assertEqual(p.Commands.SERVER_CONFIG_PUSH, self.mqtt.published[0][1]['cmd'])


if __name__ == '__main__':
    unittest.main()
