from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "custom_components" / "tater_satellite" / "settings.py"
PANEL_PATH = (
    ROOT
    / "custom_components"
    / "tater_satellite"
    / "frontend"
    / "tater-satellite-panel.js"
)
SPEC = importlib.util.spec_from_file_location(
    "tater_satellite_echo_screen_settings", SETTINGS_PATH
)
assert SPEC is not None and SPEC.loader is not None
settings = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings)


class EchoScreenPowerSettingsTests(unittest.TestCase):
    @staticmethod
    def capabilities() -> dict[str, object]:
        return {"screen": True, "screen_power_management_version": 1}

    def test_all_echo_screen_boards_are_capability_gated(self) -> None:
        for board in ("checkers", "crown", "athena", "a8h3n2", "rook"):
            self.assertTrue(settings.board_supports_screen_settings(board), board)
            self.assertFalse(
                settings.supports_screen_power_management(board, {"screen": True}),
                board,
            )
            self.assertTrue(
                settings.supports_screen_power_management(
                    board, self.capabilities()
                ),
                board,
            )

    def test_values_are_normalized_for_echo_screen_firmware(self) -> None:
        normalized = settings.normalize_settings(
            {
                "screen_auto_brightness_enabled": "yes",
                "screen_auto_brightness_minimum": 99,
                "screen_night_display": "clock",
            }
        )
        self.assertTrue(normalized["screen_auto_brightness_enabled"])
        self.assertEqual(normalized["screen_auto_brightness_minimum"], 50)
        self.assertEqual(normalized["screen_night_display"], "red_clock")
        self.assertEqual(settings.ECHO_SCREEN_DEFAULTS["screen_brightness"], 35)
        self.assertEqual(
            settings.ECHO_SCREEN_DEFAULTS["screen_night_brightness"], 3
        )

    def test_capable_athena_receives_complete_screen_payload(self) -> None:
        values = settings.normalize_settings(
            {
                "screen_brightness": 42,
                "screen_auto_brightness_enabled": True,
                "screen_auto_brightness_minimum": 8,
                "screen_night_mode_enabled": True,
                "screen_night_start": "21:30",
                "screen_night_end": "06:15",
                "screen_night_display": "red_clock",
                "screen_night_brightness": 3,
            }
        )
        payload = settings.firmware_payload(
            values,
            board="athena",
            capabilities=self.capabilities(),
            local_time_seconds=123,
        )
        self.assertEqual(payload["screen_brightness"], 42)
        self.assertTrue(payload["screen_auto_brightness_enabled"])
        self.assertEqual(payload["screen_auto_brightness_minimum"], 8)
        self.assertEqual(payload["screen_night_display"], "red_clock")
        self.assertNotIn("screen_local_time_seconds", payload)

    def test_old_echo_and_s3_keep_safe_distinct_contracts(self) -> None:
        values = settings.normalize_settings({})
        old_echo = settings.firmware_payload(
            values, board="athena", capabilities={"screen": True}
        )
        s3 = settings.firmware_payload(
            values, board="s3_box", local_time_seconds=10
        )
        self.assertNotIn("screen_brightness", old_echo)
        self.assertIn("screen_brightness", s3)
        self.assertIn("screen_local_time_seconds", s3)
        self.assertNotIn("screen_auto_brightness_enabled", s3)
        self.assertNotIn("screen_night_display", s3)

    def test_panel_schema_and_capability_filter_cover_echo_displays(self) -> None:
        section = next(
            row for row in settings.SETTINGS_SCHEMA if row["section"] == "display"
        )
        self.assertTrue(
            {"checkers", "crown", "athena", "rook"}.issubset(
                section["include_boards"]
            )
        )
        self.assertEqual(
            section["required_capability"], "screen_power_management_version"
        )
        keys = {field["key"] for field in section["fields"]}
        self.assertTrue(
            {
                "screen_auto_brightness_enabled",
                "screen_auto_brightness_minimum",
                "screen_night_display",
            }.issubset(keys)
        )
        panel = PANEL_PATH.read_text(encoding="utf-8")
        self.assertIn("capabilityRequirementMet", panel)


if __name__ == "__main__":
    unittest.main()
