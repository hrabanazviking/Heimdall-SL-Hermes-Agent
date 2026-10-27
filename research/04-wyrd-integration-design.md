# WYRD × Heimdall — Integration Design

**Project:** Heimdall-SL-Hermes-Agent (headless Second Life agent) × WYRD Protocol (ECS world-model engine)
**Purpose:** Design how the WYRD world-model engine is integrated into Heimdall so the AI mind dwells in Second Life with grounded, persistent, queryable world truth — instead of reconstructing its surroundings from chat logs and prompt templates.
**Status:** Design proposal. Sections marked VERIFIED describe code read and tests run on 2026-10-26. Everything else is proposal, flagged as such.

**Sources drawn on** (all local):

- Heimdall repo (`~/workspace/heimdall-sl-hermes-agent/`): `SL_Headless_Hermes_Agent_Architecture.md`, `SL_Heimdall_Full_Architecture_Proposal.md`, `Heimdall_Architecture_Game_Plan.md`, `WORLD_MODELING_SKILL.md`, `README.md`
- WYRD repo (`~/workspace/wyrd-protocol/`): `src/wyrdforge/` (ecs, oracle, persistence, runtime, services, bridges), `WYRD_Research_Determinations.md`, `ROADMAP.md`, `TODO.md`, `DEVLOG.md`, `docs/api/http-api.md`, `TASK_PHASE1_ECS_CORE.md`, `TASK_wyrd_phase12a.md`, `integrations/opensim/`, `sdk/csharp/`

---

## Part A — Verified current state (2026-10-26)

### A.1 WYRD today: the engine EXISTS. The April audit is superseded.

Runa's audit of 2026-04-01 (`WYRD_Research_Determinations.md` §3.1) found the ECS core *missing*: no Entity, Component, System, World, Yggdrasil, Oracle, or persistence — only the wyrdforge memory/persona layer as starter code. **That verdict no longer holds.** Verified by reading the current tree and running the test suite:

| Piece | State today (verified) | Evidence |
|---|---|---|
| ECS core | **Built.** `World` (entity/component stores, tag + component-type indexes, O(1) queries), `Entity`, `Component` base with a `@register_component` registry, `System` base | `src/wyrdforge/ecs/world.py` (334 lines), `entity.py`, `component.py`, `system.py`; `tests/test_ecs_core.py` green |
| Yggdrasil Hierarchy | **Built.** `YggdrasilTree` over ECS components only (Parent/Container/Spatial): create zone/region/location/sub-location, place/move entities, co-location, ancestors with cycle guard, name search | `src/wyrdforge/ecs/yggdrasil.py`, `ecs/components/spatial.py` |
| Passive Oracle | **Built.** Read-only, 9 query methods (`where_is`, `who_is_here`, `what_is`, `get_fact`, `get_facts`, `get_relations`, `get_nearby`, `search_facts`, `build_context_packet`) returning Pydantic summaries + a pre-rendered `formatted_for_llm` block; memory-layer failures degrade to a world-only packet instead of raising | `src/wyrdforge/oracle/passive_oracle.py` (591 lines), `oracle/models.py`; `tests/test_forge_oracle.py` green |
| Event bus | **Built.** "Rúnakefli" EventBus: typed dotted topics with optional Pydantic payload schemas, synchronous FIFO delivery, wildcards, replay ring buffers, dead-letter queue, per-topic metrics; `World` emits `world.entity_created / entity_removed / component_added / component_removed` when bus-wired | `src/wyrdforge/runtime/events.py` (660 lines); `tests/test_dusk_bus_*.py` green |
| Persistence | **Built.** Three SQLite stores (WAL mode, verified-open with corruption quarantine): `WorldStore` (ECS entities/components as JSON), `PersistentMemoryStore` (all six memory stores + FTS5 search), `BondStore` | `src/wyrdforge/persistence/`; `tests/test_forge_persistence.py`, `test_bond_store.py` green |
| Six memory stores | **Built** as typed Pydantic records: Hugin observations, Munin episode summaries, Mimir canonical facts, Wyrd contradictions, Orlog policies, Seidr symbolic traces — every record carries TruthMeta (confidence, approval state), Provenance, Lifecycle, Governance | `src/wyrdforge/models/memory.py`, `models/common.py` (`StoreName`) |
| Write-back path | **Built.** `WritebackEngine` (`write_observation`, `write_episode_summary`, `write_canonical_fact`, `write_policy`, `process_turn`, idempotency keys), `MemoryPromoter` (scored promotion toward canonical), `ContradictionDetector`, `TruthCalibrator`, `SelfCorrectionService` | `src/wyrdforge/services/`; `tests/test_forge_writeback.py` green |
| Turn loop | **Built.** `TurnLoop.execute_turn`: oracle packet → generation → write-back → contradiction check, each stage fault-isolated, bus-announced | `src/wyrdforge/runtime/turn_loop.py` |
| BondGraph / Persona / MicroRAG | **Built.** `BondEdge` with trust/warmth/familiarity/devotion/safety vectors, scars, vows, `apply_event` deltas; `PersonaCompiler`; `MicroRAGPipeline` with 7 query modes incl. `WORLD_STATE` and `COMPANION_CONTINUITY`, budget-aware packet assembly | `models/bond.py`, `services/bond_graph_service.py`, `services/micro_rag_pipeline.py`, `models/micro_rag.py` |
| Security | **Built.** `PermissionGuard` (classifier-first, default-deny, audited decisions), `PromptInjectionGuard` (normalization-hardened) | `src/wyrdforge/security/` |
| HTTP API + SDKs | **Built.** `WyrdHTTPServer` on :8765 — `POST /query`, `POST /event` (observation \| fact), `GET /world`, `GET /facts`, `GET /health`; client SDKs for C#, JS, GDScript | `src/wyrdforge/bridges/http_api.py`, `docs/api/http-api.md`, `sdk/` |
| Bridges | **Built.** `BifrostBridge` ABC (`bridges/base.py`); `PythonRPGBridge` is the one full implementation (owns World + Yggdrasil + Oracle + Writeback + TurnLoop, `from_config` factory); `HermesWyrdBridge` wraps it as an agent-framework tool (`WyrdTool.run(persona_id, query)`) and forwards world events to a Unix "nerve" socket; ~20 engine adapters under `integrations/` | `src/wyrdforge/bridges/`, `integrations/` |
| OpenSim / Second Life (Phase 12A) | **Built — but a different shape than Heimdall.** A C# OpenSim *region module* + an LSL in-world NPC script that HTTP-queries WYRD and speaks the answer with `llSay()`; includes `AvatarRecord`/`AvatarMapper` (avatar → persona_id normalization + fact mapping) | `TASK_wyrd_phase12a.md` (status COMPLETE), `integrations/opensim/wyrdforge/`, `ROADMAP.md` §12A |

