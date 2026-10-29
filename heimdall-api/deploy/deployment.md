# Deployment — Heimdall Bifröst API (Phase 2 slice 4, offline part)

**Status of this note:** slice 4's *offline* part is complete — the
single-process runtime (`heimdall/runtime.py`) that wires governor +
dispatcher (+ optional WYRD sink) + command router over the NDJSON IPC
transport + the §7.1 WebSocket server into one process, plus this
systemd user-unit template. Actual deployment has **not** happened
yet; what remains pending is listed honestly below.

## What runs

```sh
# from the heimdall-api/ directory of a checkout of this repo
HEIMDALL_WS_SECRET=<out-of-band> \
HEIMDALL_WYRD_SINK=<module:Class> \
python3 -m heimdall.runtime
```

or under the user unit in this directory (`heimdall-api.service`),
which reads both variables from an `EnvironmentFile` at
`~/.config/heimdall/heimdall.env` (mode 600).

Fail-closed behaviour, by design:

- No `HEIMDALL_WS_SECRET` → the runtime refuses to start (slice-3 auth
  behaviour; there is no unauthenticated mode).
- A configured WYRD sink that fails to import/instantiate → the
  runtime refuses to start. It never silently runs sinkless.
- Sinkless running exists only as an explicit testing posture:
  `HEIMDALL_ALLOW_SINKLESS=1`.

## Still pending (not done, not claimed)

- **Actual deployment on purplelongship** — the unit has not been
  installed and the service is not running there. Purplelongship is
  shared with the Aesir campaign; installation and any load/thermal
  check happen at deployment time, at the coordinator's word.
- **Secret provisioning** — `HEIMDALL_WS_SECRET` must be provisioned
  out of band by the operator and placed only in the host-side
  `EnvironmentFile`. The secret itself is **never** in this repo, in
  the unit file, or in any commit.
- **The C# Heimdall.Core leg** — still pending: Phase 2 slice 2 is
  blocked (no .NET SDK on the VM or purplelongship, native install
  decision pending), so there is no Core process for the IPC
  transport to talk to yet, and no event socket to pump from.
- **Any Second Life login** — Phase 1 stays blocked: credentials are
  available only through the Secure Vault flow and none were
  available. No login has been attempted.

Until the Core leg exists, the runtime's provable surface is the one
its tests exercise: assembly with fake transports/sinks, governor-first
command routing, dispatcher→sink event flow, and fail-closed startup.
