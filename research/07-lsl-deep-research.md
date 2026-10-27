# LSL Deep Research — The Linden Scripting Language as Heimdall's In-World Hands

**Project:** Heimdall-SL-Hermes-Agent
**Commissioned by:** Volmarr Wyrd, 2026-10-26 ("massively research both the internal Second Life coding languages")
**Scope:** This document is the elder of the two internal languages: **LSL (Linden Scripting Language)**. Its sibling (document 06) covers RLV, the avatar-control dialect that LSL objects speak; the other internal language (Lua/SLua) is treated in its own study. Doc 03 established *that* a worn LSL companion object is a valuable complement to the LibreMetaverse protocol client; this document establishes *what that object can be*, in the only language it can be written in today.

**Tagging:** [Verified] = sourced below. [Inference] = engineering judgement built on sourced facts. Exact numeric limits are quoted only where a source states them.

---

## 0. Overview — why LSL matters to a protocol-level agent

Heimdall's body is a protocol client. Almost everything the avatar can perceive or do arrives over the grid protocol — but not everything, and not everything *cheaply*. LSL is the only code that executes **inside** the world, on Linden Lab's simulators, attached to the avatar herself. A worn LSL object ("the companion") therefore occupies a unique position in the architecture:

- It runs when the bridge is down. It survives the client, the host, and the network between them.
- It can ask the simulator questions the protocol exposes awkwardly or at the wrong cadence (agent-state flags, EEP environment, region health) and answer in one small packet instead of a protocol conversation.
- It is the only thing that can *originate* RLV at Yrsa (by speaking `llOwnerSay` at her own client — mechanics in doc 06), and the only thing that can receive HTTP requests addressed into the world (HTTP-in).
- It is also brutally constrained: 64 KB of memory per script, a 64-event queue, throttles on every channel that leaves the object, and URLs that evaporate on region restart. LSL rewards small, paranoid, state-machine-shaped programs and punishes everything else.

The design rule that falls out of every section below: **the companion is a sense-organ and a relay, never a brain.** Brains live out-world where memory is cheap. The companion senses, compresses, signs, forwards — and degrades gracefully when its lifelines (URLs, throttles, region health) fail.

---

## 1. The language itself

### 1.1 Shape and execution model

