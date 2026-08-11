import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "petlibro-local" / "render_config.py"
TEMPLATES = ROOT / "petlibro-local" / "templates"
SPEC = importlib.util.spec_from_file_location("render_config", MODULE_PATH)
render_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render_config)


class RenderConfigTests(unittest.TestCase):
    def test_addon_manifest_uses_prebuilt_amd64_image(self):
        manifest = (ROOT / "petlibro-local" / "config.yaml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "image: ghcr.io/tannerln7/ha-addon-petlibro-local\n", manifest
        )
        self.assertIn("arch:\n  - amd64\n", manifest)

    def options(self, *, devices=None):
        options = copy.deepcopy(render_config.DEFAULTS)
        options.update(
            {
                "mqtt_host": "192.0.2.10",
                "mqtt_username": "example-user",
                "mqtt_password": "example-password",
                "verbose_logs": True,
                "enable_debug_dumps": True,
                "devices": devices or [],
            }
        )
        return options

    @staticmethod
    def manual_device(serial="EXAMPLE123"):
        return {
            "name": "petlibro_feeder",
            "product": "PLAF203",
            "serial": serial,
            "uid": "PLAF20300000000ABCD0",
            "ip_address": "192.0.2.100",
        }

    def render_all(self, options, data_dir):
        source = data_dir / "plaf203.py"
        metadata = data_dir / "camera_metadata.py"
        discovery = data_dir / "device_discovery.py"
        logging_source = data_dir / "petlibro_logging.py"
        for path in (source, metadata, discovery, logging_source):
            path.write_text("# synthetic app source\n", encoding="utf-8")
        environment = {
            "PETLIBRO_APP_SOURCE": str(source),
            "PETLIBRO_CAMERA_METADATA_SOURCE": str(metadata),
            "PETLIBRO_DEVICE_DISCOVERY_SOURCE": str(discovery),
            "PETLIBRO_LOGGING_SOURCE": str(logging_source),
        }
        go2rtc_changed = render_config.render_go2rtc(options, data_dir, TEMPLATES)
        with patch.dict(os.environ, environment):
            appdaemon_changed = render_config.render_appdaemon(
                options, data_dir, TEMPLATES
            )
        return go2rtc_changed, appdaemon_changed

    def test_renders_discovery_and_direct_ip_stream_without_leaking_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            stale = data_dir / "petlibro_camera_status_petlibro_feeder.json"
            stale.write_text('{"status":"stale"}\n', encoding="utf-8")
            options = self.options(devices=[self.manual_device()])
            render_config.validate(options)
            self.assertEqual((True, True), self.render_all(options, data_dir))

            go2rtc = (data_dir / "go2rtc.yaml").read_text(encoding="utf-8")
            apps = (data_dir / "apps.yaml").read_text(encoding="utf-8")
            appdaemon = (data_dir / "appdaemon.yaml").read_text(encoding="utf-8")
            registry = (data_dir / "devices.json").read_text(encoding="utf-8")
            self.assertIn("petlibro://192.0.2.100?", go2rtc)
            self.assertNotIn("subnet=", go2rtc)
            self.assertIn("ack=hybrid", go2rtc)
            self.assertIn(
                "status_file=%2Fdata%2Fpetlibro_camera_status_petlibro_feeder.json",
                go2rtc,
            )
            self.assertIn("dump_c2d_plain=%2Fdata%2Fpetlibro_c2d_petlibro_feeder.dat", go2rtc)
            self.assertIn("petlibro_discovery:", apps)
            self.assertIn("plaf203_example123:", apps)
            self.assertIn('client_id: "petlibro_local_backend"', appdaemon)
            self.assertNotIn("example-password", registry)
            self.assertNotIn("example-user", registry)
            saved_device = json.loads(registry)["devices"]["PLAF203/EXAMPLE123"]
            self.assertTrue(saved_device["ready"]["stream_configured"])
            self.assertFalse(stale.exists())
            self.assertEqual(0o600, (data_dir / "devices.json").stat().st_mode & 0o777)
            self.assertEqual(
                0o600, (data_dir / "appdaemon-secrets.yaml").stat().st_mode & 0o777
            )

    def test_renders_multiple_resolved_streams(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            second = self.manual_device("SECOND456")
            second.update(
                name="kitchen_feeder",
                uid="PLAF20300000000EFGH1",
                ip_address="192.0.2.101",
            )
            options = self.options(devices=[self.manual_device(), second])
            render_config.validate(options)
            self.render_all(options, data_dir)
            go2rtc = (data_dir / "go2rtc.yaml").read_text(encoding="utf-8")
            apps = (data_dir / "apps.yaml").read_text(encoding="utf-8")
            self.assertIn("petlibro_feeder:", go2rtc)
            self.assertIn("kitchen_feeder:", go2rtc)
            self.assertIn("plaf203_example123:", apps)
            self.assertIn("plaf203_second456:", apps)

    def test_unchanged_render_does_not_rewrite_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            options = self.options(devices=[self.manual_device()])
            self.assertEqual((True, True), self.render_all(options, data_dir))
            live_status = data_dir / "petlibro_camera_status_petlibro_feeder.json"
            live_status.write_text('{"status":"online"}\n', encoding="utf-8")
            self.assertEqual((False, False), self.render_all(options, data_dir))
            self.assertTrue(live_status.exists())

    def test_migrates_legacy_options_and_ignores_unknown_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            options = self.options()
            options.update(
                device_ip="192.0.2.100",
                product="PLAF203",
                serial="EXAMPLE123",
                uid="PLAF20300000000ABCD0",
                product_secret="must-not-leak",
            )
            (data_dir / "options.json").write_text(json.dumps(options), encoding="utf-8")
            loaded = render_config.load_options(data_dir)
            render_config.validate(loaded)
            self.render_all(loaded, data_dir)
            generated = "\n".join(
                path.read_text(encoding="utf-8")
                for path in data_dir.glob("*.yaml")
            )
            self.assertIn("petlibro://192.0.2.100?", generated)
            self.assertNotIn("must-not-leak", generated)

    def test_loads_discovery_first_home_assistant_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            raw = {"mqtt_host": "core-mosquitto", "lan_cidr": "10.20.0.0/16"}
            (data_dir / "options.json").write_text(json.dumps(raw), encoding="utf-8")
            loaded = render_config.load_options(data_dir)
            render_config.validate(loaded)
            self.assertTrue(loaded["device_discovery"])
            self.assertEqual([], loaded["devices"])
            self.assertEqual("10.20.0.0/16", loaded["lan_cidr"])

    def test_log_level_validation_and_legacy_verbose_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            (data_dir / "options.json").write_text(
                json.dumps({"verbose_logs": True}), encoding="utf-8"
            )
            loaded = render_config.load_options(data_dir)
            self.assertEqual("debug", loaded["log_level"])

        options = self.options()
        options["log_level"] = "noisy"
        with self.assertRaisesRegex(ValueError, "log_level"):
            render_config.validate(options)

    def test_debug_is_bounded_and_trace_enables_targeted_protocol_traces(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            options = self.options(devices=[self.manual_device()])
            options["log_level"] = "debug"
            render_config.validate(options)
            self.render_all(options, data_dir)
            go2rtc = (data_dir / "go2rtc.yaml").read_text(encoding="utf-8")
            self.assertIn("verbose=1", go2rtc)
            self.assertNotIn("trace_packets=1", go2rtc)
            self.assertFalse((data_dir / ".verbose_logs").exists())

            options["log_level"] = "trace"
            self.render_all(options, data_dir)
            go2rtc = (data_dir / "go2rtc.yaml").read_text(encoding="utf-8")
            self.assertIn("trace_packets=1", go2rtc)
            self.assertIn("trace_ack=1", go2rtc)

    def test_empty_registry_starts_discovery_without_a_camera_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            options = self.options()
            render_config.validate(options)
            self.render_all(options, data_dir)
            self.assertIn(
                "streams:\n  {}",
                (data_dir / "go2rtc.yaml").read_text(encoding="utf-8"),
            )
            apps = (data_dir / "apps.yaml").read_text(encoding="utf-8")
            self.assertIn("petlibro_discovery:", apps)
            self.assertNotIn("class: Plaf203", apps)

    def test_rejects_invalid_manual_uid_without_echoing_value(self):
        device = self.manual_device()
        device["uid"] = "too-short"
        with self.assertRaisesRegex(ValueError, r"devices\[0\]\.uid"):
            render_config.validate(self.options(devices=[device]))

    def test_rejects_oversized_or_non_ipv4_discovery_network(self):
        options = self.options()
        options["lan_cidr"] = "10.0.0.0/8"
        with self.assertRaisesRegex(ValueError, "no larger than /16"):
            render_config.validate(options)
        options["lan_cidr"] = "2001:db8::/64"
        with self.assertRaisesRegex(ValueError, "IPv4"):
            render_config.validate(options)

    def test_disabling_camera_metadata_omits_status_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            options = self.options(devices=[self.manual_device()])
            options["publish_camera_metadata"] = False
            render_config.validate(options)
            self.render_all(options, data_dir)
            self.assertNotIn(
                "status_file", (data_dir / "go2rtc.yaml").read_text(encoding="utf-8")
            )
            self.assertIn(
                "publish_camera_metadata: false",
                (data_dir / "apps.yaml").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
