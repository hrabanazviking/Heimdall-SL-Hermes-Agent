#!/usr/bin/env python3
"""Offline IPC integration proof: Python seam client <-> real C# Core.

Proves, with NO grid contact and NO credentials:
  1. the Core process starts in offline (real-session) mode;
  2. heimdall-api's IpcClient (the Python side of the §7.2 contract)
     connects and `status` returns state=not_logged_in with real
     runtime/library versions;
  3. `login` with no env credentials fails closed
     (credentials_unavailable) — the Core attempts no network login;
  4. `say` before login returns the contract error (not_logged_in)
     and an unknown command fails closed (unknown_command);
  5. in a second, explicitly simulated run, an injected chat event
     emerges on the event socket and is normalised by the Python
     client to the canonical seam event `chat`.

Usage: core_ipc_integration.py <path-to-Heimdall.Core.dll-or-binary>
Exit code 0 = all checks passed. Prints one PASS line per check.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve()
API_ROOT = HERE.parents[2] / "heimdall-api"  # worktree's heimdall-api/
sys.path.insert(0, str(API_ROOT))

from heimdall.core.ipc_client import IpcClient, IpcError  # noqa: E402
from heimdall.core.models import Command  # noqa: E402


def wait_sockets(cmd_path: str, evt_path: str, proc: subprocess.Popen) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"core exited early rc={proc.returncode}")
        if Path(cmd_path).exists() and Path(evt_path).exists():
            return
        time.sleep(0.05)
    raise SystemExit("core sockets never appeared")


def start_core(binary: list[str], tag: str, simulated: bool) -> tuple[subprocess.Popen, IpcClient]:
    cmd_path = f"/tmp/heimdall-it-{tag}.sock"
    evt_path = cmd_path + ".events"
    for p in (cmd_path, evt_path):
        Path(p).unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("HEIMDALL_SL_")}
    env["HEIMDALL_CMD_SOCK"] = cmd_path
    env["HEIMDALL_EVENT_SOCK"] = evt_path
    if simulated:
        env["HEIMDALL_SIMULATED"] = "1"
    proc = subprocess.Popen(binary, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    wait_sockets(cmd_path, evt_path, proc)
    return proc, IpcClient(cmd_path, evt_path, timeout_s=5.0)


def main() -> int:
    target = sys.argv[1]
    binary = [target] if os.access(target, os.X_OK) and not target.endswith(".dll") \
        else ["dotnet", target]
    # When run on purplelongship, prefer the device dotnet explicitly.
    dotnet = os.environ.get("HEIMDALL_DOTNET")
    if target.endswith(".dll") and dotnet:
        binary = [dotnet, target]

    checks = 0

    # --- run 1: real session, no credentials -----------------------
    proc, client = start_core(binary, "real", simulated=False)
    try:
        reply = client.send_command(Command(id="1", cmd="status"))
        assert reply["status"] == "ok", reply
        result = reply["result"]
        assert result["state"] == "not_logged_in", result
        assert result["logged_in"] is False and result["simulated"] is False, result
        assert result["libremetaverse_version"], result
        assert result["dotnet_runtime"], result
        print(f"PASS status not_logged_in (libremetaverse={result['libremetaverse_version']}, runtime={result['dotnet_runtime']})")
        checks += 1

        reply = client.send_command(Command(id="2", cmd="login"))
        assert reply["status"] == "error", reply
        assert reply["error"] == "credentials_unavailable", reply
        print("PASS login without env credentials fails closed (credentials_unavailable, no login attempt)")
        checks += 1

        reply = client.send_command(Command(id="3", cmd="say", params={"msg": "hi", "channel": 0}))
        assert reply["status"] == "error" and reply["error"] == "not_logged_in", reply
        print("PASS say before login returns not_logged_in (never fake success)")
        checks += 1

        reply = client.send_command(Command(id="4", cmd="definitely_unknown"))
        assert reply["status"] == "error" and reply["error"] == "unknown_command", reply
        print("PASS unknown command fails closed (unknown_command)")
        checks += 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # --- run 2: simulated session, injected chat event -------------
    proc, client = start_core(binary, "sim", simulated=True)
    try:
        seen: list = []
        errors: list = []

        def drain() -> None:
            try:
                for event in client.events():
                    seen.append(event)
                    if event.name == "chat":
                        return
            except IpcError as exc:  # timeout after enough events
                errors.append(str(exc))

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        time.sleep(0.3)  # let the event reader connect first
        reply = client.send_command(Command(id="1", cmd="login"))
        assert reply["status"] == "ok", reply
        reply = client.send_command(Command(id="2", cmd="say", params={"msg": "Hello!", "channel": 0}))
        assert reply["status"] == "ok", reply
        thread.join(timeout=8)
        chat_events = [e for e in seen if e.name == "chat"]
        assert chat_events, f"no chat event seen; events={[e.name for e in seen]} errors={errors}"
        assert chat_events[0].payload["msg"] == "Simulated reply: I heard you.", chat_events[0]
        assert chat_events[0].raw_name == "ChatReceived", chat_events[0]
        print("PASS simulated chat event emerged on event socket, normalised ChatReceived -> chat")
        checks += 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    print(f"INTEGRATION OK: {checks}/5 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