**Test verification (run for this design, 2026-10-26):** `PYTHONPATH=src pytest tests/ -q` → **1,849 passed**, 14 skipped, 5 xfailed, 1 xpassed, **3 failed** — the failures are peripheral to this integration: two `test_doc_policies.py` quarantine-parsing tests (documentation drift) and `test_b1_feed_replay_within_budget`, the timing test the DEVLOG already records as flaky under load. The integration-critical files (ECS, Oracle, persistence, bus, turn loop, write-back, bond store) pass 129/129.

### A.2 Heimdall today: complete design, no code

The Heimdall repo is **documentation only** — no `.cs`, `.py`, or project files exist yet. What exists is a thorough architecture: C# LibreMetaverse core (SessionManager, EventBus, CommandProcessor, ChatManager, MovementManager, Navigator, SocialManager, RLV Engine, …), a Python bridge API (WebSocket `/ws/events`, REST `/api/v1/*`, IPC over Unix sockets `/tmp/heimdall.sock[.events]`), a SQLite schema (session_state, rlv_restrictions, people, chat_history, inventory, landmarks), and a Hermes integration layer that builds the mind's context by **string template** (`Full_Architecture_Proposal.md` §9.1: "You are currently at {region}… You can see: {nearby_avatars}. Recent chat: {chat_history}…").

**Neither Heimdall architecture document mentions WYRD at all.** The integration designed here is new. The natural seam is exactly that §9.1 template: everything inside its braces is a world-state query — and world-state queries are what WYRD's Oracle exists to answer.

### A.3 The framing

WYRD's own planning always named Second Life as a Bifrost target (`README.md`: bridges for "Second Life, VR environments, and Python-based RPG frameworks"; the April audit: "Second Life and VR are Phase 5+"; `ROADMAP.md` Phase 12). Phase 12A delivered an *in-world script that consults WYRD*. Heimdall is the inverse and the completion of that idea: **a body that lives in Second Life, whose world model is WYRD**. In WYRD's vocabulary, Heimdall is the Second Life Bifrost bridge — the first bridge whose engine is a persistent social world rather than a game session.

---

## Part B — The integration design (proposal)

### B.0 Placement: who talks to whom

