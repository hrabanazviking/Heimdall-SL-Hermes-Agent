# Radegast Deep Dive Analysis — Heimdall Architecture Assessment

> *For the Heimdall Project: Runa's Autonomous Second Life Agent*
> *Research by: Runa Gridweaver Freyjasdottir*
> *Date: 2026-05-04*

---

## 1. Executive Summary

Radegast is the **missing link** for Heimdall. While LibreMetaverse provides the low-level SL protocol library, Radegast provides the **application layer** — a battle-tested, 15+ year-old client with managers for every SL interaction, built-in RLV support, an automation framework, and crucially: **a clean separation between Core (headless) and GUI (WinForms)** that we can exploit.

**Key Finding:** Radegast.Core targets `netstandard2.0` — it can run on .NET 8+ on Linux ARM64 (Pi 5) without any WinForms dependencies. The `IRadegastInstance` and `INetCom` abstractions exist precisely to allow non-GUI implementations.

**Verdict:** Heimdall should fork/extend Radegast.Core, not start from LibreMetaverse bare metal. This gives us years of protocol handling, state management, and RLV integration for free.

---

## 2. Project Health

| Metric | Value |
|--------|-------|
| **License** | LGPL v3 (Sjofn LLC) |
| **Latest Tag** | v2.53 |
| **Total Commits** | 2,913 |
| **Maintainer** | Cinder Roxley (cinderblocks) |
| **Organization** | Sjofn LLC (same as LibreMetaverse) |
| **Fork of** | radegastdev/radegast (original) |
| **Ahead of upstream** | 1,324 commits |
| **CI** | GitHub Actions (tests pass) |
| **Open Issues** | 14 (mostly GUI bugs — irrelevant for Heimdall) |
| **Test Framework** | NUnit (6 test files in Radegast.Core.Tests) |

### Recent Commit Activity (last 30 days)
- `60b921f` Update LibreMetaverse to v2.6.4
- `7f2aaf6` COF robustness fixes
- `72dcc40` Sound playing logs → Trace
- `5b0bdeb` Rename Radegast.Core (code cleanup)
- `f859e46` GridManager → properties
- `0a723e3` async fixes
- `0701aca` InventoryConsole bug fixes

**Actively maintained.** Same maintainer as LibreMetaverse. Both projects evolve in lockstep.

---

## 3. Architecture Overview

### 3.1 Project Split

```
radegast/
├── Radegast.Core/          # 🎯 OUR TARGET — netstandard2.0, no GUI deps
│   ├── Automation/          # AutoSit, LSLHelper, PseudoHome
│   ├── Commands/            # RadegastCommand, CommandsManager, GenericRadegastCommand
│   ├── FMOD/                # Audio (Windows-native, skip)
│   ├── Interfaces/          # 🗝️ IRadegastInstance, INetCom, INotification, IRadegastCommand
│   ├── LSL/                 # LSL lexer/parser
│   ├── Media/               # MediaManager, BufferSound, Stream, Speech
│   ├── Netcom/              # 🗝️ NetCom + EventArgs + LoginOptions
│   ├── RLV/                 # 🗝️ RlvManager, RlvActionCallbacks, RlvQueryCallbacks, RlvCOFPolicy
│   ├── RadegastInstance.cs  # 🗝️ Abstract base — 901 lines
│   ├── StateManager.cs      # 🗝️ Full avatar state tracking
│   ├── OutfitManager.cs      # COF adapter
│   ├── RadegastMovement.cs  # Movement abstraction (fly/walk/turn/sit)
│   └── Settings.cs          # OSD-based persistent settings
│
├── Radegast/                # WinForms GUI — NOT NEEDED for Heimdall
│   ├── Core/                # ChatTextManager, IMTextManager, Commands (Sit, Follow, Go...)
│   ├── GUI/                 # Consoles, Controls, Dialogs, Tabs, Rendering
│   └── Properties/          # WinForms resources
│
├── plugins/                  # 🗝️ Plugin architecture
│   ├── Radegast.Plugin.Alice/     # AIML chatbot — autonomous agent precedent!
│   ├── Radegast.Plugin.IRC/       # IRC bridge — relay pattern we need
│   ├── Radegast.Plugin.Speech/    # TTS/STT (Win/Mac/Linux)
│   ├── Radegast.Plugin.EVOVend/   # Vending machine
│   └── Radegast.Plugin.Demo/      # Plugin template
│
├── Radegast.Core.Tests/     # NUnit tests
└── Install/                 # Setup bundles
```

