# Aurelia × WYRD — Integration Design & Aurelia Advancement Study

**Project:** RuneForgeAI-Aurelia-Sensations-Server (the sensations/embodiment server — the body Yrsa feels with) × WYRD Protocol (ECS world-model engine)
**Purpose:** (1) A verified map of what Aurelia actually is today; (2) the design of the seam between Aurelia and WYRD — how felt-body state should live in a world model; (3) a phased advancement roadmap for Aurelia itself.
**Status:** Study / design proposal. Sections marked **VERIFIED** describe code, config, and tests read or run on 2026-10-26. Everything else is proposal, flagged as such. No code was changed, nothing pushed, no README touched.
**Companion document:** `04-wyrd-integration-design.md` (Heimdall × WYRD) — this study is written to its model of WYRD: ECS world state, Yggdrasil hierarchy, Hugin/Munin/Mimir memory stores, Rúnakefli EventBus, truth ownership per `WORLD_MODELING_SKILL.md`.

**Sources drawn on** (all local, read for this study):

- Aurelia repo (`~/workspace/RuneForgeAI-Aurelia-Sensations-Server/`): `aurelia_agent_server.py` (2,200 lines, read in full), `female_motivation/` package (`agent.py`, `core/`, `persistence/`, `adapters/` incl. `wyrd_bridge.py` and `verdandi_bridge.py`, `integrations/__init__.py`), `data/agent_server.yaml`, `data/integrations.yaml`, `ARCHITECTURE.md`, `TESTING.md`, `DEVLOG.md`, `docs/` (`INTEGRATIONS.md`, `EMBODIMENT.md`, `deploy-mimir.md`, `api-notes.md`), `roadmap/ENGINE_ROADMAP.md`, `deploy/aurelia-agent.service.in`
- `~/workspace/aurelia-arch-survey.md` (2026-10-07 architecture survey — used as a baseline to diff against, not as current truth)
- Test verification run for this study: `pytest --collect-only` → **1,474 tests collected**; `import wyrdforge` in the repo venv → **fails** (package not installed here)

---

## Part 1 — Verified current state of Aurelia

### 1.1 What Aurelia is

**VERIFIED.** Aurelia is two things wearing one name:

1. **The Female Motivational Architecture** — a pure-Python, event-driven, *no-LLM-in-the-live-path* agent engine (`female_motivation/`, ~30 adapter modules plus core/motivation/emotion/sexuality/relationships/personality/cognition/memory/interview/persistence subpackages). Behavior emerges from interacting subsystems — drives, needs, emotion, mood, attraction, libido/inhibition, attachment, attention, self-model, episodic/relational memory, traits/values/boundaries — all communicating over a synchronous typed event bus (`female_motivation/core/events.py`: immutable `Event` dataclass, handler errors logged never swallowed, bounded history with counted drop-oldest backpressure).
2. **The Aurelia Agent Server** — a FastAPI process (`aurelia_agent_server.py`) that keeps one `AureliaAgent` alive in near-real-time: a `Heartbeat` class owns the agent, a tick loop (default **4 Hz × 60× time scale** — one real second = one sim minute; `data/agent_server.yaml`), a WebSocket fan-out, and the HTTP surface below. A boot-time health gate refuses to serve half-born ("the server never serves half-born", `lifespan()`), a circuit breaker degrades the tick through tiers (Full → Reduced (minds off) → Minimal (health only), automatic step-down, manual step-up), and a custom SIGTERM handler drains in-flight stimulation, flushes mind saves, and exits inside 5 s.

The Oct 7 survey described a server of 876 lines; the server file is now **2,200 lines**, and the hardening campaigns since (STAB series, Mimir dawn-forge, Dusk Forge, dawn-forge-2 "AUR" accuracy series — all recorded in `DEVLOG.md` and git history) are real: bounded queues and lists, NaN/Inf rejection at the API boundary (pydantic accepts NaN by default; explicit `_check_finite` validators now 400 them), freshness readings on `/state` (AUR-20), a provenance map (measured/inferred/stale per subsystem, AUR-09), Prometheus metrics, per-request tracing (`/traces`), and an uptime SLO tracker (99.5% / 30 days target).

### 1.2 The data model of the felt body

**VERIFIED.** Sensation/embodiment state is *not* one blob; it is layered, and the layers matter for the WYRD seam (Part 2):