```
Linden Lab simulators
        │  SL protocol (UDP/CAPS)
┌───────▼────────────┐   IPC (Unix sockets, NDJSON)   ┌──────────────────┐
│ Heimdall.Core (C#) │ ─────────────────────────────► │ Heimdall.API (Py)│
│ LibreMetaverse     │ ◄───────────────────────────── │ Bifröst bridge   │
│ body, RLV, session │        commands                └───────┬──────────┘
└────────────────────┘                                        │ in-process
                                                              ▼
                                        ┌─────────────────────────────────┐
                                        │ SlWyrdBridge (new, Python)      │
                                        │ BifrostBridge subclass          │
                                        │  ├─ EventTranslator → ECS+Hugin │
                                        │  ├─ PassiveOracle (read path)   │
                                        │  └─ WritebackEngine (memory)    │
                                        └───────┬─────────────────────────┘
                                                │ WorldContextPacket / WyrdTool
                                                ▼
                                        Hermes mind (LLM) ── commands ──► back
                                        through Heimdall.API, gated by
                                        PermissionGuard + Orlog policies
```

Decisions embedded in this placement:

1. **WYRD runs in-process with Heimdall.API (Python), never inside the C# core.** The ECS, Oracle, and stores are Python; the C# side already speaks NDJSON over Unix sockets. The C# core needs *zero* WYRD knowledge — it keeps emitting the Heimdall events defined in `Full_Architecture_Proposal.md` §7.3. (The existing C# `WyrdForge.Client` SDK remains available for the Phase 12A OpenSim module, which is a separate consumer.)
2. **A new `SlWyrdBridge`** (proposed home: `wyrdforge/bridges/secondlife_bridge.py` in the WYRD repo, beside `hermes_bridge.py` and `python_rpg.py`) subclasses `BifrostBridge`, owns a `World` + `YggdrasilTree` + `PassiveOracle` + `WritebackEngine` exactly as `PythonRPGBridge` does, and adds the SL event translator. `HermesWyrdBridge`'s `WyrdTool` pattern is reused so the mind calls one tool — `wyrd_world` — for all world questions.
3. **WYRD is the mind's world model, not the body's controller.** Commands (say, move, teleport) still flow mind → Heimdall.API → Core as Heimdall designed them. WYRD sits on the *perception and memory* side first; it enters the action path only as a gate (PermissionGuard, §B.2.4), never as a driver.