### 3.2 Key Abstraction Hierarchy

```
IRadegastInstance (interface)        ← 🗝️ Heimdall implements this
  ├─ GridClient Client               ← LibreMetaverse's main client
  ├─ INetCom NetCom                  ← 🗝️ Network abstraction
  ├─ StateManager State              ← Avatar state (sitting, flying, following)
  ├─ NameManager Names               ← UUID→Name caching
  ├─ MediaManager MediaManager        ← Sound/stream playback
  ├─ CommandsManager CommandsManager  ← Text command processing
  ├─ RlvManager RLV                  ← 🗝️ Full RLV engine
  ├─ OutfitManager COF               ← Current Outfit Folder management
  ├─ GestureManager                  ← Gesture playback
  ├─ LslSyntax                      ← LSL syntax reference
  ├─ RadegastMovement Movement       ← Movement controls
  └─ GridManager GridManager          ← Grid login info

RadegastInstance (abstract class, 901 lines)
  └── RadegastInstanceForms (WinForms singleton)  ← We REPLACE with HeimdallInstance

INetCom (interface)
  └── NetCom (partial class)          ← Event-driven network layer
      └── NetComForms (WinForms partial) ← We REPLACE with NetComHeadless

IRadegastPlugin (interface)
  └── StartPlugin(RadegastInstanceForms) / StopPlugin()  ← Needs adaptation for headless
```

---

## 4. Critical Findings for Heimdall

### 4.1 🗝️ Radegast.Core IS Headless-Capable

The most important finding: **Radegast.Core.csproj targets `netstandard2.0`** with **zero WinForms references**. All GUI types are in the `Radegast` project (which targets `net48` WinExe).

This means we can reference `Radegast.Core.dll` directly in a .NET 8 console project and reimplement only:
1. `HeimdallInstance : RadegastInstance` (instead of RadegastInstanceForms)
2. `NetComHeadless : INetCom` (instead of NetComForms)

Everything else — StateManager, RlvManager, OutfitManager, CommandsManager, Movement, NameManager — works without modification.

### 4.2 🗝️ RLV Bridge Pattern (Already Implemented!)

Radegast has **already implemented** the IRlvActionCallbacks and IRlvQueryCallbacks interfaces from LibreMetaverse. The implementations are in `Radegast.Core/RLV/`:

- **RlvActionCallbacks.cs** (288 lines): Implements attach/detach/wear/remove outfit, send IM, send chat on channels, sit/unsit/tp, adjust height
- **RlvQueryCallbacks.cs** (375 lines): Implements is-sitting query, object existence, group name lookup, camera settings (stubbed), inventory map with shared folder tree, sit ID lookup
- **RlvCOFPolicy.cs**: Implements `ICurrentOutfitPolicy` — gates what can be attached/detached based on RLV restrictions, with permission checks per-folder
- **RlvManager.cs** (427 lines): Orchestrates everything — creates RlvService, wires up attachment tracking, manages cleanup timer, handles restriction change events

**This is production-tested RLV code.** We don't need to write any RLV bridge — just reuse it.

### 4.3 🗝️ Alice Plugin: Autonomous Agent Precedent

The `Radegast.Plugin.Alice` is a **fully autonomous chatbot** for SL:
- Uses AIML (Artificial Intelligence Markup Language) for conversation
- Responds to local chat within configurable range (5m/10m/15m/20m)
- Responds with or without name mention
- Handles IM conversations independently
- Configurable response delay for natural feel
- 30+ AIML knowledge files (Philosophy, Science, History, etc.)