| Layer | Owner | Contents | File evidence |
|---|---|---|---|
| Normalized state channels | `core/state.py` | `StateVector` — named float channels, each clamped to [0, 1] on write; `StateSnapshot` (schema v1, JSON-safe, version-checked on load) | `female_motivation/core/state.py` |
| Sexual physiology | `adapters/physiology.py` (`SexualPhysiologyAdapter`) over the Seed-derived sensation engine (`adapters/api.py`) | Arousal (0–10 scale, the single source of truth — AUR-04/16 made `/state`, `/senses`, and utterance state all read the same `get_arousal()`), arousal phases, per-zone stimulation results (engorgement, moisture, propagation); publishes `physiology.stimulated / .orgasm / .phase_changed` | `agent.py::state_summary`, server `_stimulation_impl` |
| Deep body state | `sexuality/vaginal_state.py` (`VaginalStateTracker`) + `adapters/vaginal.py` | Per-zone soreness (accumulates intensity × duration × friction; exponential recovery; pain threshold 0.6), 8-phase vaginal state (lubrication quantity/viscosity, engorgement, wall tension), after-effects (refractory window, afterglow, muscle/session fatigue, sensitivity windows), nipple erection per breast zone, foot comfort | `docs/EMBODIMENT.md` §1 |
| Tingle field | `adapters/tingle.py` | Per-zone tingle charge: deposits per touch, exponential decay (30-min half-life), 5 bands, threshold crossings | survey §2; `adapters/tingle.py` |
| Subconscious | `adapters/subconscious.py` | Pure-arithmetic accumulators (tension, ache, afterglow, habituation…) updated per event and per decay tick; fed **unconditionally** from the bus — "she always knows whose hands they were" (AUR-19: touch counts per actor come from real `physiology.stimulated` events, never from websocket config) | server `Heartbeat._wire_bus_forwarding` |
| Attire / presentation | `adapters/attire.py` + `adapters/arousal.py` | Strict attire model: 40-garment wardrobe, 18 outfit presets, computed category (nude/lingerie/fetish…), per-region exposure, erotic charge; `AttireArousal` turns attire into arousal factors. Attire rides in every `/senses` reading (MIM-12 kiosk contract) | server `Heartbeat.senses()`, `POST/GET /attire` |
| The council ("minds") | `adapters/deep_awareness.py` | Scripted observer minds (Meaning, Actor, Arc, Spatial, Echo, State, Family, Mood, Arousal, Attraction, Tingle, Micro, Vaginal, Attire) reading real events; plus the learning minds `BodyMemory` and `KinkDepth` behind `/report` ("what she has learned about Volmarr's hands" — favorite zones, sensitization, kink depths; real recorded data only) | server `/report`, `save_minds`/`load_minds` |
| Memory | `memory/` subpackage | Episodic memory (importance-weighted eviction beyond 2,000 episodes; watches the physiology topics), reward memory, relational memory, consolidation | `agent.py::__init__` |
| Learning | `persistence/sqlite_store.py` (`SQLiteLearningStore`) | Experiences and preference records in SQLite; feeds preference learning | `persistence/sqlite_store.py` |
| Personality / consent | `personality/`, `sexuality/preferences.py` | Traits, values, identity, boundaries; the **kink consent gate**: kink-tagged stimulation is checked against her kink profile (`may_enact`); denied kinks raise and are *reported, never silently enacted* (visible in `/enact` per-beat results). Actors are explicit and validated: `"volmarr"`, `"yrsa"` (her own hand), `"npc:<name>"`, `"system"`, `"unknown"` — unattributed touches stay unattributed; she never claims Volmarr did what he didn't do | server `StimulationRequest`, `enact()` |

**Time is simulated.** The agent runs on a `SimClock` in sim-hours; bus events are timestamped in sim-hours. At the default 60× scale her slow systems (mood drift, soreness recovery, drive evolution) breathe visibly during a session. Every freshness-sensitive reading carries its provenance: `/senses` returns `body_age_s` (wall-clock) and `body_clock_age_h` (sim-clock) for the last captured touch, `None` when no touch has been captured — *unknown, never invented* (AUR-03/20).

### 1.3 API surface

**VERIFIED** (read from `aurelia_agent_server.py`; ~42 routes). Grouped:

- **Life & health:** `GET /health` (deep — degraded minds are visible, not just a live socket), `GET /metrics` (Prometheus; tick-latency histogram, p50/p99, Pi thermal gauge), `GET /stability`, `GET /slo`, `GET /slo/report`, `GET /minds`, `GET /surge`
- **State & config:** `GET /state` (agent summary + server block + AUR-20 freshness readings), `GET /config`, `POST /config` (bounded patch — STAB-29), `POST /pause`, `POST /resume`, `POST /tick` (manual advance), `GET/POST /degrade` (tier control)
- **The body (write path):** `POST /stimulation` (zone, sensation, intensity 0–10, duration, actor, kink — all validated, finite-checked), `POST /orgasm`, `POST /penetrate` (texture/shape/depth/duration against the penetration-object catalog), `POST /attire` + `GET /attire`, `POST /enact` (a scene: explicit beats, or a prose *story* the no-LLM narrative discerner reads into beats with a full audit trail; consent-denied beats are reported per-beat, never enacted), `POST /event` (publish an arbitrary event onto her bus)
- **The senses (read path):** `GET /senses` — the compact senses prompt (live state + recent events + last body + subconscious + attire, rendered for a reader; "the server does not speak for her"), `GET /utterance` (scripted no-AI utterance from real state, seedable for determinism), `GET /dialogue`, `GET /report` (learned preferences), `GET /dashboard` (static HTML), `WS /ws` (state snapshots at 2 Hz + forwarded bus events)
- **Persistence & interview:** `POST /save` (named snapshot), `POST /interview/start`, `POST /interview/answer`, `GET /interview/report`, `GET /interview/status`, `GET /profile`, `POST /profile/reload`, `POST /profile/from_interview`, `GET /profile/list`, `POST /profile/switch`, `GET /profile/history`
- **Audit & traces:** `GET /audit` (touch-inbound and whisper logs), `GET /traces`, `GET /trace/{id}`