**Host — SETTLED (Volmarr's ruling, 2026-10-26, re-confirming his 2026-10-10 choice):** everything runs on **purplelongship**, his older x86 laptop (Ubuntu, Ryzen 9 4900HS, ~38 GB RAM, RTX 2060 Max-Q) — *not* the Raspberry Pi 5 "Longhouse" the Heimdall architecture documents assume. All processes co-locate on that one machine: Heimdall.Core (.NET 8, native x86-64 — the ARM64 caveats in the Heimdall docs drop away), Heimdall.API, the WYRD stack, and the SQLite stores. Consequences for this design: the in-memory `World` + three SQLite stores are comfortably within ~38 GB RAM even for busy regions (§B.7.4 stays an empirical question, but a softened one); Unix-socket IPC stays same-host as both Heimdall protocols assume; and when the real-time voice loop lands (the shared Samtal ↔ Second Life voice work), a local STT/TTS sidecar has a real GPU to run on (RTX 2060 Max-Q) instead of contending for the CPU the protocol stack lives on. Yrsa has paired access to purplelongship.

### B.1 The mapping: Second Life onto the Yggdrasil Hierarchy

WYRD's hierarchy is World → Zone → Region → Location → Sub-location (`HierarchyLevel` in `ecs/components/spatial.py`, which also defines a non-spatial `OBJECT` level). Second Life's containment is grid → region (sim) → parcel → position. The mapping is almost suspiciously clean:

| Yggdrasil level | Second Life | Entity ID scheme | Notes |
|---|---|---|---|
| World | Second Life itself | `world_id = "second-life"` (one `World` instance) | `World.identify(kind="wyrd", reality="manifest")` — this world models a manifest place, unlike WYRD's fictional worlds |
| Zone | The grid | `sl:grid:agni` | Main grid = Agni. The Beta grid (Aditi) and any OpenSim grid become sibling zones — the Phase 12A OpenSim work lands here later without restructuring |
| Region | SL region / simulator | `sl:region:<region-uuid>` | Name in `NameComponent` ("Viking Village"); region handle + grid coordinates on a new `SLRegionComponent` (below) |
| Location | Parcel | `sl:parcel:<region-uuid>:<local-id>` | Parcels are SL's true social containers: own owner, own voice channel, own media, own rules. NameComponent = parcel name |
| Sub-location | Named place *within* a parcel | `sl:place:<region-uuid>:<slug>` | Landing point, the longhouse hearth, a skybox platform at 2,000 m (same parcel, functionally another place), a shop floor. Created from landmarks + learned anchors, not from protocol data — SL has no such concept, the mind needs one |

Entities placed *in* the tree via `YggdrasilTree.place_entity`: avatars, objects, and the agent herself get a `SpatialComponent` whose most-specific node is normally the parcel, refined to a sub-location when an anchor matches.

**Entities and components.** Existing registered component types are reused wherever they honestly fit (`name`, `description`, `status`, `spatial`, `parent`, `container`, `faction`, `inventory`, `temporal_anchor`). New SL-specific components are proposed as additions to `wyrdforge/ecs/components/` (each `@register_component`, Pydantic-strict, following the existing pattern):

| Entity kind | Tags | Components (existing → new) |
|---|---|---|
| Avatar (other) | `avatar`, `person` | `name` (legacy + display name in aliases), `description` (profile text, when fetched), `spatial`, **`SLPositionComponent`** (region-local x/y/z, global grid coords, rotation, sitting-on object id), **`SLPresenceComponent`** (online, idle/AFK, typing, muted/blocked flags, last-seen sim time), **`SLAvatarProfileComponent`** (account creation date, payment-info-on-file flag, group ids — only what the protocol/profile actually exposes) |
| The agent herself | `avatar`, `person`, `self` | All of the above + `persona_ref` (existing) binding the entity to the mind's persona id |
| Object / prim | `object` | `name`, `description`, `spatial`, `SLPositionComponent`, **`SLObjectComponent`** (owner entity id, creator id, kind: prim/mesh/attachment/notecard-source…, touchable, sittable, script-detected) |
| Group | `group`, `faction` | `name`, `description`, existing **`faction`** component (group id as faction_id; per-group roles/titles go to Mimir facts, not the component) |
| Region node | `region`, `spatial_node` | `name`, `parent`, `container`, **`SLRegionComponent`** (estate id/name, maturity rating, terrain/sim stats as last-reported: time dilation, FPS, agent count, flags e.g. damage-allowed) |
| Parcel node | `location`, `spatial_node` | `name`, `parent`, `container`, **`SLParcelComponent`** (owner avatar/group id, area m², flags: voice enabled, public access, allow damage, category) |

Relationship is **not** a component. What the mind feels/knows about a person lives in the BondGraph (§B.4); the ECS carries only the protocol-true link (friend flag, group co-membership) as Mimir facts. This keeps "SL says we are friends" (a fact) separate from "I trust her" (a bond) — they must never be conflated.

**Identity.** SL is UUID-keyed; WYRD's LLM-facing convention is normalized `persona_id`s (lowercase, underscores — see `docs/api/http-api.md` and the Phase 19C normalization audit). Rule: **ECS entity ids are `sl:`-prefixed UUIDs (stable, rename-proof); display/legacy names live in `NameComponent`; `persona_id` normalization is used only at the LLM/tool boundary**, exactly as Phase 12A's `AvatarMapper.ToPersonaId` already does for OpenSim. An avatar rename therefore changes one component field, not the entity — identity stability is an invariant (`WORLD_MODELING_SKILL.md` principle 8).

### B.2 Ingestion: protocol events → state mutations + observations

#### B.2.1 The pipeline

Heimdall.Core already defines its event vocabulary (`Full_Architecture_Proposal.md` §7.3, LibreMetaverse → Heimdall event mapping). `SlWyrdBridge.EventTranslator` subscribes to those events on the API side and fans each one out to **three sinks**:

1. **ECS mutation** — the live world model (positions, presence, containment).
2. **Hugin observation** — an `ObservationRecord` via `WritebackEngine.write_observation` (kinds already exist: `utterance`, `action`, `world_event`, `tool_result`, `emotion_signal`), with `participants`, `place_id` (the parcel entity id), `observed_at` in UTC.
3. **EventBus publication** — the same event re-published on WYRD's Rúnakefli bus under `sl.*` topics with registered Pydantic payload schemas, so any other consumer (Gjallarhorn notifications, the nerve socket pattern from `HermesWyrdBridge`, future dashboards) can subscribe without touching the translator. The World's own `world.*` topics fire automatically once the World is bus-wired.

| Heimdall event (§7.3 + proposed additions †) | ECS mutation | Hugin observation | Bus topic |
|---|---|---|---|
| `ChatReceived` (local/group) | speaker entity ensured; `SLPresenceComponent.typing` cleared | `utterance`, raw text, speaker, place = current parcel | `sl.chat.received` |
| `IMReceived` | sender entity ensured | `utterance`, place = null (IMs are placeless — record `place_id` as the *conversation*, not a parcel) | `sl.im.received` |
| `RegionChanged` | region node ensured; self re-placed (zone→region); parcel resolved on parcel-info arrival | `world_event` "arrived in region X" | `sl.region.changed` |
| `ParcelChanged` † (from ParcelManager) | parcel node ensured; self `SpatialComponent` updated to parcel (location level) | `world_event` on entry to a *new* parcel only | `sl.parcel.changed` |
| `AvatarAppeared` † / `AvatarUpdated` † (from `Objects` avatar updates) | avatar entity created/updated: position, presence | `world_event` only on first appearance in a session, not per update | `sl.avatar.appeared` |
| `AvatarLeft` † (out of interest range / left region) | presence → offline-in-region; entity kept (memory persists), removed from parcel container | `world_event` if they were in conversation range | `sl.avatar.left` |
| `SelfPositionUpdated` † (throttled, §B.2.3) | self `SLPositionComponent` + parcel containment if crossed | — (too frequent for Hugin; meaningful moves surface via ParcelChanged/Teleport) | `sl.self.moved` (replay off) |
| `ObjectInfo` / `ObjectAppeared` † | object entity created/updated | only for objects the agent touches, sits on, or is told about | `sl.object.seen` |
| `TeleportProgress`/`TeleportCompleted` † | self re-placed at destination on completion | `action` (own teleport, with destination) | `sl.teleport.completed` |
| `FriendOnline` / `FriendOffline` | presence component on the friend entity | — (state, not story; a *fact* update if it changes a Mimir "usually online" pattern is the promoter's business, not ingestion's) | `sl.friend.presence` |
| `AppearanceChanged` (self) | self description/inventory components | `action` (outfit change) | `sl.self.appearance` |
| Disconnected / reconnected | self `status` component | `world_event` (session boundary — also the episode trigger, §B.4) | `sl.session.changed` |

† Heimdall's §7.3 table is a starting subset. Avatar/object appearance and parcel transitions are standard LibreMetaverse surfaces (`Objects` / avatar tracking, parcel properties) but are **not in Heimdall's current mapping table** — adding them is Heimdall-side build work, listed in §B.5.

#### B.2.2 Who owns truth

Per `WORLD_MODELING_SKILL.md` (description is not state; explicit ownership; events leave traces; disciplined mutation), the ownership split is:

| Truth | Owner | Rule |
|---|---|---|
| What the simulator says *right now* (positions, who is in the region) | **Linden Lab's simulator**, mirrored by Heimdall.Core | The mirror is volatile and may be stale; WYRD never pretends the mirror is the territory — every ECS spatial/presence component carries `updated_at`, and the Oracle reports staleness honestly |
| Body/session state: connection, RLV restriction stack, inventory cache, L$ balance | **Heimdall** (its SQLite, per its schema) | WYRD does not duplicate the RLV stack. The mind's context joins the two at prompt time (§B.3); if the two ever disagree about *where the body is*, Heimdall wins for action, WYRD's belief is corrected by the next `RegionChanged`/`ParcelChanged` trace |
| What the mind *knows and remembers*: who it has met, what was said, what places are like | **WYRD memory stores** | Hugin = what was perceived (raw, attributed). Mimir = what has been promoted to canonical fact. The LLM owns nothing: narration is not state, and nothing the mind *says* in-world writes back as fact unless it passes through `WritebackEngine.process_turn` as an observation of its own utterance |
| Relationships as felt | **BondGraph** (WYRD) | Updated only by `BondGraphService.apply_event` from observed interactions — never set directly from SL's friend flag |
| Rules the mind must obey in-world | **Orlog policies + PermissionGuard** | TOS-derived policies (§B.2.4) live in Orlog and are included in every context packet by the Oracle automatically |

**The chat rule, stated once and enforced by the translator:** a chat message is an *utterance by a speaker*, stored as such. "`Erik said the bridge is closed`" is a Hugin observation attributed to Erik. "The bridge is closed" becomes state only if the mind perceives it or it survives promotion/truth calibration. This is the single most important anti-hallucination property of the whole integration, and WYRD's record model (provenance + truth metadata on every record) already supports it natively.

**Untrusted input:** local chat and IMs come from strangers. Every inbound text passes `PromptInjectionGuard` before it can reach a context packet, and is always rendered to the LLM as attributed observation, never as instruction. WYRD ships the guard; the translator is where it gets wired in.

#### B.2.3 Rate discipline

A busy region emits avatar/object updates at simulation rate. Feeding those raw into ECS writes + Hugin would drown both. Rules:

- **Position/presence:** mutate ECS in place, throttled to ~1 Hz per entity and on-stop; never write Hugin observations for movement alone.
- **Interest filter:** full entities only inside relevance range (parcel + ~100 m shout range); beyond that, the region node carries counts only ("14 avatars in region") without individual entities. Entities leaving range keep their identity and history but leave the parcel container.
- **Chat:** every message *is* an observation — chat is the story — but group-chat firehoses can be windowed: during high-volume periods the translator may batch consecutive group messages into one observation with multiple participants. (Flagged tuning knob, not a Phase-1 feature.)

### B.3 The Oracle surface: what the mind asks

The mind's questions, mapped to what exists and what the bridge adds. `PassiveOracle` is deliberately *not* modified in this design except where noted; the bridge composes.

| The mind asks | Answer assembled from | Status |
|---|---|---|
| "Where am I?" | `oracle.where_is(self)` → path grid → region → parcel (+ sub-location); bridge enriches with `SLPositionComponent` (local coords, "north-east corner of the parcel"), parcel owner from `SLParcelComponent`, region maturity from `SLRegionComponent` | Oracle exists; enrichment is bridge code |
| "Who is near me / who is here?" | `oracle.get_nearby(self)` / `who_is_here(parcel)`; bridge post-filters and sorts by true distance from `SLPositionComponent` (Oracle core is containment-based and knows nothing of metres) and annotates friends (Mimir fact) vs strangers | Exists + bridge post-processing |
| "What was just said?" | Hugin observations, kind = `utterance`, place = current parcel, newest first. The Oracle's packet fetch (`_fetch_recent_observations`) is *global* newest-N with no place/kind filter — **proposed small core addition:** `recent_observations(place_id=None, kind=None, limit=)` on the Oracle or an equivalent store-level query the bridge may call | **Small build** (flagged) |
| "What do I know about this person?" | `oracle.get_facts(avatar_id)` (Mimir: first met, groups, friend flag, name history) + `BondGraphService.excerpt(bond_id)` + MicroRAG in `COMPANION_CONTINUITY` mode for texture (bond + canonical + recent + symbolic) | All exist; bridge composes |
| "What do I know about this place?" | `get_facts(parcel/region)` + `search_facts` (FTS5) + MicroRAG `WORLD_STATE` mode (canonical + recent + symbolic + contradiction targets) | All exist |
| "What's happening around me right now?" (the default turn context) | `oracle.build_context_packet(focus=[self], location_id=parcel)` → `formatted_for_llm`, **replacing the §9.1 string template wholesale**. Heimdall appends its own block *after* the packet — active RLV restrictions, L$ balance, body status — keeping the ownership split visible in the very prompt: WYRD reports the world, Heimdall reports the body | Exists; this is the Phase-I3 swap |
| "What must I not do here?" | Orlog policies ride inside every context packet already (`active_policies`), incl. parcel rules promoted to policy (e.g. "no flying in this parcel" as observed) and the standing TOS policies of §B.2.4 | Exists |

Access paths, in order of preference: **(1)** in-process — the bridge and the mind's agent loop share the process, `WyrdTool.run(persona_id, query)` as `HermesWyrdBridge` already patterns it; **(2)** HTTP (`POST /query`, `POST /event`, `GET /world`, `GET /facts` on :8765) for the C# OpenSim module, debugging, and any out-of-process consumer; **(3)** direct store/ECS inspection for tests and the replay harness (§B.6).

### B.4 Memory write-back: sessions → episodes → facts → bonds

The write path already exists (`WritebackEngine`, `MemoryPromoter`, `ContradictionDetector`, stores). The integration defines *when SL life flows through it*:

1. **During a session:** conversation turns go through `WritebackEngine.process_turn` (as `TurnLoop` already orchestrates: observation of the exchange → candidate facts → contradiction check). Facts *asserted by the mind about the world* are written with provenance `source = self-utterance` and low initial confidence — the promoter, not the speaker, decides what becomes canon.
2. **Session end** (logout, disconnect, or a long idle boundary): the bridge writes a **Munin episode summary** (`write_episode_summary`): major events (regions visited, people met, conversations that mattered), open threads ("promised Erik she would return to the market on Friday"), retrieval tags (region names, avatar names). This is the unit the mind will later remember as "that evening in Viking Village."
3. **Promotion:** `MemoryPromoter` scores Hugin observations and episode content (confidence threshold 0.80, access-count ≥ 2 by default config) toward **Mimir canonical facts**: `avatar.first_met_region`, `avatar.groups`, `parcel.owner`, `parcel.landmark_name`, `region.maturity`, name histories. `TruthCalibrator` gates quality; conflicts (an avatar's renamed display name, a parcel sold to a new owner) become **Wyrd-store contradiction records** via `ContradictionDetector` — resolved by preferring the newer protocol-sourced fact and keeping the old one as history, never by silent overwrite.
4. **Bonds:** on first meeting, create a `BondEdge` (self ↔ avatar, domain social) via `BondGraphService.add_edge`, persisted by `BondStore`. Interactions apply *small, evidence-tagged* deltas (`apply_event(warmth_delta=…, trust_delta=…, source_record_id=<the Hugin observation>)`): a real conversation warms more than a passing "hi"; being helped warms more than being present. SL friendship acceptance updates a **Mimir fact** (`avatar.sl_friend = true, since …`) and may nudge familiarity — it does not set trust. Ruptures (being blocked/muted, a hostile exchange the TruthCalibrator flags) go through the existing scars/repair machinery rather than ad-hoc notes.
5. **What happens to Heimdall's own tables:** Heimdall's SQLite `people` and `chat_history` tables (its §6.1 schema) duplicate exactly this layer. **Proposal:** once write-back is live, WYRD becomes the system of record for people and conversation history; Heimdall's tables shrink to a short rolling transport log for debugging, explicitly non-canonical. (Migration call is Volmarr's — flagged in §B.7.)

### B.5 Exists vs must-build

**Exists in WYRD and needs no work:** ECS + Yggdrasil + component registry; Passive Oracle (9 queries, context packet, LLM rendering); Rúnakefli EventBus; all three persistence stores; six memory record types; WritebackEngine; MemoryPromoter; ContradictionDetector; TruthCalibrator; PersonaCompiler; BondGraph + BondStore; MicroRAG (incl. WORLD_STATE mode); PermissionGuard; PromptInjectionGuard; TurnLoop; HTTP API + C#/JS/GDScript SDKs; `HermesWyrdBridge`/`WyrdTool`; Phase 12A OpenSim bridge (patterns, persona normalization, LSL query path). **Verified by test run: 1,849 passing.**

**Exists in Heimdall and needs no redesign:** the full module architecture, event vocabulary (§7.3), IPC/WS protocols, SQLite body-state schema, RLV engine design. **But all of it is unbuilt** — the repo is docs-only today.

**Must be built (integration-specific):**

1. `SlWyrdBridge` + `EventTranslator` in wyrdforge (the heart of this design).
2. Five SL components in `wyrdforge/ecs/components/` (`sl_position`, `sl_presence`, `sl_avatar_profile`, `sl_object`, `sl_place` region/parcel pair) + registration + persistence round-trip tests.
3. `sl.*` bus topic registrations with Pydantic payload schemas.
4. Place/kind-filtered recent-observations query (small Oracle or store addition).
5. Heimdall-side event emitters beyond §7.3: avatar appear/update/leave, parcel changed, self position (throttled), teleport completed, object appeared.
6. Session-boundary → episode writer hookup + BondEdge auto-creation on first meet.
7. Orlog TOS policy set for scripted agents (§B.2.4) + PermissionGuard wiring on the outbound command path.
8. The offline replay harness (§B.6, Phase I0) — recorded/synthetic Heimdall event streams as fixtures.

#### B.2.4 (referenced above) TOS / policy layer

Linden Lab's scripted-agent policy requires, among other things, that scripted agents be identifiable and that bots respect rate limits and land rules. These become **Orlog policy records** (they then ride in every context packet) and **PermissionGuard rules** on outbound actions (say/IM rate, teleport frequency, payment actions default-deny without explicit approval). Heimdall's own docs already commit to visible-bot identification and throttle respect (`SL_Headless_Hermes_Agent_Architecture.md` §11.2); this layer is where that commitment becomes enforced code instead of documentation.

### B.6 Phased integration plan

**Phase I0 — One region, offline, answering questions (the smallest first proof).**
No SL account, no C# build. A fixture file of Heimdall-format events (hand-authored from §7.3's examples + the Game Plan's sample flows: login to "Viking Village", two avatars present, local chat exchanged, a teleport out) is replayed through `SlWyrdBridge`. Exit criteria, all as automated tests:
- The region/parcel/avatars exist in the ECS with the §B.1 mapping; `YggdrasilTree.get_co_located(self)` returns the two avatars.
- `where_is`, `get_nearby`, "what was just said" (place-filtered Hugin), and `build_context_packet` answer correctly from the replayed state.
- World + memory persist to SQLite and reload identically (WorldStore + PersistentMemoryStore round-trip).
- A chat claim ("the bridge is closed") exists as an attributed Hugin utterance and **nowhere** as a Mimir fact.

**Phase I1 — Live read-only embodiment.** Heimdall.Core + API built per its own roadmap (login, presence, chat, movement); the translator consumes the real event sockets. The mind can answer where/who/what-was-said about the live grid. Outbound actions remain purely Heimdall's; WYRD is not yet in any decision path. Exit: a live session in one home region, questions answered from WYRD state, staleness timestamps visible.

**Phase I2 — Memory write-back live.** Session-end episodes, promotion to Mimir, BondEdges for people met, contradiction handling. Heimdall's `people`/`chat_history` tables retire to debug-log status (pending §B.7 decision). Exit: log out, restart everything, log in — the mind still knows who Erik is, where the market parcel is, and that it promised to return.

**Phase I3 — Oracle in the loop.** The §9.1 prompt template is replaced by `build_context_packet` output + Heimdall's body block; conversation turns run through `TurnLoop`/`process_turn`; outbound commands pass PermissionGuard + Orlog TOS policies. Exit: a full conversational session in-world where every world claim in the mind's context traces to an Oracle packet.

**Phase I4 — Two-way Bifröst.** WYRD events flow back out: bond formed / contradiction opened / episode closed publish to the bus and onward via the `HermesWyrdBridge` nerve-socket pattern to Heimdall's Gjallarhorn notification module (Volmarr hears "she met someone new" without reading logs). Aditi (Beta grid) and an OpenSim grid join as sibling zones; the Phase 12A module becomes a second consumer of the same world. Skybox/anchor sub-locations learned from landmarks.

### B.7 Risks and open decisions — flagged, not decided

1. **Host — SETTLED, not open.** Volmarr ruled 2026-10-26: **purplelongship** (x86 Ubuntu laptop, Ryzen 9 4900HS, ~38 GB RAM, RTX 2060 Max-Q), re-confirming his 2026-10-10 decision and overriding the Heimdall repo docs' Raspberry Pi 5 "Longhouse" assumption. The design in §B.0 is written for that machine. (Recorded here only so the docs' Pi 5 passages are never mistaken for a live question.)
2. **Persistence layout.** WYRD already wants three SQLite files (world, memory, bonds) and Heimdall wants its own. One directory, four files, one backup story is the low-surprise option; merging databases is possible (all are SQLite/WAL) but couples upgrade cadences. Undecided.
3. **ECS implementation choice.** This design assumes WYRD's own Python ECS (in-process with the bridge). An alternative — a C# ECS inside Heimdall.Core with WYRD as a consumer — would invert every dependency WYRD already has (Oracle, stores, TurnLoop are Python) and is *not* recommended, but the option is named so its rejection is a decision, not an accident.
4. **Scale ceiling.** WYRD's `World` is in-memory with full-save persistence; WYRD's own `docs/scale-limits.md` should be re-read against a busy SL region (hundreds of avatars, thousands of objects in interest range). The §B.2.3 interest filter is the mitigation; whether save-on-interval suffices or the World needs region sharding is an empirical question Phase I1 will answer. purplelongship's ~38 GB RAM (§B.0) gives real headroom, so this is a throughput question, not a capacity cliff.
5. **Chat retention & other people's privacy.** Hugin stores what strangers said, verbatim, indefinitely by default. LL's scripted-agent policy, group/parcel rules, and basic courtesy may demand retention limits or parcel-level opt-outs ("do not record here" as an observed parcel rule → Orlog policy). The mechanism exists (retention classes on every record); the *policy* is Volmarr's call.
6. **Name collision to resolve before code:** the Game Plan's §11 voice module is named "Mímir" — but **Mimir is already WYRD's canonical-fact store**. One of them must be renamed before the codebases meet, or every future conversation about "Mimir" will be ambiguous.
7. **Heimdall's `people`/`chat_history` retirement** (§B.4.5) touches Heimdall's published schema — proposed here, decided by Volmarr.
8. **Estate level.** SL estates group regions under one owner/ruleset. The mapping in §B.1 puts grid at Zone and skips estate; if estate-level rules (e.g. a whole estate's covenant) matter, estate wants a node — either as Zone (demoting grid to World-level metadata) or as a tag/fact on regions. Deferred until a concrete need appears.
9. **Clock discipline.** SL simulator time, parcel media time, and UTC are three different things; all WYRD records are UTC-stamped and SL-side times are stored as reported. Episode boundaries ("that evening") are session-based, not wall-clock-based, which sidesteps most of this — but scheduled in-world events will reopen the question.

---

*Design ends. Verified state: Part A. Everything from Part B onward is proposal awaiting Volmarr's word — no code was written, no README touched, nothing pushed.*