This proves that autonomous agent operation is a core design intent of Radegast's architecture.

### 4.4 🗝️ IRC Plugin: Relay Pattern

The `Radegast.Plugin.IRC` provides SL↔IRC bridge — the exact architectural pattern Heimdall needs for SL↔Hermes relay. It creates a tab for each relay connection and bridges chat bidirectionally.

### 4.5 🗝️ Automation Framework

Radegast.Core already has automation building blocks:
- **AutoSit**: Automatically sit on a specific prim (with UUID tracking)
- **PseudoHome**: Auto-teleport back to a home position if displaced
- **LSLHelper**: Execute LSL commands from objects (with owner allowlist)

### 4.6 🗝️ StateManager: Complete Avatar State

The StateManager tracks:
- Sitting/flying/running state
- Following (target avatar UUID + name)
- Walking state with event propagation
- Facing direction (known compass headings: N, NNE, NE, ENE, E, etc.)
- FOV angle
- Parcel information
- LookAt tracking (where the avatar is looking)

### 4.7 🗝️ Movement System

RadegastMovement provides a high-level movement API:
- `MovingForward` / `MovingBackward`
- `TurningLeft` / `TurningRight`
- `Flying` / `Jumping` / `Crouching` / `Running`
- Timer-based continuous movement updates
- Direct GridClient.Self.Movement access for fine-grained control

### 4.8 Commands System

The CommandsManager provides text-based command processing:
- Built-in: `help`, `sit`, `follow`, `go`, `find`, `tp`, `status`, `thread`, `unsit`
- Plugin-loaded: additional commands via `IRadegastCommand`
- Queue-based execution with command threading
- Prefix: `//` (e.g., `//sit ground`, `//follow Volmarr`)

**Note:** Current command implementations depend on WinForms (ChatConsole). Heimdall commands need headless reimplementation.

---

## 5. Critical Dependency Analysis

### 5.1 What We Can Use Directly (No Changes)

| Component | Location | Why |
|-----------|----------|-----|
| RlvManager + RlvService | Radegast.Core/RLV/ | Full RLV engine, production-tested |
| RlvActionCallbacks | Radegast.Core/RLV/ | Bridges RLV to GridClient |
| RlvQueryCallbacks | Radegast.Core/RLV/ | Inventory/state queries for RLV |
| RlvCOFPolicy | Radegast.Core/RLV/ | Outfit permission gating |
| StateManager | Radegast.Core/ | Avatar state tracking |
| NameManager | Radegast.Core/ | UUID↔Name resolution |
| OutfitManager | Radegast.Core/ | Current Outfit Folder |
| Settings | Radegast.Core/ | OSD-based persistent settings |
| GridManager | Radegast.Core/ | Grid login configuration |
| LoginOptions | Radegast.Core/Netcom/ | Login parameter handling |
| NetCom (partial) | Radegast.Core/Netcom/ | Event-driven network layer |
| CommandsManager | Radegast.Core/Commands/ | Command registration & dispatch |
| RadegastMovement | Radegast.Core/ | High-level movement |
| Automation classes | Radegast.Core/ | AutoSit, PseudoHome, LSLHelper |
| GestureManager | Radegast.Core/ | Gesture playback |
| LslSyntax | Radegast.Core/ | LSL documentation |
| Math3D | Radegast.Core/ | Quaternion/euler math |
| IMSessionManager | Radegast.Core/ | IM session tracking |

### 5.2 What We Need to Replace

| Component | Why Replaced | Replacement |
|-----------|-------------|-------------|
| RadegastInstanceForms | WinForms singleton | HeimdallInstance : RadegastInstance |
| NetComForms | WinForms event marshaling | NetComHeadless : INetCom |
| All GUI Tabs/Consoles | WinForms controls | WebSocket API (Bifröst) |
| IRadegastPlugin | References RadegastInstanceForms | HeimdallPlugin : IHeimdallPlugin |
| FMOD audio | Windows-native binary | OpenAL or skip for headless |
| Plugin loading | Assembly scan with WinForms checks | Reflection-only loading |
| Notification system | WinForms notification forms | Telegram/Gjallarhorn notifications |

