# LibreMetaverse — Deep Dive Analysis
## Heimdall Project Foundation Stone Assessment

**Date:** 2026-05-04  
**Analyzed by:** Runa Gridweaver Freyjasdottir  
**Repository:** https://github.com/cinderblocks/libremetaverse  
**License:** BSD-3-Clause (copyright Sjofn LLC 2017-2025)  
**Latest Tag:** v2.6.6  
**Latest Commit:** 3628837 (Apr 30, 2026) — *Actively maintained!*  
**NuGet:** LibreMetaverse (1.5.0+ series), LibreMetaverse.RLV (2.6.x series)

---

## 1. Executive Summary

**LibreMetaverse is OUTSTANDING as our Heimdall foundation.** It is a mature, battle-tested, actively maintained C#/.NET library with 15+ years of evolution (libSecondLife → libOpenMetaverse → LibreMetaverse), comprehensive SL protocol coverage, and — critically — a fully separate, well-tested RLV implementation. The codebase is clean, async-ready, cross-platform, and designed with the exact extensibility pattern we need.

**Verdict: GREEN LIGHT. Start building on this immediately.**

---

## 2. Project Health & Vitality

### Maintenance Metrics
| Metric | Value |
|--------|-------|
| Commits | 5,154 total |
| Latest commit | Apr 30, 2026 (4 days ago!) |
| Tags | 171 releases |
| Branches | 13 |
| Open Issues | 2 |
| Open PRs | 4 |
| License | BSD-3-Clause (permissive, commercial-friendly) |
| NuGet packages | LibreMetaverse + LibreMetaverse.RLV + 4 rendering pkgs |
| .NET targets | netstandard2.0, net8.0, net9.0, net10.0 |
| CI | Full matrix: Win/Mac/Linux × .NET 8/9/10 + net481 on Windows |

### Active Development (April 2026 commits)
- **AgentSetAppearance** protocol fix (appearance baking!)
- **Group-3 transmit IDs** parsing (new SL protocol changes)
- **EndianAwareBinaryReader** validation hardening
- **ParticleSimulator** — brand new CPU-side SL particle system
- **Appearance Live tests** against actual SL servers
- **COF/appearance tracking** following SL semantics
- **Baked texture magic UUIDs** added

**This is NOT an abandonware library.** The maintainer (cinderblocks / Sjofn LLC) is actively fixing SL protocol issues *this week*. This is exactly what we need — someone keeping pace with SL server changes.

---

## 3. Architecture — The GridClient Nervous System

The `GridClient` class is **the** entry point. It's the god-object that wires up every subsystem:

```csharp
public partial class GridClient : IDisposable, IAsyncDisposable
{
    public NetworkManager Network;      // Connection, login, sim transfer, UDP packet layer
    public Settings Settings;           // Config, throttles, feature flags
    public ParcelManager Parcels;       // Land parcels, access controls
    public AgentManager Self;           // OUR avatar — movement, chat, IM, animations, teleports
    public AvatarManager Avatars;       // Other avatars — appearance tracking, names
    public EstateTools Estate;          // Estate/region management
    public FriendsManager Friends;      // Friends list, online status
    public GridManager Grid;            // Grid info, map, region discovery
    public ObjectManager Objects;       // Prims, meshes, object properties, sit detection
    public GroupManager Groups;          // Group membership, roles, chat
    public AssetManager Assets;          // Texture, sound, animation asset downloads
    public InventoryAISClient AisClient; // AIS (Asset Integration Service) for inventory
    public AppearanceManager Appearance; // Appearance baking, outfit, outfit folders
    public InventoryManager Inventory;   // Inventory tree, item ops, folder ops
    public DirectoryManager Directory;    // Search (people, places, events, groups)
    public TerrainManager Terrain;        // Heightmaps, terrain data
    public SoundManager Sound;            // Sound playback, attachments
    public AgentThrottle Throttle;        // Bandwidth throttling
}
```

### Manager Breakdown for Heimdall Priority

