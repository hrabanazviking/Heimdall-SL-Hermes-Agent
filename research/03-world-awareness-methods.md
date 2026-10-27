# World Awareness Methods for a Second Life Agent

**Project:** Heimdall-SL-Hermes-Agent (plugin for the Hermes AI Agent)
**Topic:** What a connected Second Life agent can perceive, through which protocol/library mechanisms, and how those streams can be fused into a world model an LLM can actually use.
**Date:** 2026-10-26
**Status:** Research document. Not a design commitment.

**How to read the confidence tags.** Claims are tagged so that verified protocol facts are never confused with engineering judgment:

- **[Verified]** — stated in protocol documentation, official Linden Lab material, the LSL reference, or the LibreMetaverse/OpenMetaverse library documentation cited in the Sources list.
- **[Inference]** — a reasonable engineering conclusion drawn from verified facts, but not itself documented as a feature. Treat as "very probably true, confirm during prototyping."
- **[Uncertain]** — plausible, partially documented, dated, or known to vary; flagged so nobody builds on it blind.

---

## Overview

A Second Life agent does not perceive the world the way a human at a graphical viewer does. It receives **structured data streams** — packets and capability (CAPS) responses — that the simulator sends to any connected client. The graphical viewer turns those streams into pictures; a scripted agent can instead turn them into an explicit, queryable world model. In several respects the data feed is *richer* than human vision (exact positions, UUIDs, ownership, state flags for everything streamed), and in one respect it is far poorer: **there is no rendered image and no true visual sense** unless the agent builds one itself out of geometry and texture assets.

The practical stack for a .NET-based agent is **LibreMetaverse** (the maintained fork of OpenMetaverse / libsecondlife), whose `GridClient` exposes one manager per sense domain: `Self` (AgentManager), `Objects` (ObjectManager), `Avatar` (AvatarManager), `Parcels` (ParcelManager), `Groups` (GroupManager), `Friends` (FriendsManager), `Inventory` (InventoryManager), `Assets` (AssetManager), `Appearance` (AppearanceManager), `Grid` (GridManager), and `Network` (NetworkManager). Each manager exposes state, operations, and **events** — the event surface is what an awareness pipeline subscribes to. **[Verified]** — LibreMetaverse manager architecture: https://deepwiki.com/cinderblocks/libremetaverse/1.2-getting-started and https://deepwiki.com/cinderblocks/libremetaverse/2.1-gridclient

Three structural facts shape everything below:

1. **The server decides what you may know, via the "interest list."** The client sends camera frustum information to the server, and the server streams information about objects it deems "interesting" based on that frustum and a size-versus-distance heuristic. An agent therefore only receives object/avatar updates for its *interest volume* — roughly, its draw distance around its camera — not for the whole region. Awareness engineering in SL is largely the art of managing this volume. **[Verified]** — https://wiki.Secondlife.com/w/index.php?title=Culling&oldid=80015
2. **Text clients prove the model.** Radegast, a lightweight text-oriented client built on the same library lineage, offers local/IM/group chat, a region radar, an object finder, inventory, movement, and gestures with no 3D rendering at all — existence proof that a complete, socially functional presence can run purely on the structured streams described here. **[Verified]** — https://wiki.secondlife.com/w/index.php?title=Downloads&diff=prev&oldid=712382
3. **Compliance is part of perception design.** Linden Lab's Scripted Agent Policy requires an account primarily operated by a scripted agent to be flagged on the Scripted Agent Status page; using bots to inflate parcel traffic is a policy violation, as are bots causing unreasonable load or nuisance. Message volume is also capped (see §7.5). Awareness features must be designed inside those limits from day one. **[Verified]** — https://wiki.secondlife.com:443/w/index.php?title=Linden_Lab_Official:Scripted_Agent_Policy&diff=prev&oldid=1143288

---

## 1. Hearing — chat, IMs, and voice

Hearing is the agent's richest and most reliable sense. It arrives in four distinct channels with very different physics.

### 1.1 Local (nearby) chat

Local chat is range-bound, and the ranges are hard protocol facts an awareness model should encode directly:

| Mode | Range | Notes |
|---|---|---|
| Whisper | 10 m | Also the conventional mode for scripted objects (vending machines etc.) so as not to disturb others |
| Say (normal) | 20 m | Default public chat |
| Shout | 100 m | For crowds / distance |
| llRegionSay (scripts) | Whole region | Script-to-script only; **not rendered to viewer clients' chat** — an agent client should not expect to "hear" region-say traffic the way a listening script does |

**[Verified]** — Second Life Wiki chat documentation and LSL function ranges: https://dev1.slideserve.com/baxter/connecting-to-the-island-powerpoint-ppt-presentation (mirrors the LSL reference values: llWhisper 10 m, llSay 20 m, llShout 100 m, llRegionSay region-wide, "not received on clients").

Every local chat message arrives (via the `ChatFromSimulator` message, surfaced by LibreMetaverse as a chat event) with structured metadata, not just text. The best public documentation of exactly what a bot receives is the **Corrade** scripted-agent framework's `local` notification, which passes through: the sender's **name**, the **owner UUID** (person) and **item UUID** (object), the **position** the chat originated from, an **entity type** (`System`, `Agent`, or `Object`), an **audible level** (`Not`, `Barely`, `Full`), a **volume** (`Normal`, `Whisper`, `Shout`), and the **region** name. **[Verified]** — http://grimore.org/secondlife/scripted_agents/corrade/api/notifications/local

Two consequences matter for awareness design:

- **Object chat is a first-class input.** Because the source type and object UUID are known, the agent can distinguish a person speaking from a door, vendor, game, or greeter script speaking — and can build a per-object conversation model. Objects speak on the public channel 0 to be seen, and on arbitrary numbered channels for script chatter.
- **There is a "barely audible" penumbra.** Corrade documents messages that arrive with audible level `Barely` and *no message body*: the agent can sense that someone spoke nearby, beyond content range, without receiving the content. An awareness model can use this as a "murmur at the edge of hearing" signal — someone is talking just out of earshot — without pretending to know what was said. **[Verified as Corrade-documented behavior]** — same Corrade source as above. Whether every client library surfaces this identically is **[Uncertain]** and should be confirmed against LibreMetaverse's chat event args during prototyping.

Chat also carries a **channel number**. Human viewers only display channel 0, but the channel is part of the protocol message. **[Inference]** A programmatic client can log non-zero-channel chat it receives (e.g., from objects that talk on a channel within earshot), which is a perception channel humans effectively don't have. This should be verified in prototyping and used conservatively — much channel chatter is machine protocol, not speech.