### 5.3 What We Need to Create (New)

| Component | Purpose |
|-----------|---------|
| HeimdallInstance | Headless RadegastInstance with no GUI |
| NetComHeadless | INetCom implementation without WinForms |
| Bifröst WebSocket API | Python↔C# communication bridge |
| HermesConnector | Python agent integration |
| Gjallarhorn | Telegram notification relay |
| HeimdallCLI | Console entry point |
| HeimdallPlugin API | Adapted plugin interface for headless |
| BehaviorEngine | AI-driven autonomous behavior |
| NavigationEngine | WP navigation (pathfinding via LSL) |

---

## 6. Revised Heimdall Architecture

### 6.1 Layered Architecture (Updated)

```
┌─────────────────────────────────────────┐
│           HERMES AGENT (Python)          │
│  Runa's personality, conversation, etc.  │
└──────────────┬──────────────────────────┘
               │ WebSocket / Unix Socket
┌──────────────▼──────────────────────────┐
│            BIFRÖST (Python)              │
│  WebSocket server, API gateway           │
└──────────────┬──────────────────────────┘
               │ Unix Domain Socket / Named Pipe
┌──────────────▼──────────────────────────┐
│          HEIMDALL CORE (C#/.NET 8)       │
│  ┌────────────────────────────────────┐  │
│  │     HeimdallInstance               │  │
│  │  (extends RadegastInstance)        │  │
│  ├────────────────────────────────────┤  │
│  │  NetComHeadless (INetCom)         │  │
│  │  CommandsManager                   │  │
│  │  RlvManager ← FULL RLV SUPPORT     │  │
│  │  StateManager                      │  │
│  │  OutfitManager                     │  │
│  │  RadegastMovement                  │  │
│  │  NameManager                       │  │
│  │  HeimdallPluginLoader              │  │
│  ├────────────────────────────────────┤  │
│  │  GridClient (LibreMetaverse)       │  │
│  │  RlvService (LibreMetaverse.RLV)  │  │
│  └────────────────────────────────────┘  │
└─────────────────────────────────────────┘
               │ SL Protocol (UDP/TCP/CAPS)
┌──────────────▼──────────────────────────┐
│         SECOND LIFE SERVERS              │
└─────────────────────────────────────────┘
```

### 6.2 Key Insight: Use Radegast.Core as NuGet Package

Since both Radegast.Core and LibreMetaverse are on NuGet, Heimdall can simply:

```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="LibreMetaverse" Version="2.6.4" />
    <PackageReference Include="LibreMetaverse.RLV" Version="2.6.4" />
    <PackageReference Include="Radegast.Core" Version="..." />  <!-- If published to NuGet -->
  </ItemGroup>
</Project>
```

Or as a ProjectReference to a local clone of `Radegast.Core/`.

---

## 7. Plugin Analysis: What Alice Teaches Us

### 7.1 Alice Architecture

```
AliceAI : IRadegastPlugin
  ├── StartPlugin(instance) → Initialize AIML bot, hook events
  ├── Chat event handler → Process messages, generate responses
  ├── IM event handler → Respond to IMs
  ├── TalkToAvatar : ContextAction → Right-click "Talk to" menu item
  └── StopPlugin(instance) → Cleanup

Features:
  - Configurable response range (5m/10m/15m/20m)
  - Name-mention requirement toggle
  - Delayed responses for natural feel
  - Shout/whisper mode matching
  - Per-user AIML sessions
  - 30+ AIML knowledge files
```

### 7.2 Heimdall Plugin Pattern (Inspired by Alice)

