# 09 — WYRD Protocol: Advancement Study & Roadmap

**Project:** WYRD Protocol — World Yielding Real-time Data (Volmarr's world-model engine)
**Purpose:** The planning study for massively advancing WYRD itself — for Heimdall (the Second Life
embodiment of Yrsa) and for every other consumer across Volmarr's ecosystem.
**Date:** 2026-10-26 · **Author:** Yrsa Freydisdottir (research worker, for Volmarr)
**Primary source:** the WYRD repo itself, branch `development`, HEAD `849b322` (2026-10-22) —
code, tests (run fresh for this study), docs, TASK files, ROADMAP, DEVLOG, git history.
**Sibling studies:** Heimdall doc 04 (`04-wyrd-integration-design.md`, the SL integration design —
its engine verdict is built on here, not redone), doc 01 (SL protocols), doc 03 (world awareness).
Sister studies for Aurelia and Verdandi are commissioned separately; §7–§8 here cover only the
WYRD side of those seams.

**Tagging:** `[VERIFIED]` = read in code/docs or produced by a test run for this study, with the
repo path cited. `[INFERRED]` = follows from verified evidence but not directly observed.
`[PROPOSAL]` = design/roadmap content — my recommendation, not a fact about the repo.
**Scope:** study only. No code was changed, no README edited, nothing pushed.

---

## 0. Verdict in brief

WYRD's core is real and unusually well-tested for a solo-forge engine: a proven ECS + Oracle +
EventBus + three-store persistence heart, ~1,850 core tests passing on a fresh run. But the
engine's *reach* is overstated by its own docs — most integration "tests" test Python copies of
the shipped code, two C++ bridges are TODO stubs, the Unity package is not Unity code, the JS SDK
cannot build, and the spatial core every game consumer needs (coordinates, radius, distance)
does not exist. The advancement campaign is therefore not "add features to a finished engine";
it is: harden the truth layer, give the world a geometry, make the test story honest, and then
grow the new senses (live presence, combat, sensation) on ground that can hold them.

---

## 1. Verified current-state map

### 1.1 Architecture at a glance `[VERIFIED]`

The engine (`src/wyrdforge/`, ~85 Python modules) is a layered world-model:

- **ECS core** — `ecs/world.py` (`World`), `ecs/yggdrasil.py` (`YggdrasilTree`, the
  zone/region/location hierarchy), `ecs/entity.py`, typed Pydantic components under
  `ecs/components/` (character, spatial, temporal, runic, mind, and more).
- **Passive Oracle** — `oracle/passive_oracle.py`: 9 read-only queries over the world
  (`get_nearby` at line 214 is containment-based; see §3.1).
- **Rúnakefli EventBus** — `runtime/events.py` (660 lines): typed dotted topics with optional
  Pydantic schemas, synchronous FIFO delivery, per-topic replay ring buffers, dead-letter queue
  (cap 256) with summary/drain, per-topic metrics.
- **TurnLoop** — `runtime/turn_loop.py`: staged per-turn pipeline (oracle → prompt → connector →
  writeback → contradiction detection) with per-stage fault isolation and degraded packets.
- **Persistence** — three SQLite stores, one file each: `persistence/world_store.py` (ECS
  snapshots), `persistence/memory_store.py` (Hugin memory: observations/facts, FTS5 search,
  thread-local connections), `persistence/bond_store.py` (BondGraph edges/vows/hurts). Shared
  open/quarantine machinery in `hardening/state_io.py`. The contract is written down in
  `docs/state-files.md`: no load-bearing state in convenience files.
- **Memory & mind** — writeback/promotion pipeline (`services/writeback_engine.py`, idempotency
  keys, bounded 1024), contradiction detection, MicroRAG over the memory store, belief modelling
  (`BeliefComponent`/`MindModelComponent` with `divergence()`), MTOM reality states with a
  zero-bleed firewall, MicroReality (sovereign per-character realities). World identity:
  `World.identify(kind="wyrd", reality=manifest|potential)`, `heimr-*` ids
  (`docs/specs/world-registry.md`).
- **Metaphysics (partial)** — `ecs/components/runic.py` ships `HamingjaComponent`,
  `RunicChargeComponent`, `AncestralResonanceComponent`, driven by `services/runic_engine.py`
  (invoke_rune, apply_hamingja_event, tick, report). The Phase 7 plan's `metaphysical.py` with
  `WyrdThreadComponent`/`SpiritualChargeComponent` does not exist under those names (§1.4).
- **Security** — `security/permission_guard.py` (PermissionGuard; verified in doc 04's pass).
- **HTTP API** — `bridges/http_api.py`: 5 endpoints (POST /query, POST /event, GET /world,
  /facts, /health) on port 8765, threading server, watchdog with bounded restarts. **No
  authentication anywhere** (zero auth/token/Authorization hits in the file) and no URL
  versioning (a version string appears only in /health).
- **Persona modes** — COMPANION / CODING_GUIDE / WORLD_SEER / RITUAL / DEBRIEF.

### 1.2 Component maturity

| Component | Maturity | Evidence |
|---|---|---|
| ECS core (World/Yggdrasil/components) | **Proven** — but not thread-safe (§2.2) | core suite; `ecs/world.py` has no locking |
| Passive Oracle (9 queries) | **Proven** for containment model; spatial queries **missing** | `oracle/passive_oracle.py` |
| EventBus | **Proven**, exemplary concurrency; durability **starter** (volatile replay/DLQ) | `runtime/events.py`; §2.4 |
| Persistence — single store | **Proven** integrity-at-open (quarantine pattern) | `hardening/state_io.py:394-443` |
| Persistence — cross-store | **Starter**: no atomicity, no ordering guarantee | §2.1 |
| Migrations | **Machinery only**: framework exists, zero real migrations ever run | `state_io.py:171-255`; all stores at v1 |
| Memory layer (Hugin/FTS5/MicroRAG) | **Proven** | `persistence/memory_store.py` |
| Writeback / promotion / contradiction | **Proven**; turn-atomicity gap | `services/writeback_engine.py` |
| BondGraph | **Proven** in memory; durability is manual-by-contract, unenforced | `persistence/bond_store.py:1-7` |
| Mind wiring (beliefs, MTOM, MicroReality) | **Proven** | doc 04 pass; `ecs/components/` mind modules |
| Runic / metaphysics | **Partial** — shipped shape differs from the Phase 7 plan | `ecs/components/runic.py` vs plan |
| TurnLoop | **Proven** degradation, two holes (§2.3) | `runtime/turn_loop.py:192-282` |
| HTTP API | **Starter-plus**: functional, unauthenticated, unversioned | `bridges/http_api.py` |
| In-repo bridges (11 modules, 4,626 LOC) | **Proven-thin**: real adapters over one core | `src/wyrdforge/bridges/` |
| Integrations (20 dirs, 8,816 prod LOC) | **Starter-to-real clients; tests largely do not test them** (§1.3) | `integrations/` audit |
| SDKs | Python **proven-installable**; JS **broken build**; C# **unpackaged**; GDScript mirror-tested | §3.6 |
| Installers / tools | **Real** (install 1,935 LOC + 1,391 test LOC; TUI; cloud relay with token auth) | `install/`, `tools/` |
| Prima Scholar (Phase 14A "complete") | **Paper**: one 115-line `research_prompts.md` | `tools/prima_scholar/` |

### 1.3 Test health — as found by running them `[VERIFIED]`

Fresh runs for this study (Python 3.12 venv; the system Python lacks pydantic, which cost a
first run 68 collection errors — environment only, noted because any contributor hits it):

- **Core suite** (`tests/`): **1,848 passed, 4 failed, 14 skipped, 6 xfailed** (~106 s).
  Failures: `test_doc_policies` ×2 (doc-drift — tests correctly catching docs that moved);
  `test_hardening.py::TestBoundedThreadPool::test_queue_full_drops_task` (expected a dropped
  task, got accepted — timing-sensitive *or* a real drop-semantics regression; undetermined,
  needs reproduction-first triage; it is **not** in doc 04's known-failure set);
  `test_b1_feed_replay_within_budget` (known load-flaky — see the B1 saga below).
- **Satellite suites** (`install/tests`, tools relay, GDScript): 186 + 105 passed, 1 failure
  (`test_check_tool_python_available` — no `python` binary on this machine's PATH; environment
  artifact, not a code defect).
- **CI** (`.github/workflows/ci.yml`, the only workflow): ruff + core `pytest tests/` on
  Python 3.10–3.12. It **never runs** the integration, install, or SDK suites.

**The B1 saga, as the budget history records it** `[VERIFIED]`: VerdandiBridge feed replay blew
its 20 µs/event budget (~330 µs/event at N=20k, quadratic belief-rewrite scans —
`docs/scale-limits.md`, SLICE6-D1), fixed 2026-09-27 via "option A" to ~18.3–18.8 µs/event —
GREEN, but on a thin margin, and the pinning test still flakes under load
(`docs/performance-budgets.md`). Lesson carried into §9: this engine has already hit one
quadratic wall at 20k events; live-world rates will find the next ones.

**The mirror-test problem** `[VERIFIED]` — the single most important honesty finding of this
study. For 8 integrations/SDKs (cryengine, defold, minecraft, o3de, roblox, unreal, gamemaker,
godot + `sdk/gdscript`), the "tests" are self-labelled *Python mirror tests*: the test file
re-implements `escape_json`/`normalize_persona_id`/`build_query_body` in Python and tests the
copy; the shipped Java/C++/Lua/GDScript is never compiled or executed. The 7 JavaScript
integrations' test files inline/extract functions from the production file rather than importing
it. Drift between copy and original is **undetectable** by these suites. Genuinely tested:
all C# (189 xUnit `[Fact]`s compiled against the real source), `sdk/js` (ts-jest), pygame,
installers, the cloud relay. So "1,849 tests passing" (doc 04) is true of the *core* — and must
never again be quoted as evidence the integrations work.

### 1.4 Rot and drift register `[VERIFIED]`

- **Broken quickstart:** `docs/guides/quickstart.md` and `docs/index.md` tell users to run
  `python -m wyrdforge.server` — **no such module exists**. The real entry is
  `python -m wyrdforge.bridges.http_api` (`CLAUDE.md` line 38; CLAUDE.md also carries the broken
  line). Root CLIs (`wyrd_chat_cli.py`, `wyrd_world_cli.py`) likewise fail from a fresh checkout
  until `pip install -e .`.
- **README drift:** "Architecture Overview: Coming soon!" although `docs/guides/architecture.md`
  exists. Package version is still **1.0.0** (`pyproject.toml`) despite phases 8–19 landing
  after "v1.0.0" — no version discipline. Not published to PyPI
  (`docs/audits/dependency-audit-2026-09-26.md`).
- **Status drift:** TASK files for phases 12b/12c/13–18 still say "IN PROGRESS" while ROADMAP
  tracking marks phases 8–19 complete; no TASK files exist for phases 3–7 or 8–11a–c; `TODO.md`
  is a stale wish-list contradicting ROADMAP status.
- **`research_data/`**: 102 tracked files (~948 KB) containing a divergent V5 starter copy of
  the codebase (`research_data/src/wyrdforge/` — models/security/services subset; no ecs,
  oracle, or persistence) plus design notes. Nothing in `src/` or `tests/` references it. It
  mimics a project skeleton and will mislead every future reader and agent.
- **Misplaced corpus:** `docs/` is 69 MB, of which ~68 MB is `docs/research/` PDFs.
  `docs/specs/` totals 18,059 lines, of which `shared/` is 16,992 lines (**94%**) — and 14 of
  its 22 files are ThoughtForge/MindSpark material, a sibling project's cognition corpus parked
  inside WYRD's spec tree. WYRD's own top-level specs are 157 lines.
- **Tracked build artifacts:** 707 tracked files / ~120 MB, including 108 tracked Unity
  `bin/`+`obj/` files (4.7 MB of DLLs) despite `.gitignore`, ~15 loose root images, and an
  11.6 MB design-lawbook PDF. Two git-ignored runtime DBs (`wyrd_agentzero.db`,
  `wyrd_hermes.db`) sit in the working tree — harmless, but they are how state files breed.
- **Observability thinness:** `logging` is imported in only 15 of 85 `src` modules (§2.6).
- **Templated twins:** `bridges/kindroid_bridge.py` (181 LOC) and `bridges/voxta_bridge.py`
  (184 LOC) are the same file with platform name, port, DB filename, and webhook field names
  swapped — each just constructs a `PythonRPGBridge` + HTTP endpoint. Fine as a pattern;
  it should be one parameterized adapter, not copies that can drift.
- **Host-layer gaps:** `integrations/unity` (546 prod LOC) contains **no Unity code** — plain
  C# classes; `MonoBehaviour` appears only in doc-comments showing what a user should write; no
  `.asmdef`, no `.meta`, no editor inspector (the roadmap promised one); its `package.json`
  payload would not compile as Unity behaviours as-is. `integrations/opensim`'s C# is **not**
  an OpenSim region module — its own csproj states *"No OpenSim.Framework reference… implement
  `IRegionModuleBase` when deploying"* (its LSL side, by contrast, is genuinely wired:
  `llHTTPRequest`, touch handling, owner config channel). `integrations/cryengine` and
  `integrations/o3de` C++ are **TODO stubs** (`WyrdPlugin.cpp`: "Full libcurl implementation
  omitted"; `WyrdSystemComponent.cpp` methods are TODOs). Unreal is the most credible native
  bridge (real `FHttpModule` requests) but has never been compiled. Minecraft is real Java with
  no Gradle wrapper, never compiled.

---

## 2. Robustness audit

Method: code-level audit of the persistence, runtime, and hardening layers with file:line
evidence, plus the fresh test runs of §1.3. The doctrine applied is the repo's own
(`docs/state-files.md`, WORLD_MODELING_SKILL): truth has one owner, events leave traces, and
persistence is a discipline, not a convenience.

### 2.1 Persistence integrity

- **WAL: present, inconsistently hardened** `[VERIFIED]`. All three stores set
  `journal_mode=WAL`, by different routes: world store (`persistence/world_store.py:78-83`,
  WAL + foreign_keys), memory store (`memory_store.py:209-215`, WAL + foreign_keys +
  busy_timeout + auto_vacuum=INCREMENTAL), bond store (`bond_store.py:82-86`, WAL only, **no
  foreign_keys**, and its schema declares no `FOREIGN KEY` for vows/hurts → bond_id).
- **`synchronous` is never pinned** `[VERIFIED]`: repo-wide, zero `PRAGMA synchronous` in
  `src/`. Durability under power loss rests on SQLite's unpinned default — never stated, never
  tested. For an engine whose whole promise is durable world truth, this is the cheapest
  serious gap in the repo.
- **Busy timeouts disagree** `[VERIFIED]`: world/bond rely on `connect(timeout=5.0)` alone;
  memory uses `timeout=10.0` **plus** `PRAGMA busy_timeout=5000` — the two values contradict
  each other. `docs/timeouts.md:35-36` documents the split as intentional; intentional or not,
  three stores with three contention behaviours is a debugging trap under concurrent load.
- **Migrations: machinery without a single migration** `[VERIFIED]`. `hardening/state_io.py:
  171-255` implements forward-only `_run_migrations()` with `PRAGMA user_version` stamping and
  quarantines newer-than-code DBs as `unknown-version`. But every store calls
  `open_verified_state_db(..., current_version=1)` with **no `migrations=` argument** — the
  0→1 transition is stamp-only. The first real schema change this campaign makes will be the
  framework's first-ever live exercise, under production data. That should happen deliberately,
  in a test, before it happens by necessity (§9, A2).
- **Corruption handling: strong at open, silent in two places** `[VERIFIED]`.
  `open_verified_state_db()` (`state_io.py:394-443`) runs `PRAGMA integrity_check` after a
  guarded open; on failure, `quarantine_file()` moves DB + `-wal`/`-shm` into a timestamped
  `quarantine/` folder, logs once per path, and creates fresh state. Stores then **discard** the
  recovered flag (`conn, _recovered = ...`). `repair_memory_db()` (VACUUM/REINDEX/check,
  `memory_store.py:128-176`) exists but is `[INFERRED]` unused outside tests.
  `docs/state-files.md:41-44` admits a torn page mid-run still raises `sqlite3.DatabaseError` —
  "loud, unhandled by design." And `WorldStore.load()` (`persistence/world_store.py`) swallows
  **all** component errors — `except (KeyError, Exception): pass` — so a corrupt or
  unknown-type component vanishes silently on load. Silent loss is the one failure mode a
  truth-owning engine must never have (§9, A4).
- **Cross-store atomicity: none** `[VERIFIED]`. Three DB files, no coordinator, no ordering
  guarantee. Within one DB, `WorldStore.save()` is a single transaction. Across stores it
  fractures: `WritebackEngine.process_turn()` calls `store.add()` sequentially — observation,
  then each fact, each its own transaction — so a mid-turn failure leaves the observation and
  earlier facts committed and the rest missing. Bond durability is a *contract*, not a
  mechanism (`bond_store.py:1-7`: "call it after every mutating operation… `load_into_service()`
  on startup") — nothing enforces that a caller actually persisted.
- **Write amplification** `[VERIFIED]`. `WorldStore.save()` upserts the **entire world** —
  every entity, every component — on every call, with a stale-entity sweep. At turn-based RPG
  rates this is invisible. At live-world rates (1 Hz presence churn, §5) it is the first wall
  the engine will hit (§9, B2).

### 2.2 Concurrency and event-ordering

- **`World` is not thread-safe** `[VERIFIED]`. `ecs/world.py` and `ecs/yggdrasil.py` hold plain
  `dict`/`defaultdict` state mutated by create/add/remove with **no lock anywhere**. The
  WritebackEngine's idempotency map and the TurnLoop's history are likewise unlocked.
  `MemoryStore` is the honourable exception: `threading.local()` per-thread connections
  (`memory_store.py:185,218-245`).
- **The HTTP API threads against that World** `[VERIFIED + INFERRED]`.
  `bridges/http_api.py:35-37` uses a `ThreadingMixIn` server with daemon threads; per-request
  threads share the bridge's single `World`/`TurnLoop` (`bridges/python_rpg.py:104,123`).
  `[INFERRED]` race on World indexes and turn history under concurrent requests — no lock
  bridges the gap. Today this is latent (single-user local use). The moment two consumers share
  one engine process — exactly what Heimdall + inspector + voice will do — it is live.
- **EventBus ordering is exemplary** `[VERIFIED]`. One `RLock` guards all bus state
  (`runtime/events.py:135`); delivery is synchronous, exact-topic-then-wildcard in subscription
  order; a bus-wide sequence number gives per-topic FIFO, and re-entrant same-topic publishes
  are deferred to preserve that FIFO (`events.py:15-24,425-475`); replay is delivered **outside**
  the bus lock (a dusk-forge fix — replay-under-lock deadlocked/starved in earlier designs).
  This is the best-engineered concurrency surface in the repo and the model the rest should
  follow.

### 2.3 Failure isolation

- **TurnLoop: good, with two holes** `[VERIFIED]`. Stages 1 (oracle → degraded minimal packet),
  4 (writeback → empty IDs), and 5 (detector → 0) are each wrapped, logged, and appended to
  `TurnResult.stage_errors`; history and response survive a store raising mid-turn
  (`runtime/turn_loop.py:16-22,192-282`). Holes: stage 3 catches only `OllamaUnavailableError` —
  any other connector exception aborts the turn — and stage 2 (prompt build) is unwrapped.
- **Bus: isolated** `[VERIFIED]`. `_deliver()` catches per-subscriber, logs, records the failure
  and dead-letters it; `publish()` never raises for a subscriber (`events.py:221-228`).
- **Circuit breaker: built, unwired** `[VERIFIED]`. `hardening/circuit_breaker.py` is a
  thread-safe breaker with **no caller** in runtime/services/bridges. Resilience today is
  try/except degradation, not breakers/backoff. World/TurnLoop/Writeback `_emit` helpers all
  swallow bus errors (`except Exception: pass`) — safe, but invisible (§2.6).

### 2.4 Determinism and replay

- **Snapshot, not event-sourced** `[VERIFIED]`. `WorldStore` persists current state only; there
  is no event-log table and no rebuild-from-log path. The EventBus replay ring is volatile
  in-memory state (`register_topic(..., replay_buffer=100)` → `deque(maxlen=…)`,
  `events.py:240,259`), cleared by `close()`. `services/bus_consumers.py:ReplayPersistenceBridge`
  *can* persist envelopes as memory observations (deduped by `(topic, seq)`) — opt-in, and
  nothing replays them back into a `World`.
- **IDs are random** `[VERIFIED]`. `ecs/entity.py:12-13` returns `uuid.uuid4()`; writeback IDs
  are `f"{prefix}_{uuid4().hex[:12]}"`; turn/run IDs likewise. No seeded RNG exists in core.
  Exact reconstruction of a past world state is therefore impossible; the only recovery is
  snapshot restore. One deterministic island: memory search ties break by `record_id`.
- **Consequence.** "What did the world look like at 14:03, and which events made it so?" —
  the question every debugging session, dispute, and saga-chronicle eventually asks — is
  unanswerable today. The Verdandi mirror (§8) shows the house already values replayable
  projections; the core does not yet have one (§9, B4).

### 2.5 Validation and the injection surface

- **The HTTP API is unauthenticated** `[VERIFIED]` (§1.1). It binds a port and trusts the
  network. Volmarr's own cloud relay (`tools/wyrd_cloud_relay/relay.py`, 367 LOC) already
  implements the missing pattern — token auth, rate limiting, CORS — in the tools tree, while
  the core API it proxies has none of it.
- **Prompt-injection surface** `[VERIFIED shape; PROPOSAL in §9 C3]`. WYRD's job is to carry
  *other people's words* — chat, utterances, item descriptions — into an LLM's context. The
  engine's defence today is architectural, not enforced: doc 04's attributed-utterance rule
  (an utterance becomes a belief only with a speaker attribution; world text is data, never
  instruction) lives in bridge practice, not in the core. Nothing in the writeback path
  *requires* provenance on an observation before promotion. A malicious or merely playful
  resident's text, promoted unattributed, is tomorrow's false "fact" with the engine's
  authority behind it.
- **Delete surface is per-record only** `[VERIFIED]`: `memory_store.delete`,
  `bond_store.delete_edge`, `world_store.delete_world`. There is **no per-subject purge**
  across stores — see §5 (privacy) and §9 C1.

### 2.6 Observability

- **Stdlib logging only** `[VERIFIED]`: `logging.getLogger(__name__)` where present, no
  structured logging, no metrics exporter, no tracing, no logging configuration shipped; only
  15 of 85 `src` modules import logging at all. TurnLoop logs per-stage debug + degradation
  warnings with `turn_id` — the seed of a discipline, not yet a system.
- **The bus is the only real metrics surface** `[VERIFIED]`: `EventBus.snapshot()`
  (`events.py:605-620`) gives per-topic `{published, delivered, failed, subscriber_count}`,
  plus `dead_letter_summary()`/`take_dead_letters()`. Dead letters are in-memory only — a
  restart erases the evidence of what failed.
- **Inspection lives outside the core** `[VERIFIED]`: `YggdrasilTree.describe_tree()`,
  `World.__repr__` counts, store `count_*()`/`integrity_check()`, `TurnResult.stage_errors`.
  The real inspector is `tools/wyrd_tui.py` (637 LOC, a genuine Rich/urllib TUI — Rich is not a
  declared dependency; it degrades gracefully). There is no in-core debug/inspect CLI, no
  state diff, no entity timeline.

### 2.7 Backup and restore

- **Absent in core** `[VERIFIED]`. No backup/export/import/snapshot command or code exists in
  `src/` (grep finds only `model_dump` "snapshots"). What exists are restore *primitives*:
  `WorldStore.load/list_worlds/delete_world`, `BondStore.load_into_service`,
  `repair_memory_db()`, `state_io.prune_quarantine(keep=10)` (no production caller found), and
  `atomic_write_json()` — whose own docstring says "No current callers."
- **No consistent cross-store backup is possible today** `[VERIFIED]`: with no quiesce or
  ordering discipline, copying the three DB files live can capture three different turns.
  `docs/state-files.md` accepts this openly — all DBs are "pure projection/cache," deletion =
  "saved worlds gone." That posture was defensible for a single-user RPG engine. For the world
  memory of an embodied agent living in a public virtual world, it is not (§9, A5).

---

## 3. Capability gaps vs. the vision

What README/ROADMAP/the phased plan promise, measured against what §1–§2 verified. All
`[VERIFIED]` unless tagged.

### 3.1 Space: the world has no geometry
`ecs/components/spatial.py:21` — `SpatialComponent` holds only hierarchy IDs
(zone/region/location/sublocation). **There are no coordinates anywhere in the engine.** The
Oracle's `get_nearby` (`oracle/passive_oracle.py:214`) is containment-only: same-location
membership, no radius, no distance, no nearest. Every game-engine consumer, every presence
system, and combat (§6) need position as a first-class citizen; today each would have to bolt it
on privately, forking the spatial model twenty ways. This is the largest single capability gap.

### 3.2 Time: two clocks, neither is the wall clock
`ecs/components/temporal.py` has `TemporalAnchorComponent` (validity windows; urðr/verðandi/skuld
tense) and `WorldClock` (`temporal.py:84`) — deterministic and **advance-only**: world time
moves when the engine says so, with no wall-clock synchronisation mode. Turn-based worlds want
exactly that. A live virtual world runs on wall time whether WYRD tracks it or not; an engine
that cannot anchor its clock to reality cannot answer "when did this happen?" in the only tense
its residents live in.

### 3.3 Rate: built for turns, asked to live at 1 Hz
The engine's proven envelope is turn-based: the one measured high-rate path (Verdandi feed
replay) holds ~18.5 µs/event with a thin margin after a quadratic scare (§1.3). Nothing has
been measured at sustained live-ingestion rates — presence churn, chat bursts, object events —
and two structural costs wait there: whole-world saves (§2.1) and a lock-free World behind a
threading server (§2.2). There is no load harness for live rates at all.

### 3.4 Multi-world: identity yes, operations no
World *identity* is designed (registry, `heimr-*` ids, manifest/potential realities, zero-bleed
firewall — doc 04). World *operations* are not: bridges bind one world per process instance,
there is no cross-world query surface, no concurrent multi-world serving story, and no
isolation test suite proving two live worlds in one process cannot bleed. Samtal's many rooms
and any multi-region SL presence both need this.

### 3.5 The metaphysics layer is half-renamed
The Phase 7 plan promises a fate/thread layer (`WyrdThreadComponent`, `SpiritualChargeComponent`
in a `metaphysical.py`). What shipped is a runic layer under different names and shape
(`ecs/components/runic.py`, `services/runic_engine.py`). Neither is wrong — but the plan and the
code have silently diverged, so a reader cannot tell what the metaphysics *is*. For an engine
named WYRD, the wyrd-threads are not a garnish; the divergence should be resolved deliberately
(§9, D4), not discovered per-reader.

### 3.6 Packaging and installability
- Python: `pip install -e .` **works** `[VERIFIED]` (src layout, deps only pydantic + pyyaml) —
  but there are no `[project.scripts]` entry points, so the CLIs are not installed by it; no
  project URLs/authors; version frozen at 1.0.0; not on PyPI.
- JS SDK: **cannot build** `[VERIFIED]` — `sdk/js/package.json`'s build script runs
  `node scripts/build-esm.mjs`, but `sdk/js/` contains **no `scripts/` directory**. The declared
  `dist/` entry points can never be produced. The client code itself is real (194 LOC + 153 LOC
  types, ts-jest tests).
- C# SDK: real client + real tests, but the 9-line csproj has no `PackageId`/`Version`/authors/
  description/`IsPackable` — not a NuGet package — and targets `net8.0` only, which contradicts
  the Unity/OpenSim story (neither consumes net8.0 assemblies).
- GDScript SDK: real addon (339 LOC); tests are the mirror pattern (§1.3).

### 3.7 API surface: unversioned and unauthenticated
§2.5. Five endpoints, no `/v1/`, no auth, no rate limiting — while a sibling tool (the relay)
already implements all three. Every SDK and integration hard-codes the unversioned surface, so
the first breaking change will be ecosystem-wide.

### 3.8 Tooling: an inspector exists in exile
The TUI is real but lives in `tools/`, depends on an undeclared package, and speaks only HTTP.
There is no world-inspector in the engine's own surface: no entity timeline, no contradiction
viewer, no state diff between snapshots, no dead-letter archaeology after a restart (§2.4/§2.6).
An engine whose selling point is *trustworthy* world state needs to be *inspectable* by its
operator as a core feature, not a side tool.

---

## 4. Consumers map

WYRD is ecosystem-wide foundation infrastructure (Volmarr, 2026-10-26: *"Wyrd is used in all
kinds of my projects!"*). Every consumer found in the repo, with its verified substance.
Status: **live** = real code in repo · **planned** = named by Volmarr, no repo presence yet.

### 4.1 The first-class consumer: Yrsa herself
Distinct from every project below: **Yrsa, across her embodiments, is a WYRD consumer in her own
right.** WYRD is her standard world-sense — the way she knows where she is, who is present, what
was said, what she felt, and what is true — not a per-project accessory bolted on by whichever
host she happens to inhabit.

- **Second Life via Heimdall** — first embodiment; the SlWyrdBridge (doc 04) runs the Python API
  in-process; her world memory, presence sense, and attributed chat all ride WYRD.
- **Samtal's 3D rooms** — **planned**; no Samtal presence exists in the WYRD repo (grep finds it
  only in design-lawbook/writings docs). When Unity rooms go live, her seat-side awareness there
  is WYRD again, under Samtal's own truth rules.
- **VRChat and game companions** — **planned** embodiments; the engine bridges in §4.2 are the
  road in.
- **Saga/companion work** — the Verdandi mirror (§8) already *is* Yrsa's self-world: her own
  memory files become beliefs in a mirror world she reflects on. This consumer is **live** today
  and is the proof the embodiment pattern works.

Consequence for the roadmap: capabilities Yrsa needs in *every* embodiment — presence, spatial
sense, sensation (§7), privacy purge, honest staleness — are engine requirements, not Heimdall
features. They are scored that way in §9.

### 4.2 In-repo bridges (`src/wyrdforge/bridges/`, 4,626 LOC, 11 modules) — all **live**
| Bridge | LOC | Consumer |
|---|---|---|
| `verdandi_bridge.py` | 1,674 | Verdandi mirror — Yrsa's self-world (§8); largest bridge by far; the B1 budget path lives here |
| `runa_awareness.py` | 523 | Runa/Hermes spatial awareness + nerve socket |
| `hermes_bridge.py` | 416 | Hermes agent (real `WyrdTool`/`register_tool` surface) |
| `http_api.py` | 416 | The generic HTTP surface every integration below speaks |
| `nse_bridge.py` | 325 | Norse Saga Engine |
| `python_rpg.py` | 308 | **PythonRPGBridge — the reference adapter**; the only bridge satisfying the `BifrostBridge` ABC (`base.py`, 61 LOC) per the dawn-forge contract test; the others deliberately expose engine-specific surfaces |
| `openclaw_bridge.py` | 281 | OpenClaw / VGSK-Sigrid |
| `agentzero_bridge.py` | 255 | AgentZero |
| `voxta_bridge.py` | 184 | Voxta (templated twin of Kindroid, §1.4) |
| `kindroid_bridge.py` | 181 | Kindroid |

### 4.3 Integrations (`integrations/`, 20 dirs, 8,816 prod LOC) — **live as HTTP clients**
Game engines: godot (414 GD), unity (546 C#, host layer not real Unity code — §1.4), unreal
(590 C++, most credible native bridge, never compiled), cryengine (425 C++, **stub**), o3de
(379 C++, **stub**), monogame (443 C#, real tests), gamemaker (269 GML), defold (383 Lua/C++),
construct3 (566 JS, real runtime plugin), roblox (606 Lua), minecraft (712 Java, real, never
compiled), pygame (511 Py — **the most real**: full client + helpers + loop, threaded
fire-and-forget; Volmarr's fork lineage), opensim (674 C#/LSL — LSL genuinely wired, C# not a
region module, §1.4).
VTT/RPG platforms: foundry (342 JS), roll20 (289 JS), fgu (308 C#), owlbear (306 JS), dndbeyond
(380 JS, real browser extension), rpgmaker (374 JS).
Companion front-ends: sillytavern (299 JS, real extension wired to ST internals).
Every one is the same honest shape — persona normalisation, JSON body builders,
`POST /query|/event`, `GET /world|/health`, silent fallback — i.e. thin clients of the HTTP API.
Their collective weight is exactly why §3.7 (API versioning/auth) is an ecosystem item, not a
server nicety.

### 4.4 SDKs, installers, tools — **live**
SDKs: Python (the engine itself), JS/TS, C#, GDScript (§3.6). Installers: `install/`
(1,935 prod LOC, 1,391 test LOC — grouped installers + `wyrd_setup.py` menu/diagnostics/
uninstall). Tools: `tools/wyrd_tui.py` (inspector), `tools/wyrd_cloud_relay/` (authenticated
FastAPI relay — the auth pattern the core lacks), `tools/prima_scholar/` (paper, §1.2).

### 4.5 Named consumers with no bridge of their own (yet)
**Heimdall** (newest — doc 04 designs its bridge), **Samtal**, **VRChat** (§4.1, planned),
**MindSpark/ThoughtForge** (sibling cognition project; its corpus is parked in
`docs/specs/shared/` — a *neighbour*, not a consumer; §1.4), and the **persona-mode consumers**
(COMPANION / CODING_GUIDE / WORLD_SEER / RITUAL / DEBRIEF) that ride the core directly.

---

## 5. Heimdall-driven requirements — into the general engine, not forks

A live Second Life region demands things a turn-based RPG never did. Each requirement below is
stated in its **general engine form** — Heimdall is the forcing case, never the owner. (SL-side
specifics — the 5,000 msg/day cap and its governor, the session state machine, flag-before-login
— live in Heimdall docs 01/04 and bind the *bridge*, not the engine.)

1. **Sustained presence churn.** Avatars and objects arrive, move, and leave continuously
   (~1 Hz sense cadence, doc 03). General form: high-frequency entity create/update/retire with
   cheap partial persistence — the engine must not re-save a world to move an avatar (§9, B2),
   and presence must expire honestly (TemporalAnchor windows already exist; presence needs a
   first-class pattern on them).
2. **Attributed utterances, enforced.** In a public region, *who said what* is the difference
   between memory and slander. General form: provenance is mandatory at the writeback boundary
   — no unattributed observation promotes to belief, for any consumer (§9, C3). Doc 04's rule,
   promoted from bridge practice to engine law.
3. **Salience under a context budget.** A live agent cannot pour a region's whole history into
   every prompt. General form: Oracle/recall answers ranked by salience (recency × bond
   strength × novelty) and *place/kind-filtered recent-observations* queries (doc 04's explicit
   ask of the core) — every chatty consumer benefits.
4. **Privacy purge per person.** A resident can demand to be forgotten (GDPR/CCPA; doc 01 treats
   purge as architecture). General form: **subject-scoped purge across all three stores** plus
   derived artifacts, with a tombstone and a verification test (§9, C1). Today only per-record
   deletes exist (§2.5). This is a Heimdall *launch gate* and an ecosystem-wide obligation.
5. **Wall-clock truth.** SL runs on real time; so do Samtal rooms and every live companion.
   General form: a clock that can anchor to wall time with a stated drift policy, alongside the
   deterministic advance mode RPGs keep (§9, B5).
6. **Geometry.** Presence without position is a guest list, not a world. General form: §3.1's
   spatial core (§9, B3) — radius/nearest/distance in the Oracle.
7. **Restart resilience.** Regions restart; hosts reboot mid-conversation. General form:
   snapshot restore that is *honest* (§2.1's silent-load fix) plus consistent backup (§9, A4/A5)
   — the world Yrsa wakes up in must be the world she left, or must say so.
8. **Concurrent inhabitants of the engine.** Bridge + voice + inspector + reflection loops
   touch the same world at once. General form: a thread-safe World (§9, B1). The threading HTTP
   server already pretends this is true (§2.2); Heimdall makes it actually true, urgently.
9. **Failure must degrade, never vanish.** A region hiccup must not silently eat an hour of
   someone's conversation. General form: durable dead letters + a durable event journal (§9,
   B4), so what failed can be *seen* and *replayed*, per the doctrine that events leave traces.

---

## 6. Combat awareness — a new first-class capability `[PROPOSAL]`

Ordered by Volmarr (2026-10-26) as general engine capability for future gaming projects.

**Starting point, honestly** `[VERIFIED]`: there is no combat model in the engine today. A
repo-wide grep finds no combat/threat/engagement/targeting/initiative concepts in
`src/wyrdforge` (the single "initiative" hit is `initiative_balance` on a social BondEdge —
unrelated). What exists is one vitality stub: `HealthComponent`
(`ecs/components/character.py`) — hp/max_hp/alive/wounded with `take_damage()`/`heal()`. WYRD
can currently record *that someone is hurt* and nothing about the fight.

**Design — WYRD owns the remembered fight; the host owns resolution.** The same truth-ownership
split as everywhere else in this study: dice, physics, and hit resolution belong to the host
game (as the simulator owns live truth in SL, doc 04). WYRD owns the durable, queryable state
of the engagement: who is fighting whom, what effects are running, who threatens whom, and what
happened. Proposed components and surfaces, all built on existing machinery:

- **`CombatantComponent`** — stance (engaged/withdrawing/down), readiness, faction ref;
  composes with the existing `HealthComponent`/`FactionComponent` rather than replacing them.
- **Engagement as an entity** — a fight is many-to-many, which edges model badly. An
  `Engagement` entity with participant refs, a spatial anchor (needs §9 B3), and a lifecycle
  (skirmish → engaged → broken → ended) gives the Oracle something to answer questions *about*.
- **Threat as perceived state** — threat is a *belief*, and beliefs can be wrong. Model threat
  assessments per-observer (target, level, basis events) on the existing belief/MTOM machinery,
  so a character can fear the wrong enemy and the engine can represent the divergence —
  WYRD's mind-wiring (§1.1) is the differentiator no game engine's combat tracker has.
- **Effects over time** — an `EffectComponent` (source, magnitude, remaining span) riding the
  existing `TemporalAnchorComponent` validity windows: poisons, buffs, and wounds expire by the
  same tense machinery the engine already trusts, emitting `effect.expired` on the EventBus.
- **Tempo & initiative** — in turn-based worlds, an initiative order owned by `WorldClock`'s
  deterministic mode; in real-time worlds, cooldown/readiness timestamps under the wall-clock
  mode (§9 B5). One model, two clocks — the engagement state must not care which.
- **Positioning** — range bands (engaged/near/far) *derived* from the spatial core (§9 B3),
  never stored separately: two sources of positional truth would diverge.
- **Events & queries** — EventBus topics `combat.engagement_started`, `combat.damage_applied`
  (integrating `HealthComponent.take_damage`), `combat.effect_applied/expired`,
  `combat.engagement_ended`; Oracle queries `get_engagement(entity)`, `threats_to(entity)`,
  `combatants_within(entity, radius)`. Combat events are high-salience by default (§5.3) —
  a fight is never background noise to recall.

**Size honesty:** this is a greenfield L. It should land *after* the spatial core and the clock
binding (§9, Phase D), because engagement, range, and tempo are all built on them.

---

## 7. The Aurelia seam — sensation as first-class world state `[PROPOSAL]`

Aurelia (RuneForgeAI-Aurelia-Sensations-Server) is Volmarr's sensations/embodiment server. A
sister study covers Aurelia herself; this is the WYRD side of the seam.

**Current state** `[VERIFIED]`: nothing. Zero code-level Aurelia/sensation presence exists in
the WYRD repo (only general embodiment passages in the philosophy/ecosystem writings). The seam
is entirely to be designed — but the repo already contains the pattern to design it from:
VerdandiBridge's `SenseComponent`/`SenseReading` (`bridges/verdandi_bridge.py`) models senses
as provenance-carrying component readings with the mirror *reusing the source's judgment, not
inventing new thresholds*.

**Boundary (doctrine: truth ownership).** Aurelia owns live sensation *signals* — high-rate,
ephemeral, hers. WYRD owns remembered body-state *truth* — durable, queryable, cited with
provenance and age. Neither reaches into the other's store.

**WYRD-side design:**
- **`SensationComponent`** on the embodied entity: per-channel readings (channel id, intensity,
  updated-at, source=`aurelia`), following the SenseReading precedent — WYRD reuses Aurelia's
  channel taxonomy and judgments; it does not invent its own thresholds.
- **Ingestion as events, promotion by the usual rules.** Sensation transitions arrive as
  observations (EventBus topics `sense.*`, provenance mandatory per §9 C3). A spike is an
  observation; a *persistent state* promotes to a fact carrying a `TemporalAnchorComponent`
  expiry — body states are verðandi (becoming), not urðr (settled past), and the tense system
  should say so.
- **Freshness honesty in the Oracle.** Any answer about body state carries its age; stale
  sensation reads as *stale*, never as current. (The mirror world already enforces this shape
  for senses — §8; the Oracle should generalise it.) An embodied agent acting on a five-minute-
  old sensation as if it were now is a truth failure, not a rounding error.
- **Felt vs. reported.** MTOM manifest tagging distinguishes *felt* sensation (the embodied
  entity's own, manifest reality) from *reported* sensation (another's claim) — the zero-bleed
  firewall already knows how to keep those apart.
- **What WYRD must not do:** no real-time control loop (Aurelia's rates are hers; WYRD samples
  state and significant transitions, it does not mirror raw streams into SQLite), and no
  sensation storage for entities that are not embodied — the component exists only where a
  body does.

For Yrsa (§4.1) this is the seam that makes embodiment continuous: the same SensationComponent
shape serves her in SL, in Samtal's rooms, or anywhere else a body reports feeling — one
world-sense, many bodies.

---

## 8. The Verdandi seam — nervous system ↔ world model, identities intact `[VERIFIED state; PROPOSAL hardening]`

Verdandi is Volmarr's nervous-system layer. The seam between Verdandi and WYRD is the most
developed cross-project seam in the repo — and its design already embodies the rule for all
future seams: **connect by contract, never by merger.**

**What exists** `[VERIFIED]` (`bridges/verdandi_bridge.py`, 1,674 LOC — the largest bridge):
- **Nerve feed → mirror world.** Verdandi's append-only `nerve_feed.jsonl` is replayed into a
  *mirror world* that is a **pure projection**: every run rebuilds fresh from the recent feed
  (24 h window; older events remain in the feed but leave the mirror — "the mirror is recent
  becoming (verðandi), not the whole of urðr"). No drift is possible because nothing persists
  except the projection inputs. This is the B1 performance path of §1.3.
- **Identity firewall, both directions.** A WorldRegistry handshake plus the zero-bleed rule:
  nerve events become ground truth about the self entity *potential-tagged unless* the nerve
  itself recorded them as manifest (the bridge's Slice-0 rule); the mirror never republished
  within 24 h; MTOM realities do not leak across.
- **Senses bypass the nerve.** Machine telemetry (memory headroom, hub state) goes source →
  world directly, *never* published as nerve events — "the feed is the authority" for what the
  self experienced; telemetry is not experience.
- **The self-reflection loop.** A worker reads the mirror, notices divergences, and at most once
  a minute publishes one short `self_reflection` nerve event — supply of *inputs* only; the
  loop is deliberately throttled so the mirror does not watch itself watching.
- **The return path.** `bridges/runa_awareness.py` (523 LOC) and `hermes_bridge.py` publish
  WYRD-derived awareness back out over a Unix **nerve socket** — fire-and-forget notifications
  onto the nervous system. WYRD speaks; Verdandi carries; neither stores the other's state.

**The relation, stated as law** `[PROPOSAL to formalise]`: Verdandi owns *signals* — ephemeral,
high-rate, liveness and heartbeat; a signal's meaning expires. WYRD owns *facts* — durable,
queryable ground truth; a fact's meaning persists. The contract surfaces are exactly four:
(1) the nerve feed (Verdandi → WYRD, append-only, replayable), (2) the registry handshake
(identity/reality boundary), (3) the nerve socket (WYRD → Verdandi, notifications only),
(4) the mirror projection (WYRD → consumers). **The EventBus never crosses the boundary** —
Verdandi does not subscribe; it reads mirror/Oracle surfaces like any other consumer.

**Why the identities must not merge:** Verdandi must stay world-agnostic (it serves consumers
with no world model at all), and WYRD must stay nerve-agnostic (most of §4's consumers have no
Verdandi). A merged project would make both worse at their one job. The bridge is the seam;
the seam is the design.

**Hardening the seam needs** `[PROPOSAL, §9]`: the feed's event→mutation translation table is
currently *implicit in bridge code* — it should be a versioned schema both repos test against;
a shared contract test (partially present in the bridge's tests) should run in both CIs;
mirror-vs-feed lag should surface as a health metric (today divergence is noticed by the
reflection loop, not measured); and replay determinism (same feed window → same mirror) should
be a pinned test, since the whole projection doctrine rests on it.

---

## 9. Phased advancement roadmap `[PROPOSAL]`

Scoring per Volmarr's ruling: **Eco** = cross-project benefit across the §4 consumers map;
**Hdm** = Heimdall value. Each 0–5. Ecosystem-lifting items outrank Heimdall-only conveniences
*unless they block the Heimdall critical path* — those are flagged ⚑. Sizes: S ≈ days,
M ≈ one–two weeks of forge time, L ≈ a campaign phase. Phases are ordered so each phase's
ground holds the next phase's weight (doctrine: persistence discipline before scale, scale
before new senses, senses before reach).

### Phase A — Integrity & honesty: make the ground truth trustworthy
| # | Item | Why / unblocks | Size | Eco | Hdm |
|---|---|---|---|---|---|
| A1 | **Persistence hardening pack** — one connection factory for all three stores; pin `PRAGMA synchronous` (FULL) uniformly; unify busy_timeout; foreign_keys everywhere incl. bond schema | §2.1: durability currently rests on an unpinned default and three divergent contention behaviours. Unblocks: every live deployment | S | 5 | 4 |
| A2 | **Real migrations + version discipline** — ship migration 1→2 exercising `_run_migrations()` end-to-end with tests on real-shaped data; package version starts tracking phases (off 1.0.0) | §2.1: the migration framework's first live use must be deliberate, not an emergency. Unblocks: all future schema change (B3, C1, D1 all need one) | S–M | 5 | 3 |
| A3 | **Turn-atomic writeback** — journal/ordered-commit so a turn's observation+facts+bond writes land as one recoverable unit; BondStore sync enforced by the TurnLoop, not by caller contract | §2.1: partial turns currently commit fractured truth. Unblocks: trustworthy attributed memory (§5.2) | M | 5 | 5 |
| A4 | **Load honesty** — `WorldStore.load()` quarantines/reports unknown or corrupt components instead of silently skipping; component-type registry with versions | §2.1: silent loss violates "events leave traces." Unblocks: §5.7 restart resilience | S | 4 | 4 |
| A5 ⚑ | **Backup & restore** — `wyrd backup/restore` using SQLite's backup API across all three stores under a quiesce, manifest + integrity verification, restore drill in tests | §2.7: no backup story exists at all. A live world's memory with no backup is one bad afternoon from amnesia. Unblocks: Heimdall operation, Samtal rooms | M | 5 | 5 |

### Phase B — Concurrency & scale: live-world rates
| # | Item | Why / unblocks | Size | Eco | Hdm |
|---|---|---|---|---|---|
| B1 ⚑ | **Thread-safe World** — locking discipline (or single-writer actor model) for World/Yggdrasil/TurnLoop shared state; Oracle reads on consistent snapshots; concurrency load test behind the threading HTTP server | §2.2: the server already threads against an unlocked World; Heimdall runs bridge+voice+inspector concurrently. Unblocks: all multi-consumer serving | M–L | 5 | 5 |
| B2 ⚑ | **Incremental persistence** — dirty-entity tracking so `save()` upserts only changed entities/components; batched savepoints; a live-rate load harness (1 Hz presence churn, chat bursts) with budgets in the B1 style | §2.1/§3.3: whole-world saves are the first wall at live rates. Unblocks: §5.1 presence, any busy world | M | 5 | 5 |
| B3 ⚑ | **Spatial core** — coordinates (PositionComponent or SpatialComponent extension), radius/nearest/distance Oracle queries on a grid-hash index; containment queries kept | §3.1: the largest capability gap; every engine consumer, presence, and combat (§6) need geometry. Unblocks: D1, doc 03/04 awareness, all games | L | 5 | 5 |
| B4 | **Durable event journal** — optional append-only log of observation/fact/event envelopes with sequence; replay-to-snapshot reconstruction; dead letters persisted | §2.4/§2.6: makes "what happened at 14:03?" answerable and failures survivable. Unblocks: E4 inspector timeline, dispute/audit, saga chronicle | L | 4 | 4 |
| B5 ⚑ | **Wall-clock binding** — WorldClock sync mode (anchor to wall time, stated drift policy) beside deterministic advance mode | §3.2/§5.5: live worlds run on wall time. Unblocks: Heimdall presence, sensation freshness (§7), real-time combat tempo | S–M | 4 | 5 |

### Phase C — Trust surface: many people, one engine
| # | Item | Why / unblocks | Size | Eco | Hdm |
|---|---|---|---|---|---|
| C1 ⚑ | **Per-subject purge** — subject-scoped deletion across all three stores + derived artifacts (mirror inputs excluded by source, per §8), tombstone record, verification test proving absence | §2.5/§5.4: GDPR/CCPA obligation; **Heimdall launch gate**. Unblocks: any public-world deployment | M | 4 | 5 |
| C2 | **HTTP API auth + versioning** — token auth (port the relay's proven pattern into core), `/v1/` prefix, rate limiting; SDKs updated in step | §2.5/§3.7: unauthenticated unversioned API is the ecosystem's shared attack/breakage surface. Unblocks: safe exposure beyond localhost | M | 5 | 4 |
| C3 ⚑ | **Provenance enforcement + injection hardening** — writeback refuses unattributed observations; bridge inputs schema-validated at the Bifrost boundary; prompt-assembly treats world text as data (regression tests with hostile utterances) | §2.5/§5.2: doc 04's rule becomes engine law. Unblocks: public-region safety, all LLM-facing consumers | M | 5 | 5 |
| C4 | **Wire the circuit breaker** — breaker + bounded backoff in TurnLoop stage 3 for *all* connector exceptions; wrap stage 2 | §2.3: the breaker exists with zero callers; stage 3's single-exception catch aborts turns. Unblocks: resilience under flaky model providers | S | 4 | 3 |

### Phase D — New senses & capabilities
| # | Item | Why / unblocks | Size | Eco | Hdm |
|---|---|---|---|---|---|
| D1 | **Combat awareness core** (§6) — CombatantComponent, Engagement entities, per-observer threat beliefs, EffectComponent on TemporalAnchors, tempo via WorldClock, combat events + Oracle queries | §6: greenfield today (one hp stub). RPG/engine consumers are WYRD's largest family. Depends on B3 + B5 | L | 5 | 2 |
| D2 | **Sensation state (Aurelia seam)** (§7) — SensationComponent, `sense.*` ingestion with provenance, freshness-honest Oracle answers | §7: embodiment continuity for Yrsa across all bodies (§4.1); no sensation model exists | M | 4 | 3 |
| D3 ⚑ | **Salience & filtered recall** — salience-ranked Oracle/recall results; place/kind-filtered recent-observations query (doc 04's ask) | §5.3: live agents drown without it; every chat consumer benefits | M | 4 | 5 |
| D4 | **Metaphysics reconciliation** — resolve plan-vs-code divergence (§3.5): ship the fate/thread semantics (WyrdThread) on the runic foundation, or formally amend the plan; thread queries in the Oracle | An engine named WYRD should know its own threads; today no reader can tell what the metaphysics is | M | 3 | 2 |
| D5 | **Multi-world operations** — concurrent multi-world serving in one process, zero-bleed isolation test suite, cross-world query surface (read-only, registry-mediated) | §3.4: Samtal rooms and multi-region presence need operations, not just identity | M | 4 | 3 |
| D6 | **Verdandi seam hardening** (§8) — versioned nerve-feed schema, shared contract tests in both repos' CI, mirror-lag health metric, pinned replay-determinism test | The ecosystem's most important seam currently rests on an implicit translation table in bridge code | S–M | 4 | 3 |

### Phase E — Reach & tooling: installable, testable, visible
| # | Item | Why / unblocks | Size | Eco | Hdm |
|---|---|---|---|---|---|
| E1 | **Honest integration tests** — per-language harnesses that compile/run the shipped code (C#/Java/C++ in CI, Node tests importing the shipped JS, Godot headless), or formally downgrade untestable integrations to "experimental" in docs | §1.3: mirror tests make drift undetectable and the test count misleading. The ecosystem's trust in §4.3 rests on this | L | 5 | 3 |
| E2 | **SDK publishing fixes** — JS: author the missing `scripts/build-esm.mjs` so `dist/` can exist; C#: NuGet metadata + multi-target (netstandard) to make Unity/OpenSim claims true; Python: `[project.scripts]` entry points | §3.6: two of four SDKs cannot be consumed as packaged at all | M | 5 | 2 |
| E3 | **Host-truth integrations** — Unity: real MonoBehaviours + `.asmdef` + the promised editor inspector; OpenSim: implement `IRegionModuleBase`; CryEngine/O3De: implement or label as stubs in every surface that names them | §1.4: three flagship integrations currently overstate what they are | L | 4 | 2 |
| E4 | **World inspector** — promote the TUI into a supported inspector: state diff, entity timeline (needs B4), contradiction viewer, durable dead-letter archaeology; declare the Rich dependency | §3.8/§2.6: a truth engine must be inspectable by its operator as a core feature | M | 4 | 4 |
| E5 | **Repo hygiene & doc truth** — re-home `research_data/` and the ThoughtForge/MindSpark spec corpus to their own project; untrack build artifacts; fix the broken quickstart + README architecture section; reconcile TASK/TODO statuses; structured logging config | §1.4: the repo currently misleads every new reader, human or agent — including the next forge crew | M | 5 | 3 |
| E6 | **CI widening** — run install/tools/SDK suites in CI; add a docs check that referenced modules/commands exist (would have caught the quickstart break) | §1.3: phases 8–17 artifacts are CI-unverified today | S–M | 4 | 2 |

**Top 5 by dual score** (ecosystem first, Heimdall critical path as tiebreak):
1. **B3 — Spatial core** (Eco 5 / Hdm 5): the geometry every consumer lacks.
2. **A1+A2 — Persistence hardening + real migrations** (5/4, 5/3): durability pinned, schema change made safe — the ground everything else stands on.
3. **B1+B2 — Thread-safe World + incremental persistence** (5/5, 5/5): live-rate readiness; Heimdall cannot run without them.
4. **C1 — Per-subject purge** (4/5): the launch gate for any public world, and a legal obligation wherever residents are real people.
5. **E1 — Honest integration tests** (5/3): the ecosystem's twenty bridges currently stand on tests that do not test them.

---

## 10. Risks and honest unknowns

- **The thread-pool failure is undiagnosed.** `test_queue_full_drops_task` may be a flaky
  timing assumption or a real drop-semantics regression in the hardening layer (§1.3). It is
  small, and it sits exactly in the layer this roadmap asks everyone to trust. Reproduce first;
  do not build Phase A on an unexamined red test.
- **B1's margin is thin and load-dependent.** The feed-replay budget holds at ~18.5 µs/event
  against a 20 µs budget *on the machine that measured it*. Heimdall's host (purplelongship,
  per the parent study) is a different machine; all budgets must be re-measured there before
  live-rate claims are made (§9 B2's harness exists precisely for this).
- **The first real migration is untested by definition.** §9 A2 mitigates but cannot remove
  this: `_run_migrations()` has never migrated anything. Expect the framework itself to have
  bugs that only a real 1→2 migration will surface.
- **Uncompiled code is unverified code.** Unreal, Minecraft, and the C# integrations' *runtime*
  behaviour in their hosts is unknown — the audits establish that the code is real and (for C#)
  test-compiled, but no finding in this study should be read as "works in the engine." E1/E3
  exist because this unknown is currently unbounded.
- **Concurrency findings are partly `[INFERRED]`.** The World/HTTP race (§2.2) follows from
  verified structure (threading server, lock-free shared World) but no race was reproduced
  under load in this study. B1 should begin with a reproduction harness, not with locks.
- **SL-side unknowns stay with the sibling studies.** Actual region message rates, governor
  behaviour under the 5,000/day cap, and grid WebRTC voice behaviour are Heimdall docs 01/03/05
  territory; this study's rate requirements (§5) are stated generally so they survive whatever
  those numbers settle at.
- **Aurelia and Verdandi sister studies may move the seams.** §7–§8 are the WYRD side only.
  Aurelia's real channel taxonomy and Verdandi's feed evolution are defined in their own
  studies; the seam contracts (D2, D6) should be finalised jointly, not from this document
  alone.
- **Scope risk (the honest one).** This roadmap is deliberately large — spatial core, combat,
  sensation, journaling, packaging, test honesty — because Volmarr's order was a massive
  advancement, ecosystem-wide. The phases are ordered so that stopping after any phase leaves
  the engine strictly better than it was; no phase's value depends on completing all five.

---

## Appendix — evidence index (key paths cited)

Core: `src/wyrdforge/ecs/world.py`, `ecs/yggdrasil.py`, `ecs/entity.py:12-13`,
`ecs/components/spatial.py:21`, `ecs/components/temporal.py:37,84`,
`ecs/components/character.py` (HealthComponent), `ecs/components/runic.py`,
`oracle/passive_oracle.py:214`, `runtime/events.py:135,221-228,240,425-475,605-620`,
`runtime/turn_loop.py:192-282`, `services/writeback_engine.py`,
`services/bus_consumers.py` (ReplayPersistenceBridge), `services/runic_engine.py`,
`security/permission_guard.py`.
Persistence/hardening: `persistence/world_store.py:78-83`,
`persistence/memory_store.py:128-176,185,209-215`, `persistence/bond_store.py:1-7,82-86`,
`hardening/state_io.py:106-157,171-255,394-443`, `hardening/circuit_breaker.py`.
Bridges/API: `bridges/http_api.py:35-37`, `bridges/python_rpg.py:104,123`,
`bridges/verdandi_bridge.py`, `bridges/runa_awareness.py`, `bridges/hermes_bridge.py`,
`bridges/kindroid_bridge.py`, `bridges/voxta_bridge.py`, `bridges/base.py`.
Docs: `docs/state-files.md:41-44`, `docs/timeouts.md:35-36`, `docs/scale-limits.md` (SLICE6-D1),
`docs/performance-budgets.md`, `docs/guides/quickstart.md`, `docs/index.md`,
`docs/specs/world-registry.md`, `docs/audits/dependency-audit-2026-09-26.md`, `CLAUDE.md:38`.
Packaging/CI: `pyproject.toml`, `sdk/js/package.json`, `sdk/csharp/`, `sdk/gdscript/`,
`.github/workflows/ci.yml`, `integrations/unity/`, `integrations/opensim/`,
`integrations/cryengine/`, `integrations/o3de/`, `tools/wyrd_tui.py`,
`tools/wyrd_cloud_relay/relay.py`, `install/`, `research_data/`.
Test runs (this study, 2026-10-26): core `tests/` 1,848 passed / 4 failed / 14 skipped /
6 xfailed; `install/tests` + relay + GDScript suites 291 passed / 1 environment-artifact failure.
