from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WAKE_AUDIO_PATH = (
    ROOT / "custom_components" / "tater_satellite" / "wake_audio.py"
)
ASSIST_PATH = (
    ROOT / "custom_components" / "tater_satellite" / "assist_satellite.py"
)
MANAGER_PATH = ROOT / "custom_components" / "tater_satellite" / "manager.py"
SPEC = importlib.util.spec_from_file_location(
    "tater_satellite_wake_audio", WAKE_AUDIO_PATH
)
assert SPEC is not None and SPEC.loader is not None
wake_audio = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wake_audio)


class WakePrerollGateTests(unittest.TestCase):
    def test_marker_discards_wake_audio_and_forwards_immediate_command(self) -> None:
        gate = wake_audio.WakePrerollGate()
        gate.start(1)

        self.assertEqual(gate.add_audio(b"hey tater"), ())
        self.assertTrue(gate.mark_boundary("wake_preroll_end", 1))
        self.assertEqual(gate.add_audio(b"play the matrix"), (b"play the matrix",))
        self.assertEqual(gate.finish(abort=False), ())

    def test_legacy_firmware_stream_is_forwarded_unchanged(self) -> None:
        gate = wake_audio.WakePrerollGate()
        gate.start(0)

        self.assertEqual(gate.add_audio(b"command"), (b"command",))
        self.assertFalse(gate.mark_boundary("wake_preroll_end", 1))

    def test_missing_marker_fails_open_on_normal_stop(self) -> None:
        gate = wake_audio.WakePrerollGate()
        gate.start("1")
        gate.add_audio(b"first")
        gate.add_audio(b"second")

        self.assertEqual(gate.finish(abort=False), (b"first", b"second"))

    def test_abort_discards_buffered_wake_audio(self) -> None:
        gate = wake_audio.WakePrerollGate()
        gate.start(1)
        gate.add_audio(b"wake")

        self.assertEqual(gate.finish(abort=True), ())

    def test_buffer_is_bounded_while_waiting_for_marker(self) -> None:
        gate = wake_audio.WakePrerollGate(max_buffered_chunks=2)
        gate.start(1)
        gate.add_audio(b"one")
        gate.add_audio(b"two")
        gate.add_audio(b"three")

        self.assertEqual(gate.finish(abort=False), (b"two", b"three"))

    def test_bridge_wires_ordered_boundary_into_assist_audio(self) -> None:
        assist = ASSIST_PATH.read_text(encoding="utf-8")
        manager = MANAGER_PATH.read_text(encoding="utf-8")

        self.assertIn("self._wake_preroll_gate.start(", assist)
        self.assertIn("self._wake_preroll_gate.add_audio(data)", assist)
        self.assertIn("def device_audio_boundary(", assist)
        self.assertIn('if kind == "voice.audio_boundary":', manager)
        self.assertIn("runtime.assist_entity.device_audio_boundary(payload)", manager)


if __name__ == "__main__":
    unittest.main()