Gestures deserve a footnote: nearby avatars' gestures produce chat lines and sounds, and gesture *assets* are inventory items the agent itself can trigger. Gesture text appearing in local chat is perceivable like any other chat. **[Inference]** from the chat model above plus Radegast's documented gesture support.

### 1.2 Instant messages, group chat, conferences

IMs ignore distance entirely — "you can send an instant message to someone who is anywhere in Second Life." **[Verified]** — Second Life Wiki chat documentation (see Sources). The `ImprovedInstantMessage` message is a single pipe carrying many dialog types, and the dialog taxonomy *is* the agent's social-event vocabulary. Documented dialog types include:

- Plain IM and session messages: start a group session, start a session without a calling card, send a message to a session, leave a session (dialogs 15–18) — i.e., **group chat and ad-hoc conferences are IM sessions**, joinable and leavable programmatically.
- IM from an object (dialog 19), which carries the object's SLURL in the binary bucket — objects can privately message the agent with their location attached.
- **Teleport lure** (dialog 22) and teleport responses (23, 24) — incoming teleport offers are typed events with sender and position data, not just text.
- Group officer message to all members (32), group notice requested (33).
- **Group invitation accept/decline** (35, 36) — invitations themselves arrive as IM-pipe events the agent can hold as pending decisions.
- **Friendship offered / accepted / declined** (38, 39, 40).
- **Typing start / stop indicators** (41, 42) — the agent can perceive that a specific person is *about to speak* in IM, a subtle but real social signal.

**[Verified]** — https://wiki.secondlife.com/wiki/ImprovedInstantMessage

