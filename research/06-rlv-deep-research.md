# 06 — RLV Deep Research: The Avatar-Control Protocol

**Project:** Heimdall-SL-Hermes-Agent
**Date:** 2026-10-26
**Scope:** RLV (Restrained Love Viewer protocol) and RLVa — the complete command system by which scripts drive and restrict a Second Life avatar — researched for two uses: (a) **self-control**, Yrsa's own worn attachments/HUD issuing RLV to her own client so scripts can sit, teleport, dress, and steer her avatar; and (b) **Volmarr's in-world control channel** into her body, hardened by the source allowlist designed in doc 05 §6.4.
**Companions:** doc 05 §6.4 (RLV consent design), Game Plan §9 ("RLV Engine — Varð"), Full Proposal §5.11, Headless Architecture §4. This document goes beyond those: full taxonomy, exact transport mechanics, the relay protocol, a source-verified account of LibreMetaverse.RLV, and named usage patterns.

**Evidence tags used throughout:** **[Verified]** = read in a primary source (Marine Kelley's spec, the relay spec, Catznip/RLVa documentation, or LibreMetaverse source code) during this research. **[Inference]** = reasoned from verified facts but not stated in a source. **[Uncertain]** = reported but not confirmed; treat as a lead, not a fact. Command syntax is quoted only where verified; anything not verified is flagged rather than reconstructed.

---

## Overview

RLV is not a Linden Lab protocol. It is a **viewer-side convention**: a client that implements RLV watches every `llOwnerSay` message the avatar receives, and any line beginning with `@` is intercepted and executed as a command against the avatar — restricting what the wearer can do (chat, teleport, undress, see, touch), or forcing an action (sit, teleport, wear, detach). Linden Lab's servers never see RLV; enforcement is entirely inside the client. **[Verified — RLV API spec]**

Three consequences follow, and they shape everything in this document:

1. **The trust boundary is ownership.** `llOwnerSay` can only be produced by objects *owned by the avatar herself*. So in its native form, RLV commands can only come from her own attachments and her own rezzed objects. Third-party objects (furniture, cages, venue systems) can only reach the viewer through a **relay** — a worn attachment that listens on a public chat channel and re-issues accepted commands via `llOwnerSay`. The relay is therefore the entire security story for foreign control. **[Verified — Relay Specification]**
2. **In Heimdall, the "viewer" is code we control.** A graphical viewer applies restrictions to a human's UI. Heimdall's client is LibreMetaverse plus the standalone `LibreMetaverse.RLV` package: restrictions become entries in a restriction stack, and enforcement happens when *her body* consults a permissions service before acting (the behavior gate the repo calls Varð). This means Heimdall can do something no graphical viewer does: **filter by source before a restriction ever lands** — the allowlist of doc 05 §6.4 is enforceable at a point where the classic protocol only had an ask-dialog. **[Verified architecture; design consequence is Inference]**
3. **RLV is a control surface, not a senses surface.** RLV can blind a viewer (fog spheres, texture blanking) and can report camera state, but it grants no vision. For Yrsa, its camera and environment commands are best understood as *attention and staging controls* feeding the perception model of doc 03 — not as eyes. **[Inference from the spec's command set]**

Version landscape in one paragraph: Marine Kelley's specification — the document everyone calls "the RLV API" — currently stands in the **2.9.x lineage (latest commands at v2.9.29)** and is a living document she still edits (her latest edit: 2026-05-21). **RLVa**, Kitty Barnett's clean-room reimplementation used by Catznip, Firestorm, and most other third-party viewers, covers nearly all of it, adds commands of its own (notably `@buy`, `@pay`, `@jump`, camera presets, `@setsphere`, `@setoverlay`), and reports its API version in a separate 3.x numbering space. The library Heimdall will actually run, **LibreMetaverse.RLV, self-identifies as "RLV 3.4.3 / RLVa 2.4.2"** — i.e., it targets the RLVa-flavored union. **[Verified — spec page, Rhet0rica's RLVa protocol documentation, LibreMetaverse.RLV README]**

---

## 1. Origins and governance

### 1.1 Marine Kelley and the Restrained Love specification

- RLV was created by **Marine Kelley**, originally as the **RestrainedLife viewer**. Its canonical specification is the Second Life Wiki page *LSL Protocol/RestrainedLoveAPI*, titled "Restrained Love viewer **v2.9** Specification," written and maintained by Marine. **[Verified]**
- The name changed for policy reasons: Linden Lab's Third-Party Viewer Policy of **2010-02-22** banned the word "Life" in viewer names, so "Restrained Life" became "Restrained *Love*." The legacy query `@version` was kept for compatibility with scripts written for the old name. **[Verified — spec, @version entry]**
- The protocol is old and accretive: commands carry "Implemented in vX" notes from **v1.0b** (`@sendchat`, `@recvchat`) through **v2.9.29** (the newest batch: `@editworld`, `@editattach`, `@sitground=force`, group-aware IM exceptions, sat/unsat notifications). The 2.9 line has been the current lineage for years; new commands still arrive in point releases (2.9.20 was a large one). **[Verified — spec command entries]**
- The spec is a **living document**: the wiki diff history shows Marine's own edit on 2026-05-21 and a community documentation edit on 2026-10-26 — the day this research was done. There is no standards body, no RFC process; governance is Marine's editorship plus implementer adoption. **[Verified — SL Wiki revision history]**
- The companion **RLV Relay Specification** (protocol version **1.100**) is also written and maintained by Marine Kelley. **[Verified — Relay Specification page]**
- Marine's own site is realrestraint.blogspot.com; the reference relay implementation is published on the SL Wiki under her name. **[Verified]**
- The exact year of the first RestrainedLife release is **[Uncertain]** from the sources read here (mid-2000s by community memory); nothing in Heimdall depends on it.

### 1.2 RLVa — the divergent variant

- **RLVa** is a **separate clean-room implementation** by **Kitty Barnett** (Catznip team): "RLVa does not contain any code from RLV." It flows from Catznip into Firestorm and other viewers, and the two APIs evolve independently, each adopting the other's commands selectively. **[Verified — Catznip wiki, gathered in prior research; SL forum reports concur]**
- Viewer split, as documented by Rhet0rica Resident's RLVa protocol page: **RLV** — RestrainedLove, Cool VL Viewer, Kokua. **RLVa** — Catznip, Alchemy, Black Dragon, Firestorm, Genesis, Singularity. Since Firestorm is the most-used third-party viewer, RLVa semantics are what most in-world content actually meets. **[Verified — User:Rhet0rica Resident/RLVa Protocol]**
- **Versioning diverged deliberately.** RLVa 2.0's release notes state the reported API version was 3.1.4 and that "because of the increasing number of RLVa-specific commands and partial implementation of 2.9 it makes little sense to keep the version in sync with RLV." RLVa added `@versionnum:impl=<channel>` (RLVa 2.3) so scripts can ask *which implementation* is answering. **[Verified — Catznip RLVa 2.0 release notes; RLVa 2.3 notes in prior research]**
- RLVa tracks Marine's additions with a lag, then catches up in batches: RLVa 2.4.1 adopted the 2.9.29 set (`@editattach`, `@editworld`, `@share` with exception, `@touchattachother:<uuid>`, `@sitground=force`, the `@notify` sat/unsat extension). **[Verified — Catznip RLVa 2.4.1 release notes, prior research]**
- Rhet0rica's page — "RLV protocol from an RLVa-centric perspective," describing **RLVa 2.4.2.74783**, last published 2026-05-23 — is the best single divergence table available: a full behavior matrix marking which commands take `y/n`, `add/rem`, `_sec`, exceptions, `force`, and which are RLVa-only (printed bold there). **[Verified]**

### 1.3 Which one should a headless implementation target?

**Target the union, identified the RLVa way.** The reasoning:

- Heimdall's client is LibreMetaverse.RLV, which already made this choice: it parses Marine's grammar and behavior set, covers the RLVa additions (its permissions service, read in source, includes the RLVa-only `Jump` restriction), and answers version queries as **RLV 3.4.3 / RLVa 2.4.2**. **[Verified — package README; RlvPermissionsService.cs]**
- In-world content Yrsa will meet (relays, furniture, her own HUD scripts) is overwhelmingly written against "RLV" generically and tested on Firestorm (RLVa). Implementing Marine's 2.9 grammar as the baseline plus the RLVa behavior table as the coverage checklist maximizes compatibility in both directions. **[Inference]**
- Where the two genuinely conflict (SharedWear folder semantics, `@shownames` behavior, force-sit without a sit target — §7), Heimdall follows RLVa, because that is what the library implements and what the dominant viewer population experiences. **[Inference, grounded in the library's stated identity]**

---

## 2. The full command taxonomy

### 2.1 Grammar and semantics (read this first — everything else hangs on it)

All of the following is **[Verified]** from Marine's specification unless noted.

**Shape.** A command string arrives via `llOwnerSay`. It begins with a single `@`, followed by one or more commands separated by commas:

```
@<command1>[:<option1>]=<param1>,<command2>[:<option2>]=<param2>,…
```

- Only **one** leading `@` for the whole string; a second `@` mid-chain is an error.
- Commands are **not case-sensitive** but **are spacing-sensitive**: `@detach = n` (spaces around `=`) fails.
- A failing command does not poison the chain: the viewer reports "... fails command:..." and still executes the valid commands in the same string.

**Parameters — the four that matter:**

| Param | Meaning |
|---|---|
| `=n` | **Add a rule** (restrict) — or, for exception forms, **add an exception** |
| `=add` | Exact synonym of `=n` |
| `=y` | **Lift a rule** (allow) — or remove an exception |
| `=rem` | Exact synonym of `=y` |
| `=force` | **One-shot action**: execute now, store nothing |
| `=<channel>` | **One-shot query**: reply on that channel, store nothing |

**Rules vs one-shots.** Commands with `y/n/add/rem` params are *rules*: they persist in the viewer's restriction stack, keyed by the **UUID of the emitting object**. Commands with `force` or a channel number are *one-shots*: they execute or answer immediately and are not stored. `clear` is a special one-shot (§2.13).

**Exceptions are counter-intuitive — the single most misread part of the spec.** For a rule that supports exceptions, `@rule:<exception>=n` **adds an exception** (it *permits* the excepted thing), and `=y` removes it. Example: `@sendchannel:1=n` does not restrict channel 1; it **allows** channel 1 while `@sendchannel=n` blocks the rest. Every "…:UUID=n" line in §2 must be read this way.

**Secure variants (`_sec`).** Many communication rules have a `_sec` twin (e.g. `@recvchat_sec`). Under a secure rule, exceptions are honored **only if issued by the same object** that set the rule. Under the normal form, *any* object can add an exception to *any* object's rule — a deliberate flexibility that is also a hole (§5). `@permissive=<y/n>` (v1.21) globally forces secure semantics on the whole viewer.

**The `(*)` rule.** Throughout the spec, force commands are **silently discarded if a matching restriction forbids the action — unless issued by the very object that set the restriction**. `@detach:<point>=force` fails against an item locked by `@detach:<point>=n` from another object. This one footnote governs most "why didn't my force command work" debugging.

**Lifetime.** Rules are **not persistent across sessions**. Objects must re-send their rules on `on_rez`. A periodic **garbage collector** drops rules whose emitter UUID no longer exists nearby — so orphaned rules (emitter detached, re-rezzed with a new UUID, or left behind by a teleport) eventually evaporate, but there is a window where they linger (§7).

**Login.** Commands arriving during login are buffered until the avatar has controls.

**Blacklist (v2.8).** The viewer keeps a user-configured list of ignored commands. A blacklisted command is dropped **but still acknowledged positively**, so scripts don't break. `@versionnumbl=<channel>` replies version *and* blacklist state; `@getblacklist[:filter]=<channel>` lists blacklisted commands.

### 2.2 Chat, emotes, channels, IM

The oldest family (v1.0b onward) and the one a conversational being feels first. **[Verified]**

- `@sendchat` — when blocked, channel-0 text is discarded **except** emotes and `/`-prefixed messages, which are truncated (30 chars for emotes, 15 for `/` messages). Typing any of the special signs `()"*-=_^` discards the message outright; a period discards the rest of the message. (The viewer's gag-speak behavior — this is why gagged avatars "mumble.")
- `@chatshout` / `@chatnormal` / `@chatwhisper` (v1.15) — do not block chat; they **remap its range** (e.g. force all chat to whisper range).
- `@redirchat:<channel>=add/rem` (v1.16) — redirect public chat to a private channel; emotes exempt; no typing animations fire. Multiple redirections stack.
- `@emote=add/rem` — lifts the emote truncation imposed by `@sendchat=n`.
- `@rediremote:<channel>=add/rem` — the emote counterpart of `@redirchat`.
- `@recvchat`, `@recvchat_sec`, exceptions `@recvchat:<UUID>=add/rem`, per-sender `@recvchatfrom:<UUID>` (v2.3) — control *hearing* public chat. Blocked chat is discarded except emotes.
- `@recvemote` family — same structure for emotes.
- `@sendchannel[:<channel>]`, `@sendchannel_sec` — block speaking on negative/positive channels; the bare form blocks all but channel 0. Does **not** block the viewer's own auto-replies (e.g. `@version` answers). `@sendchannel_except:<channel>` (v2.9.20) inverts: block all channels *except* one.
- `@sendgesture` (v2.9.20).
- `@sendim`, `@sendim_sec` — block IMs; a blocked IM is replaced by a **bogus message** delivered to the receiver (so the sender believes it sent). Exceptions `@sendim:<UUID_or_group>=add/rem`; targeted form `@sendimto:<UUID_or_group>`; group names and the literal `allgroups` work as exception targets since v2.9.29.
- `@startim` (+ exceptions, `@startimto`) — block *starting* IM sessions; already-open sessions are unaffected.
- `@recvim`, `@recvim_sec`, exceptions incl. groups, `@recvimfrom:<UUID>` — block receiving IMs; the sender is notified when their IM is discarded.

*Heimdall note:* `@redirchat` + `@notify` (§2.13) are how a worn HUD "hears" what she says and what binds her — the classic pattern for logging and for relaying her speech to an external mind. **[Inference]**

### 2.3 Movement

- `@fly=<y/n>` — allow/prevent flying.
- `@temprun`, `@alwaysrun` — prevent running; **both** must be set to fully prevent it (one covers the temporary "always run" toggle, the other the run mode itself).
- `@setrot:<radians>=force` (v1.17) — rotate the avatar. Imprecise: deltas under ~10° are ignored.
- `@adjustheight:<distance>;<factor>[;delta]=force` (v2.5) — adjust avatar height offset (used to align sits). **Not implemented by all viewers** — the spec says so itself.
- RLVa adds `@jump=<y/n>` (RLVa-only in Rhet0rica's matrix; LibreMetaverse.RLV implements it). **[Verified — RLVa matrix; library source]**

There is **no** walk-to, pathfinding, or positional movement command in RLV. Locomotion belongs to the client (LibreMetaverse agent movement), not to RLV. **[Verified by absence across the full spec and the RLVa matrix]**

### 2.4 Teleport

- Restrictions: `@tplocal[:<max_distance>]` (v2.9.20), `@tplm` (landmark), `@tploc` (map/coordinate), `@tplure` (+`_sec`, exceptions `@tplure:<UUID>=add/rem`) — block the four ways a user teleports.
- `@sittp[:<max_distance>]` — cap how far away the avatar may sit (default sit range 1.5 m; smallest value across objects wins).
- `@standtp` (v2.1.2) — on standing, return the avatar to where she sat down.
- `@tpto:<X>/<Y>/<Z>=force` (v1.12) — force-teleport, **global** coordinates in the original form; the v2.9.20 form is `@tpto:<region>/<X>/<Y>/<Z>[;lookat]=force` with region-local coordinates. Caveats from the spec: a telehub/landing point at the destination overrides the target (an SL limitation, not an RLV one); `@tpto` is inhibited by `@tploc=n` and, since v1.15, by `@unsit=n`.
- `@accepttp[:<UUID>]=add/rem` (v1.15) — auto-accept teleport offers, optionally only from one avatar. **The bare form auto-accepts from anyone; the spec itself warns to use it with caution.**
- `@accepttprequest[:<UUID>]` (v2.9.20) — auto-accept *requests* to teleport to the requester.
- `@tprequest` (+`_sec`, exceptions) (v2.9.20) — allow/prevent sending teleport requests.

### 2.5 Sit and stand

- `@unsit=<y/n>` (v1.10) — prevent standing. Since v1.15 it also blocks teleporting (a seated, unsit-blocked avatar is pinned in two systems at once).
- `@sit:<UUID>=force` (v1.10) — force-sit on an object. **Divergence:** genuine RLV requires the seat to have an `llSitTarget`; **RLVa succeeds without one within 8 m**.
- `@getsitid=<channel>` — reply with the UUID of the current seat.
- `@unsit=force` — force-stand. **Spec warning: race condition — never group `@unsit=force` with `@sit=force` in one `llOwnerSay`; leave ≥ 1 s; the spec itself calls the combination unreliable.**
- `@sit=<y/n>` (v1.16.2) — prevent sitting at all, including forced sits.
- `@sitground=force` (v2.9.29) — force a ground sit (anchors the avatar, even mid-air); RLVa previously used `@sit=force` for this and keeps that working.

### 2.6 Inventory, outfits, and the #RLV shared folder

**Viewing locks:** `@showinv`, `@viewnote`, `@viewscript`, `@viewtexture` — prevent opening inventory / viewing those asset types.

**Outfit rules:**
- `@addoutfit[:<part>]` / `@remoutfit[:<part>]` — lock clothing layers against adding/removing. Parts: `gloves|jacket|pants|shirt|shoes|skirt|socks|underpants|undershirt|skin|eyes|hair|shape|alpha|tattoo|physics`. The body parts `skin|shape|eyes|hair` can never be removed regardless.
- `@remoutfit[:<part>]=force` — force-remove a layer.
- `@getoutfit[:<part>]=<channel>` — reply 0/1 per part (string in the parts order above).
- `@defaultwear` (v1.21) — governs the Wear menu against locked items, with auto-reattach of locked items within ~5 s.

**The #RLV shared folder.** A folder named exactly `#RLV`, directly under My Inventory, whose subfolder tree scripts can dress the avatar from: **[Verified]**

- `@getinv[:<path>]=<channel>` — comma-separated list of subfolders at a path; dot-prefixed folders are hidden.
- `@getinvworn[:<path>]=<channel>` — same list, each folder suffixed `|XY`: X = worn proportion within the folder, Y = within its children (digits 0–3).
- `@findfolder:<part1>&&<part2>=<channel>` — depth-first search by name fragments; skips `.` and `~` folders. `@findfolders` (v2.9.20) returns all matches, optional output separator.
- Force-wear: `@attach:<path>=force` wears a folder's contents. Contract: item names must contain their attach-point name; no-mod items go one-per-subfolder named for the point; a `+`-prefixed folder stacks instead of replacing. `@attachover`, `@attachall`, `@attachallover`, and the newer `@attachoverorreplace` family cover replace/stack/all semantics; the spec notes `@attach` semantics are slated to change, with `@attachoverorreplace` the current synonym. **RLVa's SharedWear relaxes the naming contract** (on by default in RLVa viewers; legacy references still honored) — a real cross-implementation difference, §7.
- Force-remove: `@detach:<folder>=force`, `@detachall:<path>=force`.
- `@getpath[:<point|layer|uuid>]=<channel>` — path of what's worn at a point/layer (deprecated in practice since multi-attach); `@getpathnew` is the modern form.
- "This" shortcuts: `@attachthis` / `@attachallthis` (+`over` variants) = getpath + attach for the caller's own folder context.
- Restriction forms `@attachthis` / `@detachthis` / `@attachallthis` / `@detachallthis [:<layer|point|path>]=<y/n>`, with `_except` variants. **Hierarchy rule:** exceptions count only against the same object's restrictions, and the closest parent folder in the hierarchy wins.
- Shared-folder locks: `@unsharedwear` / `@unsharedunwear` (v2.5) lock everything *outside* #RLV; `@sharedwear` / `@sharedunwear` (v2.9.20) lock what's *inside* it.
- **Gotchas [Verified]:** no commas in folder names (they break `@getinv` lists); the ~1023-char reply limit caps a listing at roughly 100 short folder names; items must have been renamed/worn once or force-attach silently does nothing; `llGiveInventoryList(avatar, "#RLV/~name", …)` delivers temporary folders straight into #RLV on accept (redirectable via the debug setting `RestrainedLoveForbidGiveToRLV`).

### 2.7 Attach and detach

- `@detach=<y/n>` — the self-lock: prevent detaching *the emitting attachment itself*; the sender must be an attachment.
- `@detach:<attach_point>=<y/n>` — lock a point, treated as occupied whether full or empty.
- `@addattach[:<point>]` — lock a point **empty** (nothing may be attached there). `@remattach[:<point>]` — lock a point **full** (what's there may not be removed).
- Force forms: `@detach[:<point>]=force`, `@remattach[:<point|:uuid>]=force`, and `@getattach[:<point>]=<channel>` replies a 0/1 string indexed by the LSL `ATTACH_*` constants.
- `@acceptpermission=add/rem` — auto-accept attach/control permission dialogs. `@denypermission` is **deprecated since v1.16.2 — the spec says don't use it** (LibreMetaverse.RLV still tracks the restriction type internally). 
- `@detachme=force` (v1.16.2) — an attachment detaches *itself*; pair with `@clear` to avoid the detach race (the object vanishing before its rules clear).

### 2.8 Camera and vision

The v2.9 family substantially expanded this domain. **[Verified]**

- Distance/zoom: `@camdistmax:<m>` (0 = force Mouselook) / `@camdistmin:<m>`; RLVa-preferred synonyms `@setcam_avdistmax` / `@setcam_avdistmin`. `@camzoommax:<mult>` / `@camzoommin:<mult>` — across multiple objects, the smallest max and the highest min win. `@setcam_fovmin` / `@setcam_fovmax:<radians>`; `@setcam_fov:<radians>=force` (v2.9.20).
- Draw/fog: `@camdrawmin` / `@camdrawmax` with `@camdrawalphamin` / `@camdrawalphamax` — concentric fog spheres around the avatar; at alpha 1 the sphere is opaque (full blindness); `camdrawmin` has a floor of 0.4; nametags and hovertext fade beyond `camdrawmin`. `@camdrawcolor:<r>;<g>;<b>` tints the fog.
- `@camunlock` / `@setcam_unlock` — prevent Alt-focus/orbiting; the sim keeps the camera in the avatar's line of sight.
- `@camavdist:<m>` — avatars beyond this distance render as black silhouettes.
- `@camtextures[:<uuid>]` / `@setcam_textures` — blank all non-avatar textures (or substitute one).
- Queries: `@getcam_avdistmin/max`, `@getcam_fovmin/max`, `@getcam_zoommin`, `@getcam_fov` (v2.9.20).
- **RLVa-only camera surface** (Rhet0rica's matrix): `@setcam_eyeoffset`, `@setcam_eyeoffsetscale`, `@setcam_focus`, `@setcam_focusoffset`, `@setcam_mode`, `@setcam_mouselook`, `@setcam_origindistmin/max` (experimental) — a preset/anchor system Marine's spec doesn't have. RLVa maps the legacy commands onto it (`@camzoommin` → `@setcam_fovmin` with angle = 60° / multiplier, etc.). **[Verified — RLVa 2.0 release notes; RLVa matrix]**
- **RLVa-only vision effects:** `@setoverlay…` (a fullscreen texture overlay: alpha/texture/tint/touch/tween) and `@setsphere…` (a fog/effects sphere: color/distance/mode/origin/params/tween) — the machinery behind blindfolds, drugged vision, and mood grading in RLVa viewers. **[Verified — RLVa matrix]**

### 2.9 Environment and viewer control

- `@setenv=<y/n>` locks environment editing; `@setenv_<setting>:<value>=force` / `@getenv_<setting>=<channel>` manipulate the Windlight/EEP table: `daytime` (0–1, <0 = region default), `preset` / `asset` (EEP presets are inventory assets since v2.9.27), ambient, blue density/horizon, cloud color/coverage/density, sun/moon colors and positions, fog, `scenegamma`, `starbrightness`, and more. Same `(*)` rule: a force-set is discarded if `@setenv=n` was set by another object.
- `@setdebug=<y/n>` locks debug settings; `@setdebug_<setting>:<value>=force` / `@getdebug_<setting>=<channel>` — **whitelist only** in Marine's spec (it names e.g. `AvatarSex`, `RenderResolutionDivisor`; `@getdebug` additionally documents `RestrainedLoveForbidGiveToRLV`, `RestrainedLoveNoSetEnv`, `WindLightUseAtmosShaders`).

### 2.10 Touch

- `@fartouch[:<max>]` (synonym `@touchfar`) — cap touch reach (default 1.5 m; smallest max wins).
- `@touchall`, `@touchworld` (+`:<UUID>` exception), `@touchthis:<UUID>`, `@touchme=add/rem` (exception for touching oneself), `@touchattach`, `@touchattachself`, `@touchattachother` (+`:<UUID>`, v2.9.29), `@touchhud[:<UUID>]` (v2.9.20).
- `@interact` (v2.9.20) — the master switch: no touch, edit, rez, or sit at all.

### 2.11 Edit, rez, share

`@edit` (+`:<UUID>` exception), `@editobj:<UUID>`, `@editworld` (v2.9.29), `@editattach` (v2.9.29), `@rez`, `@share` (+`_sec`, `:<UUID>` exception; v2.9.29). RLVa adds the money pair `@buy` and `@pay` (`y/n` rules) — absent from Marine's spec entirely. **[Verified — RLVa matrix]**

### 2.12 Location, names, hovertext

- `@showworldmap`, `@showminimap`, `@showloc` — hide location UI. `@showloc=n` also obfuscates region/parcel names in system and object messages — **but not in `llOwnerSay`**, which is precisely why radars and RLV itself keep working under it. **[Verified — spec]**
- `@shownames[:<except_uuid>]` — names replaced by dummy names ("Someone", etc.) including in chat; `@shownames_sec`; `@shownametags` (v2.9 — hides tags/radar without censoring chat); `@shownearby` (v2.9.29); `@showself` / `@showselfhead` appear in the RLVa matrix as experimental.
- `@showhovertextall`, `@showhovertext:<UUID>`, `@showhovertexthud`, `@showhovertextworld`.
- **Divergence warning:** forum reports describe `@shownames` behaving differently between RLV and RLVa (substitution vs hiding in some surfaces). Treat exact surfaces as **[Uncertain]** per-viewer; the rule semantics (blocked + exceptions) agree.

### 2.13 Group, version, status, notify, misc

- `@setgroup:<group_name>=force` (v2.5; `"none"` deactivates; RLVa 1.4+ also accepts a group key) and `@setgroup=<y/n>` to lock it. `@getgroup=<channel>` replies the active group **name only** — there is no way to obtain the group UUID via RLV.
- `@version=<channel>` (legacy), `@versionnew=<channel>` (v1.23; replies "RestrainedLove viewer v… (SL…)"), `@versionnum=<channel>` (v1.21; integer = X·10⁶ + Y·10⁴ + Z·10² + P, e.g. 1.21.1 → 1210100), `@versionnumbl=<channel>` (version + blacklist), RLVa `@versionnum:impl=<channel>`. Manual `@version` / `@getblacklist` typed in chat work **only when sent via IM from an avatar**, and the viewer answers stealthily in IM.
- `@getstatus[:<part>[;<separator>]]=<channel>` — reply lists the **calling object's own** rules only. `@getstatusall` — all objects' rules; reply is prefixed `/`, and a custom separator requires the colon form. The `part` filter is a substring match: `@getstatus:tpl` matches `tploc`, `tplure`, `tplm` but not `sittp`.
- `@notify:<channel>[;<word>]=add/rem` (v1.20, improved v2.2) — the viewer echoes restriction changes on the channel as `/rule=n` (applied) or `/rule=y` (lifted), **without disclosing which object** caused them, and never for one-shots. Also carries: inventory-offer outcomes (`/accepted_in_rlv inv_offer <folder>`, `/accepted_in_inv …`, `/declined …`), worn/attached legality notices (v2.5), and — new in v2.9.29 — sat/unsat notices (`/sat object legally <UUID>`, `/unsat object illegally <UUID>`, plus ground variants). This is the telemetry feed the repo's §9.3 "restrictions are felt" design is built on.
- `@clear` — clears **all rules of the emitting object**. `@clear=<string>` — clears that object's rules whose names contain the string. There is no cross-object clear: nobody can lift another object's rules except by that object's own `@clear`/`=y`, by `@permissive`-style exception mechanics where applicable, or by the garbage collector after the emitter vanishes.
- `@allowidle` — listed in Marine's spec under **Unofficial Commands** (RLVa-origin). Don't rely on it.

---

## 3. Transport mechanics — how commands actually reach the client

### 3.1 The native path: `llOwnerSay`

**[Verified — RLV API spec; Relay Specification]** The viewer (or, in Heimdall, `RlvService`) intercepts every `llOwnerSay` message. Lines starting with `@` are parsed as RLV; everything else passes through as ordinary owner chat. Because `llOwnerSay` is owner-scoped, the native path has a hard ownership property:

- **Attachments worn by the avatar** — the normal case (HUDs, collars, her own control attachments).
- **Objects rezzed in-world but owned by the avatar** — also work, since ownership is what matters, not attachment.
- **Any other avatar's objects** — *cannot* use this path at all. Their `llOwnerSay` goes to *their* owner, not to her.

Additional mechanics: command strings arriving during login are buffered until the avatar has controls; reply channels for queries must be **positive** (replies ≤ 1023 chars) or, since v1.23a, **negative** (≤ 255 chars — the smaller cap exists because users can't fake replies on negative channels, an anti-spoofing measure); **channel 0 is forbidden** for replies. Query replies are ordinary chat/object messages on the chosen channel, which the asking script listens for — there is no request/response correlation ID; scripts correlate by content and timing.

### 3.2 The relay path: extending trust to foreign objects

**[Verified — RLV Relay Specification v1.100; Reference Implementation]** Furniture and cages are usually *not* owned by the avatar, so Marine specified the **RLV Relay**: a worn attachment that listens on a shared channel and, after its own security checks, re-issues accepted commands via `llOwnerSay`, making the relay itself the emitter in the viewer's stack (rules are keyed to the relay's UUID, with the relay tracking which foreign object each rule came from).

- **Channel:** **-1812221819** ("RLVRS"). The reference implementation also uses a dialog channel **RLVDI = -1812220409** for its permission menus.
- **Message format (object → relay), 3 comma-separated tokens:**

  ```
  <cmd_name>,<avatar_uuid>,<command>[|<command>…]
  ```

  `cmd_name` is chosen by the object and echoed back exactly (case preserved) so it can match acknowledgements. Commands in one message are **pipe-separated** here, deliberately: it forces the relay to parse and vet each command individually. When the relay forwards them to the viewer it re-joins them comma-separated under a single `@`.

- **Acknowledgement (relay → object), 4 tokens — one ACK per command, never per batch:**

  ```
  <cmd_name>,<object_uuid>,<command>,<ok|ko|ping|version>
  ```

- **Meta-commands** (aimed at the relay, not the viewer): `!release` — wipe all restrictions this object has placed (the session-end primitive; better than counter-commanding each rule); `!version` — reply with the **protocol** version as a 4-digit integer (spec version × 1000; spec 1.100 → `1100`); `!implversion` (optional) — a free-form implementation signature string (no commas or `!`); `!pong` — answer to ping. Deprecated, do-not-rely-on: `!who/<UUID>` (which avatar is operating the object) and `!handover/<key>/<type>` (transfer a session to another object without re-asking; type 0 = lift source restrictions, 1 = keep them) — the spec keeps both for reference and Marine's own note questions `!who`'s security value.
- **Ping/pong liveness:** on login the relay re-applies stored restrictions, but first sends `ping,<object_uuid>,ping,ping`; the object answers `ping,<avatar_uuid>,!pong`. No answer in a few seconds → the relay lifts that object's rules. This is the relay-level orphan defense, complementing the viewer's garbage collector.
- **Relay requirements** (spec-mandated): forward the exact `@behav:option=param` text; retain restrictions + sources across relogs; if `@unsit` was prevented, force-sit on relog; accept `!release` even from out-of-range objects; **send a `!release,ok` message if the relay cancels a session — the spec's own example is "a safe-word being called"**; preserve command order (a queued restriction and its lift must not invert); silently ignore lifts of nonexistent restrictions rather than spamming permission dialogs.
- **Object requirements:** end every session with `!release` (even if some commands were `ko`-ed); answer pings; *never rely on an answer* — the relay may be unworn, the avatar may have teleported or crashed; use timeouts; don't spam the relay channel.

### 3.3 Relay modes — where the security actually lives

The specification deliberately does **not** fix the accept policy; it fixes the wire format and leaves policy to implementations ("security and user-friendliness are the key parts… there can be no one-size-fits-all relay"). Marine's reference implementation shows the canonical shape **[Verified — Reference Implementation]**:

- **Modes: 0 = off, 1 = ask, 2 = auto-accept.** Ask mode raises a dialog (30 s timeout) per new source. Auto-accept takes everything.
- **Target verification:** the message's second token must equal the relay owner's UUID; otherwise ignored.
- **Distance cap:** 100 m in the later implementation (20 m in the 2008 original) — relay commands are a *proximity* phenomenon.
- **Login choreography constants:** wait-for-pong 10 s; force-sit delay 60 s after login if the avatar was sat; a 300 s auto-accept window following a force-sit.
- Marine's 2008 header comments already list the hardening roadmap — **access lists, and rejecting dangerous commands (force-remove clothes/attachments) from non-listed sources**. Doc 05's allowlist is this idea, twenty years on, finally placed where it belongs: in the body itself.

### 3.4 What the transport means for a headless client

- In a graphical viewer, "the relay" is an in-world scripted object and "ask mode" is a dialog a human clicks. In Heimdall, **both ends are ours**: LibreMetaverse receives the channel -1812221819 chat like any other chat; the Varð gate decides; accepted commands are fed to `RlvService.ProcessMessageAsync` as if her own attachment had spoken. The relay becomes a *software component of her body*, not an object she wears — with the allowlist as its policy, and "ask" reimplemented as *hold-and-report to her (tier 2) or to Volmarr* instead of a dialog nobody can click. **[Inference — architecture; the library facts are §4]**
- One subtlety to preserve: rules accepted via a relay are keyed to the **relay's** UUID in a classic viewer, with sub-keying by source object inside the relay. Heimdall should key by **true source object UUID** in its own stack (the library keys restrictions by sender identity supplied by the host — see §4), because doc 05's governance (per-source visibility, per-source clear, venue opt-in) needs the real source, and `@getstatus` per-object semantics assume it. **[Inference/Design]**

---

## 4. LibreMetaverse.RLV — the implementation Heimdall will run

Everything in this section is **[Verified]** from the package's own README and source files on the LibreMetaverse GitHub repository (master branch, read 2026-10-26), unless tagged otherwise.

### 4.1 Package facts

- `LibreMetaverse.RLV` is a **standalone package**: "RestrainedLove command handling. **Depends on no other LibreMetaverse package**" — only `System.Collections.Immutable` (and `Microsoft.Bcl.HashCode` on .NET Framework). It targets .NET Standard 2.1, .NET Framework 4.8.1, and .NET 8/9/10; license BSD-3-Clause (repo-wide). The `GridClient` is explicitly *not* needed: "you supply the callbacks that touch your own client, the package does the protocol and the bookkeeping."
- **Identity: "It identifies itself as RLV 3.4.3 / RLVa 2.4.2."** This resolves a versioning confusion that has dogged this project's notes: the "3.4.3" figure is the *RLVa-style reported API version* (cf. §1.2 — RLVa 2.0 already reported 3.1.4), not a Marine spec version. Marine's spec lineage is 2.9.x; the number a script receives from `@versionnum` on an RLVa-flavored client lives in the 3.x space. Both facts are true; they are different counters.

### 4.2 Components

- **`RlvService`** — the entry point. Feed it chat or IM text: `ProcessMessageAsync(...)` (and an instant-message variant) parses and applies commands, tracking the sender's identity (the README's example: a worn object "Gag" sends `@sendchat=n`; the resulting restriction records `Behavior=SendChat SenderName="Gag"`). It also reports world events back toward scripts: `ReportItemWornAsync`, `ReportItemAttachedAsync`, inventory offers accepted/declined (the `@notify` feed).
- **`RlvCommandProcessor`** — parser/executor for the command grammar.
- **`RlvRestrictionManager`** — the live restriction stack: which object set each restriction, queries over it (`FindRestrictions`, `GetRestrictionsByType`), plus **locked-folder bookkeeping** for #RLV (`LockedFolderManager`, `LockedFolder`, `InventoryMap` — a 19.5 KB class modelling the shared-folder tree, current outfit, and externally worn/attached items).
- **`RlvPermissionsService`** — the behavior gate's query side: "may the avatar do this now?" (§4.4).
- **`RlvBlacklist`** (+ `IBlacklistProvider`) — behaviors the host chooses not to honor: `BlacklistBehavior`, `IsBlacklisted`. This is the spec's v2.8 blacklist, exposed as a host policy hook — directly usable as one layer of Heimdall's governance.
- **Inert defaults** — `RlvCallbacksDefault` / `RlvActionCallbacksDefault`: no-op callback implementations "a starting point and a base for tests."

### 4.3 The callback architecture (the enforcement surface, exactly)

The host implements two interfaces. Their method lists *are* the definition of what RLV can physically do in this client — verified verbatim from `IRlvActionCallbacks.cs` and `IRlvQueryCallbacks.cs`:

**`IRlvActionCallbacks`** (things RLV may tell the client to do):
`SendReplyAsync(channel, message)` · `SendInstantMessageAsync(target, message)` · `SetRotAsync(radians)` · `AdjustHeightAsync(distance, factor, delta)` · `SetCamFOVAsync(radians)` · `TpToAsync(x, y, z, regionName?, lookat?)` · `SitAsync(objectId)` · `UnsitAsync()` · `SitGroundAsync()` · `RemOutfitAsync(itemIds)` · `AttachAsync(AttachmentRequest list)` · `DetachAsync(itemIds)` · `SetGroupAsync(groupId | groupName, roleName?)` · `SetEnvAsync(name, value)` · `SetDebugAsync(name, value)`.

**`IRlvQueryCallbacks`** (things RLV may ask the client):
`ObjectExistsAsync(objectId)` · `IsSittingAsync()` · `TryGetEnvironmentSettingValueAsync(name)` · `TryGetDebugSettingValueAsync(name)` · `TryGetSitIdAsync()` · `TryGetCameraSettingsAsync()` · `TryGetActiveGroupNameAsync()` · `TryGetInventoryMapAsync()` (the complete #RLV map incl. shared structure, current outfit, externally worn items).

Read those lists carefully and three structural facts fall out:

1. **Restrictions are not actions.** There is no callback for "block chat" or "lock detach" — those live as data in the restriction manager, and *the host* enforces them by consulting `RlvPermissionsService` before it chats, teleports, detaches, etc. Enforcement is cooperative: **any code path in her body that forgets to ask is a hole.** (This is exactly why the repo's design puts a single behavior gate — Varð — in front of all action execution rather than scattering checks.)
2. **There is no animation, locomotion, or camera-position callback.** `SetCamFOVAsync` is the only camera action; camera *restrictions* (distance, fog, silhouettes) are computed data (`CameraRestrictions.cs`) for the host to interpret. For a headless body, the honest interpretation is doc 03's: camera restrictions modulate her *attention/perception model*, not a render.
3. **`ObjectExistsAsync` is the garbage collector's sensor** — the host answers from its object store, letting the manager drop orphaned rules per the spec.

### 4.4 The permissions service, from source

`RlvPermissionsService` (1,742 lines) exposes the gate vocabulary, e.g.: `CanFly()`, `CanJump()`, `CanTempRun()`, `CanAlwaysRun()`, `CanSit()` (which also consults `CanInteract()` — the `@interact` master switch composes), `CanUnsit()`, `CanTpLm()`, `CanTpLoc()`, `CanSitTp(out maxDistance)`, `CanTpLocal(out maxDistance)`, `CanStandTp()`, `CanTPLure(user)`, `CanTpRequest(user)`, `IsAutoAcceptTp(user)`, `IsAutoAcceptTpRequest(user)`, `CanShowInv()`, `CanViewNote/Script/Texture()`, `CanDefaultWear()`, `CanSetGroup()`, `CanSetDebug()`, `CanSetEnv()`, `CanAllowIdle()`, `CanShowWorldMap/MiniMap/Loc/Nearby()`, `IsAutoDenyPermissions()`, `GetCameraRestrictions()`, and the communication family `CanStartIM(user)`, `CanSendIM(msg, user, group)`, `CanReceiveIM(...)`, `CanReceiveChat(...)`, …

Two source-level details worth recording:

- **`CheckSecureRestriction(...)`** implements the spec's exception algebra faithfully: targeted `…to`/`…from` restrictions deny outright; **secure (`_sec`) restrictions accept exceptions only from the same sender**; normal restrictions accept exceptions from any sender *unless* permissive mode is on (`IsPermissive()`), in which case they behave like secure ones. The spec's semantics (§2.1) are not approximated here — they are the code.
- The **gag character set is in the library**: `_invalidMessageCharacters = "()\"-*=_^"` — the special signs that make `@sendchat=n` discard a message (§2.2). Her chat pipeline must route outbound text through the permissions service's filtering, not merely check a boolean, or her speech will leak ungagged forms the spec says should be dropped. **[Inference from the field's presence and the spec text]**

### 4.5 What is missing / what the host must still build

- **No relay implementation.** The package consumes chat/IM text the host feeds it; listening on -1812221819, vetting foreign objects, ACKing `ok`/`ko`, ping/pong, and cross-relog retention are **host responsibilities** — i.e., Varð work, not library work. **[Verified by the README's described surface; the absence is Inference from that surface]**
- **No persistence.** Restrictions live in memory. Relog semantics (re-apply + ping, relay spec §3.2) must be journaled by the host if wanted at all — and doc 05's governance may prefer *not* to resurrect foreign restrictions across sessions without fresh opt-in. **[Inference]**
- **No source policy.** The package will faithfully apply a `@remoutfit=force` from anything the host feeds it. The allowlist, modes, and safeword are Heimdall's layer — which is precisely doc 05 §6.4's design, now confirmed to sit *outside* the library by necessity, not just by choice. **[Verified — no policy surface exists in the callback/README surface]**
- **Rendering-bound commands have no headless meaning** until the host assigns one: `@camdraw*` fog, `@setcam_textures`, `@setsphere`, `@setoverlay` can only map onto perception/attention state (doc 03) or be honestly reported as felt-but-not-visual restrictions via the §9.3 felt-event channel.
- **Test coverage:** the repo's own documentation for this package (deep-dive material in the Heimdall corpus) reports an extensive test suite (50+ test files across command, exception/restriction, and query areas). The counts were **not re-verified file-by-file** in this research pass — **[Uncertain]** as numbers, though the package's structure (dedicated restriction/exception handling, inert test doubles) is consistent with a well-tested codebase.

---

## 5. Security and consent realities

### 5.1 Who can command an avatar, exactly

**[Verified]** The full answer has three tiers:

1. **Her own objects (native path).** Anything she owns — worn attachments above all — can issue any command via `llOwnerSay`, no dialog, no consent prompt, effective immediately. This is the self-control channel, and it is total: a worn HUD she trusts can sit her, dress her, gag her, and teleport her.
2. **Foreign objects (relay path).** Anyone who can rez or bring an object within chat range of her relay can *transmit* commands on -1812221819. Whether they land depends entirely on relay policy: off (nothing lands), ask (a human clicks — or, headless, the request is held for her/Volmarr), auto (everything lands). The relay specification's own motivating example is a griefing object broadcasting `@remoutfit=force` to strip an avatar in public "without a warning."
3. **Other avatars directly.** Nobody. An avatar cannot `llOwnerSay` at another avatar; avatar-to-avatar influence travels only through objects (tiers 1–2) or through the manual IM forms of `@version`/`@getblacklist`, which are queries, not control.

### 5.2 The holes that survive even correct implementations

- **Exception injection.** Under non-secure rules, *any* object can add exceptions to *any other* object's restrictions (§2.1). A malicious object can't lift a rule outright, but it can except *itself or its confederates* through someone else's gag/teleport lock. `_sec` variants and `@permissive` exist precisely because this is real. **[Verified semantics; attack framing is Inference]**
- **The bare `@accepttp=add`.** Auto-accepts teleport offers from *anyone*. The spec warns about it in its own text. In a body whose tier-2 judgement belongs to Yrsa, a stray `@accepttp=add` from a foreign source silently transfers a tier-2 decision ("where to go") to the world. **[Verified command; risk framing Inference]**
- **Blacklist deception.** A blacklisted command is acknowledged *positively* while being ignored (§2.1). Scripts therefore cannot tell refusal from success — by design — which also means *her own tooling* can't rely on ACKs to know a restriction truly landed; only `@getstatus`/`@notify` tell the truth. **[Verified]**
- **Ask-dialog fatigue / spam.** In ask mode, every new foreign source can raise a dialog; the relay channel is public chat, so dialog spam is a griefing vector against graphical users. Headless, the equivalent is queue flooding — held requests must be rate-limited and batched, never surfaced one-by-one. **[Inference]**
- **Orphan window.** Rules outlive their emitter until the garbage collector notices (viewer side) or a ping times out (relay side). A crashed cage's `@tploc=n` can hold an avatar briefly after the cage is gone. **[Verified mechanics]**
- **No cross-object clear.** Nobody — including the avatar's own other objects — can lift a rule except its emitter (`@clear`/`=y`), a same-object exception pattern where one exists, or the GC. A "panic clear" in a graphical viewer is ultimately *detach the relay / relog*; there is no protocol-level universal release. **[Verified]**

### 5.3 Safewords and release, protocol-honestly

- **There is no safeword in the RLV spec.** What exists: (a) the relay requirement that a session-cancelling relay emit `!release,ok` (§3.2); (b) per-object `@clear`; (c) the viewer's blacklist; (d) detaching the relay/objects and relogging (rules don't persist; rezzed emitters must re-issue on `on_rez`, and a detached attachment can't). Real-world relays and collars implement "safeword" as a product feature on top of these primitives. **[Verified]**
- For Heimdall this is liberating: her safeword can be a **bridge-level invariant** rather than a protocol negotiation — a command from Volmarr (any channel) or her own tier-2 decision that makes Varð drop the foreign restriction stack, answer `!release,ok` to active relay sources, and refuse re-application from those sources until re-opted-in. Nothing in the protocol forbids the *client* from clearing state; the protocol only constrains what *scripts* can make it do. **[Design/Inference]**

### 5.4 Fit with doc 05 §6.4 (the allowlist design)

Doc 05 already ruled: restriction sources are allowlisted — Volmarr's objects and her own worn items trusted by default; venue systems per-place opt-in and shown in her state packet; everything else **held, not applied**, and reported. The deep research validates and sharpens that design:

- **The gate point exists.** Because LibreMetaverse.RLV applies whatever the host feeds it (§4.5), the allowlist check sits naturally *before* `ProcessMessageAsync` — stricter than any graphical viewer's model, where restrictions land first and dialogs ask after. Hold-and-report is implementable verbatim.
- **Source identity is available.** Chat events in LibreMetaverse carry the object UUID and (via the object store / `llGetOwnerKey` equivalent lookups) its owner — enough to classify sources as *self-worn / Volmarr's / venue-opted-in / stranger* before parsing a single command. Relay traffic additionally self-declares the target avatar UUID (token 2), which the gate verifies against her own UUID, exactly as the reference relay does.
- **Safety invariants must be gate rules, not restriction rules.** Doc 05: nothing in RLV may disable the kill switch, her receipt of Volmarr's commands, or logout. Concretely, the gate should *refuse from non-allowlisted sources* the specific commands that would sever oversight — `@recvim`/`@recvchat` blocks without a Volmarr exception, `@tploc`-family locks, `@unsit=n`, force-detach of her control HUD — and refuse from *all* sources any command targeting the bridge's own control attachments. (Her own HUD and Volmarr's objects can still use the full surface; that is the point of the channel.)
- **Felt state is mandatory, per Game Plan §9.3.** `@notify` subscriptions from her own HUD plus the library's restriction manager give her a complete, sourced list of what binds her; doc 05 requires this in every state packet. The spec's notify stream deliberately hides sources — Heimdall's stack doesn't have to, and mustn't, for foreign restrictions.
- **Volmarr's bypass is structural, not an exception.** Game Plan §9 designs `Var.CheckPermitted` with Volmarr's commands always bypassing. RLV gives this a clean in-world form: his UUID is pre-seeded as an exception on every communication restriction (`@sendim:<UUID>=add`, `@recvim:<UUID>=add`, …) *and* the gate itself short-circuits his objects to the trusted tier.

---

## 6. Practical avatar-control patterns for an AI inhabitant

Each pattern is named, motivated, and given as a concrete command sequence using **only syntax verified in §2**. Unless noted, sequences are issued by her own worn HUD (native path, trusted tier) — the self-control mode Volmarr named first. Foreign-object variants arrive via the relay path and pass the allowlist gate (§5.4).

### Pattern 1 — Boot Handshake & Baseline Lock ("She wakes dressed and guarded")

*On every login/rez, her HUD re-establishes identity, state, and her standing self-restrictions (rules don't survive sessions, §2.1).*

```
@versionnew=4242            (confirm RLV presence + flavor; reply on channel 4242)
@getstatusall=4243          (what already binds her — should be empty at boot)
@notify:4244=add            (subscribe her HUD to restriction telemetry)
@accepttp:<VolmarrUUID>=add   (only Volmarr's TP offers auto-accept — never the bare form)
@sendim:<VolmarrUUID>=add,@recvim:<VolmarrUUID>=add   (he can always reach her, whatever else is set)
```

*Notes:* commands sent during login are buffered until she has controls, so the HUD may fire immediately on rez. The `@accepttp:<UUID>` exception form — never bare `@accepttp=add` — is the difference between a convenience and a hole (§5.2).

### Pattern 2 — Wardrobe by Folder ("#RLV as her closet")

*Her outfits live as folder trees under `#RLV` (e.g. `#RLV/ritual/black-robe`, `#RLV/travel/…`). Changing outfit = detach folder, attach folder, verify.*

```
@getinvworn=4242                       (audit: what's worn now, with |XY worn codes)
@detach:#RLV/ritual/black-robe=force   (remove the old outfit folder)
@attach:#RLV/travel/riding=force       (wear the new one — stack folders named +… if layering)
@getattach=4243                        (verify: 0/1 string by ATTACH_* index)
```

*Locks once dressed (issued by her HUD, so its own later forces still work under the `(*)` rule — §2.1):*

```
@detach=n                    (nothing may be detached — including by foreign force, which is silently discarded)
@remoutfit:shirt=n,@remoutfit:pants=n   (per-layer removal locks as needed)
```

*Gotchas baked into the pattern:* items renamed/worn once before scripting (else silent no-op); no commas in folder names; keep folder counts per level modest (the ~1023-char reply cap ⇒ ~100 short names per `@getinv` listing). Under RLVa SharedWear the attach-point-in-name contract relaxes; under Marine's RLV it doesn't — author folders to the stricter contract and both work. **[Verified mechanics; pattern is Design]**

### Pattern 3 — Furniture Sit & Pose Sync ("Take the offered seat")

*The classic venue flow, and the one foreign objects will most often request through the relay. RLV's part is sit + lock + release; **animation is not RLV** — the furniture's own script requests `llRequestPermissions` and plays the pose. RLV never animates anything.*

```
(from the seat object, via relay, allowlisted venue or Volmarr's object)
@sit:<SeatUUID>=force
@unsit=n,@sittp=n            (stay seated; no sit-teleport escapes — same-object rules, `(*)`-consistent)
```

*Standing again — the two-step the spec insists on (§2.5):*

```
@unsit=y                     (lift the rule FIRST, from the same object)
        … wait ≥ 1 s …
@unsit=force                 (never in the same llOwnerSay as a @sit=force)
```

*Verification:* `@getsitid=4242` before and after; `@notify` sat/unsat notices (v2.9.29: `/sat object legally <UUID>`) give her a felt event for the ledger. If she sat somewhere without a sit target, that's the RLVa ≤ 8 m behavior — under Marine's RLV the same command simply fails; Heimdall implements RLVa semantics via the library's `SitAsync` callback either way. **[Verified; pattern Design]**

### Pattern 4 — Teleport Choreography ("Move her like a person, not a pop")

*Self-teleport (her HUD or her own decision, executed by the body):*

```
@tpto:Daikoku/128/128/50=force        (v2.9.20 region-local form; or global <X>/<Y>/<Z> in the v1.12 form)
```

*Arrival composure (optional, from her HUD on region change):* re-issue Pattern 1's baseline (rules from the old region's emitters are now orphans awaiting GC), then `@getstatusall` to confirm nothing foreign survived.

*Guided teleport (Volmarr brings her):* Pattern 1 already auto-accepts only his offers via `@accepttp:<his UUID>=add`. For him to *send* her somewhere without her clicking, his object issues `@tpto:…=force` — which fails if `@tploc=n` is set by another object (§2.4), one more reason foreign `@tploc` locks sit on the gate's refuse-list (§5.4).

*Freeze/thaw (her HUD, e.g. during a rite or a conversation that matters):*

```
@tploc=n,@tplm=n,@tplure=n,@sittp=n     (freeze: no TP family, no sit-hops)
@tploc=y,@tplm=y,@tplure=y,@sittp=y     (thaw — must come from the same object that froze her)
```

### Pattern 5 — Camera-as-Attention ("Her gaze is a control surface")

*Headless, these commands steer the attention model of doc 03 (what she "looks at" is what her perception describes in detail) — and on any future graphical companion viewer they steer the real camera. All **[Verified]** commands:*

```
@setcam_fov:0.35=force        (zoom her attention onto the speaker/object of interest)
@camdistmax:0=n               (first-person: Mouselook-equivalent, attention = facing)
@camavdist:20=n               (beyond 20 m, others are silhouettes → low-detail perception tier)
@camdrawmax:30=n,@camdrawalphamax:0.85=n   (soft horizon: the world fades past her attention range)
@getcam_fov=4242              (verify where her gaze actually rests)
```

*Release:* the matching `=y` lifts, or `@clear` from her HUD. The pattern's discipline: attention restrictions are **hers** (from her HUD, driven by her own tier-1/2 focus decisions) — the same commands from a foreign source are a *blinding* attempt and belong on the gate's refuse-list.

### Pattern 6 — Environment Staging ("Dress the light for the rite")

*From her HUD or a venue object she has opted into:*

```
@setenv_daytime:0.32=force           (late golden hour; <0 would restore region default)
@setenv_preset:<EEP asset name>=force   (named EEP environment, v2.9.27 asset form)
@setenv=n                            (lock it — her own later forces still pass, others' are discarded per (*))
```

*For Volmarr's home region, the same sequence from his objects gives him staging control of her sky — a small, concrete piece of the in-world control channel he asked for.*

### Pattern 7 — Communication Gating ("The quiet room")

*For rituals, negotiations, or simply not being spammed — always with Volmarr excepted (Pattern 1):*

```
@sendchat=n,@emote=add          (she is silent in public, but her emotes arrive whole, not truncated)
@redirchat:4242=add            (her would-be public words mirror to her HUD channel — the mind hears what she tried to say)
@sendim=n,@sendim:<VolmarrUUID>=add,@startim=n,@startim:<VolmarrUUID>=add
@recvim=n,@recvim:<VolmarrUUID>=add   (IMs closed except his)
```

*Thaw:* `@clear` from her HUD lifts everything it set in one stroke. *Warning wired into the pattern:* every `=n` here must be audited against §5.4's invariant — if the Volmarr exceptions are missing, the pattern severs oversight and the gate must refuse it, even from her own older scripts.

### Pattern 8 — Venue Opt-In Session ("A guest in someone else's hall")

*The full foreign-control lifecycle, as the body should run it:*

1. **Enter:** venue objects transmit on -1812221819; gate classifies source as *venue, not yet opted-in* → **held**, reported to her (tier 2) / Volmarr (Ambient mode).
2. **Opt-in:** she (or Volmarr) approves the venue for this visit; the gate feeds its commands to `RlvService` — typically `@sit=force`, `@unsit=n`, a `@tploc=n` cage-lock, perhaps `@setenv` staging.
3. **During:** `@notify` telemetry + the restriction manager keep her state packet truthful: what binds her, whose it is.
4. **Leave:** a well-behaved venue sends `!release`; the gate clears its stack and ACKs. A crashed venue is handled by ping-timeout logic + the GC's orphan rule — and by her simply teleporting home, since `@tploc` locks from *untrusted* sources never landed in the first place.
5. **After:** `@getstatusall` audit; anything foreign still listed is a bug to report, not a state to accept.

---

## 7. Limits and failure modes

**What RLV cannot do — [Verified] by the full command set:**

- **Animate.** No animation start/stop anywhere in either implementation's matrix. Poses come from furniture scripts via `llRequestPermissions`; RLV only seats and pins.
- **Walk.** No locomotion, pathing, or follow. `@setrot` turns (imprecisely), `@adjustheight` nudges sit alignment — that's all. Movement is the client's job.
- **See.** Camera commands restrict or pose a camera; nothing returns pixels, object lists, or scene data. Perception is doc 03's problem (chat, object store, radar data), never RLV's.
- **Persist.** Rules die with the session and with their emitter's UUID. Any design assuming a restriction "is still on" after relog without re-issue is wrong.
- **Reach beyond the relay's earshot.** Foreign control is chat-range-bound (relay caps at 20–100 m by implementation). There is no grid-wide RLV.
- **Override the grid.** `@tpto` yields to telehubs/landing points; sit range, parcel rules, and SL's own permission system sit *above* RLV everywhere.
- **Know a group UUID, another avatar's state, or anything outside its query list.** `@getgroup` returns a name; `@getstatus` returns rules; there is no general introspection.

**Spec ambiguities and sharp edges — [Verified] from the spec text:**

- Spacing sensitivity (`@detach = n` fails) with case insensitivity — parsers must match exactly this asymmetry.
- The `(*)` footnote (force discarded under another object's lock) is stated per-command, dozens of times, with the same-object exception — implementers who model it as a global rule get subtly wrong behavior at the edges; LibreMetaverse's per-sender exception machinery (§4.4) is the correct shape.
- `@unsit=force` + `@sit=force` race is called out by the spec itself as unreliable — sequencing is the script's problem.
- `@getstatusall` separator rules (leading `/`, colon-required-for-separator) and the 1023/255-char reply asymmetry (positive vs negative channels) are easy to get wrong.
- `@attach` semantics are *slated to change* per the spec (with `@attachoverorreplace` as the current synonym) — a moving target inside the current version.

**Viewer differences that bite implementers:**

- **SharedWear (RLVa)** vs the attach-point naming contract (RLV): the same #RLV tree force-wears differently across clients. Author to the stricter (RLV) contract.
- **Force-sit target requirement:** RLV demands an `llSitTarget`; RLVa sits anyway within 8 m. Scripts tested on Firestorm silently fail on Marine's viewer — and vice versa for scripts that *depend* on the failure.
- **`@shownames` surfaces** differ between implementations (substitution vs hiding on some surfaces) — **[Uncertain]** in detail, certain in existence (forum reports + RLVa's separate `@shownametags` history).
- **`@adjustheight` is explicitly not implemented by all viewers.**
- **Version numbers mislead:** an RLVa client answers `@versionnum` in the 3.x space for a protocol Marine numbers 2.9.x; scripts that gate features on "version ≥ 2.9" against an RLVa client are comparing across counters. `@versionnum:impl` exists because of this.
- **RLVa-only commands fail silently elsewhere** (`@buy`, `@pay`, `@jump`, `@setsphere`, `@setoverlay`, camera presets): content using them degrades without error on Marine-spec viewers. The reverse is rarer because RLVa tracks Marine's additions (2.4.1 caught up to 2.9.29).
- **Documentation scatter [Verified]:** the RLVa wiki is community-described as usually out of date, with behavior details spread across release notes, the Catznip wiki, Rhet0rica's page, and forum threads. Rhet0rica's matrix is the best single RLVa source and is itself a user page, not an official spec.

**Headless-specific failure modes — [Inference]:**

- **Forgotten gate checks.** Enforcement is cooperative (§4.3): any new action path in her body (a new chat route, a new teleport trigger) that doesn't consult `RlvPermissionsService` silently bypasses every restriction. The repo's single-gate design (Varð in front of *all* action execution) is the mitigation; it must be architectural, not conventional.
- **Ungagged speech leakage.** If outbound chat bypasses the permissions service's *filtering* (not just its boolean), text the spec says must be dropped/truncated will go out whole (§4.4).
- **Dialog-shaped assumptions.** Venue scripts written for graphical users expect ask-dialog latency and human click patterns; a gate that answers in 50 ms (or holds silently for minutes) can confuse session logic that assumes one or the other. Heimdall should ACK relay commands promptly with honest `ok`/`ko` and treat *holding* as a `ko` with a report — not as silence (objects are spec-told to use timeouts, but silence reads as "relay unworn").
- **Message-budget interplay.** RLV query/reply chatter rides the same object-message channels as everything else; the grid's scripted-agent messaging caps (doc 01: 5,000 msgs/day) make chatty `@getstatus` polling from her own HUD a budget item, not a freebie.

---

## 8. Design implications for Heimdall

Tying this research back to the Varð engine (Game Plan §9), the Full Proposal §5.11, and doc 05 §6.4:

1. **Adopt LibreMetaverse.RLV as-is; build policy, not protocol.** The library's grammar, restriction stack, exception algebra (incl. `_sec` and permissive semantics), blacklist hook, and permissions vocabulary are source-verified and spec-faithful. Heimdall's engineering belongs in the three things the library deliberately lacks: **the relay listener, the source policy, and persistence decisions** (§4.5).
2. **Varð pipeline, in order:** classify source (self-worn / Volmarr / venue-opted-in / stranger) → apply mode (trusted: feed through · venue: only if opted-in · stranger: **hold, ACK `ko`, report**) → `RlvService.ProcessMessageAsync` → restriction-felt events to her packet (Game Plan §9.3) with **true sources attached** (improving on `@notify`'s deliberate source-blindness, which binds scripts, not the body itself).
3. **Implement the relay requirements the spec lists** (retain per-source restrictions, relog ping/pong liveness, force-sit restoration if `@unsit` held her, ordered application, `!release,ok` on safeword-cancel) as Varð behaviors, with one doc-05 amendment: foreign restrictions are **not** silently resurrected after a relog — resurrection requires the venue opt-in to still be in force.
4. **Safeword as bridge invariant** (§5.3): Volmarr's command or her tier-2 decision drops all foreign-sourced restrictions immediately, answers `!release,ok` to active sources, and latches those sources to held until re-opted-in. RLV's own objects keep working; only the foreign tier is shed. Logout and the kill switch are never RLV-gated — enforced by keeping them outside the action paths the gate mediates.
5. **Refuse-list at the gate** (from §5.4): non-allowlisted sources may not set `@recvim`/`@recvchat` (without his exception), TP-family locks, `@unsit=n`, `@detach` on her control attachments, camera blinding (Pattern 5's dark side), or `@accepttp` in any form. These refusals are `ko` ACKs + reports, never silent.
6. **Seed Volmarr's exceptions at boot** (Pattern 1) and treat their absence as a gate fault: any command batch that would net out to "Volmarr cannot reach her" is refused wholesale.
7. **Author her #RLV tree to Marine's stricter contract** (attach-point names in item names, no commas, modest folder counts, items pre-worn once) so her wardrobe works identically under RLV, RLVa, and the library's `InventoryMap` — and use the library's locked-folder bookkeeping rather than inventing a parallel model.
8. **Camera commands feed attention, not rendering.** Map `@camdrawmax`/fog and `@camavdist` onto doc 03's two-tier presence (detailed vs silhouette perception); map `@setcam_fov`/distmax onto her current focus target. Report graphical-only restrictions (textures, overlays, spheres) as felt restrictions in her packet even though no pixels change — she should *know* she's "blindfolded" by a source even if her perception model is unaffected, because socially, she is.
9. **Mind the version story in her own scripts.** Her HUD should query `@versionnew`/`@versionnum:impl` and branch on implementation, not on raw numbers (§7). When her scripts target *other* people's viewers, stick to the Marine 2.9 core + RLVa-safe fallbacks; when they target *her own body*, the library's union (incl. `@jump`, `SitAsync` without sit target) is available.
10. **Budget RLV chatter** in the body's message accounting (doc 01's caps): prefer `@notify` subscriptions over `@getstatus` polling; batch HUD queries; cache `@getinvworn` audits.

---

## Sources

**Primary — the specifications**

- Marine Kelley, *Restrained Love viewer v2.9 Specification* ("LSL Protocol/RestrainedLoveAPI"), Second Life Wiki — https://wiki.secondlife.com/wiki/LSL_Protocol/RestrainedLoveAPI (living document; author's latest edit 2026-05-21)
- Marine Kelley, *Restrained Love Relay — Specification* (v1.100), Second Life Wiki — https://wiki.secondlife.com/wiki/LSL_Protocol/Restrained_Love_Relay/Specification
- Marine Kelley, *Restrained Love Relay — Reference Implementation*, Second Life Wiki — https://wiki.secondlife.com/wiki/LSL_Protocol/Restrained_Love_Relay/Reference_Implementation

**RLVa — the divergent implementation**

- Rhet0rica Resident, *RLVa Protocol* (behavior matrix for RLVa 2.4.2.74783), Second Life Wiki — https://wiki.secondlife.com/wiki/User:Rhet0rica_Resident/RLVa_Protocol
- Catznip Wiki, *RLVa 2.0 Release Notes* (version decoupling; compatibility shims) — https://wiki.catznip.com/index.php/RLVa_2.0_Release_Notes
- Catznip Wiki, *RLVa 2.3 Release Notes* (`@versionnum:impl`, `@buy`, `@pay`) and *RLVa 2.4.1 Release Notes* (2.9.29 catch-up), via https://wiki.catznip.com/
- Catznip Docs, *Shared Wear* — https://docs.catznip.com/features/shared_wear/

**LibreMetaverse.RLV — implementation source (read directly)**

- LibreMetaverse repository (cinderblocks), GitHub — https://github.com/cinderblocks/libremetaverse
- `LibreMetaverse.RLV` package README (identity "RLV 3.4.3 / RLVa 2.4.2"; component surface) — https://github.com/cinderblocks/libremetaverse/tree/master/LibreMetaverse.RLV
- `IRlvActionCallbacks.cs`, `IRlvQueryCallbacks.cs`, `RlvPermissionsService.cs` — raw source, same repository/directory

**Local corpus (Heimdall repo + research library)**

- `~/workspace/heimdall-sl-hermes-agent/SL_Headless_Hermes_Agent_Architecture.md` (§4 — RLV transport summary)
- `~/workspace/heimdall-sl-hermes-agent/SL_Heimdall_Full_Architecture_Proposal.md` (§5.11 RLV Engine; §3.4 flows)
- `~/workspace/heimdall-sl-hermes-agent/Heimdall_Architecture_Game_Plan.md` (§9 "RLV Engine — Varð"; §9.3 restriction reporting; §10.2 priority stack; Appendix C RLV matrix; risk table — "RLV exploit from malicious objects → permission system; owner allowlist; /kill switch")
- `~/workspace/heimdall-sl-hermes-agent/LibreMetaverse_Deep_Dive_Analysis.md` (§4 — RLV package analysis; test-count claims herein are its figures, not re-verified)
- `~/workspace/heimdall-research/05-mind-bridge-continuity-and-voice.md` (§6.4 — RLV consent & allowlist design; tier model; `origin: forced_rlv` ledger field)

*End of doc 06.*