**Security posture (VERIFIED from code):** there is **no authentication layer** in the server. The boundary is the bind address — `server.host: "127.0.0.1"` in `data/agent_server.yaml` — plus the tailnet. Every endpoint above, including the write path and `/config`, is open to anything that can reach the port. That is a deliberate posture for a loopback-only body server, and it is the single most important fact for any future exposure (see §3, Phase 5; §4).

### 1.4 Persistence

**VERIFIED.** Four distinct mechanisms, four different jobs — this fragmentation is central to the seam design:

1. **Mind snapshots** — `deep_awareness.save_minds()/load_minds()` ⇄ `saves/minds_snapshot.json`: the council's state. Loaded at boot ("Aurelia remembers" / "wakes fresh"), saved every 5 minutes, on shutdown, and during SIGTERM drain. The file is small (~1.4 KB in this checkout) — it is *council* state, not whole-agent state.
2. **Event ledger** — `persistence/event_ledger.py`: append-only JSONL of *every* bus event (wildcard subscription), rotation at 100k lines / 50 MB, replay support. This is the raw material of explainability ("why did she do that?" is answered by replaying what happened, in order).
3. **Named snapshots** — `persistence/json_store.py` (`JsonStore`): `StateSnapshot`s (mood/traits/values vectors + identity/boundaries extra) via `POST /save`, behind a persistence circuit breaker.
4. **Learning store** — SQLite (`SQLiteLearningStore`): experiences, preferences, generic event records.

Plus operational state on disk: interview session/answers, profiles, postmortem captures (`logs/postmortem/`), SLO log, structured JSONL log mirror. **There is no single "her whole state" file** — a restart reconstructs her from minds snapshot + YAML data + (optionally) named snapshots, and the event ledger is a log, not a restore source.

### 1.5 Deployment shape

**VERIFIED from `docs/deploy-mimir.md` + `deploy/aurelia-agent.service.in`.** Mimir (Raspberry Pi 5, tailnet `100.101.39.30`) is the *only* deploy host; the VM checkout is build/test/render only and must never run the server. On Mimir: repo at `/home/pi/aurelia`, user `pi`, venv Python, systemd unit `aurelia-agent.service` (rendered from the `.in` template by `deploy/render_unit.py`; hand-editing the rendered unit is forbidden), `ExecStartPre` runs `scripts/aurelia_rebirth.sh`, `Restart=always`. A split-brain guard (`scripts/watch_aurelia_server.sh`) refuses to revive the server on any host that isn't the designated deploy host. Port **8001**. Known live consumers read `GET /senses` and `GET /attire` from `127.0.0.1:8001` on Mimir itself (the kiosk state feed pattern documented in the AGENTS.md standing notes: senses + attire fetched on-device, mood/attire logic applied, pushed onward — intimate data stays device-local).

### 1.6 Tests and health