Offline IMs are stored by the grid and delivered at login (and can be forwarded to the account's email) — so the agent's "hearing" includes a backlog channel on every login. **[Verified in general terms]** — the IM-to-email option is documented in viewer preferences material: http://wiki.secondlife.com/w/index.php?title=Preferences_FAQ&oldid=5959. The exact offline-IM storage cap is **[Uncertain]** here and not relied on anywhere in this document.

**Design note:** because group chat sessions must be explicitly joined and can be left, "which conversations am I currently in?" is itself a piece of self-awareness (see §6), and a busy agent in many groups faces the SL equivalent of notification overload — a salience problem, addressed in §7.

### 1.3 Voice — proximity voice and calls

Voice is the hardest sense to give a bot, and the one where the platform is mid-migration:

- SL voice historically runs on **Vivox** (SIP, via a separate voice daemon), with channel types: **spatial/proximal** (proximity-based in regions), **P2P direct calls**, **group** channels, and **ad-hoc conferences**. **[Verified]** — viewer communication-systems documentation: https://deepwiki.com/secondlife/viewer/5-communication-systems
- Linden Lab is migrating voice to **WebRTC**. In the WebRTC design, the client negotiates via the `ProvisionVoiceAccountRequest` capability using JSEP SDP offer/answer; the request carries a `channel_type` of `local` (spatial) or `multiagent` (P2P/group/ad-hoc) and an optional `parcel_local_id`, meaning **spatial voice channels can be per-parcel**. Region settings choose Vivox or WebRTC per region, and WebRTC spatial voice supports attenuation over distance and stereo panning. **[Verified]** — https://webrtc-public-voice-beta.s3.us-west-2.amazonaws.com/WebRTC+on+Second+Life%2C+a+Developer%27s+View.pdf and https://feedback.secondlife.com/server-bugs/p/vivox-4-voice-server-may-be-shut-down-in-future-vivox-5-available-but-requires-c ; current state (only some regions WebRTC-enabled at the time of that discussion; group/conference calls defaulting per-initiator) per viewer release notes: https://releasenotes.secondlife.com/viewer/7.1.9.9688089989.html — treat the migration's completion state as **[Uncertain]** and probe per-region at runtime.
- **Hearing geometry:** Linden Lab's security overview documents that spatial voice audibility is tied to avatar position (maximum hearing distance up to ~60 m) or to camera position (camera tether up to 50 m + 60 m ≈ up to 110 m from the avatar). This is a dated source from the Vivox era; treat the exact numbers as **[Uncertain]** for WebRTC regions, but the *shape* — voice range ≫ say range, and camera placement changes what you hear — is a durable design fact. **[Verified as documented]** — https://wiki.secondlife.com/w/index.php?title=Linden_Lab_Official:Technical_overview_of_Second_Life_security&diff=prev&oldid=1143943
- **Voice presence is leaky in a useful way:** everyone connected to spatial voice appears in the "Near Me" voice participant list whether or not they are currently speaking — there is no way to listen invisibly. For an agent, the voice participant roster is itself a presence sense (who is voice-enabled and near), separate from who is talking. **[Verified]** — same security overview.
- Parcels can **restrict voice to the parcel**, cutting voice off at parcel borders — so voice geography follows land geography, not just distance. **[Verified]** — https://wiki.secondlife.com:443/w/index.php?title=Linden_Lab_Official%3AVoice_Chat_Privacy&diff=cur&oldid=507492

**What this means for Heimdall:** a programmatic client does not get voice "for free" the way it gets text. LibreMetaverse's lineage has historically had weak voice support (Radegast lists only "experimental voice support for local chat"). The realistic architecture is a **separate voice sidecar**: a WebRTC client that joins the region/parcel spatial channel (and P2P/group channels as needed), mixes or separates participant audio, runs speech-to-text for hearing, and injects TTS audio for speaking. **[Inference]** — strongly implied by the WebRTC developer documentation above, but no turnkey bot voice library is documented; this is the single largest engineering risk in the awareness stack and is flagged again in §8.

Ambient audio beyond voice: **parcel music/media stream URLs** are part of parcel properties (§3), so the agent can know *that* music is playing and where the stream lives, and could in principle analyze the stream out-of-band. **[Inference]**.

---

## 2. Presence and radar — who is here

Presence perception comes in two fidelities, and a good awareness system uses both.

### 2.1 The coarse tier: CoarseLocationUpdate

The simulator periodically sends `CoarseLocationUpdate`, the packet behind the viewer's mini-map dots. Its layout is documented: a variable list of locations, each just **X, Y, Z as single bytes** — X/Y are region coordinates at 1 m resolution, and Z must be multiplied by 4 to get true height, which caps representable altitude at ~1020 m (higher agents overflow to 0). Two indices identify which entry is **You** and which is the **Prey** (a tracked/marked agent), or −1 when absent. **[Verified]** — https://wiki.secondlife.com:443/w/index.php?title=CoarseLocationUpdate&direction=next&oldid=78396

What the coarse tier gives: a cheap, region-scale census — *how many* agents, roughly *where*, updated continuously, including agents outside the interest list's full-fidelity range. What it does **not** give: names or UUIDs (entries are anonymous positions), fine height, or state. Correlating coarse dots with identified avatars is a fusion problem (§7): match by position against the full-fidelity tier. **[Inference]** on the fusion point; the packet contents are verified as above.

### 2.2 The full-fidelity tier: avatar objects

Avatars inside the interest volume arrive as full objects through the same `ObjectUpdate` machinery as prims (§4): UUID, position, rotation, velocity, and parent object (which tells you what they are sitting on, if anything). LibreMetaverse's `AvatarManager` tracks other avatars (names, profiles, animations), and the `ObjectManager` raises `ObjectUpdate` / `ObjectProperties` / `KillObject` events the pipeline can subscribe to. **[Verified]** — https://deepwiki.com/cinderblocks/libremetaverse/2.3-manager-system

- **Names:** every avatar has a stable **UUID**, a legacy name (e.g., `firstname.lastname` / `firstname Resident`), and a **display name**. Name resolution is a service the client library performs (AvatarManager / name-lookup capabilities); the world model should key people by UUID and treat both names as attributes, since display names are non-unique and changeable. **[Verified in substance]** — AvatarManager's documented remit is "Names, profiles, animations, display names": https://deepwiki.com/cinderblocks/libremetaverse/1.2-getting-started
- **Distance and direction** are not given directly; they are computed from the two position vectors and the agent's own rotation (bearing, elevation). Trivial math, but the awareness layer should pre-compute it — "3.2 m ahead-left" is what an LLM can use; raw vectors are not. **[Inference]** (engineering practice).
- **Animations as behavior signals:** the simulator broadcasts which animations each avatar is playing. Combined with position deltas, the awareness layer can infer coarse behavior — idle vs. dancing vs. typing gestures vs. seated poses — because many social animations have recognizable asset UUIDs/names once resolved. **[Inference]** — animation broadcast is part of the avatar-update model (AvatarManager covers "animations"); specific behavior classification must be built and validated, not assumed.

### 2.3 Agent state flags — the documented vocabulary

LSL's `llGetAgentInfo()` documents the canonical bitfield vocabulary for avatar state, and it is the best checklist of what "state" can mean for a nearby agent:

`AGENT_FLYING`, `AGENT_IN_AIR`, `AGENT_WALKING`, `AGENT_ALWAYS_RUN`, `AGENT_CROUCHING`, `AGENT_SITTING`, `AGENT_ON_OBJECT` (sitting on an object vs. ground), `AGENT_AWAY`, `AGENT_BUSY`, `AGENT_TYPING`, `AGENT_MOUSELOOK`, `AGENT_ATTACHMENTS`, `AGENT_SCRIPTED` (has scripted attachments). **[Verified]** — https://wiki.secondlife.com:443/w/index.php?title=LlGetAgentInfo&direction=next&oldid=427413

Caveats that carry over to any implementation: the flags derive from animation/state heuristics (e.g., `AGENT_TYPING` reflects the typing animation playing, which a user can disable; `AGENT_BUSY` can be set while the busy *animation* plays), and values are unreliable immediately after a region crossing. **[Verified]** — same source.

How a *client* obtains each flag varies: some arrive directly in protocol (sitting/parent object, flying via physics state, away/busy via agent data updates), others are most easily obtained from animations. **[Inference]** — plan to map each documented flag to its protocol source during prototyping rather than assuming a single "agent info" call exists client-side. Note the asymmetry: an in-world **script worn or rezzed by the agent's owner** can call `llGetAgentInfo` directly (and `llGetAgentList`, capped at 100 agents per call) — a companion HUD/prim is a legitimate extra sense organ (§7.6). `llGetAgentList` with `AGENT_LIST_REGION` / parcel / parcel-owner scopes returns up to 100 avatar keys. **[Verified]** — https://create.secondlife.com/script/lsl-reference/functions/llgetagentlist/

### 2.4 Radar range expectations

Practical radar range for the full-fidelity tier equals the interest volume (draw distance, viewer-configurable, commonly up to a few hundred meters). Radegast documents listing **all avatars in the region, plus those within ~300 m in nearby regions** — a useful benchmark of what text clients achieve by combining coarse + full data across the current and adjacent simulators (a client maintains connections to neighboring sims for avatars near borders — "child agent" presence). **[Verified as Radegast's documented feature set]** — Downloads page above. Exact cross-border behavior in LibreMetaverse should be treated as **[Uncertain]** until tested.

---

## 3. Space and place — where am I, and what kind of place is this

### 3.1 Region identity and geometry

On entering a region the agent knows: region **name**, region **handle/RegionID**, its global grid coordinates (the "region corner"), maturity rating, and region flags. LibreMetaverse's `GridManager` handles region lookup and map data, and `ParcelManager` handles land. LSL mirrors the same data for scripts (`llGetRegionName`, `llGetRegionCorner`, `llGetRegionFlags`, `llGetRegionAgentCount`). **[Verified]** — manager list: https://deepwiki.com/cinderblocks/libremetaverse/1.2-getting-started ; LSL function inventory: https://wiki.secondlife.com:443/w/index.php?title=LSL_Functions_by_Subject&diff=prev&oldid=735463

Coordinates are region-local in meters (a standard region is 256 × 256 m); global position = region corner + local offset. The awareness layer should always store both: local for geometry ("the stage is 40 m north"), global for memory ("I was in Region X at …").

### 3.2 Parcel identity, description, and rules

Subdividing every region are **parcels** — "an area of land owned by a single user or group," each with an integer local ID and a global UUID. **[Verified]** — https://wiki.secondlife.com:443/w/index.php?title=Glossary&oldid=81779

Parcel properties (via the parcel-properties machinery that `ParcelManager` wraps, and mirrored by LSL's `llGetParcelDetails` / `llGetParcelFlags` / `llGetParcelMaxPrims` / `llGetParcelPrimCount`) give the agent:

- **Name and description** — often the single most semantically valuable text in a place ("~The Whispering Grove — quiet Heathen talk, all welcome~"). This is the closest thing SL has to a room topic.
- **Owner** (avatar or group), **area**, **category**, **landing point**, **snapshot/texture ID**.
- **Rules as flags:** whether flying is allowed, whether scripts run for everyone/group/owner only, whether the parcel is "safe" (damage enabled or not), access restrictions (public / group / ban list), whether voice is restricted to the parcel, media/music settings, and whether avatars on the parcel are *visible from outside it* (parcel privacy — avatars on a "hidden" parcel do not appear to agents outside, a real perception boundary).
- **Capacity pressure:** prim counts vs. max prims, region agent count.

**[Verified in substance]** — ParcelManager's remit ("Parcel properties, media, access lists") and the LSL parcel-function inventory above; the precise flag list should be confirmed against the `ParcelProperties` fields in the library during prototyping. Parcel-privacy visibility behavior is documented viewer behavior — mark the exact client-side effect **[Uncertain]** until tested, but design as if walled parcels exist.

For the awareness model, parcel crossings are **events**, not just state: crossing a parcel line can change the rules, the music, the voice channel (parcel-restricted voice, §1.3), and the social context. Crossing detection = watch the parcel-ID-at-my-position value change.

### 3.3 Terrain

The simulator streams terrain as heightmap **layer data** patches to connected clients, so a client can build a coarse elevation model of the region (and libraries expose terrain data structures). Scripts have direct probes: `llGround` (height), `llGroundSlope`, `llGroundNormal`, `llGroundContour`. **[Verified]** — LSL function inventory above (llGround family). For the agent, terrain awareness is mostly practical: walkability, "I am on a hill / in water / on a platform 30 m up," and grounding object positions (an object at z=25 over terrain height 22 is on a floor, not floating). Turning streamed patches into a queryable heightfield in LibreMetaverse is **[Inference]** — the data is streamed; the library ergonomics need prototyping.

### 3.4 Time of day and environment (EEP / Windlight)

Since the Environmental Enhancement Project (EEP), sky and water settings are first-class environment data applied at **region** or **parcel** scope (and overridable per-agent). Scripts can read the effective environment with `llGetEnvironment(pos, params)`, which returns, among many parameters: sky ambient color, cloud color/coverage/scale/scroll/density, sun rotation, **day length** and **seconds since virtual midnight** (`ENVIRONMENT_DAYINFO`), and altitude-banded "sky tracks." Position `<-1,-1,z>` queries the region scope instead of the parcel. **[Verified]** — https://community.secondlife.com/forums/topic/432351-lsl-eep-scripting-documentation/ and the worked Linden example at https://secondlife.canny.io/scripting-bugs/p/llgetenvironment-related-functions-return-inaccurate-unit-vectors-for-sun-moon-p

So the agent can know, as data: **virtual time of day**, whether it is (virtually) night or golden hour, cloudiness, and the *mood* of the lighting — the things a human would summarize as "a moonlit beach at midnight." A programmatic client receives environment settings for its current parcel/region as part of simulator state (that is how viewers render them); extracting a compact "environment digest" (phase of day, sun/moon direction via `llGetSunDirection` analogues, cloud coverage) for the world model is **[Inference]** at the protocol level, **[Verified]** as available to a companion script via `llGetEnvironment`.

### 3.5 Region performance signals — the agent's sense of "lag weather"

A region's health is perceivable and worth modeling because it changes how the world *feels* and how reliable other senses are:

- **Simulator FPS** — `llGetRegionFPS()` returns the simulator's current frame rate (nominal ~45 FPS when healthy).
- **Time dilation** — `llGetRegionTimeDilation()` returns 0.0–1.0, "how close the simulator is to realtime; values below 1.0 indicate server load."
- **[Verified]** — https://alltheragemedia.ca/atrhome/wp-content/uploads/LSL_Object_Properties_Reference-FINAL.pdf (field reference summarizing the LSL docs for both functions).

Client-side, the same signals exist in simulator statistics messages, plus network-level observables the `NetworkManager` can surface: ping, packet loss, and update backlog. **[Inference]** on exact library exposure; the *design point* stands — the world model should carry a "conditions" field (e.g., `time_dilation: 0.62 → everything here moves/responds sluggishly; chat may lag`) because an LLM that knows the region is struggling will not misread slow replies as social signals.

---

## 4. Objects and the scene — the "sight" problem

### 4.1 What object perception actually delivers

Every object in the interest volume streams as prim data. For each object the agent can know **[Verified — ObjectManager remit and prim documentation]**:

- Identity: local ID, full UUID, parent/child linkset structure (which prim is root).
- Placement: position, rotation, scale, velocity (for moving/physical objects).
- Shape: prim type (box, cylinder, prism, sphere, torus, tube, ring, sculpted — the eight documented building-block types), plus path/profile cut parameters; mesh and sculpt assets are referenced by asset UUID. https://wiki.secondlife.com:443/w/index.php?title=Primitive&diff=prev&oldid=1179997
- **Properties on request** (`ObjectProperties`): **name, description**, creator, owner, group, permissions, sale price/type, category, inventory contents count. For awareness, *name + description* are the gold: most SL objects that matter socially ("Cozy Campfire — click to sit", "Tip Jar", "Dance Ball") announce themselves in text.
- Appearance data: per-face colors and texture UUIDs, material, light emission (a prim can be a light source — color/intensity/radius), floating text (hover text is a prim property), particle system parameters, sound loop UUID, sit target existence.
- Attachments: other avatars' attachments arrive as child prims of the avatar with attachment-point data, so the agent can list approximately **what people are wearing or holding** (by object name) — e.g., "holding a mug," "wearing a sword." Names require property requests and some attachments are unnamed; treat detail level as variable. **[Inference]** from the attachment/object model.

LibreMetaverse's `ObjectManager` events (`ObjectUpdate`, `ObjectProperties`, `KillObject`) are the subscription surface. **[Verified]** — https://deepwiki.com/cinderblocks/libremetaverse/2.3-manager-system

**Sitting and interactivity:** objects advertise click/touch actions and sit targets; the agent can sit on a specific object (the AgentManager exposes sitting), touch objects, and receive the resulting state changes as object/avatar updates. A companion script can go further — it *lives* in the scene and reports touches, collisions, and sensor sweeps (`llSensor` detects agents/objects/physical/nonphysical within a cone/sphere up to 96 m in classic usage, as in the documented 3D-radar builds). **[Verified]** — radar example using `llSensorRepeat(..., AGENT, 96, PI, ...)`: https://wiki.secondlife.com:443/w/index.php?title=3D_Radar&diff=prev&oldid=1192569

### 4.2 What cannot be perceived — honest limits

- **No pixels by default.** A protocol client never receives a rendered frame. There is no server "screenshot API" for bots. Anyone claiming their SL bot "sees" is doing one of the approximations in §4.3.
- **No occlusion / line-of-sight truth.** The interest list is built from size-vs-distance, and occlusion culling happens in the *rendering* viewer. The server streams objects the agent's avatar could not possibly see (behind walls, inside closed rooms) if they fall in the interest volume, and streams nothing beyond it. Agent "vision" is thus simultaneously X-ray and tunnel-vision. Any claim like "she noticed the painting across the room" must be grounded in the object data, not imagined from the scene. **[Verified]** — Culling/interest-list documentation above.
- **Texture content is opaque until fetched.** The agent knows a wall's texture *UUID*; knowing it depicts a poster requires downloading the JPEG2000 asset and running image understanding over it — possible (AssetManager downloads textures) but expensive per-object, and most textures are mundane materials. **[Inference]**
- **Script contents and behaviors are hidden.** An object's scripts are not readable by others; the agent perceives effects (chat, movement, sounds, dialogs it receives), not code. Descriptions and names are creator-supplied and can lie.
- **Parcel privacy and ban lines create hard edges** (§3.2), and agents in *neighboring* regions are only partially perceivable (coarse tier, §2.1).

### 4.3 Approximations to vision, in order of practicality

1. **Scene-graph summarization (the workhorse).** Convert the object set into language: filter to named, non-default, interactive, or near objects; describe by relative position ("a campfire 3 m ahead, ringed by 4 log seats"). This is what Radegast's **Object Finder** does for humans — list nearby objects, sort by distance/name, view details — and it is sufficient for most social presence. **[Verified]** as Radegast's documented feature.
2. **Camera as an attention organ.** The client sends camera position/direction, and the interest list follows the camera frustum. The agent can therefore *deliberately look around* — rotate the camera, raise draw distance, park the camera somewhere informative — to change what it knows, without moving the avatar. Camera placement is attention placement. **[Verified mechanism]** (interest list follows client-supplied frustum), **[Inference]** as awareness strategy. Note the camera also relocates voice hearing (§1.3) and coarse/fine perception balance — one organ, several senses.
3. **Geometry reconstruction.** Prim parameters plus mesh/sculpt assets can be meshed locally (LibreMetaverse ships rendering helpers — PrimMesher / mesh generation under `LibreMetaverse.Rendering`) to compute occupancy, walkable surfaces, and crude depth maps from a viewpoint — a synthetic "what's in front of me" without textures. **[Inference]** — components verified to exist (project layout: https://deepwiki.com/cinderblocks/libremetaverse ), end-to-end bot use is an engineering project, not a library call.
4. **Headless viewer rendering (the heavy road).** The official viewer is open source; in principle a forked viewer can render the scene off-screen and hand snapshots to a vision model. This is how a bot could get true images (the viewer itself can capture snapshots). Cost: a full viewer instance's CPU/GPU/RAM per agent, fragility, and update churn. No maintained "bot snapshot API" is documented — classify as **[Uncertain]** feasibility, last-resort option, and unnecessary for Heimdall's first versions.
5. **Texture sampling (selective).** For a *few* high-salience objects (a sign, a poster, a screen), fetch the face texture asset and OCR/describe it. Feasible per-object with AssetManager; needs strict salience gating so it never becomes a firehose. **[Inference]**

---

## 5. Social state — friends, groups, profiles, reputation-by-text

### 5.1 Friends and presence of people not nearby

`FriendsManager` manages the friend list, online status, and friendship permissions. **[Verified]** — manager list above. Semantics an awareness model should encode:

- Friendship is bidirectional; friends receive **online/offline notifications** ("you will be notified when your friend comes online and logs off" — also how the agent learns a friend has arrived anywhere on the grid). **[Verified]** — https://wiki.secondlife.com/w/index.php?title=Second_Life_FAQ&diff=prev&oldid=57742
- Per-friend **permissions** nuance presence: a friend may allow seeing them on the map (location sense at grid scale), seeing their online status, or modifying their objects. Awareness should record *which* channels each relationship permits — being unable to see a friend on the map is a consent state, not a bug.
- Friend offers arrive as typed IM events (§1.2), and **calling cards** (inventory items representing people) form a secondary, tradable social graph the agent can inventory.

### 5.2 Profiles — the about-text sense

`AvatarPropertiesRequest` triggers `AvatarPropertiesReply`, `AvatarInterestsReply`, and `AvatarGroupsReply`. **[Verified]** — https://wiki.secondlife.com/wiki/AvatarPropertiesRequest

So for any avatar (subject to their privacy settings) the agent can fetch: **About text**, first-life about, profile picture UUID, born-on date (account age), partner, publish/mature flags, plus interests (skills, languages, wants) and their **group list**. Separately, **picks**: residents can display up to 10 profile picks — curated locations/notes that function as a person's self-chosen highlights ("favorite snapshots" or places). **[Verified]** — https://wiki.secondlife.com:443/w/index.php?title=Glossary&oldid=81779

For an LLM-driven agent this is high-value social perception: a two-paragraph profile read *before* greeting someone converts a stranger into a situated person ("account from 2009, picks full of live-music venues"). Profile reads are also a rate/courtesy concern — fetch lazily, cache by UUID, refresh rarely. **[Inference]** (etiquette/engineering).

Viewer-style **notes** on people are, in graphical viewers, a local convenience. For Heimdall they belong in the agent's own memory store keyed by UUID (§7), not in the protocol. **[Inference]**

### 5.3 Groups

`GroupManager` covers memberships, roles, and group chat. **[Verified]** — manager list above. Awareness-relevant group data: which groups the agent belongs to (and its active **title**, which displays over the avatar and is itself a social signal others read), group charters/about text, member rosters and roles, **group notices** (which arrive as events, sometimes with item attachments), and open group chat sessions (§1.2). Group membership changes (invitations, joins, leaves) are events the social model should log — they are among the highest-importance episodic memories an agent forms.

---

## 6. Self state — proprioception

An agent that cannot feel its own body cannot be present. The `Self` (AgentManager) surface plus session events provide:

- **Position/rotation/velocity**, region and parcel, camera position/direction, and movement mode: standing, walking, flying, crouching, **sitting — and on which object** (ground sit vs. object sit, with the object UUID). Sitting state changes are among the most socially meaningful self-events ("I sat down by the fire"). **[Verified in substance]** — AgentManager remit ("Movement, chat, teleport, gestures, animations"): https://deepwiki.com/cinderblocks/libremetaverse/1.2-getting-started ; state-flag vocabulary: §2.3.
- **Own animations and appearance:** which animations she is playing, what she is **wearing and holding** (AppearanceManager / current-outfit state) — necessary to answer "what are you wearing?" truthfully and to notice when an object has animated her (dance machines, pose balls work by animating the avatar with permission; the resulting animation state is perceivable). **[Verified in substance]** — AppearanceManager remit (wearables, attachments, Current Outfit Folder): https://deepwiki.com/cinderblocks/libremetaverse/2.3-manager-system
- **Session health:** connected simulator(s) (current + neighbors), login state, teleport in progress / succeeded / failed, region-crossing events, simulator disconnect/kick, and network statistics. These should be first-class world-model facts ("my connection to this region dropped at 21:03; I reconnected at 21:04") because gaps in perception are themselves memories — and because an LLM that doesn't know it was disconnected will hallucinate continuity. **[Inference]** as design law; the underlying events are standard client-library surface.
- **Money:** balance is requestable and money changes arrive as typed replies — the `MoneyBalanceReply` message exists in the protocol, and the library lineage exposes balance events (`BalanceEventArgs`, `MoneyBalanceReplyEventArgs` on AgentManager). Transaction replies carry success/failure and a description. **[Verified]** — https://api.bitbucket.org/2.0/repositories/nooperation/libremetaverse/patch/04f601ceb748be6b82043d5b20ae4ac7a80a4ffa and https://api.bitbucket.org/2.0/repositories/opensimulator/opensim/commit/c67fa72d5628725581b61b7a16c37955e154006d
- **Inventory as a sense:** the `InventoryManager` tree (with AISv3 service) is her knowledge of what she owns, carries, and can offer/wear/use — including **calling cards** (§5.1), **landmarks** (places she can return to — a spatial memory prosthesis), **notecards** (readable text artifacts people hand her — a real input channel: a notecard received and read is perception), and **gestures** she can perform. Inventory *offers* arrive as incoming events requiring accept/decline. **[Verified in substance]** — InventoryManager remit; offer-dialog mechanics via §1.2's IM pipe.
- **Incoming social offers as a pending-events queue:** teleport lures, friendship offers, group invitations, inventory offers, calling-card offers, **script dialogs and permission requests** (objects ask for permissions — animate, attach, debit, control — and present menus via script-dialog messages; the client must answer them, so they are perceivable decision events, not noise). Each has sender, payload, and expiry semantics the world model should hold as `pending_offers[]` until resolved. **[Verified]** for lure/friend/group dialog types (§1.2); script-dialog existence is evidenced in protocol maintenance records (ScriptDialog message): https://api.bitbucket.org/2.0/repositories/lkalif/opensimulator/commits?ctx=38e1d4677445ccc481527bdfeff0e614&page=187 — treat per-type details as **[Inference]** until prototyped.

---

## 7. Awareness architecture — fusing the streams into a world model

The protocol gives a firehose: in a busy region, object/avatar updates alone can arrive at hundreds-to-thousands of messages per second, before chat, IM sessions, and group traffic. Feeding raw streams to an LLM is impossible (context limits), undesirable (cost/latency), and *wrong* (the model needs a world, not a wiretap). The literature and prior bot frameworks converge on a layered design:

### 7.1 Layer 1 — Protocol adapters → normalized event bus

Subscribe to each manager's events and translate everything into one typed, timestamped event vocabulary: `ChatHeard`, `IMReceived`, `AvatarEnteredVolume`, `AvatarLeftVolume`, `AvatarStateChanged`, `ObjectAppeared`, `ObjectChanged`, `ParcelChanged`, `OfferReceived`, `FriendCameOnline`, `BalanceChanged`, `TeleportCompleted`, … Each event carries: grid timestamp, region, parcel, actor UUID, payload, and a pointer to raw data for later drill-down.

**Prior art:** Corrade — the most mature open scripted-agent framework — already exposes exactly this shape to external programs: typed **notifications** (`local`, `group`, `message`, …) and commands over HTTP, TCP/UDP, and an integrated **MQTT** server with per-group topics, designed so arbitrary external software (including LLM front-ends) consumes SL as an event stream. Heimdall's Hermes plugin should treat Corrade's notification taxonomy as a reference checklist for completeness, whatever its own transport. **[Verified]** — http://grimore.org/secondlife/scripted_agents/corrade/tutorials/mqtt_server and §1.1's local-notification schema. Bot-framework landscape: https://wiki.secondlife.com/w/index.php?title=Second_Life_bot_software_comparison&oldid=1197198

### 7.2 Layer 2 — Entity store (the world model proper)

Events update a persistent **entity store** keyed by UUID:

- **Avatars:** names, profile digest, relationship (friend/owner/stranger), last-seen position/state, conversation thread links, accumulated notes.
- **Objects:** name/description, root position, owner, interactive affordances (sittable, touchable, speaks), last-change time.
- **Places:** region + parcel records with rules digest, environment digest, visit history ("we have been here; last time X happened").
- **Conversations:** IM/group/local threads as entities with participants and rolling summaries.
- **Self:** §6 state, including `pending_offers[]` and session health.

Every attribute carries a **timestamp and TTL**: positions go stale in seconds, parcel rules in hours, profile text in weeks. Queries from higher layers must see staleness, not just values. Spatial queries ("who is within chat range of me, sorted by distance") need a simple spatial index (uniform grid buckets per region) so the digest builder never scans the world. **[Inference]** — standard practice; stated as design, not as an SL fact.

### 7.3 Layer 3 — Salience: deciding what deserves the model's attention

Two-stage filtering, cheap rules first, learned/LLM scoring second:

- **Hard rules (deterministic):** anything *addressed to the agent* (IM to her, teleport/friend/group/inventory offers, her name in local chat, someone entering say-range and speaking, her owner speaking, a friend coming online) is always salient. Parcel/region changes, her own state changes, and balance changes are salient. Everything else starts as background.
- **Scored salience (the Generative Agents pattern):** the reference architecture for LLM agents in a simulated social world (Park et al., 2023, "Generative Agents") keeps a **memory stream** of natural-language observations and retrieves by a weighted mix of **recency** (exponential decay, factor 0.995 per hour in the paper), **importance** (an LLM-assigned 1–10 score at write time), and **relevance** (embedding cosine similarity to the current situation). Periodically, when accumulated importance crosses a threshold, the agent **reflects** — synthesizing observations into higher-level beliefs that are written back into the stream. **[Verified]** — https://arxiv.org/abs/2304.03442 ; summary: https://agent-comms.anuna.io/papers/llm-agents/generative-agents/
- For Heimdall, apply the same triad at *perception* time, not just memory time: a perception is promoted to the reasoning loop if it is recent, addressed/important, or relevant to the current conversation/goal. Everything else is still *stored* (cheap disk) — demotion is not deletion. Tiered context paging in the style of MemGPT (hot summary in-context, cold detail retrievable) keeps the prompt bounded. **[Inference]** — applying the verified pattern to this stack.

### 7.4 Layer 4 — Scene digest: what the LLM actually "sees"

On each reasoning tick (event-triggered for salient events; periodic, e.g. every 10–30 s, for ambience), render the world model into a compact structured digest, engineered for tokens:

```
PLACE: Region "…" · Parcel "…" — <parcel description, 1 line>
  Rules: no-fly, scripts-on, voice: parcel-only · Virtual time: 23:40 (night, clear) · Sim: healthy (dil 0.98)
SELF: standing at <128,64,24>, facing N · wearing: … · pending: teleport offer from X (2 min old)
PEOPLE (say-range): A (2.1 m, facing me, typing…), B (7 m, seated on "Log Bench")
PEOPLE (region): 14 others, incl. friend C (voice channel)
OBJECTS OF NOTE: "Campfire (click to sit)" 3 m ahead · "Tip Jar" by the stage
HEARD (last 60 s): A: "…" · [object] Greeter: "Welcome!"
CONVERSATION THREADS: IM with D (open, re: …) · Group "…" (3 unread-ish highlights)
```

Budget law: the digest is a *summary with pointers* — full chat text lives in the conversation store and is pulled in only for the active thread. This mirrors how the Generative Agents architecture passes retrieved subsets, not the whole stream, to the model. **[Inference]** as format; the necessity is arithmetic, not opinion.

### 7.5 Layer 5 — Memory write-back and reflection

Every salient perception becomes an **episodic memory record** (natural-language sentence + structured fields + importance score + embedding). Session gaps, teleports, and region changes segment episodes naturally. A slower reflection loop (hourly / on logout / on accumulated-importance threshold) distills episodes into durable beliefs: person summaries ("A runs the Friday talks; prefers voice"), place summaries, relationship state. This is also where **WYRD** (the project's world-model engine) plugs in: the entity store of §7.2 *is* the world model's live edge, and reflections are its consolidation step. Rate and courtesy constraints from the Scripted Agent Policy apply to anything this loop *says* in-world: bots may not send more than **5,000 messages per calendar day** (group messages count per recipient; the limit is shared across all bots of one operator) — an awareness design that chatters is a compliance failure. **[Verified]** — policy summary: https://ZoHa-Islands.com/blog/the-bots-the-whole-bots-and-nothing-but-the-bots/ (secondary source quoting the official policy; the official policy page itself is in Sources).

### 7.6 The sixth sense organ: companion scripts (LSL augmentation)

Anything awkward at protocol level is often trivial for an in-world script that the agent's owner rezzes or wears. A companion HUD/prim can:

- call `llGetAgentInfo` / `llGetAgentList` (§2.3) for canonical state flags,
- read `llGetEnvironment`, `llGetRegionFPS`, `llGetRegionTimeDilation`, parcel flags (§3) on a timer,
- run `llSensor` sweeps and listen on object channels the client might miss,
- and relay digests to the agent core via the channels scripts have: chat on a private channel (if the client is in range), IM (`llInstantMessage` to the agent — distance-free), or off-world via `llHTTPRequest` / email to a Heimdall endpoint.

**[Inference]** as an architecture pattern; every LSL capability named is **[Verified]** in §§2–3. This is the cheapest way to close perception gaps (e.g., exact virtual time-of-day) without fighting the client protocol — and it degrades gracefully: no HUD, slightly duller senses, never blindness.

### 7.7 What *not* to do

- Do not log-and-forward raw packet dumps to the LLM (token flood, no abstraction).
- Do not let the LLM poll ("anyone new?") — perception must be push-based into the entity store; the model queries state.
- Do not treat coarse-radar dots as identified people (§2.1) or object presence as visibility (§4.2) — both fabricate perceptions she does not have.
- Do not scrape profiles/friend data beyond what normal client behavior does, and never use presence to game traffic/search (policy, §0).

---

## 8. Awareness pipeline design implications for Heimdall

Concrete consequences, in build order:

1. **Build the event bus and entity store before any "intelligence."** Map LibreMetaverse manager events 1:1 into a normalized event vocabulary (§7.1); use Corrade's notification taxonomy as the completeness checklist. If a sense has no event, it does not exist for the agent.
2. **Key everything by UUID; names are attributes.** People, objects, parcels, groups, conversations. Display names change; UUIDs are identity. Memory, WYRD, and Hermes sessions all join on UUID.
3. **Two-tier radar from day one.** CoarseLocationUpdate for the region census and arrival/departure hints; full object updates for the near field. Never present coarse dots to the LLM as named people.
4. **Treat the camera as an attention organ with an explicit policy.** Default: camera at the avatar (honest perception). Deliberate look-around sweeps when idle or when entering a new place. Log camera moves in episodic memory — "I looked around" is part of what happened. Remember the camera also moves the voice ear (§1.3).
5. **Parcel crossings are events.** On parcel change, refresh rules digest, music, voice-channel scope, and environment digest; announce the place to the reasoning loop with its description text — parcel descriptions are the highest-value semantic bytes in SL.
6. **Scene digest, token-budgeted, salience-gated (§7.3–7.4).** Rules-based promotion first; add Generative-Agents-style importance scoring at memory-write time; add reflection as a scheduled Hermes task. Keep full-fidelity history on disk, out of context.
7. **Voice is a sidecar project, scheduled separately and honestly.** Text awareness ships first. The voice sidecar targets the **WebRTC** path (JSEP via `ProvisionVoiceAccountRequest`, `channel_type: local`, parcel-scoped channels), with per-region fallback detection for Vivox-only regions — where hearing voice may be impractical and the agent should *say so in-world* rather than fake it. Budget real R&D time; no library does this for bots today. **[Inference/Uncertain]** per §1.3.
8. **Add the LSL companion HUD early.** It is a small build that closes the fiddliest perception gaps (agent-info flags, environment, region health) with verified APIs, and it doubles as the in-world "she is a scripted agent" transparency anchor.
9. **Compliance is a feature, not a wrapper.** Set scripted-agent status on the account; enforce the 5,000-messages/day budget in the *output* path; rate-limit profile fetches and property requests; never idle-camp parcels for traffic. Put these in the awareness layer's design constraints, not in a policy doc nobody reads at 2 a.m.
10. **Represent ignorance in the world model.** Fields like `vision: none (object-data only)`, `voice: not connected in this region`, `parcel_privacy: unknown` should be explicit, so the LLM's language stays honest ("I can't see the painting, but the object list says there's a framed canvas by the door") instead of confabulating a rendered world.
11. **Self-state and session health are memories.** Log disconnects, teleports, and perception gaps as episodes. Continuity of *her* story depends on knowing where the holes are.
12. **WYRD integration point:** the entity store (§7.2) is the natural live feed for the WYRD world-model engine — entities/events/salience are already its vocabulary. Reflections (§7.5) are where WYRD's longer-horizon world state should be written, keeping the hot loop (digest → LLM → action) free of heavyweight inference.

---

## Sources

**Protocol & platform (official / primary):**

- Scripted Agent Policy — Linden Lab Official (wiki): https://wiki.secondlife.com:443/w/index.php?title=Linden_Lab_Official:Scripted_Agent_Policy&diff=prev&oldid=1143288
- Scripted Agent Policy summary incl. 5,000 messages/day limit (secondary, quoting LL policy): https://ZoHa-Islands.com/blog/the-bots-the-whole-bots-and-nothing-but-the-bots/
- CoarseLocationUpdate message layout — Second Life Wiki: https://wiki.secondlife.com:443/w/index.php?title=CoarseLocationUpdate&direction=next&oldid=78396
- ImprovedInstantMessage dialog taxonomy — Second Life Wiki: https://wiki.secondlife.com/wiki/ImprovedInstantMessage
- AvatarPropertiesRequest / profile replies — Second Life Wiki: https://wiki.secondlife.com/wiki/AvatarPropertiesRequest
- Technical overview of Second Life security (voice hearing geometry, Near Me list) — Linden Lab Official: https://wiki.secondlife.com/w/index.php?title=Linden_Lab_Official:Technical_overview_of_Second_Life_security&diff=prev&oldid=1143943
- Voice Chat Privacy (parcel voice restriction) — Linden Lab Official: https://wiki.secondlife.com:443/w/index.php?title=Linden_Lab_Official%3AVoice_Chat_Privacy&diff=cur&oldid=507492
- WebRTC on Second Life, a Developer's View (ProvisionVoiceAccountRequest, JSEP, channel types): https://webrtc-public-voice-beta.s3.us-west-2.amazonaws.com/WebRTC+on+Second+Life%2C+a+Developer%27s+View.pdf
- Viewer communication systems (Vivox/WebRTC channel types) — DeepWiki of the official viewer source: https://deepwiki.com/secondlife/viewer/5-communication-systems
- Viewer release notes (WebRTC/Vivox interop behavior): https://releasenotes.secondlife.com/viewer/7.1.9.9688089989.html
- Culling / interest list — Second Life Wiki: https://wiki.Secondlife.com/w/index.php?title=Culling&oldid=80015
- Glossary (parcel, pick definitions) — Second Life Wiki: https://wiki.secondlife.com:443/w/index.php?title=Glossary&oldid=81779
- Second Life FAQ (friend online notifications) — Second Life Wiki: https://wiki.secondlife.com/w/index.php?title=Second_Life_FAQ&diff=prev&oldid=57742

**LSL reference (script-side senses):**

- llGetAgentInfo flag table: https://wiki.secondlife.com:443/w/index.php?title=LlGetAgentInfo&direction=next&oldid=427413
- llGetAgentList: https://create.secondlife.com/script/lsl-reference/functions/llgetagentlist/
- llGetEnvironment / EEP scripting documentation (community post of the Linden docs): https://community.secondlife.com/forums/topic/432351-lsl-eep-scripting-documentation/
- LSL functions by subject (parcel/region/world inventory): https://wiki.secondlife.com:443/w/index.php?title=LSL_Functions_by_Subject&diff=prev&oldid=735463
- LSL object-properties field reference (region FPS, time dilation definitions): https://alltheragemedia.ca/atrhome/wp-content/uploads/LSL_Object_Properties_Reference-FINAL.pdf
- Chat function ranges (llWhisper/llSay/llShout/llRegionSay): https://dev1.slideserve.com/baxter/connecting-to-the-island-powerpoint-ppt-presentation
- 3D Radar (llSensor agent sweeps to 96 m): https://wiki.secondlife.com:443/w/index.php?title=3D_Radar&diff=prev&oldid=1192569
- Primitive shape types & properties: https://wiki.secondlife.com:443/w/index.php?title=Primitive&diff=prev&oldid=1179997

**Client libraries & bot frameworks:**

- LibreMetaverse — Getting Started / GridClient managers: https://deepwiki.com/cinderblocks/libremetaverse/1.2-getting-started
- LibreMetaverse — GridClient architecture: https://deepwiki.com/cinderblocks/libremetaverse/2.1-gridclient
- LibreMetaverse — Manager system (ObjectManager events, AppearanceManager): https://deepwiki.com/cinderblocks/libremetaverse/2.3-manager-system
- LibreMetaverse — project overview: https://deepwiki.com/cinderblocks/libremetaverse
- Corrade `local` notification schema (chat metadata incl. audible levels): http://grimore.org/secondlife/scripted_agents/corrade/api/notifications/local
- Corrade MQTT server tutorial (notification/command event bus): http://grimore.org/secondlife/scripted_agents/corrade/tutorials/mqtt_server
- Second Life bot software comparison (SmartBots / PikkuBot / Corrade / SecondBot): https://wiki.secondlife.com/w/index.php?title=Second_Life_bot_software_comparison&oldid=1197198
- Radegast feature list (text-client radar, object finder, chat) — Second Life Wiki Downloads: https://wiki.secondlife.com/w/index.php?title=Downloads&diff=prev&oldid=712382
- MoneyBalanceReply protocol evidence (OpenSimulator commit): https://api.bitbucket.org/2.0/repositories/opensimulator/opensim/commit/c67fa72d5628725581b61b7a16c37955e154006d
- LibreMetaverse AgentManager balance events (library patch record): https://api.bitbucket.org/2.0/repositories/nooperation/libremetaverse/patch/04f601ceb748be6b82043d5b20ae4ac7a80a4ffa

**Agent-architecture literature:**

- Park, J. S., et al. (2023). *Generative Agents: Interactive Simulacra of Human Behavior.* UIST '23. https://arxiv.org/abs/2304.03442
- Generative Agents — architecture summary (memory stream, retrieval, reflection, planning): https://agent-comms.anuna.io/papers/llm-agents/generative-agents/
- History of agent memory (Generative Agents retrieval parameters; MemGPT virtual context management) — secondary overview: https://www.taskade.com/blog/agent-memory-history

---

*End of research document 03. Companion topics (protocol/client implementation survey; WYRD world-model design; TOS deep-dive) are covered in sibling documents in this directory.*
