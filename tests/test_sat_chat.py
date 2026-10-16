from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SAT_CHAT_PATH = ROOT / "custom_components" / "tater_satellite" / "sat_chat.py"
MANAGER_PATH = ROOT / "custom_components" / "tater_satellite" / "manager.py"

SPEC = importlib.util.spec_from_file_location("tater_satellite_sat_chat", SAT_CHAT_PATH)
assert SPEC is not None and SPEC.loader is not None
sat_chat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sat_chat)


class FakeManager:
    def __init__(self) -> None:
        self.runtimes: dict[str, FakeRuntime] = {}


class FakeRuntime:
    def __init__(
        self,
        manager: FakeManager,
        device_id: str,
        *,
        board: str = "checkers",
        capabilities: dict | None = None,
    ) -> None:
        self.manager = manager
        self.device_id = device_id
        self.name = device_id.replace("-", " ").title()
        self.room = self.name
        self.board = board
        self.connected = True
        self.capabilities = capabilities or {
            "sat_chat_audio": True,
            "sat_chat_video": True,
            "sat_chat_can_initiate": True,
            "sat_chat_can_end": True,
            "sat_chat_ui": True,
        }
        self.sent: list[tuple[str, dict]] = []
        self.binary: list[bytes] = []
        self.block_next_binary = False
        self.binary_started = asyncio.Event()
        self.binary_release = asyncio.Event()
        self.two_binary_frames = asyncio.Event()
        manager.runtimes[device_id] = self

    async def async_send(self, kind: str, payload: dict) -> bool:
        if not self.connected:
            return False
        self.sent.append((kind, dict(payload)))
        return True

    async def async_send_bytes(self, data: bytes) -> bool:
        if not self.connected:
            return False
        if self.block_next_binary:
            self.block_next_binary = False
            self.binary_started.set()
            await self.binary_release.wait()
        self.binary.append(bytes(data))
        if len(self.binary) >= 2:
            self.two_binary_frames.set()
        return True


def _messages(runtime: FakeRuntime, kind: str) -> list[dict]:
    return [payload for message_type, payload in runtime.sent if message_type == kind]


class SatChatCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.manager = FakeManager()
        self.kitchen = FakeRuntime(self.manager, "kitchen")
        self.office = FakeRuntime(self.manager, "office")
        self.coordinator = sat_chat.TaterSatChatCoordinator(self.manager)

    async def asyncTearDown(self) -> None:
        await self.coordinator.async_shutdown()

    async def test_video_call_publishes_directory_and_receiver_profile(self) -> None:
        result = await self.coordinator.async_start(
            self.kitchen, "office", mode="video"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["call"]["state"], "active")
        self.assertNotIn("_video_profiles", result["call"])
        kitchen_started = _messages(self.kitchen, "sat_chat.started")[0]
        office_started = _messages(self.office, "sat_chat.started")[0]
        self.assertEqual(kitchen_started["video_send_profile"], "echo-low")
        self.assertEqual(office_started["video_send_profile"], "echo-low")

        kitchen_directory = _messages(self.kitchen, "sat_chat.directory")[-1]
        self.assertEqual(kitchen_directory["participant_count"], 1)
        self.assertEqual(kitchen_directory["participants"][0]["id"], "office")
        self.assertTrue(kitchen_directory["participants"][0]["busy"])
        self.assertEqual(kitchen_directory["call"]["id"], result["call"]["id"])

    async def test_call_audio_routes_to_peer_and_not_to_assist(self) -> None:
        await self.coordinator.async_start(self.kitchen, "office", mode="audio")
        pcm = b"\x01\x02" * 320

        handled = await self.coordinator.async_route_binary(self.kitchen, pcm)

        self.assertTrue(handled)
        self.assertEqual(self.office.binary, [pcm])

    async def test_video_queue_keeps_only_newest_waiting_frame(self) -> None:
        await self.coordinator.async_start(self.kitchen, "office", mode="video")
        self.office.block_next_binary = True
        frames = [
            sat_chat.VIDEO_FRAME_PREFIX + b"\xff\xd8" + bytes([value]) * 8
            for value in (1, 2, 3)
        ]

        await self.coordinator.async_route_binary(self.kitchen, frames[0])
        await asyncio.wait_for(self.office.binary_started.wait(), timeout=1)
        await self.coordinator.async_route_binary(self.kitchen, frames[1])
        await self.coordinator.async_route_binary(self.kitchen, frames[2])
        self.office.binary_release.set()
        await asyncio.wait_for(self.office.two_binary_frames.wait(), timeout=1)

        self.assertEqual(self.office.binary, [frames[0], frames[2]])

    async def test_tagged_video_never_falls_through_as_assist_audio(self) -> None:
        await self.coordinator.async_start(self.kitchen, "office", mode="audio")
        tagged_jpeg = sat_chat.VIDEO_FRAME_PREFIX + b"\xff\xd8jpeg"

        handled = await self.coordinator.async_route_binary(
            self.kitchen, tagged_jpeg
        )

        self.assertTrue(handled)
        self.assertEqual(self.office.binary, [])

    async def test_disconnect_ends_call_and_updates_remaining_peer(self) -> None:
        result = await self.coordinator.async_start(
            self.kitchen, "office", mode="video"
        )
        self.office.sent.clear()
        self.kitchen.connected = False

        await self.coordinator.async_handle_disconnect(self.kitchen)

        ended = _messages(self.office, "sat_chat.ended")
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["id"], result["call"]["id"])
        self.assertEqual(ended[0]["reason"], "disconnected")
        directory = _messages(self.office, "sat_chat.directory")[-1]
        self.assertEqual(directory["participants"], [])
        self.assertEqual(self.coordinator.snapshot()["active_call_count"], 0)


class SatChatManagerIntegrationTests(unittest.TestCase):
    def test_websocket_routes_sat_chat_before_other_binary_consumers(self) -> None:
        source = MANAGER_PATH.read_text(encoding="utf-8")
        binary_handler = source.split(
            "elif frame.type == WSMsgType.BINARY:", 1
        )[1].split("elif frame.type in", 1)[0]

        self.assertLess(
            binary_handler.index("self.sat_chat.async_route_binary"),
            binary_handler.index("self.intercom.add_audio"),
        )
        self.assertLess(
            binary_handler.index("self.sat_chat.async_route_binary"),
            binary_handler.index("is_wake_verifier_packet"),
        )
        self.assertLess(
            binary_handler.index("self.sat_chat.async_route_binary"),
            binary_handler.index("device_audio"),
        )
        self.assertIn('"sat_chat": True', source)
        self.assertIn('"sat_chat_audio": True', source)
        self.assertIn("async def async_send_bytes", source)
        self.assertIn("MAX_SAT_CHAT_FRAME_BYTES", source)


if __name__ == "__main__":
    unittest.main()
