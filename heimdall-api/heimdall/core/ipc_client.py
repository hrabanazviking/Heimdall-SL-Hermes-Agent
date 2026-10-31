"""IPC client for Heimdall.Core — §7.2 NDJSON over Unix sockets.

Two sockets, as designed:

- Command socket (default ``/tmp/heimdall.sock``): request-response,
  one NDJSON line out, one NDJSON line back.
- Event socket (default ``/tmp/heimdall.sock.events``): server-push,
  one NDJSON event per line, read until the Core closes it.

Synchronous and standard-library only: the seam's consumers drive it
from their own loop, and tests can stand up a fake Core with a plain
``socketserver`` — no viewer, no grid, no login needed to prove the
transport. An async facade, if wanted, belongs in a later slice and
must wrap this contract, not replace it.
"""
from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .models import Command, SeamEvent, parse_ndjson_line

DEFAULT_COMMAND_SOCKET = "/tmp/heimdall.sock"
DEFAULT_EVENT_SOCKET = "/tmp/heimdall.sock.events"


class IpcError(RuntimeError):
    """The Core could not be reached or answered nonsense."""


class IpcClient:
    def __init__(
        self,
        command_socket: str = DEFAULT_COMMAND_SOCKET,
        event_socket: str = DEFAULT_EVENT_SOCKET,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self.command_socket = command_socket
        self.event_socket = event_socket
        self.timeout_s = timeout_s

    # -- commands -------------------------------------------------
    def send_command(self, command: Command) -> dict[str, Any]:
        """Send one command; return Core's §7.2 response dict."""
        if not Path(self.command_socket).exists():
            raise IpcError(f"command socket not present: {self.command_socket}")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_s)
                sock.connect(self.command_socket)
                sock.sendall(command.to_ipc_line())
                reply = self._read_one_line(sock)
        except OSError as exc:
            raise IpcError(f"command IPC failed: {exc}") from exc
        parsed = parse_ndjson_line(reply)
        if parsed is None:
            raise IpcError("command IPC returned a malformed response")
        return parsed

    # -- events ---------------------------------------------------
    def events(self) -> Iterator[SeamEvent]:
        """Yield normalised events from the event socket until close.

        Malformed lines and unknown event names are skipped (the
        dispatcher dead-letters unknowns it is handed directly; on
        this transport a line we cannot even name is dropped, never
        guessed at).
        """
        if not Path(self.event_socket).exists():
            raise IpcError(f"event socket not present: {self.event_socket}")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_s)
                sock.connect(self.event_socket)
                buffer = b""
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        wire = parse_ndjson_line(line)
                        if wire is None:
                            continue
                        event = SeamEvent.from_wire(wire)
                        if event is not None:
                            yield event
        except OSError as exc:
            raise IpcError(f"event IPC failed: {exc}") from exc

    @staticmethod
    def _read_one_line(sock: socket.socket) -> bytes:
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        return data.split(b"\n", 1)[0]