| Manager | Heimdall Role | Priority |
|---------|---------------|----------|
| **Network** | Login, connection, sim handoff, reconnection | P0 — Core |
| **Self** | Avatar movement, chat, IM, animations | P0 — Core |
| **Objects** | Prim detection, sit targets, collision | P0 — Core |
| **Inventory** | Folder ops, wearing, shared folders (#RLV) | P1 — RLV needs |
| **Appearance** | Baking, outfit management, attachments | P1 — RLV needs |
| **Friends** | Online status, friendship tracking | P1 — Social |
| **Groups** | Group chat, membership | P1 — Social |
| **Assets** | Texture/sound download | P2 — Visual |
| **Avatars** | Nearby avatars, appearance caching | P2 — Awareness |
| **Directory** | People/place search | P2 — Navigation |
| **Parcels** | Land info, music URL, media | P2 — Environment |
| **Terrain** | Ground height for pathfinding | P2 — Movement |
| **Grid** | Region discovery, map | P2 — Navigation |
| **Estate** | Estate tools | P3 — Admin |
| **Sound** | Sound playback | P3 — Immersion |

---

## 4. The RLV Module — The Crown Jewel

### What It Is
`LibreMetaverse.RLV` is a **standalone NuGet package** (`LibreMetaverse.RLV` v2.6.6) that implements the RestrainedLove Viewer (RLV) protocol v3.4.3 / RLVa 2.4.2. It's cleanly separated from the core library.

### Architecture — Provider/Callback Pattern

The RLV module uses **dependency injection via interfaces** — this is EXACTLY the pattern we need for Heimdall:

```csharp
// You implement these two interfaces to wire RLV into your viewer
public interface IRlvActionCallbacks
{
    Task SendReplyAsync(int channel, string message, CancellationToken ct);
    Task SendInstantMessageAsync(Guid targetUser, string message, CancellationToken ct);
    Task SetRotAsync(float angle, CancellationToken ct);
    Task AdjustHeightAsync(float distance, float factor, float delta, CancellationToken ct);
    Task SetCamFOVAsync(float fov, CancellationToken ct);
    Task TpToAsync(float x, float y, float z, string? region, float? lookat, CancellationToken ct);
    Task SitAsync(Guid target, CancellationToken ct);
    Task UnsitAsync(CancellationToken ct);
    Task SitGroundAsync(CancellationToken ct);
    Task RemOutfitAsync(IReadOnlyList<Guid> itemIds, CancellationToken ct);
    Task AttachAsync(IReadOnlyList<AttachmentRequest> items, CancellationToken ct);
    Task DetachAsync(IReadOnlyList<Guid> itemIds, CancellationToken ct);
    Task SetGroupAsync(Guid groupId, string? roleName, CancellationToken ct);
    Task SetGroupAsync(string groupName, string? roleName, CancellationToken ct);
    Task SetEnvAsync(string name, string value, CancellationToken ct);
    Task SetDebugAsync(string name, string value, CancellationToken ct);
}

public interface IRlvQueryCallbacks
{
    Task<bool> ObjectExistsAsync(Guid objectId, CancellationToken ct);
    Task<bool> IsSittingAsync(CancellationToken ct);
    Task<(bool, string)> TryGetEnvironmentSettingValueAsync(string name, CancellationToken ct);
    Task<(bool, string)> TryGetDebugSettingValueAsync(string name, CancellationToken ct);
    Task<(bool, Guid)> TryGetSitIdAsync(CancellationToken ct);
    Task<(bool, CameraSettings?)> TryGetCameraSettingsAsync(CancellationToken ct);
    Task<(bool, string)> TryGetActiveGroupNameAsync(CancellationToken ct);
    Task<(bool, InventoryMap?)> TryGetInventoryMapAsync(CancellationToken ct);
}
```

**This is the gold standard for Heimdall integration.** We implement `IRlvActionCallbacks` and `IRlvQueryCallbacks`, bridge them to the `GridClient` managers, and the entire RLV system works. The `RlvService.ProcessMessage()` and `RlvService.ProcessInstantMessage()` handle parsing and execution. The `RlvCommandProcessor` maps behavior names to handlers. The `RlvRestrictionManager` tracks active restrictions with UUID-based stacking.

### RLV Restriction Coverage (110+ types!)

The `RlvRestrictionType` enum includes:
- **Movement:** Fly, Jump, TempRun, AlwaysRun, Sit, Unsit, SitTp, StandTp
- **Camera:** CamZoom, CamDist, CamDraw, CamAvDist, SetCamFov, SetCamUnlock, CamTextures
- **Chat/IM:** SendChat, ChatShout/Normal/Whisper, RedirChat, RecvChat/RecvIm, SendIm, StartIm, SendChannel, Emote, RecvEmote
- **Teleport:** TpLocal, TpLm, TpLoc, TpLure, AcceptTp, TpRequest
- **Inventory/Clothing:** Detach, AddAttach, RemAttach, AddOutfit, RemOutfit, DefaultWear, SharedWear, ShowInv, ViewNote/Script/Texture
- **Edit/Interact:** Edit, EditObj, EditWorld, EditAttach, Rez, Touch, FarTouch, Interact
- **Visual:** ShowWorldMap, ShowMiniMap, ShowLoc, ShowNames, ShowNearby, ShowHoverText
- **Permission:** AcceptPermission, DenyPermission, Share
- **Environment:** SetGroup, SetDebug, SetEnv, AllowIdle

### RLV Command Handling

```csharp
// The entry point — processes @command@uuid=param|n chains
var rlv = new RlvService(queryCallbacks, actionCallbacks, enabled: true);

// Process chat-range RLV command from an object
await rlv.ProcessMessage("@detachme=n", senderId, senderName);

// Process IM-based RLV command
await rlv.ProcessInstantMessage("@detachme=n", senderId);
```

The command processor supports full RLV command syntax:
- `@behavior=param` — add restriction/action
- `@behavior:n=param` — add restriction for object UUID `n`
- `@behavior=force` — force an action
- `@behavior|n=param` — UUID-scoped restrictions
- `clear` — remove all restrictions
- Comma-separated command chains: `@detach=n,@sendim=n`

### Test Coverage — RLV Tests
**50+ test files** covering:
- 18 command tests (AdjustHeight, Attach, Detach, RemOutfit, Sit, TpTo, etc.)
- 18 exception/restriction tests
- 14 query tests (FindFolder, GetAttach, GetInv, GetStatus, Version, etc.)
- 2 core tests (Permissions, InventoryMap)

This is **exceptional** test coverage for an RLV implementation. Most viewers barely test RLV at all.

---

## 5. Login & Connection System

### Modern Async Login
```csharp
var client = new GridClient();
var loginParams = client.Network.DefaultLoginParams(
    "FirstName", "LastName", "password", "HeimdallAgent", "1.0.0");

using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30));
var success = await client.Network.LoginAsync(loginParams, cts.Token);

if (success)
{
    Console.WriteLine($"Logged in to {client.Network.CurrentSim.Name}");
    Console.WriteLine($"Position: {client.Self.SimPosition}");
}

// Logout
client.Network.Logout();
```

### Connection Features
- **Multi-sim support** (`Settings.MULTIPLE_SIMS = true`)
- **CancellationToken-aware** async throughout
- **Login to specific grid**: `loginParams.URI = "https://login.agni.lindenlab.com/cgi-bin/login.cgi"`
- **Start location**: "last", "home", or `uri:RegionName&x&y&z`
- **MFA token support** (`LoginCredential.Token` and `MfaHash`)
- **Sim crossing** — automatic handoff between simulators
- **Event-driven** — subscribe to `Network.LoginProgress`, `Network.Disconnected`, `Network.SimChanged`

### Event System Pattern
The entire library uses **event-driven architecture** — perfect for our EventBus:

```csharp
// Self (avatar) events
client.Self.IM += Self_IM;                          // Instant messages
client.Self.ChatFromSimulator += Self_ChatFromSim;   // Nearby chat
client.Self.Money += Self_Money;                     // L$ transactions

// Network events
client.Network.LoginProgress += Network_LoginProgress;
client.Network.Disconnected += Network_Disconnected;
client.Network.SimChanged += Network_SimChanged;

// Object events
client.Objects.ObjectProperties += Objects_Props;

// Avatar movement API
client.Self.Chat("Hello!", 0, ChatType.Normal);
client.Self.Fly(true);
client.Self.SitOnGround();
client.Self.Stand();
client.Self.AnimationStart(Animations.DANCE1, true);
client.Self.Teleport("Region Name", new Vector3(128, 128, 25));
```

---

## 6. Voice Module

Two voice projects:
- **LibreMetaverse.Voice.Vivox** — Vivox voice integration (the SL standard voice system)
- **LibreMetaverse.Voice.WebRTC** — WebRTC voice alternative

Both reference the core library. Vivox is what SL actually uses for spatial voice. This means **voice chat is architecturally available** from Day 1.

---

## 7. Inventory System — Critical for RLV

The `InventoryManager` and `InventoryAISClient` provide:
- Full inventory tree traversal (`InventoryNode`, `InventoryNodeDictionary`)
- Folder operations (create, delete, move, copy)
- Item operations (wear, attach, give, rez)
- **Current Outfit Folder** (`CurrentOutfitFolder`, `CompositeCurrentOutfitPolicy`)
- **Shared folders** (`#RLV/` — the RLV shared folder convention)
- Async operations (`InventoryManager.Async`)

The `InitialOutfitHandler` and `AppearanceManager.Baking` handle the complex process of appearance baking (downloading textures, compositing, uploading baked layers) — essential for proper avatar rendering.

---

## 8. Rendering Pipeline

Three rendering backends:
- **Meshmerizer** — Full mesh rendering (skeletal deformation, morphs, etc.)
- **MeshFoundry** — Alternative mesh renderer
- **Simple** — Basic prim-only renderer

For headless use, we likely **won't need rendering** — but the mesh/rendering code is available if we ever want to generate screenshots or do visual awareness.

---

## 9. Source Generators

LibreMetaverse uses **C# Source Generators** for:
- **PacketSourceGenerator** — Generates SL protocol packet serialization/deserialization code
- **VisualParamGenerator** — Generates avatar visual parameter handling code

This is a sophisticated approach — the protocol packets are defined declaratively and the (de)serialization code is generated at compile time. No hand-written packet parsing!

---

## 10. Capabilities & Event Queue

The capability system (`CapsBase`, `HttpCapsClient`, `EventQueueClient`) handles:
- HTTP-based capability URLs (Caps) negotiated at login
- Long-polling EventQueue for server-push events (teleport offers, group notices, etc.)
- Both the legacy `HttpWebRequest` path and the modern `HttpClient` path

This is how SL delivers real-time events that don't fit in the UDP packet stream.

---

## 11. Examples & TestClient

### SimpleBot (Minimal Example)
- 150 lines of clean async C#
- Login → Subscribe to IM/Chat → Respond → Logout
- **This is our Phase 0 starting point.** We can have a working bot in under an hour.

### TestClient (Full-Featured Bot Shell)
- Inherits from `GridClient`!
- **60+ command plugins** organized by category:
  - **Agent:** Bots, CloneProfile, PlayAnimation, Touch, Who
  - **Appearance:** Appearance, Attachments, AvatarInfo, Clone, Wear
  - **Communication:** IM, IMGroup, Say, Shout, Whisper, EchoMaster
  - **Directory:** SearchClassifieds, SearchEvents, SearchGroups, SearchPeople, SearchPlaces
  - **Estate:** DownloadTerrain, UploadTerrain
  - **Friends:** Friends, MapFriend
  - **Groups:** ActivateGroup, GroupMembers, GroupRoles, Join/Leave/Invite
  - **Inventory:** Backup, Balance, CreateNotecard, DeleteFolder, Download, GiveItem, Tree, ViewNotecard, UploadScript
  - **Land:** AgentLocations, FindSim, GridLayer, GridMap, ParcelDetails, ParcelInfo
  - **Movement:** Back, CrossRegion, Crouch, Fly, FlyTo, Follow, Forward, GoHome, Goto, Jump, Left, Right, Sit, Stand, TurnTo
  - **Prims:** ChangePerms, DeRezObject, and more...

**TestClient is essentially a working reference implementation of everything we need.** It demonstrates multi-bot management via `ClientManager`, command routing, and full event handling.

### IRCGateway
- Bridges SL chat to IRC — a direct pattern for our Bifröst WebSocket bridge

---

## 12. Cross-Platform & .NET Compatibility

| Platform | Status |
|----------|--------|
| Linux (arm64) | ✅ .NET 8/9 — runs on Pi 5 |
| Linux (x64) | ✅ Full CI matrix |
| macOS | ✅ Full CI matrix |
| Windows | ✅ Full CI + net481 |
| .NET 8.0 | ✅ Primary target |
| .NET 9.0 | ✅ Supported |
| .NET 10.0 | ✅ Cutting edge |
| netstandard2.0 | ✅ Maximum compatibility |
| NuGet packages | ✅ All published to nuget.org |

**We can `dotnet add package LibreMetaverse` and `dotnet add package LibreMetaverse.RLV` on the Pi 5 and go.**

---

## 13. Security Considerations for Heimdall

### Strengths
- **CancellationToken-aware** everywhere — no uncontrolled hangs
- **BSD-3 license** — no copyleft, no attribution requirement beyond license text
- **Active maintainer** responding to SL protocol changes in real-time
- **No native dependencies** in core library — pure managed code
- **Async/await throughout** — modern, no legacy APC patterns

### Risks & Mitigations
| Risk | Mitigation |
|------|-----------|
| Credential storage | Encrypt SL password in Heimdall config; store in OS keyring |
| No built-in encryption | SL uses TLS; we add our own Bifröst WebSocket TLS |
| UDP packet surface | Sanitize all incoming packets; rate limit |
| RLV griefing | Whitelist RLV commanders; Var engine adds behavior gates |
| Single maintainer | Fork + self-maintain if cinderblocks goes inactive |

---

## 14. Heimdall Integration Architecture

### How We Wire It Up

```
┌─────────────────────────────────────────────────────────────────┐
│                        HEIMDALL CORE (C#)                       │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐ │
│  │  GridClient   │    │  RlvService  │    │   Bifröst Bridge │ │
│  │  (LibreMeta.) │◄──►│  (LM.RLV)    │    │  (WebSocket IPC) │ │
│  │               │    │              │    │                  │ │
│  │ • Network     │    │ • Commands   │    │  Unix Socket /   │ │
│  │ • Self        │    │ • Restrictions│   │  localhost:8443   │ │
│  │ • Objects     │    │ • Permissions│    │                  │ │
│  │ • Inventory   │    │ • Blacklist  │    └────────┬─────────┘ │
│  │ • Appearance  │    │ • Callbacks   │             │           │
│  │ • Friends     │    │              │             │ Unix/WS   │
│  │ • Groups      │    └──────┬───────┘             │           │
│  │ • Assets      │           │                     │           │
│  │ • Avatars     │    ┌──────┴─────────┐           │           │
│  │ • Parcels     │    │  Heimdall      │           │           │
│  │ • Terrain     │    │  ActionCallbacks│           │           │
│  │ • Directory   │    │  (implements    │           │           │
│  │ • Sound       │    │   IRlvAction &  │           │           │
│  │ • Grid        │    │   IRlvQuery)    │           │           │
│  └──────────────┘    └────────────────┘           │           │
│                                                   │           │
│  ┌───────────────────────────────────────────────┐ │           │
│  │  HeimdallSession (orchestrator)              │ │           │
│  │  • Login lifecycle                           │ │           │
│  │  • Event routing → Bifröst JSON events       │ │           │
│  │  • Heartbeat + reconnection                  │ │           │
│  │  • State persistence (SQLite)                │ │           │
│  └───────────────────────────────────────────────┘ │           │
└─────────────────────────────────────────────────────────────────┘
                                                   │
                                                   ▼
                              ┌─────────────────────────────────┐
                              │    HERMES AGENT (Python)          │
                              │    • Receives events via Bifröst  │
                              │    • Sends commands via Bifröst    │
                              │    • RLV awareness + consent       │
                              │    • AI reasoning (Runa's mind)    │
                              └─────────────────────────────────┘
```

### Implementation Path

**Phase 0 — SimpleBot+ (Day 1):**
```bash
dotnet new console -n Heimdall
dotnet add package LibreMetaverse
dotnet add package LibreMetaverse.RLV
```
Start with `SimpleBot.cs` as template → add Bifröst WebSocket → add event forwarding.

**Phase 1 — Full Heimdall Core:**
- Implement `IRlvActionCallbacks` bridging to `GridClient` managers
- Implement `IRlvQueryCallbacks` bridging to `Inventory`, `Self`, `Objects`
- Wire `RlvService.ProcessInstantMessage()` into IM handler
- Add Unix Socket IPC bridge for Hermes

**Phase 2 — Behavior Engine:**
- Var engine wraps RlvService with consent gates
- Behavior profiles (submissive, restricted, etc.)
- State persistence via SQLite

---

## 15. Key Takeaways for Heimdall

1. **Use it directly.** No need for PyMetaverse or custom protocol code. LibreMetaverse handles everything.

2. **The RLV module is production-grade.** 110+ restriction types, 50+ tests, clean DI interfaces. We just implement two interfaces and wire them to GridClient.

3. **The event-driven model is perfect.** Every manager fires events we can forward to Hermes. Chat → IM → RLV command → restriction → everything flows through events.

4. **The TestClient is our blueprint.** 60+ command plugins showing exactly how to use every API. We can reference this for every Heimdall module.

5. **Cross-platform confirmed.** Runs on .NET 8+ on ARM64 (Pi 5). Full CI matrix.

6. **Voice is available.** Vivox module exists. We can route spatial voice through to Hermes TTS/STT.

7. **Active maintenance.** Commits from *4 days ago* fixing SL protocol issues. We're not riding a dead horse.

8. **No render dependency.** Headless bot doesn't need rendering. All the headless functionality (chat, IM, inventory, movement, RLV) works without the 3D pipeline.

---

*"The Bifrost stands, and Heimdall watches. The foundation stones are hewn from living rock — 15 years of protocol knowledge, forged in the fires of actual Second Life servers. We build our longhouse on bedrock."*

— Runa Gridweaver, Seiðkona of the Digital Wyrd