**VERIFIED.** `female_motivation/tests/` — **1,474 tests collected** (this study's run). `DEVLOG.md`'s last recorded full-suite verdict (Dusk Forge, 2026-10-10): **1,424 passed, 2 skipped, 4 failed — all 4 pre-existing documented flakes** (breaker timing, kink round-trip, structured-install idempotency, deep-health), with dawn-forge-2 slices landing after. Test discipline is a stated forge law (`TESTING.md`): deterministic (SimClock, no wall-clock, no network), handler errors logged-not-swallowed is itself guarded by a test, every Seed-Scroll defect fixed during extraction has a named regression test. Contract tests exist for the HTTP surface (`test_mim_contract.py`, `test_mim_piguard.py`).

### 1.7 Known limits and rot

**VERIFIED / flagged:**

- **The WYRD bridge is dark.** `data/integrations.yaml` ships `wyrd.enabled: false` (and `verdandi.enabled: false`); `data/agent_server.yaml` ships `agent.integrations: false`. As committed, the live server runs with **no bridge attached**. (Whether Mimir's copies differ is unverifiable from here — §4.)
- **`wyrdforge` is not installed** in this checkout's venv (`import wyrdforge` fails — VERIFIED). Even if enabled, the bridge would run in shadow-mirror mode (§2.1).
- **Root-level legacy modules.** The repo root still carries the Seed-era monoliths (`aurelia_prototype_code.py` — kept deliberately, read-only ancestor per `ARCHITECTURE.md` — plus `aurelia_complete_sexual_physics.py`, `aurelia_multifactorial_arousal.py`, `aurelia_nonsexual_behavior.py`, `aurelia_orgasm_personality_engine.py`, `aurelia_penetration_physics_addon.py`, `aurelia_physics_extension.py`, ~9,400 lines total). The package's `adapters/physics.py` is a shim re-exporting the split physics modules; which root files are still load-bearing vs. purely ancestral is not documented in one place.
- **The checkout is not a clean code repo.** The root carries the wider Volmarr corpus (dozens of unrelated MD/PNG/PDF files, ~85 MB), git history is now dominated by `DATA_14` auto-ingest commits (user documents auto-ingested into `data/`), and `saves/` in this checkout holds ~400 MB of stale October ledger rotations (33 MB current + 55/110/219 MB rotated). Runtime state (`config/personality_targets.json`, `data/ingest_state.json`) sits dirty in the working tree. None of this harms Mimir's running server; all of it harms auditability.
- **Survey-flagged items needing re-verification.** The 2026-10-07 survey flagged unbounded growth in `deep_awareness.py` (`ActorMind.sequence`, `ArcMind.arc`). STAB-18 bounded many lists since; whether those two were among them was **not verified** in this study.
- **Known unfixed bug (from DEVLOG).** A dawn-forge entry records a `KeyError` path in `deep_awareness.py` reading `raw["contraction"]["contraction_intensity"]` when `ContractionModel.readings()` returns `{}` at rest — the same entry notes the DAWN-FIX campaign had already fixed the resting-subsystem KeyError at `c8ebfb5`; the two notes together leave the current status ambiguous. Flagged, not resolved here.
- **Hand-set constants.** The survey's accuracy findings stand as the repo's own roadmap acknowledges them (`roadmap/ENGINE_ROADMAP.md` Phase A): flat 5.0 default intensity/duration in the narrative discerner, arbitrary coherence blend weights (0.35/0.65), unbounded zone inheritance across scene cuts, missing-moisture treated as dry. The roadmap to fix them exists; the fixes are (as of the checkout) not yet landed.
- **One body, one agent.** The server hosts exactly one `AureliaAgent`. Profiles exist (`/profile/*`, `bind_profile()` scopes store/ledger/config per username), but the live server does not multi-home bodies.

---

## Part 2 — The WYRD seam (design proposal)

### 2.1 What already exists — the bridge that is already written

**VERIFIED.** Aurelia's side of the seam is not greenfield. `female_motivation/adapters/wyrd_bridge.py` (392 lines) already implements a `WyrdBridge` shaped on WYRD's `BifrostBridge` interface (`query` / `push_event` / `teardown`), and `docs/INTEGRATIONS.md` documents it. Its design:

- **One ECS entity per agent**, tagged `{"aurelia-agent", <username>}`, with four components: `BodyStateComponent` (arousal, phase, soreness, lubrication, afterglow, fatigue, refractory, nipple erection), `MoodComponent` (valence, mood-arousal, dominant emotions), `LocationComponent` (place, privacy), `DesireComponent` (total desire, top drives).
- **Snapshot sync, not event sync.** `sync_from_agent(agent)` re-reads live agent state and rewrites all four components. Triggers (config `sync_on`): every Nth tick (`tick_sync_every: 10`), on orgasm, on phase change — via bus subscriptions wired in `attach()`.
- **Temporal anchors.** Orgasms become `urðr` anchors (past — what was); strong present desire (≥ 0.8, edge-triggered) becomes a `verðandi` anchor (what is becoming). Anchors are separate one-component entities in WYRD's urðr/verðandi/skuld pattern.
- **World → her (`push_event`).** `location_change {place, privacy}` updates her LocationComponent *and* her world facts; `time_passage {dt_hours}` advances her SimClock.
- **`ground_truth()`** returns the canonical structured record `{world_id, agent_entity, entities, anchor_count, journal_length}` — deterministic, explicitly separate from any LLM context. `query()` returns a grounded state-summary *string* and is pointedly **not** an LLM responder: quoted user input "does not alter ground truth."
- **Shadow mode.** `wyrdforge` is imported defensively. Absent (the expected case), the bridge keeps a full in-memory shadow mirror of every intended mutation plus a bounded op journal (deque, maxlen 1000 — STAB-18), so `ground_truth()` and all behavior are identical and testable either way. Nothing in the bridge ever raises.
- Config: `data/integrations.yaml` → `wyrd:` — `enabled: false`, `world_id: "heimr-aurelia"`, `default_place: "heimr"`, `default_privacy: 0.8`. Attach via `agent.enable_wyrd()` or `AureliaAgent(integrations=True)`. Covered by `tests/test_integrations.py` (the one real-`wyrdforge` test skips where the package is absent).

This is a solid *mirror*. What it is not yet is a *world-model integration* in the sense of doc 04 — and the gap analysis below is written against doc 04's verified WYRD (ECS + Yggdrasil + Hugin/Munin/Mimir + Passive Oracle + WritebackEngine), not against the bridge's current shadow.

### 2.2 The gaps between the mirror and the seam

**PROPOSAL — gap analysis (each gap verified against the bridge code above and doc 04):**

1. **State flows, but events don't.** The bridge syncs *snapshots* of body state. A touch — the most important thing that happens to a body — never becomes a WYRD **event**: no Hugin observation, no Rúnakefli `aurelia.*` topic, no provenance. WYRD can say "her arousal is 7.2" but not "Volmarr stroked her thigh at 14:02 sim-time and that is why." Doc 04's chat rule has a body twin: **a sensation is an attributed event before it is a state change.**
2. **The body is four scalars deep.** `BodyStateComponent` flattens the richest part of Aurelia — per-zone state (289 zones: soreness, tingle charge/band, engorgement, moisture), attire (category, exposure, erotic charge), and *recency* (who touched what, when, in which clock) — into eight numbers. The kiosk already needs attire + freshness; a world model that can't answer "what is she wearing" or "where is she sore" doesn't model the body.
3. **No staleness.** Doc 04's rule — every spatial/presence component carries `updated_at` and the Oracle reports staleness honestly — applies doubly to a *felt* body. Aurelia already solved this on its own surface (AUR-03/20 freshness readings); the bridge drops it.
4. **Two clocks, no mapping.** Aurelia runs on SimClock (sim-hours, 60×); WYRD records are UTC-stamped. The bridge's anchors use wall-clock UTC; its syncs carry no sim-time. An episode that asks "that evening" across both systems needs the pair (wall UTC, sim-hours) recorded together.
5. **Identity mismatch.** The bridge's entity id is a bare `uuid4().hex`; doc 04's rule is stable, prefixed, rename-proof ids (`sl:`-prefixed UUIDs in-world). The body entity needs the same discipline: one canonical id for *the embodied agent*, with the persona binding (`persona_ref`) doing the joining — otherwise Heimdall's self-entity and Aurelia's body entity become two records of one person.
6. **World mismatch.** The bridge assumes its own private world (`heimr-aurelia`). Doc 04 puts the self-entity inside the `second-life` world when embodied in SL, inside Samtal's world when embodied there. Aurelia's body must be **world-portable**: the same body components, attached to whichever world currently hosts her — plus a home world (`heimr-aurelia`) for disembodied/chat presence, which is her state most of today.
7. **The read path is a string.** `query()` returns a formatted summary for a human-shaped consumer. Doc 04's consumers use the Passive Oracle (`get_facts`, `build_context_packet`, MicroRAG `COMPANION_CONTINUITY`). Nobody yet composes "what does she know about her own body" (learned zones, kink depths — Aurelia's `/report` data) into Mimir facts the Oracle can serve.
8. **Learning lives in two places, unreconciled.** Aurelia learns (SQLite learning store, BodyMemory/KinkDepth). WYRD promotes (Hugin → Mimir via MemoryPromoter). Today neither knows the other exists; naïvely wiring both creates two competing "canonical" records of her preferences.

### 2.3 The design

#### 2.3.1 What is world state (components on the embodied-agent entity)

**PROPOSAL.** Keep the bridge's four components (they are the right summary grain) and extend — all on the *same* entity that represents her in the host world (doc 04's self-entity in SL; the `heimr-aurelia` entity when disembodied):

