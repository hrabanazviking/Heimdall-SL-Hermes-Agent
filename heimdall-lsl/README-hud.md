# Heimdall Companion HUD — HUD1 wire format

The companion HUD (`heimdall_companion_hud.lsl`, design doc 03 §7.6,
doc 07 §5) is a worn LSL sense organ: it samples the wearer's
situation on a 60-second timer and POSTs one compact digest line to a
configured off-world Heimdall endpoint via `llHTTPRequest`. The
Python mirror of this format is
`heimdall-api/heimdall/awareness/hud_ingest.py` — **one format, both
ends**; this file is the spec the two are written against, and the
test-suite asserts the format tag matches on both sides.

> **Honest status:** Phase 1 (real grid login) is blocked — Secure
> Vault credentials are unavailable — so this script has **not** been
> worn, compiled, or tested in-world. It is code-side complete only.
> The `// VERIFY` comments in the script are the pre-flight list for
> the first in-world session.

## Format: `HUD1`

A single line (no trailing newline required), pipe-separated
`key=value` fields in exactly this order:

```
HUD1|v=1|ts=<unix>|region=<esc>|fps=<float>|dil=<float>|agents=<int>|apos=<vec>;<vec>;...|self_pos=<vec>|self_flags=<int>|env_day_len=<int>|env_day_off=<int>|env_sec=<float>|parcel=<esc>|parcel_area=<int>|parcel_flags=<int>|parcel_id=<uuid>|sensor_count=<int>|sensor_pos=<vec>;<vec>;...|free_mem=<int>[|auth=<secret>]
```

- `HUD1` is the format tag; `v=1` is the payload version. Both must
  match exactly or the parser rejects the payload whole.
- `<vec>` is the LSL `(string)vector` form `<x, y, z>`.
- `<esc>` is `llEscapeURL()` output (region and parcel names); the
  parser URL-decodes them.
- Position lists (`apos`, `sensor_pos`) are `;`-separated vectors and
  may be empty (`apos=`).

### Fields and their in-world sources

| Field | Source (LSL) | Meaning / out-world use |
|---|---|---|
| `ts` | `llGetUnixTime()` | Digest time (seconds). |
| `region` | `llGetRegionName()` | Region name → place record. |
| `fps` | `llGetRegionFPS()` | Simulator FPS → conditions record. |
| `dil` | `llGetRegionTimeDilation()` | 0.0–1.0, 1.0 = realtime → conditions. |
| `agents` | `llGetAgentList(AGENT_LIST_REGION, [])` | Full region census count (≤ 100) → conditions (reported count). |
| `apos` | `llGetObjectDetails(key, [OBJECT_POS])` per census key, capped at 32 | **Anonymous** positions → PresenceTracker **coarse tier only**. Keys are reduced to positions in-world; no key or name ever crosses the wire. |
| `self_pos` | `llGetPos()` | Wearer's position → conditions/self input. |
| `self_flags` | `llGetAgentInfo(llGetOwner())` | Wearer state bitfield (doc 03 §2.3) → conditions. |
| `env_day_len`, `env_day_off`, `env_sec` | `llGetEnvironment(pos, [ENVIRONMENT_DAYINFO])` | Virtual day length / GMT offset / seconds since virtual midnight → conditions. |
| `parcel`, `parcel_area`, `parcel_id` | `llGetParcelDetails(pos, [PARCEL_DETAILS_NAME, PARCEL_DETAILS_AREA, PARCEL_DETAILS_ID])` | Parcel identity → place record. |
| `parcel_flags` | `llGetParcelFlags(pos)` | Parcel rule bitfield → place record. |
| `sensor_count`, `sensor_pos` | `llSensor("", NULL_KEY, AGENT\|ACTIVE\|PASSIVE, 32.0, PI)` + `llDetectedPos(i)` | Nearby agents/objects sweep (LSL caps at 16 nearest) → conditions. Sensor positions are anonymous points, never identities. |
| `free_mem` | `llGetFreeMemory()` | Companion vital sign → conditions. |
| `auth` (optional) | `CONFIG_SECRET` in the script | Body-carried shared secret (doc 07 §2.2/§5.3). Present only when configured; the parser drops it and never stores it. Ships empty. |

## Laws this format carries

- **Coarse-tier only (doc 03 §2.1/§7.7).** Census data is anonymous:
  it may populate count + positions in the coarse presence tier and
  must never fabricate names or identities. The full-fidelity tier is
  never touched by HUD data.
- **Fail closed.** The parser validates the whole payload before
  applying anything: a malformed digest is rejected whole (and
  counted), never partially applied.
- **Attributed sensor data.** A HUD report is a sensor report, not a
  canonical fact merely because the HUD reported it (the Phase I0
  utterance/fact separation applied to senses). Ingest results are
  labelled `source="hud"` throughout.
- **Disclosure (doc 05).** The HUD itself is the in-world
  scripted-agent transparency anchor: it carries the disclosure as
  hover text at all times and answers touches with the same text.
- **Throttle (doc 07 §3.2).** One POST per 60 s, far inside the
  `llHTTPRequest` budgets (25/20 s per object). A throttled
  (`NULL_KEY`) call drops that digest and waits for the next cycle;
  email is deliberately not a fallback for a worn attachment
  (doc 07 §2.3/§5.5).

## Ingest side

`HudIngest` (`heimdall-api/heimdall/awareness/hud_ingest.py`) is
transport-agnostic: `ingest(payload)` is a callable the runtime can
mount behind any HTTP endpoint. It parses, then — only on full
success — feeds the coarse tier, and returns a `HudIngestResult`
carrying the conditions record, the place record, and HUD events
(`hud_census`, `hud_conditions`, `hud_place`) for the caller to route.
