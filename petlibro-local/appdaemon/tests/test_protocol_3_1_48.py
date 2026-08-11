import datetime
import json
import sys
import types
import unittest
from pathlib import Path
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

    def log(self, value):
        self.logs.append(value)

    def error(self, value):
        self.errors.append(value)


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

    def test_boot_ack_precedes_config_sync_with_configurable_endpoints(self):
        calls = []
        fake_client = types.SimpleNamespace(
            device_start_event_send=lambda message: calls.append(('event', message.to_mqtt_payload())),
            device_config_sync_send=lambda message: calls.append(('service', message.to_mqtt_payload())),
        )
        backend = p.Backend()
        backend.client = fake_client
        backend.mqtt_host = 'local-broker'
        backend.mqtt_port = 1883
        backend.https_addr = 'local-api'
        backend.tutk_p2p_region = 'REGION_US'
        backend.device_info_callback = None
        backend.device_wifi_info_callback = None
        backend._device_timestamp_sync_drift_check_and_adjust = lambda timestamp: None

        boot = p.DeviceStartEventIn.from_mqtt_payload(BOOT_REQUEST['payload'])
        backend._device_start_event_cb(boot)

        self.assertEqual(['event', 'service'], [channel for channel, _ in calls])
        self.assertEqual(boot.message_id.data, calls[0][1]['msgId'])
        self.assertEqual([{'host': 'local-broker', 'port': 1883}], calls[1][1]['mqttAddr'])
        self.assertEqual('local-api', calls[1][1]['httpsAddr'])

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
        self.assertEqual('SINGLE_BOWL', storage.bowl_mode)
        self.assertEqual(p.SdCardFileSystem.UNKNOWN, storage.sd_card_file_system)

        self.client.attr_push_event_send(p.AttrPushEventOut.create(
            message_id=parsed.message_id, code=p.Code.OK))
        self.assertTrue(self.mqtt.published[0][0].endswith(topic_suffix(ATTR_PUSH_RESPONSE)))

        set_payload = p.AttrSetServiceOut.create(
            enable_audio=True, light_aging_type=p.AgingType.NON_SCHEDULED_ENABLED).to_mqtt_payload()
        self.assertIs(set_payload['enableAudio'], True)
        self.assertEqual(1, set_payload['lightAgingType'])
        json.dumps(set_payload)

    def test_device_log_is_parsed_and_ignored_without_ack(self):
        request = DEVICE_LOG_REQUEST['payload']
        self.client._mqtt_recv_event_cb('', {'payload': json.dumps(request)}, {})
        self.assertEqual([], self.mqtt.published)
        self.assertEqual([], self.ad.errors)
        self.assertIn('Ignored DEVICE_LOG_REPORT_EVENT', self.ad.logs[-1])

    def test_adjacent_runtime_name_fixes(self):
        self.assertEqual(23, p.HourMinTimestamp.from_mqtt_payload_value('23:00').time.hour)
        message = p.ServerConfigPushOut(p.MessageId('id'), p.Timestamp.now(), '10')
        self.client.server_config_push_send(message)
        self.assertEqual(p.Commands.SERVER_CONFIG_PUSH, self.mqtt.published[0][1]['cmd'])


if __name__ == '__main__':
    unittest.main()