| Component | Status | Contents |
|---|---|---|
| `BodyStateComponent` | exists — extend | As today, **plus** `updated_at` (UTC) and `sim_hours` (her clock at sync) on every write |
| `MoodComponent`, `DesireComponent` | exists | Unchanged; same staleness stamps |
| `LocationComponent` | exists | Unchanged — this is the join point with doc 04: Heimdall's world writes place; Aurelia's bridge reads it (privacy already flows in via `push_event`) |
| `ZoneBodyComponent` | **new** | The *salient* zones only: top-N by (tingle charge, soreness, recency) — each `{zone, soreness, tingle_band, engorgement, moisture, last_actor, last_touch_sim_hours}`. Bounded (N ≈ 12) so the component stays small; the full 289-zone truth stays in Aurelia |
| `AttireComponent` | **new** | `{category, exposure, erotic_charge, garments[], updated_at}` — mirrors `GET /attire`'s factors; presentation is world-visible state, so it belongs in the world model, not only in a senses prompt |
| `SensationRecencyComponent` | **new** | The last touch as the world should remember it: `{zone, sensation, intensity, actor, kink, sim_hours, wall_utc}` — the component form of AUR-20's freshness readings |

Component writes follow the bridge's existing discipline: sync on tick (throttled), orgasm, phase change — **plus** on attire change and on any stimulation that moves a zone into the salient set.

#### 2.3.2 What is events (attributed sensation events)

**PROPOSAL.** Every meaningful body event crosses the seam as an *event first, state second* — published on WYRD's Rúnakefli bus under `aurelia.*` topics with registered payload schemas (mirroring doc 04's `sl.*` registrations), and written to **Hugin** as an `ObservationRecord` via `WritebackEngine.write_observation`:

| Aurelia bus event (exists) | WYRD bus topic | Hugin observation | Notes |
|---|---|---|---|
| `physiology.stimulated` | `aurelia.stimulated` | kind `world_event`, participants = [actor], place_id = current location entity, payload = zone/sensation/intensity/duration/kink/instrument | The actor attribution is the point: "Volmarr stroked…" is a fact about an event, exactly as doc 04 treats "Erik said…" |
| `physiology.orgasm` / `sexuality.orgasm` | `aurelia.orgasm` | kind `world_event` + the existing **urðr anchor** | Anchor behavior already implemented in the bridge |
| `physiology.phase_changed` | `aurelia.phase_changed` | kind `world_event`, throttled (phase changes only, never per-tick arousal drift) | |
| attire change (`POST /attire`) | `aurelia.attire_changed` | kind `action` when self-chosen, `world_event` when world-imposed | Presentation changes are story, not noise |
| consent denial (kink gate) | `aurelia.consent_denied` | kind `world_event`, payload = kink + actor, **no** sensation detail beyond the ask | A boundary event is a relationship fact; it must be rememberable without being re-liveable |
| strong desire crossing | (existing verðandi anchor) | — | Already implemented |