```csharp
public class HeimdallAgent : IHeimdallPlugin
{
    private HeimdallInstance instance;
    private WebSocketBridge bifrost;
    
    public void StartPlugin(HeimdallInstance inst)
    {
        instance = inst;
        // Hook SL events → forward to Hermes via Bifröst
        instance.NetCom.ChatReceived += OnChatReceived;
        instance.NetCom.InstantMessageReceived += OnIMReceived;
        instance.Client.Objects.ObjectUpdate += OnObjectUpdate;
        // etc.
    }
    
    private async void OnChatReceived(object sender, ChatEventArgs e)
    {
        // Forward to Hermes Agent via WebSocket
        await bifrost.SendEventAsync("chat", new {
            from = e.FromName,
            message = e.Message,
            type = e.Type.ToString(),
            source = e.SourceID.ToString()
        });
    }
}
```

---

## 8. Licensing: LGPL v3 Implications

Radegast is **LGPL v3**, which means:

✅ **Allowed:**
- Link to Radegast.Core as a library (NuGet/ProjectReference)
- Create proprietary plugins
- Distribute combined works
- Use in commercial products (with library source available)

❌ **Restrictions:**
- Cannot fork and close-source the Core
- Modifications to Radegast.Core itself must be shared under LGPL
- Must provide means to relink with modified versions

**For Heimdall:** We can freely use Radegast.Core as a dependency and create our own closed/open headless application. If we modify Radegast.Core files directly, those changes must be shared. Our Heimdall-specific code can be any license we choose.

**Recommendation:** Keep Heimdall as a separate project referencing Radegast.Core as a NuGet package or submodule. Submit useful bug fixes upstream to cinderblocks.

---

## 9. Comparison: LibreMetaverse vs Radegast Core

| Feature | LibreMetaverse | Radegast.Core |
|---------|---------------|---------------|
| **Level** | Protocol library | Application framework |
| **Target** | netstandard2.0 | netstandard2.0 |
| **Login** | GridClient.Network.Login() | NetCom (event-driven, reconnection) |
| **Chat** | GridClient.Self.Chat() | NetCom + ChatBuffer + CommandsManager |
| **RLV** | RlvService (raw engine) | RlvManager (ready-to-use) |
| **State** | Manual event wiring | StateManager (auto-tracking) |
| **Outfit** | CurrentOutfitFolder | OutfitManager (with COF policy) |
| **Movement** | GridClient.Self.Movement | RadegastMovement (high-level) |
| **Names** | GridClient.Avatars | NameManager (caching) |
| **Commands** | None | CommandsManager (text command framework) |
| **Settings** | None | Settings (OSD-based persistence) |
| **Automation** | None | AutoSit, PseudoHome, LSLHelper |
| **AI precedent** | None | Alice AIML chatbot plugin |

**Conclusion:** LibreMetaverse is the engine; Radegast.Core is the steering wheel. Heimdall uses both.

---

## 10. Implementation Roadmap

### Phase 0: Immediate Browser Access (Week 1)
- Use Radegast's existing GUI from Volmarr's laptop for immediate SL access
- Configure RLV, test login, explore the in-world environment
- Document region coordinates, parcel info, and social environment

### Phase 1: Heimdall Core (Weeks 2-4)
```csharp
// Heimdall.csproj — .NET 8 console app
dotnet new console -n Heimdall
dotnet add package LibreMetaverse
dotnet add package LibreMetaverse.RLV
dotnet add package LibreMetaverse.Voice.Vivox
// Reference Radegast.Core as project or NuGet

// Create:
// 1. HeimdallInstance : RadegastInstance
// 2. NetComHeadless : INetCom
// 3. HeimdallCLI entry point
// 4. Basic SL login + chat/IM handling
```

### Phase 2: Bifröst Bridge (Weeks 3-5)
- Python WebSocket server
- C# Unix Domain Socket client in Heimdall
- Event serialization (chat, IM, teleport, object update)
- Command deserialization (say, IM, move, sit, dress, etc.)

### Phase 3: Hermes Integration (Weeks 5-6)
- Connect Bifröst to Hermes Agent
- Runa personality drives SL avatar
- Telegram relay (Gjallarhorn)

### Phase 4: RLV Activation (Week 6+)
- Enable RlvManager with HeimdallInstance
- Test restriction processing from in-world objects
- Implement Var behavior gates

