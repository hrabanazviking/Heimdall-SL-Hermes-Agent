# Heimdall.Core — design notes (Phase 2, slice 2)

The Second Life body: a headless LibreMetaverse client that speaks
the §7.2 NDJSON seam to the Python Bifröst API in `heimdall-api/`.

## Layout

- `src/Program.cs` — entry point; builds the host, runs until signalled.
- `src/CoreHost.cs` — assembles session + IPC server, wires §7.3 events
  onto the event socket, and owns the credentials-gated startup login.
- `src/CoreInfo.cs` — real version facts (runtime, LibreMetaverse
  assembly) reported by the `status` command.
- `src/CoreConfig.cs` — environment-only configuration.
- `src/IpcServer.cs` — Unix command socket (`/tmp/heimdall.sock`) +
  event socket (`/tmp/heimdall.sock.events`), one JSON object per line.
- `src/CommandProcessor.cs` — §7.2 command dispatch and its two
  structural refusals (no passwords over IPC; `move_to` refused, not
  faked).
- `src/SlSession.cs` — the real client: login/logout, local chat,
  IM + group IM, region change, friend online/offline, appearance-set,
  object properties, teleport, sit/stand, ~1 Hz self-position,
  disconnect — emitted under the §7.3 Core event names.
- `src/SimulatedSession.cs` — offline fixture session (explicit
  `--simulated` / `HEIMDALL_SIMULATED=1`); every event it emits is
  stamped `"simulated": true`.
- `tests/` — the IPC contract proven over real Unix sockets against
  the simulated session, the real session's offline fail-closed
  behaviour (`RealSessionTests.cs`: status / login-no-credentials /
  say / unknown command), and `core_ipc_integration.py`, which drives
  the built Core process from the Python side's own `IpcClient`
  (heimdall-api) in both real-no-credentials and simulated modes.

## Version pin

LibreMetaverse **2.6.4**, the pin in
`Heimdall_Architecture_Game_Plan.md` §6.2. Verified by reflection on
the build host 2026-10-29: the 2.6.4 assemblies keep the
`OpenMetaverse` namespaces, and the GridClient / Network / Self /
Friends / Appearance / Objects surface matches the corpus sketches
(one correction: 2.6.4's appearance signal is `AppearanceSet`; there
is no `AppearanceChanged` event — it is mapped onto the §7.3 name).
The current latest is 3.3.0, whose line split the packages; nothing
in the corpus targets it, so the pin stands until a corpus-wide
review says otherwise.

## Commands (§7.2)

`status` (aliases `get_status`, `ping`), `login`, `logout`, `say`
(alias `chat`), `im`, `group_im`, `teleport`, `sit`, `stand`,
`set_mode`, `rlv_status`. Unknown commands fail closed with
`unknown_command`; grid commands before login return `not_logged_in`;
`login` with no env credentials returns `credentials_unavailable`
without any network login attempt; a command carrying a password is
refused (`password_not_accepted_over_ipc`).

## Environment

| Variable | Meaning |
|---|---|
| `HEIMDALL_CMD_SOCK` | command socket path (default `/tmp/heimdall.sock`; legacy alias `HEIMDALL_CMD_SOCKET`) |
| `HEIMDALL_EVENT_SOCK` | event socket path (default command path + `.events`; legacy alias `HEIMDALL_EVT_SOCKET`) |
| `HEIMDALL_SL_FIRST_NAME` / `HEIMDALL_SL_LAST_NAME` / `HEIMDALL_SL_PASSWORD` | account credentials, provisioned out-of-band by the operator (Secure Vault flow) — never a file, never IPC, never chat |
| `HEIMDALL_SL_LOGIN_URI` | login URI (default: the Agni login endpoint) |
| `HEIMDALL_SL_START` | start location (default `last`) |
| `HEIMDALL_SIMULATED` | `1` selects the offline fixture session |

Without credentials the Core still runs: `status` answers (state `not_logged_in`, with real runtime /
LibreMetaverse versions) and `login` fails closed with
`credentials_unavailable`. That is deliberate — the
seam must be provable while the login spike waits on credentials.

## Not in this slice (honest scope)

RLV (no engine attached; `rlv_status` says so), walking automation
(`move_to` refused), parcel tracking, avatar appeared/left, voice,
LSL companion. Each lands in its own roadmap phase.
