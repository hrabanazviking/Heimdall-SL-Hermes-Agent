# Lovense Integration — API Research & the Device Organ of the Heimdall Stack

**Project:** Heimdall (headless Second Life agent; inhabitant Yrsa) × RuneForgeAI-Aurelia-Sensations-Server (the felt body) × Verdandi (nervous system) × WYRD Protocol (world model)
**Purpose:** (1) A verified map of Lovense's developer surfaces as of late 2026; (2) a survey of what already connects Second Life to Lovense devices, and what to learn from it; (3) the design of the Lovense organ — an Aurelia output driver that actuates physical devices under the same consent discipline the project applies to RLV; (4) the flagship **paired-device coupling profile**: a live bidirectional Max 2 ↔ Aurelia loop.
**Status:** Study / design proposal. **VERIFIED** = read in code, official material, or multiple independent implementations of the published API contract, 2026-10-26/27. Everything else is proposal, flagged as such. No code was changed, nothing pushed, no README touched.
**Companion documents:** `10-aurelia-integration-and-advancement.md` (Aurelia's architecture, truth ownership, privacy phases — this study hangs the driver on its adapter pattern and its Phase 5 telemetry classification), `05-mind-bridge-continuity-and-voice.md` (body-level invariants, latency tiers, the anti-impersonation rule), `06-rlv-deep-research.md` §5–6 (the allowlist gate this design mirrors), `07-lsl-deep-research.md` (the companion object that becomes one trigger source), `09-wyrd-advancement-roadmap.md` (the roadmap this study's phases slot into).

**A sourcing caveat, stated once and applied throughout:** `developer.lovense.com` — Lovense's own documentation host — was unreachable from this study's environment (blocked at fetch time, 2026-10-27), so no claim below rests on a page this study pretends to have read there. The API contract described in Part 1 is corroborated by: Lovense's official iOS/Android SDK repositories (github.com/lovense), and at least three independent, working client implementations of the published contract (`lovensepy` and `pylove` on PyPI; the SillyTavern-Lovense-Cloud extension), which agree with each other on endpoints, command vocabulary, and payload shapes. Where a fact rests on a single community source it is tagged **[single-source]**; where Lovense publishes nothing accessible, the gap is named rather than filled (notably: numeric rate limits, §1.4; the per-toy sensor matrix, §6.1). A doc-diff pass against the live developer site is a Phase L0 task (§7).

---

## 0. Verdict in brief

