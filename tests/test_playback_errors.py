from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANAGER_PATH = ROOT / "custom_components" / "tater_satellite" / "manager.py"


class PlaybackErrorReportingTests(unittest.TestCase):
    def test_satellite_playback_error_is_preserved_and_logged(self) -> None:
        source = MANAGER_PATH.read_text(encoding="utf-8")

        self.assertIn('text(payload.get("error"))', source)
        self.assertIn('self.add_log("error", message, kind="playback.finished")', source)
        self.assertIn('_LOGGER.warning("%s on %s", message, self.name)', source)


if __name__ == "__main__":
    unittest.main()
