import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "petlibro-local" / "render_config.py"
SPEC = importlib.util.spec_from_file_location("render_config", MODULE_PATH)
render_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render_config)


class RenderConfigTests(unittest.TestCase):
    def options(self):
        return {
            "mqtt_host": "192.168.1.10",
            "mqtt_port": 1883,
            "mqtt_username": "example-user",
            "mqtt_password": "example-password",
            "device_ip": "192.168.1.100",
            "product": "PLAF203",
            "serial": "YOUR_DEVICE_SERIAL",
            "uid": "PLAF20300000000ABCD0",
            "go2rtc_stream_name": "petlibro_feeder",
            "camera_quality": "hd",
            "ack_mode": "hybrid",
            "send_delay_ctrl": True,
            "hd_probe_wait_ms": 15000,
            "go2rtc_api_port": 1984,
            "go2rtc_rtsp_port": 8554,
            "go2rtc_webrtc_port": 8555,
            "verbose_logs": True,
            "enable_debug_dumps": True,
        }

    def test_renders_configs_and_protects_mqtt_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            source = data_dir / "plaf203.py"
            source.write_text("# synthetic app source\n", encoding="utf-8")
            options = self.options()
            render_config.validate(options)
            render_config.render_go2rtc(
                options, data_dir, ROOT / "petlibro-local" / "templates"
            )
            with patch.dict(os.environ, {"PETLIBRO_APP_SOURCE": str(source)}):
                render_config.render_appdaemon(
                    options, data_dir, ROOT / "petlibro-local" / "templates"
                )

            generated = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (
                    data_dir / "go2rtc.yaml",
                    data_dir / "appdaemon.yaml",
                    data_dir / "apps.yaml",
                    data_dir / "appdaemon-secrets.yaml",
                )
            )
            self.assertIn("ack=hybrid", generated)
            self.assertIn("hd_probe_wait_ms=15000", generated)
            self.assertIn("dump_c2d_plain=%2Fdata%2Fpetlibro_c2d.dat", generated)
            self.assertIn('mqtt_password: "example-password"', generated)
            self.assertEqual(
                0o600, (data_dir / "appdaemon-secrets.yaml").stat().st_mode & 0o777
            )

    def test_ignores_legacy_unknown_options(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            source = data_dir / "plaf203.py"
            source.write_text("# synthetic app source\n", encoding="utf-8")
            options = self.options()
            options["product_secret"] = "legacy-value"
            (data_dir / "options.json").write_text(
                json.dumps(options), encoding="utf-8"
            )

            loaded = render_config.load_options(data_dir)
            render_config.validate(loaded)
            render_config.render_go2rtc(
                loaded, data_dir, ROOT / "petlibro-local" / "templates"
            )
            with patch.dict(os.environ, {"PETLIBRO_APP_SOURCE": str(source)}):
                render_config.render_appdaemon(
                    loaded, data_dir, ROOT / "petlibro-local" / "templates"
                )

            generated = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (
                    data_dir / "go2rtc.yaml",
                    data_dir / "appdaemon.yaml",
                    data_dir / "apps.yaml",
                    data_dir / "appdaemon-secrets.yaml",
                )
            )
            self.assertNotIn("legacy-value", generated)

    def test_loads_home_assistant_options_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            options = self.options()
            (data_dir / "options.json").write_text(json.dumps(options), encoding="utf-8")
            loaded = render_config.load_options(data_dir)
            self.assertEqual("PLAF203", loaded["product"])
            self.assertTrue(loaded["verbose_logs"])

    def test_rejects_invalid_uid_without_echoing_secret(self):
        options = self.options()
        options["uid"] = "too-short"
        with self.assertRaisesRegex(ValueError, "uid must contain exactly 20"):
            render_config.validate(options)


if __name__ == "__main__":
    unittest.main()