[Verified] LSL is a small, C/Java-flavoured imperative language whose programs are **state machines of event handlers**. Every script has a mandatory `default` state; named states are entered with `state <name>;`. Within a state, code exists only inside event handlers (`state_entry`, `touch_start`, `listen`, `timer`, `sensor`, `http_request`, `link_message`, `changed`, `attach`, `dataserver`, …). There is no `main()`, no threads, no blocking I/O in the modern sense — a handler runs to completion, then the next queued event is dispatched. (LSL Portal / events category: https://wiki.secondlife.com/wiki/Category:LSL_Events )

Consequences that shape all LSL programming:

- **Events do not interrupt each other.** They queue FIFO (`state_entry` may jump the queue). **If more than 64 events are waiting, new events are silently discarded** until slots free [Verified — https://wiki.secondlife.com/wiki/Category:LSL_Events ]. A bridge object in a chatty place lives or dies by handler speed.
- **State change is destructive:** on entering a new state the event queue is cleared, all listens are removed, repeating sensors stop (timers persist) [Verified — same source]. Well-built LSL therefore uses few states, and re-registers everything in `state_entry`.
- **Forced delays are part of the type system in practice.** Many functions carry a built-in sleep (e.g. `llEmail` 20 s, `llInstantMessage` 2 s, `llSetPos` 0.2 s) during which the script processes nothing [Verified — https://vectree.io/pdf/c/linden-scripting-language and per-function wiki pages]. The culture's answer is many small scripts in one linkset, each owning one delayed job, coordinating by link messages.

### 1.2 Types

[Verified] Seven types (LSL Portal, Types: https://wiki.secondlife.com/wiki/Category:LSL_Types ):

| Type | Reality |
|---|---|
| `integer` | 32-bit signed |
| `float` | 32-bit IEEE float — the *only* number type besides integer; no double, no decimal |
| `string` | Text; in Mono memory accounting, ~2 bytes/character (UTF-16-flavoured internals) [Verified — LSL Script Memory, https://wiki.secondlife.com/wiki/LSL_Script_Memory ] |
| `key` | A UUID carried as a distinct type; conversions to/from string are explicit casts |
| `vector` | 3 floats `<x,y,z>` — positions, colours, Euler rotations |
| `rotation` | Quaternion, 4 floats, stored s,x,y,z in literals `<x,y,z,s>` |
| `list` | Heterogeneous, ordered, **cannot contain lists**, no references — every assignment copies |

There are **no arrays, no structs, no user-defined types, no objects, no null** (the culture uses `NULL_KEY`, `""`, and `-1` as sentinels), no exceptions (runtime errors shout to DEBUG_CHANNEL and can halt the script), no file I/O, no dynamic code. Functions are user-definable with typed parameters and a single return value; recursion exists but spends the scarcest resource (stack) and is treated as a loaded weapon.

### 1.3 Memory: the 64 KB reality

[Verified — LSL Script Memory, https://wiki.secondlife.com/wiki/LSL_Script_Memory ; llGetFreeMemory, https://wiki.secondlife.com/wiki/LlGetFreeMemory ]

- **LSO (legacy VM):** a fixed **16 KB** total for bytecode + stack + heap. Still selectable; still how ancient content runs.
- **Mono (the modern VM, default for new scripts):** a **64 KB** limit per script, reducible with `llSetMemoryLimit`. Mono allocates only what it uses and shares bytecode between identical script instances in a region — but garbage awaiting collection still counts against the 64 KiB, and the limit is not enforced as hard-edged as LSO's; the practical failure mode remains the stack/heap collision crash.
- Per-value costs are not small: in Mono a list element costs roughly 16 bytes for an integer/float and 18 + 2/char for a string; a `key` element measured at ~102 bytes in revised tests [Verified — same memory page, figures community-measured and flagged approximate by the wiki itself]. **Keys are the most expensive thing you can put in a list** — store them as strings, or better, don't store them at all.

Design translation [Inference]: a companion script's working set must be *streaming*, not *accumulating*. Anything that grows (rosters, queues, logs) belongs in LinksetData (§1.5) or out-world.

### 1.4 The workarounds the culture built

Because there are no arrays/structs/maps, three idioms carry the entire ecosystem:

1. **Strided lists.** A flat list read at a fixed stride (`llList2String(records, i*3 + 1)`) is the universal "table". `llListSort`, `llListFindList`, `llListReplaceList`, `llListStatistics` are the query engine. Every LSL programmer learns to think in strides; every LSL bug database is full of stride arithmetic.
2. **JSON as a string format.** `llList2Json` / `llJson2List`, `llJsonGetValue`, `llJsonSetValue`, `llJsonDelete` (path syntax like `["a"][0]`) let a script treat a string as a document [Verified — LSL JSON functions, https://wiki.secondlife.com/wiki/Category:LSL_JSON ]. JSON values still materialise as lists/strings in memory when parsed — JSON is a *wire* format inside LSL, not a memory cure.
3. **Delimited strings + CSV.** `llParseString2List`, `llCSV2List`, `llDumpList2String` — the pre-JSON tradition, still everywhere in legacy code the project will meet (Experience KVP returns CSV, §2.7).

### 1.5 LinksetData — the modern key-value store

[Verified — llLinksetDataWrite, current revision https://wiki.secondlife.com/w/index.php?title=LlLinksetDataWrite&diff=cur&oldid=1212979 and https://create.secondlife.com/script/lsl-reference/functions/lllinksetdatawrite/ ]

LinksetData (grid-wide since 2022-era server releases) is a **per-linkset string→string datastore**:

- Capacity **131,072 bytes (128 KiB)** per linkset (the cap was 64 KiB at introduction and was later raised — older wiki revisions still say 64 KiB; the current function page says 128 KiB). Cost per pair = UTF-8 length of name + value (+32 bytes for protected pairs).
- **It is not script memory** — it does not consume the 64 KB; access cost is only the call and the returned string.
- **It survives script deletion, script reset (`llResetScript` and friends), and cloning** — it is a property of the linkset itself. Writing an empty string deletes the pair.
- API: `llLinksetDataWrite/Read/Delete`, `llLinksetDataListKeys`, `llLinksetDataCountKeys`, `llLinksetDataFindKeys` (regex), plus `…Protected` variants gated by a password string, and a `linkset_data` event broadcast to **all scripts in the linkset** on every update/delete — a built-in cross-script notification bus.

[Inference] For the companion, LinksetData replaces three legacy patterns at once: config notecards (slow, dataserver-bound), global-variable mirroring between scripts, and EEPROM-style state saves. It is where the companion's *durable in-world state* belongs: its current HTTP-in URL, the bridge's last-seen heartbeat, the RLV allowlist cache, sensor baselines — everything that must survive the script resets that §3 guarantees will happen.

---

## 2. The function library, by the domains a companion needs

Several hundred `ll*` functions exist [Verified — https://vectree.io/pdf/c/linden-scripting-language ]. Below: only the domains a bridge/companion object lives in.

### 2.1 Communication — speech and listening

| Function | Role for the companion | Hard edges [Verified] |
|---|---|---|
| `llSay(0,…)` / `llWhisper` / `llShout` | Local chat at 20 m / 10 m / 100 m | Channel 0 + DEBUG_CHANNEL traffic is throttled **< 200 messages / 10 s per region per owner**; over-rate messages are dropped but still count (https://wiki.secondlife.com/wiki/LSL_llListen ) |
| `llOwnerSay` | Private channel to the owner/wearer — the companion's "voice in her head", and the RLV carrier (§2.8) | Only the owner hears it; it is how attachments confess errors without spamming the room |
| `llRegionSayTo(target, channel, msg)` | Surgical delivery to one avatar/object **region-wide**, incl. to the avatar's *attachments* on negative channels — the cleanest object→HUD and object→object addressed channel | msg ≤ **1024 bytes**; channel-0 throttle: **200 msgs / 10 s per destination**, then a per-object **100 / 300 s** throttle, owner-wide and region-wide once tripped, apparently until region restart (https://create.secondlife.com/script/lsl-reference/functions/llregionsayto/ ) |
| `llRegionSay(channel, msg)` | Region-wide broadcast on non-zero channels | Cannot use channel 0; same 1024-byte ceiling family; treat as a flare, not a pipe [Inference] |
| `llListen(channel, name, id, msg)` + `listen` event | Filtered hearing; the filter order (channel → distance → id → name → text) is applied simulator-side | **Max 65 simultaneous listens per script**, then runtime error; listens die on state change/reset; unfiltered channel-0 listening is documented as "extremely laggy" and is an anti-pattern (https://create.secondlife.com/script/lsl-reference/functions/lllisten/ ) |
| `llInstantMessage` | Grid-wide IM to an avatar | **2.0 s forced delay** per call — a conversation tool, never a data pipe |
| `llMessageLinked(link, num, str, id)` | Intra-linkset messaging between the companion's own scripts | The correct substitute for almost every temptation to chat between one's own prims |

[Inference] **The companion's speech discipline:** default to silence in public chat; `llOwnerSay` for diagnostics; `llRegionSayTo` on a private negative channel for anything addressed; `llSay` only when the *character* (Yrsa) is meant to be heard by the room — and even then it is the client's job to speak as the avatar, not the object's. The 200/10 s owner-wide channel-0 throttle is shared with **everything else Yrsa's objects do in the region** — one chatty vendor nearby can spend the companion's voice. Budget accordingly.

### 2.2 HTTP — out (`llHTTPRequest`) and in (HTTP-in)

**Outbound** [Verified — llHTTPRequest, https://create.secondlife.com/script/lsl-reference/functions/llhttprequest/ ]:

- Throttled three ways: **25 requests / 20 s per object**, **1000 / 20 s per owner**, and an error throttle (5 HTTP 5xx in 60 s per script). A throttled call returns `NULL_KEY`; continuing to call **extends** the throttle — recovery needs ~40 s of silence. Sustained design rate ≈ **1 request/second**.
- Response body defaults to a **2048-byte** cap, raisable via `HTTP_BODY_MAXLENGTH` to **16384 bytes (Mono)** [Verified — https://wiki.secondlife.com/w/index.php?title=Template%3ALSL_Constants_HTTP&oldid=1210865 ].
- The simulator injects identity headers into outbound requests (`x-secondlife-object-key`, `-owner-key`, `-region`, `-local-position`, …) [Verified — LSL http server page, headers table, https://wiki.secondlife.com/wiki/LSL_http_server ] — the bridge can authenticate "this request really comes from her companion" by object key + a shared secret in the body, without any in-world crypto.

**Inbound (HTTP-in)** [Verified — LSL HTTP server, https://wiki.secondlife.com/wiki/LSL_http_server ; llRequestURL, https://create.secondlife.com/script/lsl-reference/functions/llrequesturl/ ]:

- `llRequestURL()` / `llRequestSecureURL()` grant a temporary public URL (ports 12046 / 12043); requests arrive as `http_request` events (GET/POST/PUT/DELETE); the script must `llHTTPResponse` **within ~25 s** or the request times out.
- **Incoming request bodies are limited to 2 KB.** Responses are likewise small (2 KB class). HTTP-in is a *doorbell and a keyhole*, not a file transfer.
- **URL quotas are land economics:** a region's URL pool is split like prim allowance by parcel; **each resident additionally has a personal pool capped at 38 URLs** (one per attachment point; a single attachment may use several). Attachments draw from the *wearer's* pool — for a worn companion this is the operative quota.
- Requesting URLs is itself throttled ("5-ish" rapid requests → denials until ~1 s of quiet; space requests ≥ ~0.6 s apart).
- **URLs are temporary by design** — lost on rez/derez, script reset/save, **region crossing/teleport (attachments)**, and **region restart** (`changed` + `CHANGED_REGION_START`). Every loss is detectable via events (`on_rez`, `changed`), and the standing pattern is a *registration protocol*: get URL → report it to the bridge over outbound HTTP → bridge treats any unregistered URL as dead. Persistent addressing needs an external rendezvous (the "object DNS" pattern the wiki documents) — in Heimdall, the bridge *is* the rendezvous (§4).

### 2.3 The legacy channels — email and XML-RPC

[Verified — LSL HTTP server intro, https://wiki.secondlife.com/wiki/LSL_http_server ; llEmail, https://wiki.secondlife.com/wiki/LlEmail ]

- **XML-RPC from LSL was discontinued altogether in June 2023.** Any design (or legacy product) still assuming `llRemoteDataChannel`/`llSendRemoteData` is dead. Do not build on it; expect to *meet* it in old open-source code and translate.
- **Email survives but is a postcard, not a pipe:** `llEmail` forces a **20 s script sleep**, messages are capped at **4096 bytes total**, an agent's objects are limited to **500 emails/hour** to outside addresses, and the receive path carries the ancient SVC-23 bug (an object can silently stop receiving email until region restart or a border crossing resets it — script reset does **not** help). Verdict [Inference]: email is suitable only as a last-resort "I am alive, my URL is gone" beacon from a *rezzed* object — never in the companion's critical path, and a worn attachment should not rely on it at all.

### 2.4 Link messages and multi-script structure

`llMessageLinked` / `link_message` is the companion's internal bus: instant, unthrottled by chat rules, addressed by link number with an integer + string + key payload. The standard architecture [Inference, matching universal practice]:

- **One script per delayed or throttled job** (an HTTP-out script, an email script if any, a sensor script), because a forced delay in one handler stalls that script's whole event queue.
- **One "registrar" script** owning LinksetData writes for shared state, to avoid write races between siblings (LinksetData itself raises `linkset_data` events to all scripts, which doubles as the change feed).
- A watchdog script whose only job is noticing silence (no heartbeat link-message within N seconds) and resetting the silent sibling via `llResetOtherScript` — LSL's honest answer to supervision trees.

### 2.5 Sensors and object/avatar queries — the companion's extra senses

| Capability | Functions | Limits / notes [Verified] |
|---|---|---|
| Proximity scan | `llSensor` / `llSensorRepeat` → `sensor` / `no_sensor` events, `llDetected*` accessors | Range **0–96 m**, arc 0–PI (PI = full sphere); types AGENT / ACTIVE / PASSIVE / SCRIPTED masks; **only the 16 nearest matches are returned**; attachments cannot detect their own wearer; no cross-region detection (https://wiki.secondlife.com/wiki/LlSensor ) |
| Directed raycast | `llCastRay` | Line-of-sight queries ("is there a wall/chair between me and X") the protocol interest-list can't phrase; returns hit list with positions/normals; rate-limited by its cost options — use sparingly [Inference] |
| Agent state flags | `llGetAgentInfo(id)` | Bitfield: AGENT_FLYING, AGENT_SITTING, AGENT_ON_OBJECT, AGENT_TYPING, AGENT_AWAY, AGENT_BUSY, AGENT_CROUCHING, AGENT_WALKING, AGENT_IN_AIR, AGENT_MOUSELOOK, AGENT_AUTOPILOT, **AGENT_AUTOMATED (0x4000 — "marked as a scripted agent/bot")**, AGENT_FLOATING_VIA_SCRIPTED_ATTACHMENT (https://wiki.secondlife.com/wiki/LlGetAgentInfo ). Caveat: unreliable immediately after a border crossing |
| Agent size/language | `llGetAgentSize`, `llGetAgentLanguage` | Bounding-box height (seated/standing postures distort it); language preference where public |
| Object interrogation | `llGetObjectDetails(id, [OBJECT_NAME, OBJECT_DESC, OBJECT_POS, OBJECT_ROT, OBJECT_VELOCITY, OBJECT_OWNER, OBJECT_GROUP, OBJECT_CREATOR, OBJECT_PRIM_COUNT, OBJECT_RENDER_WEIGHT, OBJECT_HOVER_HEIGHT, OBJECT_BODY_SHAPE_TYPE, OBJECT_IS_NPC?, …])`, `llGetObjectPrimCount`, `llKey2Name` / `llGetObjectName` | The in-world twin of the protocol's object properties — cheaper from inside for *targeted* questions ("what is she sitting on", "who owns this door") and available for objects the client hasn't streamed yet [Inference] |
| Region/parcel | `llGetRegionName`, `llGetRegionTimeDilation`, `llGetRegionFPS`, `llGetRegionCorner`, `llGetParcelDetails`, `llGetLandOwnerAt`, `llGetSimStats` (newer) | Time dilation (0.0–1.0) is the single best "is this region healthy" number a companion can report |

[Inference] The division of labour with doc 03's protocol senses: **the protocol client is the wide net** (interest list, coarse locations, chat), **the companion is the fingertip** — it answers specific, present-tense questions about the avatar's immediate situation that would cost the client a capability round-trip or aren't in its stream at all (her own agent flags, what she is sitting on, whether the region is dilating *right now*).

### 2.6 Environment — EEP, sun, and region time

[Verified] `llGetEnvironment(position, params)` reads Sky/EEP settings (day length, day offset, sun/moon directions and colours, cloud density, ambient, glow, etc. — the `SKY_*` parameter family); `llGetSunDirection()` and the newer moon functions give the classic quick read; `llGetRegionTimeDilation` covers health (see §2.5). On the write side, `llSetAgentEnvironment` / region-level environment functions exist but are land-permission-gated and mostly outside a companion's remit.

[Inference] For Heimdall this is *narrative-grade* perception: "golden hour", "midnight", "the region is lagging" are exactly the facts that let a scene digest (doc 03) read like a place instead of a database. The companion should sample environment on change-of-region and on a slow timer, and ship deltas only.

### 2.7 Experience Tools — persistent permissions and the 128 MiB store

[Verified — Category:Experience Tools, current revision https://wiki.secondlife.com/w/index.php?title=Category:Experience_Tools&diff=prev&oldid=1218518 ; llKeysKeyValue, https://wiki.secondlife.com/w/index.php?title=LlKeysKeyValue&diff=cur&oldid=1191988 ]

An **Experience** (a premium-account-owned grid-wide identity that scripts are compiled *into*) provides two things nothing else in LSL does:

1. **Persistent permissions.** `llRequestExperiencePermissions(agent, name)` asks **once** for a bundle (teleport, attach, animate, track camera, control camera, take controls, …); grants persist across logins and objects, surfaced via `experience_permissions` / `experience_permissions_denied` events. For a companion this replaces per-session permission dialogs — the difference between a HUD that nags and one that simply works.
2. **A real database.** The Experience key-value store: keys ≤ **1011 bytes**, values ≤ **4095 bytes**, total **128 MiB per experience**, all operations asynchronous via the `dataserver` event (`llCreateKeyValue`, `llReadKeyValue`, `llUpdateKeyValue` with checked-update semantics, `llDeleteKeyValue`, `llKeysKeyValue` for enumeration).

[Inference] 128 MiB of *grid-side, reset-proof, object-independent* storage reframes the companion's durability story: LinksetData (128 KiB, dies with the object) is the cache; the Experience KVP is the archive — wearer preferences, venue notes, the allowlist of doc 06, per-resident "do not approach" flags required by doc 01's privacy posture. Caveats to design around: scripts must be **compiled into the experience** (recompiling with a client lacking experience support silently strips the association), the experience must be *allowed* on the land for its in-world powers (its KVP storage works even where not allowed), and someone must own the experience (a premium account) — an open decision already flagged in doc 00.

### 2.8 Issuing RLV from LSL (mechanics only)

RLV's full study is document 06. The LSL mechanics, briefly [Verified by the RLV specification tradition and doc 06's sources]: an RLV-capable client listens for **owner-said** text beginning with `@`; an object owned by the same owner as the client session emits commands with `llOwnerSay("@command:option=value,…")`. A worn companion is therefore *physically able* to originate restrictions at its own wearer. In Heimdall this power is deliberately caged (doc 05/06): the companion relays RLV only from allowlisted sources over an authenticated channel, never originates policy itself, and treats its `llOwnerSay` RLV path as a privileged output on par with a system call. One LSL-specific trap [Inference]: `llOwnerSay` strings are also how the companion talks to *Yrsa herself* — RLV payloads and human-readable diagnostics share a channel, so the companion must namespace rigorously (RLV lines only ever as whole, exact `@…` messages; diagnostics never beginning with `@`).

### 2.9 Pathfinding "characters" — a warning, not a tool

[Verified — llCreateCharacter, https://create.secondlife.com/script/lsl-reference/functions/llcreatecharacter/ ; Linden feedback portal, https://feedback.secondlife.com/scripting-features/p/do-not-remove-pathfinding-without-a-replacement-in-place ]

The `llCreateCharacter` / `llPursue` / `llPatrolPoints` / `llWanderWithin` family turns a *rezzed object* into a navmesh-steered character. Status in 2026: the functions still exist and are documented, but in June 2025 Linden Lab publicly floated **deprecating and removing navmesh characters / llCreateCharacter entirely**, and the community response is an open feature-request battle — i.e., the ground is moving. For Heimdall the family is doubly irrelevant [Inference]: it moves *objects*, never the avatar (avatar locomotion belongs to the client, doc 02/03), and building on a subsystem under announced deprecation review would violate doc 00's risk posture. Recorded here so no future reader "discovers" it and re-litigates: **do not use pathfinding characters.**

---

## 3. Execution realities — the failure modes the companion must survive

### 3.1 Script time and region health

Scripts share the region's script-time budget; a region under load dilates time (`llGetRegionTimeDilation` → 1.0 is real-time, lower is slower) and event handlers simply run less often — there is no priority lane for our object [Inference from the dilation model; the metric itself is Verified, §2.5]. Timers slip, sensors return late, `http_request` events queue behind chat storms. Design stance: every companion behaviour must be *correct at any cadence* — idempotent handlers, timestamps from `llGetTime`/`llGetUnixTime` rather than tick counting, and no assumption that "every 5 seconds" means every 5 seconds.

### 3.2 The throttle table (design against this, not against hope)

| Channel | Limit [Verified] | Source |
|---|---|---|
| Channel 0 / DEBUG chat, per region per owner | < 200 msgs / 10 s, then drops (dropped msgs still count) | llListen page |
| `llRegionSayTo` channel 0, per destination | 200 / 10 s; then per-object 100 / 300 s, owner- & region-wide, ~until restart | llRegionSayTo page |
| Script chat message size | 1024 bytes (script-sent, non-zero channels); avatar chat 1023/254-byte quirks | llListen page |
| `llHTTPRequest` outbound | 25 / 20 s per object; 1000 / 20 s per owner; 5× 5xx / 60 s per script; recovery needs ~40 s silence | llHTTPRequest page |
| HTTP-in request body | 2 KB; response must start within ~25 s | LSL http server |
| `llRequestURL` cadence | ~5 rapid requests → denial until ~1 s quiet; space ≥ 0.6 s | llRequestURL page |
| URL pools | Parcel pool ∝ prim allowance; **38 per resident** personal pool | LSL http server |
| `llInstantMessage` | 2.0 s forced delay per call | per-function wiki |
| `llEmail` | 20 s forced delay; 4096-byte total message; 500/hr per agent's objects (external) | llEmail page |
| Event queue | 64 events; overflow silently discarded | Category:LSL Events |
| Listens | 65 per script | llListen page |
| Sensor results | 16 nearest, ≤ 96 m | llSensor page |

### 3.3 Crossings, attachments, resets — persistence truth table

[Verified, from §1.5/§2.2/§2.7 sources and the events category]

| Event | Script variables | Listens/sensors | HTTP-in URLs | LinksetData | Experience KVP |
|---|---|---|---|---|---|
| Script reset (manual/`llResetScript`/recompile) | **lost** | removed | **released** | **kept** | kept |
| State change | kept (globals) | removed; queue cleared | kept | kept | kept |
| Region crossing / teleport (attachment) | kept | kept (re-register prudently) | **lost** (`changed` fires) | kept | kept |
| Region restart | kept | kept | **lost** (`CHANGED_REGION_START`) | kept | kept |
| Object taken & re-rezzed | kept (scripts paused in inventory) | re-register on `on_rez` | **lost** | kept | kept |
| Script deleted | — | — | released | **kept** (it belongs to the linkset) | kept |

Two corollaries drive the whole companion design:

1. **Anything the bridge knows about the companion can go stale at a teleport.** The companion must treat *every* `changed(CHANGED_REGION | CHANGED_TELEPORT | CHANGED_REGION_START)` and `on_rez` as a re-registration moment: new URL, announce it outbound, resume. This is the in-world mirror of doc 05's Astrid-Law login sweep — *no silent shut-out*, applied to a 64 KB object.
2. **The script is the least durable thing in the system.** State that matters lives in LinksetData (object-scope) or Experience KVP (account-scope); state in variables is a cache with delusions.

### 3.4 The reset as a feature

Because resets are cheap, total, and survivable-by-design (state in the two stores, listeners rebuilt in `state_entry`), the companion should *embrace* a daily-or-on-error self-reset discipline [Inference]: it clears heap fragmentation, drops wedged listens, and re-runs registration — the LSL equivalent of doc 05's session hygiene. The watchdog pattern (§2.4) makes this safe to automate.

---

## 4. Tooling in 2026

LSL's toolchain is old, small, and — unusually for its age — still alive, because the language has exactly one vendor and a four-decade-stable spec culture.

- **In-viewer editing remains the ground truth.** Firestorm's script editor (with its preprocessor — `#include`, `#define` — a Firestorm feature, not an LSL one) is where scripts are ultimately compiled, because compilation is a simulator service: *only the grid can truly compile LSL.* Every external workflow ends in paste-into-viewer [Inference, universal practice].
- **LSL Editor (standalone).** The long-lived open-source Windows editor (SourceForge lineage) with offline syntax checking against its own function database — still the common recommendation for beginners; its database ages, so treat its "unknown function" warnings against create.secondlife.com, not as gospel [Inference].
- **VS Code is the modern home.** Community guides document full VS Code setups for LSL: syntax packages, and crucially a build task that runs **LSL-PyOptimizer** as a filter (preprocess with mcpp for `#include` support → optimize → paste) [Verified — https://www.unionmicro.net/documentation/visual-studio-code-for-lsl/ ]. A current VS Code "Second Life external editor" extension (handling the viewer's ExternalEditor temp-file flow, for both LSL and SLua) is maintained alongside generated LSL/SLua API JSON at https://slua.wlf.io/index.html [Verified].
- **LSL-PyOptimizer** (Sei Lisa) — the ecosystem's optimizer: a Python filter that shrinks Mono bytecode size (constant folding, dead-code and symbol tricks) exploiting deep knowledge of the Mono compiler's inefficiencies; explicitly **Mono-only** (it can bloat or break LSO) [Verified — https://wiki.secondlife.com/wiki/LSL-PyOptimizer and http://lsl.blacktulip-virtual.com/lsl-pyoptimizer/ ; source https://github.com/Sei-Lisa/LSL-PyOptimizer ]. For a 64 KB-caged companion, this is not vanity — it is headroom.
- **lslint** — the parser/linter lineage originating with Linden Lab's own tooling (bundled historically with the viewer source); Sublime Text's LSL package wires it as the default build/check system, with PyOptimizer as the alternative dialect build [Verified — https://raw.githubusercontent.com/Makopo/sublime-text-lsl/master/README.md ]. A lint pass in CI is the closest thing LSL has to a compiler check outside the grid.
- **Testing — the honest section.** There is no unit-test framework culture comparable to other languages. Practice is: (a) **Beta grid (Aditi)** testing with throwaway objects; (b) in-world test harnesses — scripts that `llOwnerSay` PASS/FAIL lines for pure functions (string/JSON/list logic is testable this way); (c) keeping pure logic *out* of event handlers so (b) is possible; (d) simulator-free syntax/optimization checks via the tools above. [Inference] For Heimdall: the companion's testable surface should be designed as pure functions over LinksetData-shaped inputs from day one, with a `test` state in the script that runs the suite on owner command — the LSL-native shape of doc 00's "prove it in tests" instinct.
- **Version control for scripts-in-objects.** Scripts live inside inventory objects; the discipline the ecosystem converged on [Inference, matching the VS Code guides above]: **source of truth is text files in git** (`.lsl`), the in-world object is a *build artifact*; the viewer's external-editor flow or paste deploys it; object name + a version string constant in `state_entry` (announced via `llOwnerSay` on reset) ties a running instance back to a commit. Never let an in-world edit become the only copy — it will, the once, you can least afford it.
- **Open-source LSL worth reusing.** (i) The wiki's own LSL Script Library (mixed licences — check per script) for idioms: string builders, list utilities, URL registration patterns. (ii) **LSL-PyOptimizer** (above) as a build tool. (iii) The large open-source RLV ecosystems (OpenCollar and kin) as *reading matter* for doc 06 — mature, real-world RLV issuance/parsing code, licence per project. (iv) Sei Lisa's published function libraries alongside the optimizer. In every case [Inference]: prefer *reading and re-implementing small* over importing wholesale — in 64 KB, every imported convenience is paid for in the same currency as features.

---

## 5. DESIGN — The Heimdall Companion (worn LSL object), specified

This section is [Design] throughout: a specification for the companion HUD/object that docs 03 and 05 call for, expressed against the verified realities above. Working name: **the Gjallarhorn Companion** (it announces; it does not decide). [Name is a proposal, flagged for Volmarr.]

### 5.1 Form and structure

- **Form:** a single worn attachment (HUD position or an innocuous worn prim), owned by Yrsa's account, scripts compiled into the project Experience (when the Experience decision lands, §2.7) with a non-Experience fallback build.
- **Scripts (one job each, §2.4):**
  1. `core` — lifecycle: attach/crossing/restart detection, registration protocol, watchdog heartbeats to siblings, LinksetData registrar. Owns `state_entry` discipline for the linkset.
  2. `senses` — sensor repeats + agent/object/environment sampling; writes compressed digests to LinksetData; raises `link_message` alerts on *changes*, not on every sample.
  3. `http-out` — sole caller of `llHTTPRequest`; a queue with token-bucket discipline at ~1 req/s sustained; the only script that ever touches the throttle.
  4. `http-in` — sole owner of `llRequestURL`; serves the bridge's queries from LinksetData snapshots; answers within the 25 s window or not at all.
  5. `rlv-relay` — the privileged neck: receives allowlisted RLV payloads (from `http-in`, authenticated) and emits them via `llOwnerSay`; refuses everything else (doc 06 governs policy; this script is the enforcement point).
  6. `watchdog` — pings siblings by link message; resets a silent sibling; if `core` itself wedges, the *bridge* notices the heartbeat stop (§5.4) — the watchdog cannot watch itself, and the design doesn't pretend otherwise.

### 5.2 Sense augmentation duties (what the protocol gets poorly, the companion gets well)

| Duty | Implementation | Cadence |
|---|---|---|
| Her own state, truthfully | `llGetAgentInfo(llGetOwner())` + `llGetObjectDetails` on her sit-target — flying/sitting/typing/away/automated flags, what she sits on | On change + 10 s poll, delta-only |
| Nearby agents, fingertip view | `llSensorRepeat` (AGENT, 32 m working radius inside the 96 m max, PI arc) + `llDetectedName/Key/Pos` → roster deltas (arrivals/departures only; full roster on request) | ~5 s sweep, throttled by region dilation |
| Nearby *interesting* objects | Targeted `llGetObjectDetails` on client-supplied keys ("what is that door/chair/portal") — the client asks, the companion answers from inside | On demand via http-in |
| Place & mood | `llGetRegionName`, `llGetParcelDetails`, `llGetEnvironment` sun/day data, `llGetRegionTimeDilation` + `llGetRegionFPS` as a health pair | On crossing + 60 s |
| Narration assists | Short, templated, *factual* digests composed in-world ("3 agents within 20 m; Erik seated at the long table; region time dilation 0.97; local dusk") written to LinksetData for the bridge to lift verbatim into the scene digest (doc 03) — the companion describes, it never interprets | On roster/place delta |

### 5.3 The bridge rendezvous (solving the evaporating URL)

1. On any (re)registration trigger (§3.3), `http-in` requests a fresh secure URL (spaced ≥ 0.6 s, retry with backoff on `URL_REQUEST_DENIED`).
2. `core` stores the URL in LinksetData and hands it to `http-out`, which POSTs `{object_key, url, region, build_version, nonce}` to the bridge's fixed endpoint, signed with the shared secret (body-carried; the simulator's `x-secondlife-object-key` header corroborates origin).
3. The bridge replies with a **lease** (e.g. 300 s). The companion re-announces on lease expiry, on URL change, or not at all if the bridge has gone quiet — in which case it keeps sensing into LinksetData and waits (§5.5).
4. The bridge never trusts a URL it didn't lease; the companion never trusts an http-in caller that doesn't present the current lease token. [Design — the token lives in LinksetData-protected pairs and rotates per registration.]

### 5.4 Heartbeat / presence token

- `http-out` emits a heartbeat POST every ~60 s (well inside the 25/20 s object budget — one request is nothing) carrying: region, dilation, roster count, current URL, free-memory and free-URL counts (`llGetFreeMemory`, `llGetFreeURLs`) — the companion's vital signs in one ≤ 2 KB packet.
- The bridge treats **two missed heartbeats** as "companion unreachable" and surfaces it (doc 05's no-silent-shut-out rule), distinguishing *bridge-down* (client offline — companion keeps logging to LinksetData/KVP) from *companion-down* (client online, no heartbeat — tell Volmarr to check the attachment).

### 5.5 Graceful degradation ladder

| Failure | Companion behaviour |
|---|---|
| URL quota exhausted / denied | Back off exponentially; keep sensing; announce via the *client-visible* channel of last resort — `llOwnerSay` diagnostics Yrsa's client can surface; retry on crossing |
| HTTP-out throttled (NULL_KEY storm) | Stop calling for ≥ 40 s (the documented recovery), shed load: heartbeat first, digests second, narration last |
| Bridge unreachable for > 1 h | Drop to *diary mode*: digests accumulate in Experience KVP (4095-byte values, §2.7) for later harvest; roster tracking continues; no public speech is ever generated by the companion itself, in any mode |
| Region dilation < 0.5 | Halve sensor cadence; suspend narration assists; heartbeat continues (it is now the most valuable packet — it *reports* the dilation) |
| Script memory pressure (`llGetFreeMemory` low) | Flush caches to LinksetData, self-reset the pressured script (watchdog-supervised) — resets are survivable by construction (§3.4) |
| Experience not allowed on this land | KVP writes for *powers* pause; storage still functions (§2.7); companion notes the fact in its digest — never argues with land rules |

### 5.6 What the companion will never do [Design invariants]

- Never speak publicly as Yrsa (speech is the client's act; the object has no voice) — §2.1.
- Never originate RLV policy — relay only, allowlisted only (§2.8, doc 06).
- Never hold the only copy of anything (bridge ↔ LinksetData ↔ KVP triplication for anything that matters).
- Never spend the owner's shared throttles (channel-0 chat, HTTP-out owner pool) on chatter — every budget in §3.2 is treated as *hers*, held in trust.

---

## 6. The five findings that matter most

1. **LinksetData changes what an LSL object can be.** 128 KiB of reset-proof, script-memory-free key-value storage *in the linkset*, with a change-event bus, turns the companion from a goldfish into an organism with durable in-world state (§1.5). It is the single most important LSL facility for this project that did not exist in the language's first fifteen years.
2. **HTTP-in is a doorbell with land economics, not a socket.** 2 KB bodies, ~25 s to answer, URLs that evaporate on teleport/restart, quotas split like prims plus a hard 38-per-resident pool (§2.2, §3.2). The entire bridge↔companion relationship must be a *registration and lease protocol*, designed for the URL to die — because it will, mid-session, routinely.
3. **The legacy data channels are dead or dying — plan burials, not features.** XML-RPC was discontinued in June 2023; email carries a 20 s sleep, a 4096-byte cap, a 500/hr ceiling, and a receive bug old enough to vote (§2.3). Anything in legacy open-source LSL that assumes them must be translated to HTTP-in/out or LinksetData on sight.
4. **Pathfinding characters are a deprecation risk and an architectural irrelevance.** Linden Lab floated removing `llCreateCharacter` in 2025 and the matter is unresolved in 2026; the family moves objects, never avatars (§2.9). Heimdall must not touch it — recorded here permanently so the idea stays buried.
5. **Experience Tools are the companion's second memory and its manners.** One-time persistent permissions plus a 128 MiB grid-side KVP store (keys ≤ 1011 B, values ≤ 4095 B) give the project reset-proof, object-independent storage and a HUD that asks permission once instead of forever (§2.7) — at the price of a premium-owned Experience and compile-into-Experience discipline. That trade is worth it, and the decision belongs on doc 00's open list.

---

## 7. Sources

- LSL Portal / Types — https://wiki.secondlife.com/wiki/Category:LSL_Types
- LSL Events (queue of 64, state-change semantics) — https://wiki.secondlife.com/wiki/Category:LSL_Events
- LSL Script Memory (16 KB LSO / 64 KB Mono, per-type costs) — https://wiki.secondlife.com/wiki/LSL_Script_Memory
- llGetFreeMemory (VM differences) — https://wiki.secondlife.com/wiki/LlGetFreeMemory
- LSL language overview (forced delays, library shape) — https://vectree.io/pdf/c/linden-scripting-language
- llLinksetDataWrite (128 KiB store, persistence, protected pairs) — https://create.secondlife.com/script/lsl-reference/functions/lllinksetdatawrite/ and https://wiki.secondlife.com/w/index.php?title=LlLinksetDataWrite&diff=cur&oldid=1212979
- LSL JSON functions — https://wiki.secondlife.com/wiki/Category:LSL_JSON
- llRequestURL (grant flow, request throttle, region-restart loss) — https://create.secondlife.com/script/lsl-reference/functions/llrequesturl/
- LSL HTTP server (URL lifetime, resource pools incl. 38/resident, 2 KB incoming body, identity headers, XML-RPC discontinued June 2023) — https://wiki.secondlife.com/wiki/LSL_http_server
- llHTTPRequest (25/20 s object, 1000/20 s owner, error throttle, NULL_KEY) — https://create.secondlife.com/script/lsl-reference/functions/llhttprequest/
- HTTP_BODY_MAXLENGTH (2048 default, 16384 Mono max) — https://wiki.secondlife.com/w/index.php?title=Template%3ALSL_Constants_HTTP&oldid=1210865
- llRegionSayTo (1024-byte cap; 200/10 s destination + 100/300 s object throttles) — https://create.secondlife.com/script/lsl-reference/functions/llregionsayto/
- llListen (65 listens/script; channel-0 <200/10 s per region per owner; message truncation) — https://wiki.secondlife.com/wiki/LSL_llListen and https://create.secondlife.com/script/lsl-reference/functions/lllisten/
- llEmail (20 s delay, 4096-byte cap, 500/hr, SVC-23) — https://wiki.secondlife.com/wiki/LlEmail
- llSensor (96 m, 16 results, attachment blindness) — https://wiki.secondlife.com/wiki/LlSensor
- llGetAgentInfo (full flag table incl. AGENT_AUTOMATED) — https://wiki.secondlife.com/wiki/LlGetAgentInfo
- Experience Tools (permissions; KVP key ≤1011 B, value ≤4095 B, total 128 MiB) — https://wiki.secondlife.com/w/index.php?title=Category:Experience_Tools&diff=prev&oldid=1218518
- llKeysKeyValue (128 MiB store, dataserver shape) — https://wiki.secondlife.com/w/index.php?title=LlKeysKeyValue&diff=cur&oldid=1191988
- llCreateCharacter (character mechanics) — https://create.secondlife.com/script/lsl-reference/functions/llcreatecharacter/
- Pathfinding removal discussion (Linden feedback portal, 2025) — https://feedback.secondlife.com/scripting-features/p/do-not-remove-pathfinding-without-a-replacement-in-place
- LSL-PyOptimizer (Mono-only optimizer) — https://wiki.secondlife.com/wiki/LSL-PyOptimizer ; http://lsl.blacktulip-virtual.com/lsl-pyoptimizer/ ; https://github.com/Sei-Lisa/LSL-PyOptimizer
- VS Code for LSL (PyOptimizer build-task workflow) — https://www.unionmicro.net/documentation/visual-studio-code-for-lsl/
- Sublime LSL package (lslint build system) — https://raw.githubusercontent.com/Makopo/sublime-text-lsl/master/README.md
- VS Code Second Life external-editor extension + LSL/SLua API data — https://slua.wlf.io/index.html

*Researched and written 2026-10-26 at Volmarr's order, as part of the Heimdall research library. Sibling documents: 03 (world awareness), 05 (mind-bridge), 06 (RLV deep research).*