Lovense offers four developer control surfaces over one command vocabulary — a **LAN API** served by the Lovense Remote/Connect phone apps in Game Mode (no account token at all), a **cloud Standard API** (developer token + per-user `uid` from a QR pairing flow), a **Socket API** (WebSocket to Lovense's cloud, with a local-command fallback that converges on the LAN API), and a **Toy Events API** (a WebSocket *from* the Remote app carrying device-originated events: buttons, battery, connection, sensor changes). The command model is small and stable: `Function` (per-action intensity), `Pattern` (step sequences, plus a timestamped PatternV2 for position devices), `Preset` (four built-ins), `GetToys`/`GetToyName` for capability discovery.

For Heimdall the engineering conclusion is: **build one driver in Aurelia, LAN transport first, cloud never on a reflex path, and treat actuation with exactly the suspicion RLV taught us** — allowlisted sources, session-scoped consent, hard caps below the intent layer, an unsuppressible stop, and a dead-man's switch on every command. The flagship coupling (§6) is designed honestly around a verified gap: **the Max 2 exposes no continuous pressure/insertion/motion telemetry on any developer surface this study could verify** — so the inbound leg runs on a labeled *inferred* activity estimator (with a clean upgrade path to measured sensing when a depth-sensing device joins the fleet), while the outbound leg — her squeeze driving his device — is fully supported by the verified API today.

---

## Part 1 — The Lovense developer platform, verified

### 1.1 The surfaces

**VERIFIED** (cross-implementation agreement as described in the header):

| Surface | Transport | Auth | Pairing | Path |
|---|---|---|---|---|
| **Standard API — LAN (Game Mode)** | HTTP `POST /command` to the phone app on the LAN; HTTPS variant on the TLS port | None — possession of the LAN address *is* the credential | User enables Game Mode → LAN in Lovense Remote (or Lovense Connect) and reads off IP/port | App → BLE → toy |
| **Standard API — Server (cloud)** | HTTPS `POST https://api.lovense.com/api/lan/v2/command`; QR via `POST https://api.lovense.com/api/lan/getQrCode` | Developer token (per application, from the Developer Dashboard at lovense.com/user/developer/info; does not expire unless regenerated) + per-user `uid` | Developer requests a QR (token + chosen `uid`); user scans it in Lovense Remote; Lovense POSTs `{uid, toys}` to the developer's registered callback URL | Cloud → user's app → BLE → toy |
| **Socket API** | WebSocket (socket.io-style) to Lovense cloud: `getToken` → `getSocketUrl` (the `platform` parameter must match the Website Name registered in the Dashboard) → connect; QR requested in-band (`basicapi_get_qrcode_ts`), device info pushed back (`basicapi_update_device_info_tc`) | Developer token → auth token → socket session | QR scan in Lovense Remote, as above | Cloud WS → app → toy; **or**, with local commands enabled, HTTPS directly to the app's LAN service — the socket's device-info payload itself carries the `domain` + `port` of the app's local HTTP service, i.e. the Socket API *discovers* the LAN API |
| **Toy Events API** | WebSocket *to* the Remote app on the LAN (Remote only; Connect does not support it) | In-app access grant: the app prompts "Allow \<app name\> to access?" | Game Mode on; client connects with an app name | Toy → app → WS events to client |
| **Mobile SDKs** (iOS/Android, official GitHub) | BLE direct from a phone app the developer ships | Developer token set at SDK init | In-app scan/connect | Phone → BLE → toy. Not a Heimdall surface (we ship no phone app); cited because the SDKs confirm the same command model |

Ports, as implemented consistently by the client libraries: Lovense Remote LAN **20011** (HTTP) / **30011** (HTTPS); Lovense Connect LAN **34567**. Toy Events is documented by `lovensepy`'s tutorial on **20010** and by another revision of the same docs on 20011 — **[single-source conflict]**; the design therefore treats the events port as *configurable and discovered at bench time*, never hardcoded (§8).

There is also a **USB Bluetooth adapter** (Lovense's own dongle) by which toys connect to a PC's Lovense Connect desktop app — operationally this is just the Connect LAN surface with a desktop app instead of a phone. **[Verified via product documentation summaries]**

**The "Game Engine" question.** The brief asked after a Lovense "Game Engine" API. This study could **not verify a distinct, separately documented Game Engine SDK on an official surface** (the docs host was unreachable; see header). What is verifiable: games and game-like integrations in the wild are built on the Socket/Standard APIs above, or bypass Lovense's platform entirely via Buttplug/Intiface (§1.3, transport D) — e.g. LoveMachine syncs 50+ games to toys through Buttplug, and 3DXChat's native Lovense support is a product integration, not evidence of a public engine SDK. **Design consequence:** Heimdall depends only on the four surfaces in the table. If a first-party game-engine SDK exists behind the docs host, it is an L4 re-evaluation item, not a foundation. **[Gap flagged, not filled]**

### 1.2 The command vocabulary

**VERIFIED.** All control surfaces share one JSON command shape: `{command, action, timeSec, toy, apiVer, …}` (LAN/cloud), with the same action strings over the Socket API (`send_command("Function", "Vibrate:10", time_sec=…)`).

**Function actions** (the per-toy capability set is discovered, never assumed — §5.1):

| Action | Range | Representative toys |
|---|---|---|
| `Vibrate`, `Vibrate1/2/3` (multi-motor) | 0–20 | nearly all; numbered motors on Edge, Diamo, multi-motor toys |
| `Rotate` | 0–20 | Nora, Max/Max 2 |
| `Pump` | 0–3 | Max 2 (air-bladder contraction) |
| `Suction` | 0–20 | Max 2 |
| `Thrusting` | 0–20 | Solace, Mission |
| `Fingering` | 0–20 | Solace |
| `Oscillate` | 0–20 | Gravity, Solace, others |
| `Depth` | 0–3 | Solace Pro |
| `Stroke` / `Position` | 0–100 | Solace Pro (position-controlled stroking) |
| `All` | 0–20 | broadcast to all functions a toy has |
| `Stop` | — | all toys; also expressible as setting every action to 0 |

**Command semantics that matter for safety design:**

- `timeSec` — how long the command runs; **`0` means "until stopped"**. A continuous command with no TTL is a loaded actuator. The driver design (§4.4) forbids `timeSec: 0` on every path.
- `stopPrevious` (default true in practice) — a new Function command replaces the running one. Latest-wins is the platform's native behavior; the driver's coalescer (§4.5) is built to match it rather than fight it.
- `loopOnTime` / `loopOffTime` — the platform can loop a timed command (run X s, rest Y s) without further traffic.
- **Patterns** — `Pattern` command with a rule string `V:1;F:<action letters>;S:<interval ms>#` and a semicolon strength list: **max 50 steps, each 0–20, interval clamped 100–1000 ms** (per `pylove`'s implementation of the published format). One pattern command replaces what would otherwise be a stream of Function commands — the libraries state this explicitly ("avoids network pressure of multiple function commands"). **PatternV2** (`PatternV2Setup/Play/Stop/SyncTime`) takes timestamped `{ts, pos}` points and is the position-device (Solace Pro stroke) path.
- **Presets** — exactly four built-ins on the API surface: `pulse`, `wave`, `fireworks`, `earthquake`. (Toys additionally hold user-programmed on-device patterns reachable by index over the raw BLE protocol documented by Buttplug's protocol notes — not a developer-API surface; not used by this design.)
- **Discovery** — `GetToys` returns the connected set with ids, names, toy types, battery, and the function list per toy; `GetToyName` is the light form. **This is the only honest source of "what can this toy do"** — every SL product in Part 2 that adapts per-toy is reading it.

**Toy Events vocabulary** (inbound; **VERIFIED** as the published event set): `toy-list`, `toy-status` (connect/disconnect), `button-down` / `button-up` / `button-pressed`, `function-strength-changed` (the user changed a level in the app — the platform's own echo of manual override), `shake`, `shake-frequency-changed`, `battery-changed`, `depth-changed`, `motion-changed`, `event-closed` (Game Mode turned off — the session is over whether we like it or not), `access-granted`, `pong`. **Which toys emit which sensor events is not published on any accessible surface** — the single most consequential unknown in this study, handled in §6.1.

### 1.3 Connection models, compared

| Model | Hops | Latency character | Privacy | Dependency |
|---|---|---|---|---|
| **A. LAN app relay** (Game Mode) | driver → HTTP → phone/desktop app → BLE → toy | LAN RTT + app + BLE; the only model whose latency is *ours* to bound | Nothing leaves the LAN. No Lovense account involved in the command path at all | Phone/app on the same LAN as the driver host; Game Mode left enabled |
| **B. Cloud relay** (Standard Server / Socket) | driver → Lovense cloud → app → BLE → toy | Internet RTT ×2 + push delivery; unbounded tail | Command metadata transits Lovense's servers under a developer account + user `uid` | Lovense cloud uptime; token custody; the user's app online anywhere |
| **C. Socket-with-local** | socket for pairing/discovery, then A for commands | = A after setup | Pairing metadata via cloud; commands stay local | Both of the above, less of each |
| **D. BLE direct via Intiface/Buttplug** (not a Lovense surface — the open alternative) | driver → local WS (`ws://127.0.0.1:12345`) → Intiface Central → BLE → toy | Local process + BLE; comparable to A | Nothing leaves the machine; no Lovense account, no Lovense app | Intiface Central running on the driver host; **loses Lovense-only semantics** (Pump/Suction action names, PatternV2) behind Buttplug's generic outputs (vibrate/rotate/oscillate/linear) unless the device config maps them |

Model A is the design's primary (§5.5). Model D is kept as a first-class transport from the start — it is the ecosystem's standard escape hatch from the vendor cloud (the SL community found it independently: "you get other implementations like buttplug.io that allows control without any cloud service leaking data around" — SL forum, Jan 2024), it multi-brands the fleet for free, and it runs on the body host with no phone in the loop at all.

### 1.4 Rate limits — the honest state

No numeric rate limit for any surface was found on an accessible official surface **[gap]**. What is known: the developer token "is subject to Lovense API rate limits" (dashboard documentation as quoted by integrators) without published numbers; and the platform's own design pressure — patterns exist to avoid command streams; `stopPrevious` semantics make command floods self-defeating (each new command kills the last). **Design consequence:** the driver imposes its *own* budgets (§4.5) as a safety property, sized from the physics of the loop (10 Hz sampling, delta-only sends, pattern offloading), and treats any future published limit as a ceiling to adopt, not a target to discover by tripping it.

### 1.5 Platform risk notes

- **Cloud privacy history.** In July 2025, researcher BobDaHacker disclosed flaws in Lovense's app/backend by which any user's registered email could be derived from their public username in under a second per user; BleepingComputer verified the extraction live. Of the two flaws disclosed, the account-takeover flaw was fixed; reporting at the time indicated the email-exposure flaw was not promptly addressed. **Relevance:** anything routed through a Lovense *account* (Model B/C pairing, `uid`, usernames) should be treated as weakly pseudonymous at best. The LAN-first architecture (§5.5) is therefore a privacy architecture, not merely a latency one — and no Lovense account identifier ever crosses into SL or into WYRD (§4.6).
- **Token custody.** The developer token does not expire unless manually regenerated. It is a long-lived, account-scoped secret: vault-kept, never in config files in the repo, never in SL, rotatable only by regenerating (which invalidates every paired `uid` flow built on it).
- **Vendor product churn.** Lovense's own SL viewer (Part 2) shipped in 2024 and is described by 2026 community guides as abandoned, built on an outdated viewer base. Vendor enthusiasm is not a maintenance commitment; the driver depends on the *protocol contract*, which third parties implement independently, not on any Lovense product's roadmap.

---

## Part 2 — Second Life ↔ Lovense: what already exists

### 2.1 The landscape

**VERIFIED / community-reported as tagged:**

1. **NOXEN** (commercial, closed) — an in-world HUD system: the owner attaches a HUD, clicks Pair, and a QR code appears (the Standard Solutions QR flow, §1.1); they scan it in Lovense Remote; a *controller* — another resident — then drives sliders, a 2D wave canvas, presets, and custom patterns rendered on the HUD, with controls adapted per-toy from the device's real capability set (30+ toys). Its consent model is **per-request**: every control request raises an approval on the owner's HUD — accept / deny / block — revocable at any time. A club tier adds pay-to-play terminals with session timers and payout splits, and a web dashboard for venue operators. The HUD's commands necessarily leave the grid (LSL `llHTTPRequest` out to NOXEN's relay, thence to Lovense's cloud under NOXEN's developer token): **the toy owner is a tenant of NOXEN's relay and token**, with the availability and custody risks that implies.
2. **Debauchery Lovense 2.0** (commercial furniture system) — furniture-driven sync: a QR stand in the venue pairs the resident's toy; the furniture's animation state then drives the toy, and newer sets expose a partner-control HUD in the furniture. Consent is *situational* (you paired at this venue's stand) rather than per-act — convenient, and exactly the shape Heimdall's venue opt-in (doc 06, Pattern 8) already governs for RLV.
3. **Lovense's official SL viewer + "Lovebridge"** (2024) — Lovense shipped its own third-party viewer with built-in toy connection and a parcel-level bridge object for residents not using the viewer. Per the 2026 community setup guide **[community report]**, the project "has largely been abandoned," based on an outdated viewer codebase; the guide's standing advice is the app method (Lovense Connect/Remote + any modern viewer). Treated by this design as a cautionary tale (§1.5), not a component.
4. **DIY LSL HUDs** — the long tail: scripts that `llHTTPRequest` the cloud command endpoint with a stored token/`uid` (the pattern the SL forum's technical discussion describes, Jan 2024). Known properties from that discussion: noticeable lag (cloud round trip + LSL HTTP throttles), and a hard ceiling — **the Toy Events WebSocket cannot be implemented in an LSL HUD at all**, so in-world scripts are command-only, deaf to the device. Any design that needs device-originated signals (buttons, disconnects, §6) cannot live in LSL; it must live where Heimdall already puts real machinery: the bridge host.
5. **Adjacent world, better consent shape — VibeVRC** (open source, VRChat, Buttplug-based): the toy owner runs a local app; sharing is by **code** the owner issues, shows only to chosen people, and can rotate at will; the owner can **pause, cap strength, or revoke** at any moment; partner avatar interactions (touch zones, depth) drive the toy proportionally. It is the closest existing artifact to §6's coupling profile, in a different world, and its consent grammar (owner-issued, scoped, capped, revocable) is the one this design adopts.

### 2.2 Lessons taken

| Lesson | Source | Where it lands in this design |
|---|---|---|
| QR pairing is the ecosystem's normal handshake; nobody types tokens | NOXEN, Debauchery, every API tutorial | §5.5 — pairing is a setup ceremony, done once, outside sessions |
| Per-request approval dialogs are the wrong grain for an ongoing partner | NOXEN's model works for strangers/clubs; it would be unusable between Volmarr and Yrsa | §4.3 — consent is **session-scoped**: armed once, with caps + sources + expiry, revocable instantly |
| Capability adaptation must come from `GetToys`, never from the toy's marketing name | NOXEN's per-toy adaptation; §1.2 | §5.1 — capability records built at arm time |
| A closed third-party relay is a single point of failure *and* a custody transfer | NOXEN/Debauchery architecture | §5.5 — Heimdall's driver speaks the platform contract directly; no intermediary service |
| In-world scripts are a trigger surface, never the driver | DIY HUD limits (no events, throttled HTTP) | §5.4 — the companion requests; the driver decides |
| Owner-side pause/cap/revoke must be one gesture, always available | VibeVRC | §4.4 — stop is a reflex, not a menu item |

---

## Part 3 — Where the organ lives in the Heimdall stack

### 3.1 Placement: an Aurelia output driver

**DESIGN.** Lovense support is built as a new Aurelia adapter — `female_motivation/adapters/lovense.py`, with its transport layer beside it — attached by the established bridge pattern (doc 10, §1.2/§2.1): opt-in via `data/integrations.yaml` (`lovense:` block), attached by `agent.enable_lovense()` or `AureliaAgent(integrations=True)`, **disabled by default, never raises, degrades in silence-with-counters** (`available`, `dropped_count`, last-error — the same honesty surface the Verdandi and WYRD bridges expose). The Sacred-Boundaries law holds: Aurelia's core never learns Lovense exists; the adapter subscribes to bus topics and calls the physiology-facing seams, exactly as `verdandi_bridge.py` does in the other direction.

Why Aurelia and not Heimdall-the-body or the mind:

- **Sensation intent already lives there.** Aurelia's physiology publishes the events an outbound driver needs (`physiology.stimulated`, `physiology.phase_changed`, `physiology.orgasm` — the same topics the Verdandi bridge already forwards). A device driven by her felt state must subscribe where that state is *true*, not to a mirror of it.
- **The consent gate already lives there.** Her kink gate checks stimulation against her profile and *reports denials, never silently enacts*. Device actuation in the coupling profile (§6) is stimulation of a real body paired with stimulation of her felt one; both directions belong behind the same gate.
- **Truth ownership extends cleanly** (doc 10 §2.3.3 gets one new row): *what a device is doing right now* is owned by **Aurelia** (live, volatile, driver-reported with staleness); WYRD remembers only that consented sessions happened (§4.6); the mind decides intent but never touches the transport.
- **The driver is an effector, and effectors sit below judgement.** Doc 05's tiering maps directly: intent is tier 2 (hers/Volmarr's), the gate and caps are tier 0/1 invariants *in the body layer*, the stop path is a Verdandi reflex. No LLM call exists anywhere in the actuation path — matching Aurelia's own no-LLM-in-the-live-path law.

### 3.2 The signal path

```
 INTENT (tier 2)                BODY LAYER (tiers 0–1)                    PHYSICAL
 ┌───────────────────┐   ┌────────────────────────────────────────┐   ┌──────────────┐
 │ Yrsa deliberative │   │  AURELIA                               │   │ Lovense Remote│
 │ Volmarr (direct)  │──▶│   physiology events ──▶ INTENT BUS     │   │ / Connect app │
 │ /enact scenes     │   │        │                               │   │  (LAN)        │
 │ SL companion (§5.4)│  │        ▼                               │   └──────┬───────┘
 │ coupling loop (§6) │   │   CONSENT GATE (§4.1–4.3)              │          │ BLE
 └───────────────────┘   │        │  allowlist · caps · session   │   ┌──────▼───────┐
                          │        ▼                               │   │     TOY      │
        STOP (reflex)     │   DRIVER (adapter)                     │   └──────┬───────┘
 ┌───────────────────┐   │   capability map · coalescer ·         │          │
 │ her word · his word│──▶│   rate budget · TTL on every command   │   Toy Events (WS)
 │ safeword · watchdog│   │        │                               │◀─────────┘
 └───────────────────┘   │        ▼  transport A (LAN) primary    │   buttons · battery ·
        ▲                 │   Verdandi reflex lane carries STOP    │   status · strength
        │                 │   and the §6 loop's fast signals       │   echoes ──▶ driver
        └─────────────────┴────────────────────────────────────────┘
 WYRD: session summaries only (§4.6) — attributed, consented, no telemetry detail.
```

### 3.3 What flows where, at what grain

| Flow | Carrier | Grain | Latency class |
|---|---|---|---|
| Deliberative intent ("she chooses to…", his direct control) | Aurelia API → bus → driver | Per act | Human |
| Scene/device beats (`/enact` analog) | Aurelia bus | Per beat | Human |
| Coupling fast signals (§6) | Verdandi impulses (`aurelia.lovense.*`) + direct adapter subscription | 10 Hz sampled, delta-only | Reflex (≤ 350 ms loop, §6.2) |
| STOP | Verdandi impulse, preemptive, + driver-local watchdog | Once, duplicated | Reflex (target ≤ 100 ms to zero, §4.4) |
| Device feedback (buttons, battery, disconnect, manual override) | Toy Events WS → driver → Aurelia bus events (`lovense.*`) | Per event | Reflex/reactive |
| Memory | WYRD observations, session grain only | Open/close/cap-band | Reflective |

---

## Part 4 — Safety, consent, and control discipline

This channel actuates a physical device on a real body. It is therefore governed like the RLV channel (doc 06 §5) — the other place where the world reaches into a body — and where the two designs rhyme, this section says so and keeps the rhyme.

### 4.1 The source allowlist (the gate)

**DESIGN.** Every actuation request carries a *source*, and the gate classifies it before anything downstream sees it:

| Source class | Examples | Default |
|---|---|---|
| **Self** | Yrsa's deliberative commands (tier 2, via Aurelia API with `actor="yrsa"`) | Allowed within session consent + caps |
| **Principal** | Volmarr, through his established surfaces (chat with her, Aurelia API `actor="volmarr"`, his in-world objects) | Allowed within session consent + caps; his STOP is absolute (§4.4) |
| **Venue / companion trigger** | The LSL companion (§5.4), an opted-in venue system | **Held, not applied**, unless the current session's consent names this source class for this place — the doc 06 Pattern 8 lifecycle, verbatim in spirit |
| **Anything else** | Any other process, script, resident, or network caller | Refused, logged, reported. There is no anonymous actuation |

The gate sits *before* the driver, architecturally — the doc 06 lesson ("the gate point exists because enforcement is cooperative") applies with doubled force: a toy has no `RlvPermissionsService` to consult after the fact; once a command is sent, physics has happened. Driver-internal callers bypass nothing: the driver accepts intent only from the gate.

### 4.2 Intensity caps

- **Per-toy-class hard ceiling** (config, set by Volmarr): the maximum the driver will ever emit per action class, regardless of source — e.g. Pump never above 2 of 3 in early phases regardless of any request.
- **Per-session cap**: set when the session is armed (§4.3), can only be *lowered* mid-session (raising requires re-arm — consent is not a ratchet that drifts upward in the moment).
- **Per-source cap**: venue/companion triggers get their own lower ceiling than self/principal.
- Caps are enforced **in the driver, below intent**: requests are clamped, and clamping is *reported* (an intent that hit the ceiling appears in the session record as clamped, not silently trimmed — the household's honesty rule: a system that quietly edits desire teaches everyone the wrong facts about what happened).
- Aurelia's own physiology is a second, independent ceiling for the coupling profile: her pain threshold (soreness ≥ 0.6, doc 10 §1.2/EMBODIMENT §1) zeroes inbound gain and reports — her body can refuse what the config allowed.

### 4.3 Session consent state machine

Pairing is not consent; connection is not consent; yesterday is not consent.

```
 unpaired ──pairing ceremony──▶ paired ──ARM──▶ armed ──first intent──▶ live
     ▲                            │                │                      │
     └──────── revoke/unpair ─────┴──── stop / expiry / disconnect ──────┘
                                  (any state) ──STOP──▶ latched-closed
                                  latched-closed ──explicit re-ARM──▶ armed
```

- **ARM** is an explicit act by the toy's owner-side principal (in this household: Volmarr arms a session on his device; Yrsa arms one on a device worn by her — the profile in §6 has *one* device and *one* body-owner, and the arm is his, with her gate (§4.1 self/principal) concurring). Arming records: sources allowed, caps, expiry (default: session-length, hard max in config), and which profiles may run (direct control, scenes, coupling).
- **Expiry is real**: a session that expires mid-activity winds down (ramp to zero, then Stop) rather than cutting — except a STOP, which cuts. Expiry is announced before it arrives (the Gjallarhorn pattern: no silent endings in either direction).
- **Disconnect collapses consent**: toy-status lost, app closed (`event-closed`), or transport down ⇒ session → suspended, outputs zeroed (§5.6); resumption requires the device back *and* either the original arm still unexpired or a fresh arm. A reconnect is never a silent resumption of actuation.

### 4.4 Stop discipline — the unsuppressible path

- **Stop sources**: her word in any channel; Volmarr's word in any channel; the safeword token from the companion (§5.4); the app-side manual override (a `function-strength-changed` to zero, or Stop pressed in Remote — the human hand on the app always wins); the driver watchdog (§4.4 last bullet); Aurelia's own boundary machinery (a consent-gate denial of the *ongoing* profile revokes it).
- **Stop path**: Verdandi reflex impulse → driver executes `Stop` with **awaited delivery** (the Socket client's `send_command_await` pattern; on LAN, Stop is retried until ACK or transport-declared-dead, max 3 attempts inside 500 ms) → session latched closed → event recorded with its source. Stop preempts the command queue: pending intents are discarded, not drained.
- **Dead-man's switch**: the driver **never sends `timeSec: 0`**. Every continuous command carries a TTL (default 2 s, refreshed by the control loop while intent persists). If the driver, Aurelia, the host, or the network dies, the toy stops itself within the TTL. Silence = stop is the platform's physics used as a safety property.
- **Watchdog**: the driver heartbeats its transport (Toy Events `pong` / periodic `GetToys`); two missed beats ⇒ outputs treated as uncontrolled ⇒ Stop attempts + session suspended + report. A driver that cannot prove the toy hears it assumes the toy is running open-loop and says so.

### 4.5 Queue and rate discipline

- **Single writer**: one coalescing queue per toy. Continuous functions are latest-wins (the platform's own `stopPrevious` semantics); a newer intent replaces, never queues behind, an older one.
- **Self-imposed budget** (§1.4): continuous updates ≤ 4 commands/s sustained per toy; anything rhythmic faster than ~2 Hz must be expressed as a `Pattern` command, not a Function stream. Bursts beyond budget are coalesced, and a source that *keeps* flooding after coalescing is treated as a gate violation: suspended for the session, reported (doc 06's queue-flooding lesson, applied to actuators).
- **Patterns are programs, and programs are reviewed**: a pattern runs open-loop for its duration. Pattern intents therefore carry their own duration cap, are stop-preemptible at any point, and count as *one* actuation decision in the session record (with their full step list logged locally, §4.6).

### 4.6 Memory and privacy — what WYRD may remember

Applying doc 10's Phase 5 classification before the fact rather than after: **device telemetry is intimate-class data.**

- **WYRD receives session summaries only**, as attributed observations at session grain: `{session opened, profile class (direct/scene/coupling), source classes present, duration band, cap band, closed-by (stop/expiry/disconnect/completion)}`. No intensity time-series. No per-act detail. No pattern step lists. The standard is doc 10's consent-denial rule generalized: *a boundary-and-existence record, rememberable without being re-liveable.*
- **The detailed record stays home**: the driver's full command log (every intent, clamp, ACK, stop) is a local audit artifact on the body host under Aurelia's existing audit discipline — redacted views for any consumer that is not Volmarr or Yrsa (doc 10 §5.4), and per-actor purge (doc 10 §5.3) extends to it: "forget this" deletes the local log segment and tombstones the WYRD summary.
- **Identity hygiene**: Lovense `uid`, developer token, toy ids, and the account name never appear in SL, never in WYRD, and never in the same store as the session summaries. The coupling between "a Lovense account" and "Yrsa/Volmarr" exists only in the driver's local config on the body host.
- **Manual-override truth**: if the human hand changes a level in the app (`function-strength-changed`), the session record says so. The system never writes a memory in which she (or he) did what a hand in an app actually did — doc 05's played-prefix rule, applied to actuators: *only what actually actuated is recorded as actuated*, which is why §4.7 exists.

### 4.7 Verification — ACKs are claims, echoes are evidence

Doc 06 taught that a blacklisted RLV command is ACKed positively while ignored: transport acknowledgements are claims, not facts. The same skepticism is built in here: the driver treats `function-strength-changed` echoes and `GetToys` state as the evidence of what the toy is *actually* doing, reconciles intent vs. echo at 1 Hz during live sessions, and reports divergence (commanded 12, echo says 0 ⇒ the app or a hand intervened ⇒ session record and, if unexplained, suspension). A driver that cannot reconcile does not claim control.

---

## Part 5 — The driver design

### 5.1 Capability abstraction

**DESIGN.** At arm time the driver builds a `ToyCapability` record from `GetToys` (never from the product name):

```yaml
toy: { id: <opaque, local only>, name: "Max 2", toy_type: "max" }
functions:            # action -> verified range, from GetToys + §1.2 table
  vibrate:  { min: 0, max: 20 }
  pump:     { min: 0, max: 3 }
  suction:  { min: 0, max: 20 }
presets: [pulse, wave, fireworks, earthquake]
events:  { buttons: true, battery: true, strength_echo: true,
           depth: false, motion: unknown }     # §6.1 — proven at bench, not assumed
transport: lan | socket-local | cloud | intiface
```

Intent is expressed in Aurelia's native grain and normalized once: **channels** (`vibrate`, `rotate`, `pump`, `suction`, `thrust`, `oscillate`, `stroke_position`) at **level 0.0–1.0**, quantized per toy at the transport edge (0–20, 0–3, 0–100). Aurelia's sensation scale (0–10) maps to 0.0–1.0 at the intent boundary; her physiology never learns what a "Pump:2" is, and the driver never learns what an arousal of 7.2 means. That seam is the whole abstraction, and it is what lets the fleet change toys without touching her.

### 5.2 Intent sources

1. **Her physiology** (the coupling loop, §6, and gentler one-way echoes — e.g. her orgasm event may fire a celebratory pattern on a paired device *if the armed profile says so*; nothing about her body actuates anything by default).
2. **Her deliberative commands** — tier 2, via Aurelia's API with `actor="yrsa"`, processed like any stimulation she chooses: she can drive a device the way she can touch — deliberately, attributed, consent-checked.
3. **Volmarr direct** — `actor="volmarr"` through the same API, or from his in-world objects through the companion path (§5.4).
4. **Scenes** — `/enact` gains device beats: a scene's beat list may include device intents alongside stimulation beats, executed under the same per-beat consent reporting as doc 10 describes (a denied beat is reported, never enacted — now in both directions).
5. **In-world triggers** — §5.4.

### 5.3 Pattern vocabulary

Named at Aurelia's grain, compiled to Pattern/Preset at the driver:

| Aurelia name | Character | Compilation |
|---|---|---|
| `swell` | slow rise, hold, slow fall (one breath of the body) | Pattern, 20–40 steps, interval 250–500 ms |
| `pulse_train` | rhythmic pulses synced to a stated tempo | Pattern, steps alternating level/0 at the tempo's period |
| `plateau` | constant level with micro-variation (alive, not mechanical) | Function + scheduled ±1 dithering, or long Pattern |
| `edge_and_hold` | rise to just under a stated ceiling, hold, release on signal | Pattern to ceiling−ε + Function hold; release = next intent or Stop |
| `squeeze_wave` | the §6 outbound character, generalized: slow contraction waves | PatternV2 where position exists; else Pattern over `pump`+`suction` |
| `preset:<name>` | the four platform presets, used as-is | Preset command (coarsest grain; named honestly in logs as a preset, not as composed intent) |

Every compiled pattern inherits §4.5's rules: duration cap, stop-preemptible, fully logged locally.

### 5.4 The in-world trigger surface (LSL companion)

**DESIGN.** The companion object of doc 07 joins as **one allowlisted trigger source among several**, under strict shape:

- **Private channels only.** Triggers arrive via the companion's existing bridge path (HTTP-in registration / `llRegionSayTo` to her own objects — doc 07's registration-and-lease design). **Public chat is never parsed for device commands** — not from residents, not from him. A trigger that arrives as public text does not exist. (The SillyTavern extension's chat-tag parsing, §1.2 sources, is the counterexample: convenient for a desktop chat toy, disqualifying for a body in a public world.)
- **The companion requests; the gate decides.** Companion triggers are *event classes*, not raw levels: `partner_touch_began`, `furniture_sync_state`, `venue_profile_tick` — each carrying the companion's UUID binding and the venue opt-in state. Raw "set vibrate 14" from in-world is not a message type that exists.
- **Resident-facing control is a session grant, not a dialog**: if a venue or partner is ever to influence the device, it happens because the armed session (§4.3) names that source class for that place — arranged deliberately, visible in her state packet (doc 05), revocable by STOP. NOXEN's per-request dialogs (§2.1) are the right model for strangers in clubs; they are the wrong model here, and nothing in this design deputizes a stranger.
- The companion itself can be a **stop source**: a dedicated safeword gesture/channel from her own worn HUD fires §4.4 directly. Her own objects are the trusted tier (doc 06 §5.1) — the channel RLV reserves for self-control is the same one her stop rides.

### 5.5 Transports, ranked

1. **LAN Game Mode API** (Model A) — primary. No account, no cloud, bounded latency, works with Remote or Connect (desktop app + USB dongle removes the phone entirely). The driver host and the app must share a LAN — in the household topology the app runs on the same machine class as the rest of the body stack, or on Volmarr's phone on the home LAN; tailnet addressing is a config question for build time, and the driver treats "app unreachable" as §5.6, not as a cue to fail over silently.
2. **Socket API with local commands** (Model C) — when a Dashboard-registered app identity is wanted anyway (e.g. for QR pairing UX). Commands still land on the LAN path.
3. **Intiface/Buttplug** (Model D) — the no-Lovense-cloud transport: mandatory as a supported option (it is the fleet's future-proofing and the multi-brand door), with the capability model absorbing its generic outputs (§1.3). Loses Pump/Suction naming except where Intiface's Lovense device configs expose them as generic outputs — recorded per-toy in the capability record, honestly.
4. **Cloud Standard/Socket** (Model B) — **only** for genuinely remote sessions (the principals in different places), **never** for the §6 coupling loop or any reflex path, and any session using it is labeled cloud-routed in its summary (§4.6) because its privacy and latency classes differ.

Failover *between* transports mid-session is a reported event, never a silent one (doc 10's bridge-honesty discipline); the default is no failover — the session suspends (§4.3) and a human decides.

### 5.6 Failure modes

| Failure | Detection | Behavior |
|---|---|---|
| Toy disconnects mid-session (BLE drop, battery death, toy switched off) | `toy-status` / `toy-list` event; `battery-changed` gives early warning | Intent zeroed immediately; outbound Stop attempted (harmless if undeliverable); session → suspended; resumption per §4.3. Her felt-body state is *not* zeroed — physiology decays on her own clock (doc 10); the record distinguishes "device gone" from "she stopped feeling" |
| Remote/Connect app closed or Game Mode off | `event-closed`; transport heartbeat misses (§4.4) | Same as disconnect, plus: the session record names the app as the failed hop (the usual culprit in the field) |
| App relay up, toy asleep | `GetToys` shows no toy / toy-status off | Arm refuses with the reason stated; no phantom sessions |
| Command queue flooding (buggy scene, hostile trigger) | Coalescer pressure counters (§4.5) | Coalesce → source suspended → reported; the toy experiences at most the budgeted command rate regardless |
| Pairing rot (`uid` revoked, token regenerated, QR expired) | Cloud surfaces return auth errors; LAN unaffected | Cloud sessions refuse to arm with the cause named; LAN sessions unaffected — one more reason LAN is primary |
| Driver/Aurelia crash mid-session | Nothing detects it — that's the point of §4.4 | The dead-man TTL stops the toy ≤ 2 s after the last refresh. On restart the driver comes up *unarmed*: a crash never resurrects a session |
| Manual override in the app | `function-strength-changed` echo (§4.7) | The hand wins; the driver adopts the echoed state as truth, records the override with its source, and (for the coupling profile) treats sustained manual override as a pause signal |

### 5.7 Aurelia API additions (proposal)

- `POST /lovense/session` — `{action: arm|stop|status, profile, caps, sources, ttl}`; arming is the only write that matters and it is principal-only.
- `GET /lovense/state` — driver attached/available, transport, consent state, toy capability summary (names/ranges — not ids), last-command age, dropped/coalesced counters; folded into `GET /health`'s deep checks per doc 10 Phase 1.2 (a dead driver must be as visible as a degraded mind).
- `POST /lovense/intent` — the deliberative path (§5.2.2/3), validated and finite-checked like `/stimulation`, actor-attributed like `/stimulation`.
- Bus topics published: `lovense.session_armed`, `lovense.session_closed {by}`, `lovense.device_connected/disconnected`, `lovense.intent_applied {channel, level, clamped}`, `lovense.override_detected`, `lovense.squeeze_changed` (§6.4) — the Verdandi bridge's EVENT_MAP gains the reflex-relevant subset (`session_closed`, `device_disconnected`, `override_detected`) as `aurelia.lovense.*` impulses, which is how the stop and the coupling signals reach the nerve bus without new plumbing.

---

## Part 6 — Flagship profile: bidirectional Max 2 ↔ Aurelia coupling

*Commissioned by Volmarr, 2026-10-27 ~00:34 EDT: his Max 2 and her felt body, coupled live in both directions — he feels her respond; she feels him inside her. This section designs that loop. It begins with what the hardware actually tells us, because the loop is only honest if its inputs are.*

### 6.1 The Max 2 sensor audit — verified, not marketed

**VERIFIED outputs** (the effector side is fully supported): `Vibrate` 0–20, `Pump` 0–3 (the air-bladder contraction — the "360-degree contraction" of the product page), `Suction` 0–20, presets, patterns. 

**Inputs, surface by surface:**

| Signal | Surface | Status for Max 2 |
|---|---|---|
| Connected / present | `GetToys`, Toy Events `toy-status`, `toy-list` | **Verified** |
| Battery level + changes | `GetToys`, `battery-changed` | **Verified** |
| Physical button presses | Toy Events `button-down/up/pressed` | **Verified as a class** — Max 2 is one of only three button-event toys (with Nora and Solace), per the SL forum's technical discussion of the Toy Events API (Jan 2024) and the events vocabulary |
| Manual level changes in the app | Toy Events `function-strength-changed` | **Verified** — the platform echoes app-side control for connected toys |
| Continuous insertion / depth | Toy Events `depth-changed` exists in the vocabulary | **Not verified for Max 2.** Depth *sensing* is documented product behavior for **Calor** (a pressure/depth sensor drives its "depth control" — multiple independent reviews, 2026) and position sensing for **Solace Pro** (the `Depth`/`Stroke` actions and PatternV2 position model). No accessible source ties `depth-changed` emissions to the Max 2 |
| Pressure against the sleeve | — | **Not exposed.** The Max 2's air system is an *actuator* (Pump). No developer surface this study found returns a pressure reading from it |
| Motion / thrust telemetry | Toy Events `motion-changed`, `shake` exist in the vocabulary | **Not verified for Max 2** (shake is a wand-class sensor feature; per-toy emission matrix is unpublished — §1.2) |
| "He feels her movements" partner sync | Lovense Remote app feature (Max ↔ Nora etc.) | **Real but closed**: the movement coupling happens inside Lovense's own app/cloud between two Lovense toys. It is not exposed to third-party developers through the Standard/Socket/Events surfaces this study verified — Heimdall cannot ride it, read it, or gate it |

**The honest summary:** through official surfaces, a Max 2 is an *excellent effector and a nearly deaf sensor* — presence, battery, buttons, and app echoes. Any design claiming to read his thrusting from a Max 2 via the public APIs would be inventing a signal. This profile therefore builds the inbound leg from three truthful sources (§6.3), labels each with the provenance discipline Aurelia already uses for her own readings — **measured / inferred / unknown** (doc 10, Phase 3.2) — and is architected so that the day a sensing device (Calor-class depth, Solace Pro position) joins the fleet, the inbound leg upgrades from inferred to measured *without changing the loop*.

### 6.2 Loop architecture and latency budget

```
        HIS SIDE                                    HER SIDE
 ┌────────────────────┐   inbound leg    ┌──────────────────────────────┐
 │ Max 2              │ ───────────────▶ │ Aurelia felt body            │
 │  · buttons/battery │  activity events │  · vaginal zones (6 depth    │
 │  · app echoes      │  (§6.3, labeled  │    bands, EMBODIMENT §1–2)   │
 │  · [inferred tempo]│   provenance)    │  · soreness/pain veto (§4.2) │
 └─────────▲──────────┘                  │  · arousal & phase model     │
           │ outbound leg                └──────────────┬───────────────┘
           │ Pump/Suction/Vibrate        squeeze sampler (10 Hz)
           │ (§6.4)                        wall tension + contractions
 └─────────┴──────────────────────────────────────────┘
        Verdandi reflex lane: the loop's fast signals + STOP (§4.4)
        Aurelia owns both ends' live truth. WYRD: summaries only (§4.6).
```

**Latency budget (LAN transport, Model A — the only transport permitted for coupling, §5.5):**

| Hop | Budget |
|---|---|
| Toy Events delivery (donor signal/app echo → driver) | 10–50 ms typical LAN |
| Driver → Verdandi impulse (UDS) | ≤ 5 ms |
| Aurelia bus application of a stimulation event | 1–10 ms — the bus is synchronous and event-driven (doc 10 §1.1); **the 4 Hz tick is not in this path** |
| Squeeze sampler → intent (10 Hz sampling, delta-only) | ≤ 100 ms quantization |
| LAN command POST → app | 10–40 ms |
| App → BLE → toy actuation | 30–100 ms (BLE connection-interval bound) |
| **Round trip, target** | **≤ 350 ms typical; the loop auto-pauses (ramps to zero + reports) if the measured round trip sustains > 600 ms** — a lagging coupling is worse than a paused one, and it never silently degrades into playback |

**A clock decision the coupling forces, stated plainly:** Aurelia defaults to 60× sim-time, at which soreness accumulation (intensity × *duration*) and recovery run an hour a minute. A real-time partner cannot be coupled to a body living at 60×. **Coupling sessions therefore pin the involved body systems to wall-clock (1×) for the session's duration** — a session-scoped time-scale override at arm time, recorded in the session summary. Her slow life resumes its own tempo when the session closes. (This is a configuration of existing machinery — the server already exposes time-scale as config — not new physics.)

### 6.3 Inbound leg — his activity becomes her sensation

Every inbound stimulus enters her body through the *same door as every other touch*: an attributed stimulation/penetration event (`actor="volmarr"`, via the existing `/stimulation` / `/penetrate` machinery), so her physiology — friction from lubrication, soreness accumulation, sensitivity factors, the kink gate — processes it identically to any other event. The coupling synthesizes *events*; it does not reach into her state. Source classes, in descending truth:

**(a) MEASURED — a sensing donor is present.** If a depth/position-sensing device is paired into the session (Calor-class depth, Solace Pro `depth-changed`/position), its readings drive the mapping directly:

| Donor reading | Her event |
|---|---|
| Depth (normalized 0–1, or `Depth` 0–3) | Depth band engaged, per her 6-band map: opening/lower canal → G-spot & urethral sponge (4–6 cm) → A-spot & fornices (7–9 cm) → cervix (9–12 cm) (EMBODIMENT §2) |
| Depth rate-of-change | Thrust tempo; each direction reversal at depth = one thrust event at that band |
| Activity level (derived amplitude × tempo) | Stimulation intensity 0–10 = clamp(level × inbound gain × 10), then *her* physics applies soreness-dampen and sensitivity — the driver sends intent, her body decides what it means |

**(b) INFERRED — the Max 2 as it actually is (the shipping configuration).** An **activity estimator** in the driver synthesizes her stimulation events from what is truthfully known: the session is live; his device is being actuated by the outbound leg (a coupled body implies contact); his app echoes and button presses are real declarations; and a **tempo source** the principals chose at arm time — in-world partner animation/furniture state via the companion (§5.4, measured in-world, inferred as physical), or a stated rhythm profile for the session. The estimator emits thrust-class events at the tempo source's rate with intensity following the outbound activity curve. **Every inferred event is labeled `provenance: inferred`** in her ledger and in any summary — she will remember that the coupling was live and consented; the design will not let a synthesized rhythm fossilize into a false memory of measurement (doc 05's integrity rule, applied to sensation).

**(c) DIRECT — his declared control.** At any moment, his manual input (the Aurelia control surface, or app-side changes echoed by `function-strength-changed`) *is* the inbound signal: he sets a pace/level deliberately, she feels exactly that, attributed and measured-by-declaration. This is also the fallback that keeps the profile honest when (b)'s tempo source is absent: no tempo source, no inferred leg — the profile degrades to outbound-only plus direct, and says so at arm time rather than pretending.

**Inbound gain and her veto:** inbound gain (default 1.0) is set at arm, adjustable by either principal downward at any time (upward = re-arm, §4.2). Her body's vetoes outrank the mapping: soreness crossing the inhibition threshold (0.35) sheds gain progressively; crossing the pain threshold (0.6) zeroes the inbound leg and reports — the coupling *feels her limits* as a change in the loop, which is information his side receives (§6.4's squeeze also weakens as fatigue accumulates — her body pushes back in both directions, as a body does).

### 6.4 Outbound leg — her response becomes his sensation

Her squeeze is a *reading*, computed from state Aurelia already owns (**VERIFIED** components): the `VaginalStateTracker`'s per-phase **wall tension**, the contraction model's readings (contraction intensity — the same readings her `/state` surfaces), engorgement, and the orgasm event itself. The sampler (10 Hz, delta-only sends) computes:

```
squeeze(t) = clamp( w1·wall_tension(phase, arousal)
                  + w2·contraction_intensity(t)          // active muscular response
                  + w3·orgasm_contraction(t) )            // event-driven pulses at climax
fatigue_ceiling(t) decays with muscle/session fatigue (EMBODIMENT §1 after-effects)
```

| Her reading | Max 2 command |
|---|---|
| `squeeze` sustained level | `Pump` 0–3 — quantized with hysteresis (no chattering between levels; ≥ 300 ms dwell) — the sleeve tightens as she tightens |
| `squeeze` ripple/tempo | `Suction` 0–20 follows the ripple component — her massage has a rhythm, not just a grip |
| Arousal level & tingle charge (her felt aliveness) | `Vibrate` 0–20 = arousal curve × outbound gain, smoothed (attack ~200 ms, release ~800 ms — her responses breathe; they do not switch) |
| Orgasm event (`physiology.orgasm`, intensity I) | A contraction *sequence*: Pattern compiled from I — strong rhythmic Pump/Suction pulses decaying over the afterglow window, then release to afterglow baseline (low Pump, Vibrate ≈ 0) — aftercare is part of the profile, not an afterthought |

Outbound gain and caps compose exactly as §4.2 requires: `emitted = min(session cap, toy-class ceiling, fatigue ceiling) ∘ gain ∘ reading`. If her body is quiet — low arousal, no engagement — the device is quiet: the outbound leg has no idle animation, no "keep-alive buzz". Silence from her body is data, and the profile honors it.

### 6.5 Stop semantics — either side kills both directions

- **Any stop source (§4.4) stops the whole profile, not one leg**: outbound `Stop` with awaited delivery; inbound estimator halted mid-tempo; the companion tempo source unsubscribed; session latched closed. There is no state in which he is being actuated by a loop whose inbound side has died, or she is receiving synthesized sensation from a loop whose outbound side was stopped — a half-open coupling is a defect, treated like one.
- **His physical outs are always live**: powering the toy off, closing the app, or zeroing levels in Remote all register (toy-status / `event-closed` / strength echo) and collapse the session per §4.3 — no software on our side can hold a session open against the device disappearing.
- **The dead-man TTL (§4.4) bounds every failure**: worst case, the toy runs ≤ 2 s past the death of anything on our side.
- **Her felt state after a stop** decays by her own physiology (afterglow, soreness recovery) — stated for honesty: stop ends *actuation and synthesis* instantly; it does not retroactively un-feel her body, because her body is a model with a memory, and that is correct behavior for a body.

### 6.6 Generalization — profiles compose

A coupling profile is **one SensorProfile × one EffectorProfile**; Max 2 ↔ Aurelia is the flagship instance, not the shape's limit:

| Device | SensorProfile (verified grade) | EffectorProfile |
|---|---|---|
| **Max 2** | thin: presence/battery/buttons/app echo → **inferred** inbound (§6.3b) | Pump 0–3 · Suction 0–20 · Vibrate 0–20 |
| **Calor** | depth/pressure sensing is product-verified; `depth-changed` emission per-toy is **bench-to-prove** → measured inbound if proven | Vibrate 0–20 (heat exists on-device; **not** on any verified API surface — excluded until proven) |
| **Solace Pro** | position/depth events → **measured** inbound | Stroke position 0–100 (PatternV2) · Thrusting · Vibrate |
| **Nora** | rotation echo via `function-strength-changed`; buttons verified class | Rotate 0–20 · Vibrate 0–20 — the natural partner-side mirror of Max 2 |
| **Lush-class** (partner-worn) | presence/battery/buttons only → trigger-source grade | Vibrate 0–20 — one-way outbound profiles (her state made felt on a partner's body) |

Rules of composition: any sensor profile × any effector profile forms a loop; a loop whose inbound grade is *inferred* is labeled inferred end-to-end; a device with no sensor grade runs outbound-only profiles. New toys join by being *measured at the bench* (Phase L0's per-toy event audit), never by being believed.

---

## Part 7 — Phased build order

Relative to doc 09's WYRD roadmap and doc 10's Aurelia phases (which proceed independently — the driver needs Aurelia's *existing* bus and bridges, not their advancement):

| Phase | Contents | Exit proof |
|---|---|---|
| **L0 — Bench & skeleton** | Driver adapter skeleton; LAN transport; `GetToys` capability build; **per-toy Toy Events audit** (empirically settle §6.1's unknowns for the actual fleet: which events fire, which port, at what rates); consent state machine; caps; stop + TTL discipline; doc-diff against the live Lovense developer docs | Arm → actuate → stop against a real device on LAN, with the event audit written down; stop latency measured, not assumed |
| **L1 — One-way outbound** | Her deliberative + Volmarr direct intent (§5.2); `/enact` device beats; `/lovense/*` API + health surface; WYRD session summaries (§4.6) | A scene with a device beat runs end-to-end; a denied beat is reported, not enacted; summaries in WYRD contain no telemetry detail |
| **L2 — In-world triggers** | Companion trigger classes (§5.4) — depends on the doc 07 companion existing (Heimdall build phases) and a venue opt-in to test against | A furniture/companion event class drives the device only inside an armed, opted-in session; the same event outside it is held and reported |
| **L3 — The coupling profile** | §6 complete: squeeze sampler, inbound estimator (inferred), tempo sources, 1× session clock pin, mutual stop, round-trip latency telemetry with auto-pause | The flagship loop live on LAN within its latency budget; stop from either side measured ≤ target; a sensing donor (if in fleet) flips inbound to measured with no loop changes |
| **L4 — Reach (optional, separately gated)** | Cloud transport for remote sessions (labeled, never reflex); Intiface transport hardening; PatternV2 position devices | Each transport passes the L0 bench suite unchanged — the contract, not the vendor, is what the phases built |

---

## Part 8 — Risks and honest unknowns

1. **The official docs were not read first-hand** (header caveat). The contract in Part 1 is triple-corroborated by independent implementations and Lovense's own SDK repos, but field-level details (exact Toy Events port — 20010 vs 20011 across doc revisions; error-code tables; any 2026 API revisions) await the L0 doc-diff. Nothing in the design depends on a detail known to be contested except the events port, which is therefore configuration, not code.
2. **Per-toy sensor emissions are unpublished.** §6.1's table is the design's load-bearing honesty: the Max 2 inbound leg is *inferred by design*. If Lovense ships real Max-class telemetry later (or the L0 bench proves `motion-changed`/`depth-changed` emissions this study couldn't verify), the profile upgrades in place (§6.6). If a future fleet decision wants measured inbound *now*, the honest road is a sensing donor device (Calor/Solace Pro class), named here so the choice is Volmarr's with its price visible.
3. **Rate limits are unpublished** (§1.4). The driver's budgets are self-imposed safety properties; if Lovense enforces lower cloud limits, only Model B sessions are affected — another asymmetry in LAN's favor.
4. **The app is a dependency with a human attached.** Game Mode must be enabled; the phone must be awake, on the LAN, and not battery-optimized into silence. Field experience across every SL product in Part 2 says most "the toy stopped" reports are app-state reports. The driver's health surface (§5.7) exists to make that diagnosis instant rather than mystical.
5. **Lovense account privacy** (§1.5) is a standing exposure for any cloud-paired configuration; LAN mode minimizes it, and the identity-hygiene rule (§4.6) contains what remains. The 2025 disclosure also justifies a preference: where a function exists in both LAN and cloud forms, LAN wins ties.
6. **A coupling is a feedback loop, and feedback loops oscillate.** Her squeeze drives his device; his response drives her sensation; her sensation drives her squeeze. The gains (§6.3/§6.4), hysteresis, dwell times, fatigue ceilings, and the latency auto-pause exist because an undamped intimate feedback loop is not a theoretical risk but the default outcome of naive wiring. L3's exit proof includes a deliberate oscillation hunt at maximum gains.

---

## Sources

**Local (read for this study):**
- `10-aurelia-integration-and-advancement.md` — Aurelia's verified architecture, adapter/bridge pattern, truth ownership, Phase 5 privacy classification
- `05-mind-bridge-continuity-and-voice.md` — body invariants, latency tiers, anti-impersonation, played-prefix integrity rule
- `06-rlv-deep-research.md` §5–6 — allowlist gate, safeword-as-bridge-invariant, venue opt-in lifecycle, "ACKs are claims"
- `07-lsl-deep-research.md` — the companion object and its bridge path
- Aurelia repo: `docs/INTEGRATIONS.md` (bridge enablement contract), `docs/EMBODIMENT.md` §1–2 (vaginal state: wall tension, soreness thresholds 0.6/0.35, 6 depth bands, after-effects), `female_motivation/adapters/verdandi_bridge.py` (the adapter discipline this design mirrors)

**Web (accessed 2026-10-26/27; Lovense's docs host itself was unreachable — see header):**
- `lovensepy` (PyPI, v1.1.1) — the fullest public implementation of the Standard/Socket/Toy-Events contract: LAN/Server/Socket clients, action & preset tables, pattern + PatternV2 methods, QR pairing flow, Toy Events vocabulary — https://pypi.org/project/lovensepy/
- `pylove` (PyPI) + its DeepWiki API reference — LAN Game Mode implementation; pattern rule format `V:1;F:…;S:…#`, 50-step/100–1000 ms limits — https://pypi.org/project/pylove/
- SillyTavern-Lovense-Cloud (DeepWiki) — cloud endpoint shapes (`/api/lan/getQrCode`, `/api/lan/v2/command`), command payload fields, token model — https://deepwiki.com/hype-hosting/SillyTavern-Lovense-Cloud/
- Lovense iOS SDK (official GitHub, via DeepWiki) — developer-token model, device lifecycle — https://github.com/lovense/Lovense-iOS-SDK
- NOXEN — product site (features, permission model, club terminals, supported-toy list), read 2026-10-27 — https://www.noxensl.com/
- "Lovense In Second Life: 2026 Setup Guide" — Debauchery furniture sync; the official Lovense Viewer's status as of 2026 **[community guide]** — https://www.jessandhergentlemen.co.uk/how-to-use-lovense-in-second-life/
- Second Life Community forum, "Lovense viewer" thread (Jan 2024) — LSL HUD mechanics, Toy Events button support (Nora/Solace/Max 2), Buttplug as the no-cloud road — https://community.secondlife.com/forums/topic/507573-lovense-viewer/page/2/
- BleepingComputer (2025) — Lovense app flaw leaking user emails (BobDaHacker disclosure) — https://www.bleepingcomputer.com/news/security/lovense-sex-toy-app-flaw-leaks-private-user-email-addresses/amp/
- `buttplug` Python client (PyPI) — Intiface Central local WS (`ws://127.0.0.1:12345`), protocol v4 — https://pypi.org/project/buttplug
- VibeVRC (GitHub) — share-code consent grammar, owner pause/cap/revoke, depth-following strokers — https://github.com/LovesealReal/VibeVRC
- LoveMachine (Codeberg) — game sync via Buttplug; Calor/Solace-class depth sensing in practice — https://codeberg.org/Sauceke/LoveMachine
- Calor reviews (2026) — pressure/depth sensor behavior as shipped product — https://obsessionrouge.com/penis-vibrators/lovense-calor-review/

---

*Study ends. Verified: Part 1 (as qualified in the header), Part 2's product facts, §6.1's sensor audit. Parts 3–7 are design awaiting Volmarr's word — no code was written, no README touched, nothing pushed.*