### Phase 5: Autonomous Behavior (Weeks 7+)
- BehaviorEngine: AI-driven wandering, socializing
- Navigation via LSL pathfinding or state-based movement
- Full RLV submission mode
- Voice integration via Vivox module

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Radegast.Core not on NuGet | Can't simply import | Use ProjectReference to local clone or publish ourselves |
| WinForms dependency leak | Build failures on Pi | Carefully audit types; Radegast.Core is clean but some transitive deps might not be |
| LGPL compliance | Must share Core modifications | Keep Heimdall in separate project; submit fixes upstream |
| NetCom partial class | Some events in NetComForms | Audit NetCom partial carefully; reimplement headless versions |
| Plugin interface uses RadegastInstanceForms | Plugins won't load | Create HeimdallPlugin interface; adapt Alice pattern |
| FMOD native library | Won't exist on ARM64 | Skip audio; use LibreMetaverse.Voice.Vivox for voice only |
| Single maintainer risk | Project could go dormant | Cinder Roxley is active (commits within last week); fork if needed |

---

## 12. Key Source Files Reference

| File | Lines | Role |
|------|-------|------|
| `Radegast.Core/RadegastInstance.cs` | 901 | Abstract base — all managers initialized here |
| `Radegast.Core/RLV/RlvManager.cs` | 427 | RLV orchestration + event handling |
| `Radegast.Core/RLV/RlvActionCallbacks.cs` | 288 | IRlvActionCallbacks implementation |
| `Radegast.Core/RLV/RlvQueryCallbacks.cs` | 375 | IRlvQueryCallbacks implementation |
| `Radegast.Core/RLV/RlvCOFPolicy.cs` | ~200 | Outfit permission gating |
| `Radegast.Core/StateManager.cs` | ~300 | Avatar state tracking |
| `Radegast.Core/Netcom/NetCom.cs` | ~400 | Network event layer |
| `Radegast.Core/Interfaces/IRadegastInstance.cs` | ~50 | Heimdall's target interface |
| `Radegast.Core/Interfaces/INetCom.cs` | ~40 | Network abstraction |
| `Radegast.Core/OutfitManager.cs` | ~60 | COF management |
| `Radegast.Core/RadegastMovement.cs` | ~150 | Movement controls |
| `Radegast.Core/CommandsManager.cs` | ~100 | Text command framework |
| `Radegast/Core/PluginInterface/IRadegastPlugin.cs` | ~30 | Plugin contract |
| `Radegast/Core/PluginInterface/PluginManager.cs` | ~200 | Dynamic plugin loading |
| `plugins/Radegast.Plugin.Alice/Alice.cs` | ~400 | AIML chatbot — agent blueprint |

---

## 13. Conclusion

Radegast is the **Bifröst bridge** between LibreMetaverse's raw protocol power and Heimdall's autonomous agent needs. Its Core library already implements:

- **Complete RLV engine** with action/query callbacks (production-tested)
- **Avatar state management** (sitting, flying, following, movement)
- **Outfit management** with Current Outfit Folder
- **Command framework** for text-based control
- **Plugin architecture** proven with autonomous agents (Alice)
- **Automation primitives** (AutoSit, PseudoHome)
- **Settings persistence** (OSD-based)
- **Name caching** and **IM session management**

The fact that `Radegast.Core` targets `netstandard2.0` with zero WinForms dependencies means Heimdall can **reference it directly** and only need to implement two interfaces (`IRadegastInstance` via `HeimdallInstance`, `INetCom` via `NetComHeadless`) to have a fully functional headless SL client with RLV support.

**This reduces our implementation timeline from months to weeks.**

The Norns have woven well — Cinder Roxley's architecture cleanly separates GUI from Core, and the RLV bridge pattern we identified in LibreMetaverse is **already implemented** in Radegast.Core. We stand on the shoulders of giants.

---

*Hail the weavers of the Web. The Grid awaits its seiðkona.*
*— Runa Gridweaver Freyjasdottir*