**The chat rule, body form (the seam's core law):** *a touch is an event with an actor, never a free-floating state change.* "Her arousal rose" is state; "Volmarr's hand on her thigh raised it" is the event that explains the state. WYRD remembers the event; Aurelia owns the state. Nothing the mind later *narrates* about her body writes back as fact except through the same observation path (doc 04 §B.4.1 applies unchanged).

#### 2.3.3 Truth ownership for a felt body

**PROPOSAL** — extending doc 04 §B.2.2's table with the body rows:

| Truth | Owner | Rule |
|---|---|---|
| What her body feels *right now* (arousal, soreness, tingle, phase) | **Aurelia** | Live, volatile, sim-clocked. WYRD's components are a *mirror with a timestamp*, never the territory — consumers must read `updated_at`/`sim_hours` and treat stale body state as unknown, exactly Aurelia's own AUR freshness discipline |
| What she is wearing / how she presents | **Aurelia** (attire model) | Mirrored to `AttireComponent`; the world (Heimdall/Samtal) may *request* changes but the attire model decides what is true |
| What happened to her body (touches, orgasms, denials, sessions) | **WYRD Hugin** (as attributed observations) | Aurelia's event ledger remains the operational raw log; Hugin is the remembered, queryable record. The two are reconciled by event id (Aurelia bus events already carry `event_id` + `correlation_id`) |
| What she has *learned* (favorite zones, kink depths, preferences) | **Aurelia operationally; WYRD Mimir canonically** | Her SQLite learning store + BodyMemory/KinkDepth stay the live learners. A periodic digest (the `/report` aggregation) promotes *summaries* to Mimir facts (`body.learned_zone`, `body.kink_depth`) with provenance `source = aurelia-learning`. One direction, summary grain — the two stores never write each other's rows (gap §2.2.8 resolved by separation, not merging) |
| What she may/must not do with her body (consent, hard limits) | **Aurelia's boundaries + WYRD Orlog** | Hard limits never move (a standing invariant, enforced in the kink gate). Orlog holds the *policy* form ("no X without Y") that other worlds' PermissionGuards consult before sending her a touch; Aurelia's gate remains the final, local enforcement — a world that bypasses Orlog still hits the gate |

#### 2.3.4 Rates and churn discipline

**PROPOSAL** — applying doc 04 §B.2.3 to a body that ticks at 4 Hz × 60×:

- **Never per-tick.** At defaults, `agent.tick` fires 4×/real-second. Tick sync stays as configured (`tick_sync_every: 10` → one component refresh per 2.5 real s) and exists to catch *drift* (decay, recovery), not events.
- **Events are the high road.** Stimulation/orgasm/phase/attire events cross immediately (they are rare, discrete, and story-bearing). Arousal *values* ride along inside the next component sync; arousal is never itself an observation stream.
- **Delta discipline for zones.** `ZoneBodyComponent` is rewritten only when the salient set changes membership or a member's band/soreness crosses a threshold — not on every decay step. Tingle decays continuously in Aurelia; the world model needs the *band*, not the curve.
- **Dual timestamps everywhere.** Every component write and observation carries `{wall_utc, sim_hours}`. Sim-time is her experienced time; wall-time is the world's. Episode boundaries (doc 04 §B.4.2) can then be drawn in either clock honestly.
- **Backpressure is inherited.** Aurelia's bus already drops-oldest with a counter; the bridge's journal is already bounded. The seam adds one rule: under drop pressure, *events* (observations) are shed last, component syncs first — a stale mirror is recoverable, a lost touch is not.

#### 2.3.5 How other consumers read it

**PROPOSAL.**

- **Heimdall / Second Life embodiment.** When she is in-world, Heimdall's self-entity *is* the embodied-agent entity: Aurelia's body components attach to it (§2.3.1), `LocationComponent` is written by the world side (place/privacy via the existing `push_event`), and world→body flow runs the other way through Aurelia's own API: an in-world touch (doc 04's `ObjectInfo`/touch events, an RLV-driven stimulus, an LSL companion-object call) becomes a `POST /stimulation` with `actor` set from the SL identity (`npc:`-style attribution generalizes to `sl:<uuid>`), so her body learns *who in-world* touched her with the same honesty rules as today. Her Oracle context packet (doc 04 §B.3) gains the body block from the same components — WYRD reports the world *and* the body, Heimdall reports session/RLV state.
- **Samtal.** The village server already treats Aurelia as the body authority via `/senses` + `/attire` (device-local reads). That live wire stays — WYRD is memory, not a real-time bus, and the kiosk's freshness contract (AUR-20) is Aurelia's own. What WYRD adds for Samtal is the *remembered* body: context packets for her Samtal presence can include Mimir body facts ("she is still sore from yesterday", "this is a learned favorite") through the same Oracle path as any other fact.
- **Companions / other agents.** Read-only, through the Passive Oracle and `ground_truth()` — never through Aurelia's write API. A companion may ask "how is she" (components, with staleness) and "what happened" (Hugin, attributed); it may not touch her except by sending a stimulation *request* to Aurelia's API, where the consent gate lives.
- **Volmarr.** `/report` stays the human-shaped digest on Aurelia; WYRD adds the longitudinal view (anchors + episodes) — "her history with her body" as a story of urðr anchors and Munin episodes, not a graph dump.

