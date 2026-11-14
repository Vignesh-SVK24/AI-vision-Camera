"""Unit tests verifying go2rtc local-only configuration and credential injection."""

from __future__ import annotations

import importlib
import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GO2RTC_CONFIG = PROJECT_ROOT / "config" / "go2rtc.yaml"


def _load_run_go2rtc():
    if "dotenv" not in sys.modules:
        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *_args, **_kwargs: False
        sys.modules["dotenv"] = fake_dotenv
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    return importlib.import_module("tools.run_go2rtc")


class TestGo2RtcSecurityAndConfig(unittest.TestCase):
    def test_go2rtc_yaml_keeps_api_local_and_disables_rtsp_output(self) -> None:
        text = GO2RTC_CONFIG.read_text(encoding="utf-8")
        self.assertIn('listen: "127.0.0.1:1984"', text)
        self.assertIn('listen: ""', text)
        self.assertIn("${GO2RTC_CAMERA_USER}:${GO2RTC_CAMERA_PASS}@${CAMERA_IP}", text)
        self.assertNotIn("change_me", text)
        self.assertNotIn("0.0.0.0", text)

    def test_build_go2rtc_env_url_encodes_special_chars_and_defaults_local(self) -> None:
        mod = _load_run_go2rtc()
        env, missing = mod.build_go2rtc_env(
            {
                "CAMERA_IP": "192.168.1.50",
                "CAMERA_USER": "admin@home",
                "CAMERA_PASS": "p@ss:w#rd/1?",
            }
        )
        self.assertEqual(missing, [])
        self.assertEqual(env["GO2RTC_CAMERA_USER"], "admin%40home")
        self.assertEqual(env["GO2RTC_CAMERA_PASS"], "p%40ss%3Aw%23rd%2F1%3F")
        self.assertEqual(env["GO2RTC_WEBRTC_LISTEN"], "127.0.0.1:8555")
        self.assertEqual(env["GO2RTC_WEBRTC_CANDIDATE"], "stun:8555")

    def test_build_go2rtc_env_reports_missing_credentials(self) -> None:
        mod = _load_run_go2rtc()
        _env, missing = mod.build_go2rtc_env({"CAMERA_IP": "192.168.1.50"})
        self.assertEqual(missing, ["CAMERA_USER", "CAMERA_PASS"])


if __name__ == "__main__":
    unittest.main()
