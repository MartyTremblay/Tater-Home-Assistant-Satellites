"""Wake-audio boundary handling for Home Assistant Assist streams."""

from __future__ import annotations

from collections import deque
from typing import Any

WAKE_PREROLL_BOUNDARY_VERSION = 1


def _version(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class WakePrerollGate:
    """Keep local wake audio out of Home Assistant command STT and VAD."""

    def __init__(self, *, max_buffered_chunks: int = 256) -> None:
        if max_buffered_chunks < 1:
            raise ValueError("max_buffered_chunks must be positive")
        self._buffered: deque[bytes] = deque(maxlen=max_buffered_chunks)
        self._boundary_expected = False
        self._boundary_received = True

    @property
    def waiting_for_boundary(self) -> bool:
        """Return whether wake pre-roll is currently being held."""
        return self._boundary_expected and not self._boundary_received

    def start(self, boundary_version: Any) -> None:
        """Start one stream, enabling the gate only for marker-aware firmware."""
        self._buffered.clear()
        self._boundary_expected = (
            _version(boundary_version) >= WAKE_PREROLL_BOUNDARY_VERSION
        )
        self._boundary_received = not self._boundary_expected

    def add_audio(self, data: bytes) -> tuple[bytes, ...]:
        """Return audio ready for Assist, or hold wake pre-roll until the marker."""
        chunk = bytes(data or b"")
        if not chunk:
            return ()
        if self._boundary_received:
            return (chunk,)
        self._buffered.append(chunk)
        return ()

    def mark_boundary(self, kind: Any, version: Any) -> bool:
        """Open the gate at the ordered firmware wake/command boundary."""
        if not self.waiting_for_boundary:
            return False
        if str(kind or "").strip().lower() != "wake_preroll_end":
            return False
        if _version(version) < WAKE_PREROLL_BOUNDARY_VERSION:
            return False
        self._buffered.clear()
        self._boundary_received = True
        return True

    def finish(self, *, abort: bool) -> tuple[bytes, ...]:
        """Finish the stream, failing open if an advertised marker never arrived."""
        fallback = (
            tuple(self._buffered)
            if not abort and self.waiting_for_boundary
            else ()
        )
        self._buffered.clear()
        self._boundary_expected = False
        self._boundary_received = True
        return fallback