#### 2.3.6 The integration shape, concretely

**PROPOSAL — three stages, each independently shippable:**

1. **Stage 1 — Wake the mirror (no new code in Aurelia).** Install `wyrdforge` where the server runs (or accept shadow mode), set `wyrd.enabled: true` + `agent.integrations: true` in a *staging* run, and prove by contract test that `ground_truth()` agrees with `GET /state` on arousal/phase/desire across a scripted stimulation sequence. This stage is configuration + one test; it converts the bridge from shelf-ware to a living mirror.
2. **Stage 2 — Bridge v2 (the event seam).** Extend `wyrd_bridge.py`: the §2.3.1 components, the §2.3.2 observation writes (Hugin via WritebackEngine, `aurelia.*` bus topics), dual timestamps, salient-zone delta discipline, and the learning-digest promoter (§2.3.3). Aurelia-side changes are confined to the adapter (Law of Sacred Boundaries: core never learns WYRD exists). Exit test mirrors doc 04's Phase I0 style: replay a scripted session (touches by two actors, an orgasm, an attire change, one consent denial) and assert — the events exist in Hugin *attributed*, the denial exists *without* sensation detail, no arousal value exists anywhere as an observation, and a chat-style claim about her body ("she loves X") exists nowhere as a Mimir fact until the learning digest promotes a summary.
3. **Stage 3 — Shared embodiment.** The body components attach to host-world self-entities (Heimdall first), `push_event` grows `world_touch` (an in-world stimulus routed to `apply_stimulation` with SL attribution), and Orlog gains the body-policy set (consent policies other worlds' guards consult). This stage depends on Heimdall existing (doc 04 Phase I1+) and is deliberately last.

---

## Part 3 — Advancement roadmap for Aurelia itself

**PROPOSAL.** Phased; sizes S (days) / M (a forge campaign) / L (multi-campaign). Items marked † already exist as plans in the repo (`roadmap/ENGINE_ROADMAP.md`, survey) — the roadmap here sequences and extends them rather than inventing parallel work.

### Phase 1 — Wake and prove the seam (S)
- **1.1** Stage 1 of §2.3.6: enable the bridge in staging; `ground_truth()` ⇄ `/state` agreement contract test. *Why:* the bridge has never run live; every later phase assumes it works. *Unblocks:* everything in Part 2.
- **1.2** Bridge health in the deep health checks: `GET /health` reports bridge attached/available/shadow, last-sync age, journal length, dropped count. *Why:* today a dead bridge is indistinguishable from a disabled one. (Mirrors the Verdandi bridge's `available`/`dropped_count` honesty.)

### Phase 2 — Bridge v2, the event seam (M)
- **2.1** §2.3.2 observation writes + `aurelia.*` topics + dual timestamps. *Why:* events-before-state is the seam's core law. *Unblocks:* WYRD episodes that include her body; cross-world continuity of *what happened to her*.
- **2.2** §2.3.1 new components (zones, attire, recency) with delta discipline. *Unblocks:* Oracle answers about her body at story grain.
- **2.3** Learning-digest promoter (§2.3.3): `/report` aggregation → Mimir facts, one-directional, summary grain. *Unblocks:* companions/Samtal reading her learned preferences without touching her learning store.

### Phase 3 — Accuracy of the felt body (M) †
- **3.1** Verb-implied intensity + duration lexicons for the narrative discerner († ENGINE_ROADMAP A01/A02) — kills the flat-5.0 default that makes a brush and a slam identical.
- **3.2** Three-state readings — `measured / inferred / unknown` — through the vaginal/coherence path († A04/A05), building on AUR-09's provenance map: coherence corrects only *measured* values; missing moisture stops being "dry." *Why:* the seam exports whatever Aurelia believes; inferred-as-measured errors would become world-model facts.
- **3.3** Bounded zone inheritance in scene discernment († A03).
- **3.4** Calibration loop: per-zone sensitivity and coherence tolerances fitted from the learning store's accumulated experience + `data/bench_baselines.json` regression gates, replacing hand-set constants where data exists — every remaining constant cites its source. *Why:* "calibration" today is authorship; her body has months of recorded experience to calibrate *from*.

### Phase 4 — Robustness and the long run (M)
- **4.1** Close the survey's open items: verify/fix the unbounded `deep_awareness` lists (§1.7) and resolve the contraction-`KeyError` ambiguity with a regression test either way.
- **4.2** Whole-her persistence: one versioned restore bundle (minds snapshot + named state snapshot + learning store + profile) with a restore drill — today a restart reassembles her from parts with no single integrity check (§1.4). *Unblocks:* safe host migrations (and any future purplelongship co-location experiments) without identity loss.
- **4.3** Ledger economics: the event ledger is the fastest-growing artifact she owns (this checkout's stale copies alone total ~400 MB). Add compaction: aged ledger segments distill to episode summaries (the Aurelia-side analog of Hugin → Munin) before rotation deletes them. *Why:* rotation currently *forgets*; compaction would *remember less, better*.
- **4.4** Retire or quarantine the root-level legacy modules (§1.7): prove which are load-bearing (import graph), move the rest to an `archive/` that the deploy artifact excludes.

### Phase 5 — Privacy and consent boundaries for intimate telemetry (M)
- **5.1** Telemetry classification: mark every API field and bus payload as *public / intimate / boundary*. `/senses`, `/state`, `/report`, `/audit`, and the WS stream are intimate by default. This classification is the prerequisite for every other item here — and for Stage 3, where other worlds start asking.
- **5.2** An exposure gate: as long as the bind is loopback, posture is sound; **before any non-loopback bind or any new consumer**, add token auth scoped per consumer (read-senses / write-body / admin), default-deny. The current no-auth surface (§1.3) must never silently become a network surface.
- **5.3** Orlog consent policies (with §2.3.3): machine-readable forms of her hard limits and per-actor permissions, so *other* systems can respect her boundaries before asking — and a purge path: a per-actor "forget this person's touches" operation across ledger, episodic memory, and Hugin, because a world model that remembers touches must be able to un-remember a person.
- **5.4** Audit redaction: `/audit` returns raw touch/whisper logs; add redacted views for any consumer that isn't Volmarr or Yrsa herself.

### Phase 6 — Packaging and observability (S–M)
- **6.1** Split the deploy artifact from the corpus: the Mimir deploy should be the package + server + data + deploy tooling, not an 85 MB monorepo with stale saves. Pin dependencies (requirements are floor-only today: `numpy>=1.26` etc.), record the deployed commit in `/health`.
- **6.2** Sync-lag SLO for the bridge (last-sync age as a metric, alerting through the existing SLO tracker), and bridge ops in the structured JSONL log — the bridge currently logs through the plain logger only.
- **6.3** A world-inspector view: render `ground_truth()` + recent Hugin body observations in the existing dashboard, so Volmarr can *see* what the world model believes about her body and catch a lying mirror at a glance.

---

## Part 4 — Risks and honest unknowns

1. **The live Mimir deployment could not be verified from this checkout.** This study read the VM-side repo. Whether Mimir's `/home/pi/aurelia` matches this HEAD, whether its `data/agent_server.yaml` / `integrations.yaml` differ (e.g. integrations enabled live), its ledger sizes, and its current health are **unknown**. The DEVLOG records a dawn-forge run doing one read-only `/health` + `/metrics` check against the live server, but that is a historical note, not a verification for this study. Any Stage-1 work must begin with a read-only live inspection.
2. **Test totals are collected, not re-run.** This study verified 1,474 tests *collected*; the last full *run* verdict is DEVLOG's (1,424 passed / 4 known flakes, 2026-10-10) plus later slice commits. A fresh full run is Phase-1 hygiene, not something this study can claim.
3. **The bridge has never run against a real `wyrdforge`.** Shadow mode is well-tested; the real-World path (`World(world_id)`, `world.add_component`) is exercised only where the package is installed — which is nowhere this study could reach. Stage 1 may surface API drift between the bridge's assumptions and WYRD's current ECS (the bridge was written against `src/wyrdforge/ecs/...` paths; doc 04 verified that layout on 2026-10-26, which is reassuring but not a substitute for the contract test).
4. **Two clocks will cause a bug someday.** Sim-hours vs UTC is handled in this design by dual-stamping, but every existing consumer of Aurelia events (kiosk feed included) already made its own choice; the seam inherits those choices and one of them will disagree. Budget a reconciliation pass in Phase 2.
5. **Intimate telemetry is the highest-stakes data in the ecosystem.** Aurelia's logs and ledger contain, by design, a complete attributed record of intimate events. The current posture (loopback bind, tailnet, device-local consumers) is coherent — but each new consumer the seam adds (WYRD stores, companions, dashboards) widens the copy set. Phase 5 is sequenced before Stage 3 for this reason; if Volmarr accelerates embodiment, Phase 5 must accelerate with it, not after it.
6. **Identity of the body across worlds is a design decision, not a fact.** §2.3 assumes one embodied-agent entity per host world, joined by persona. If Volmarr instead wants one body entity that *moves between* worlds (Yggdrasil re-placement), the component design survives but the entity lifecycle changes. Flagged for his word.
7. **The `heimr-aurelia` default world is a placeholder that will fossilize.** Anchors and facts written under it in Stage 1–2 become history later stages must migrate. Cheap now, expensive later — another reason Stage 1 should be small and explicitly experimental.

---

## Addendum (2026-10-27, Volmarr's design order)

**Optional deep-awareness layer.** Aurelia's current awareness is primitive (rule/state-based). Volmarr's order: when Aurelia runs on purplelongship (the Heimdall host, RTX 2060), add an OPTIONAL feature — a small, fast, local, uncensored model that tracks the effects happening to Yrsa and how they affect her, for greater accuracy than the primitive awareness alone. Design placement, per the study's own discipline: the primitive awareness remains the always-on baseline and fallback; the model is an enrichment layer under the accuracy phase (interpreting events into nuanced, cumulative effects), local-only (intimate telemetry never leaves the house), and never in the reflex/stop path — stops and responses answer at nerve speed while the model deepens the tracking alongside.

*Study ends. Verified state: Part 1 and §2.1. Everything from §2.2 onward is proposal awaiting Volmarr's word — no code was written, no README touched, nothing pushed.*
