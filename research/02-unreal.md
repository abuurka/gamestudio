# 02 — How Unreal Engine Works (UE 5.x, as of October 2026)

*Research input for the "game engine on top of Blender" architecture decision. Focus: what to copy, what to avoid. Researched 2026-10-09.*

**Version context.** UE 5.7 shipped in November 2025. UE 5.8 shipped on June 17, 2026, at State of Unreal (Unreal Fest Chicago), and Epic expects it to be the last UE5 release, keeping 5.9 as an option. UE6 Early Access is targeted for "end of 2027 (ish)", with a stable release 12–18 months after that. Where I could not verify a claim against a primary source, it is marked **(unverified)**.

---

## 1. TL;DR

- **Reflection is the spine of Unreal.** One set of annotations (`UCLASS`/`UPROPERTY`/`UFUNCTION`, parsed by the Unreal Header Tool) feeds the Details panel, serialization, GC, Blueprints, replication, Python and, since 5.8, the tool schemas that AI agents see (`meta=(AICallable)`). This is the single most important idea to copy. Blender already has an equivalent (RNA / `bpy.props`) that we can generate into.
- **Prefab-style defaults (CDOs) and "save only what differs from the default"** make class defaults propagate to every instance that hasn't overridden them. Copy these semantics.
- **The Gameplay Framework (GameMode, Controller, Pawn, and so on) is powerful but opinionated** and shooter- and multiplayer-shaped. Epic is itself replacing Actors with an entity/component/prefab **Scene Graph** written in **Verse** for UE6. Don't copy the deep `AActor` inheritance model. Copy *Subsystems* (lifetime-scoped services) instead.
- **Blueprints are great for designers and glue code, but at scale they don't hold up**: they're binary, can't be merged, are hard to review and slow to interpret. Nativization was removed in UE 5.0, and UE6 will eventually deprecate Blueprints in favor of Verse (text). Hazelight ships on a text-scripting fork (AngelScript, 1.7M lines in *Split Fiction*). **Make gameplay logic text-first.**
- **The top pain points are iteration and collaboration**: C++ build and restart loops, shader-compile storms, binary `.uasset` merge conflicts, hashed One-File-Per-Actor filenames, and the fragile Blender FBX round trip. All of these are avoidable design choices.
- **Unreal's Blender story is weak and community-run.** "Send to Unreal" is now volunteer-maintained, there's no official Blender Datasmith exporter, and FBX has well-known scale, extra-root-bone and material problems. Owning both ends is our strategic advantage.
- **AI in Unreal, 2026.** 5.7 added an experimental assistant panel (Epic Developer Assistant). 5.8 added an **experimental MCP server** whose tools are generated from reflected "toolsets". It runs serially on the game thread with **no auth and no automatic undo**. A hands-on review found it good for small, well-defined tasks but slow (5–15 min per response) and weak at systems, and it sometimes silently works around things it can't do.
- **For our engine**, build agent assistance on the same reflection schema. Make every agent action an undoable, previewable transaction, scope it to a selection, give it read/propose/apply permission tiers, and make agents report what they couldn't do.

---

## 2. Body

### 2.1 Where Unreal is heading (context for everything below)

| Item | State (Oct 2026) | Source |
|---|---|---|
| UE 5.8 | Released 2026-06-17. Highlights: MegaLights production-ready, Lumen Lite (beta), Mesh Terrain (exp.), Mass overhaul, Iris production-ready for licensees, incremental cooking (beta), **MCP server plugin (exp.)**, USD-via-Interchange production-ready for asset import, uFBX-based FBX import (exp.) | Epic 5.8 release notes; GameFromScratch |
| UE6 | "UE5 plus UEFN equals UE6" (Sweeney). Verse becomes the gameplay language. The new **Scene Graph** (entities + components + prefabs, built on Verse) eventually takes the Actor system's role. Actors and Blueprints stay in early UE6 and are deprecated "when the new framework is sufficiently mature", with conversion tools promised. | GamesBeat, CG Channel, Ludus analysis |
| AI stance | LLMs and tools like Claude and Codex "playing a central role". Epic is "exposing a broad set of engine capabilities through the MCP protocol" and "bring your own favorite models". EDA continues as an "optional turnkey solution". | Shacknews (quoting Epic blog), CG Channel |
| Version control | **Lore** (formerly Unreal Revision Control, used in UEFN) open-sourced under MIT. Centralized, content-addressed, chunked large files, fetch-on-demand. | CG Channel, Linuxiac, github.com/EpicGames/lore |

The takeaway: Epic itself has concluded that deep Actor inheritance, binary visual scripting and C++-only iteration need replacing. The replacement it chose is text code, composition and prefabs, plus transactional semantics.

### 2.2 The UObject system

**Reflection (UHT).** `UObject` classes are annotated with `UCLASS`/`USTRUCT`/`UENUM`/`UPROPERTY`/`UFUNCTION` macros carrying specifiers and metadata (`EditAnywhere`, `BlueprintCallable`, `Replicated`, `meta=(ClampMin=0)`). Before compiling, the **Unreal Header Tool** parses headers and generates `*.generated.h/.cpp` glue that registers type metadata at startup. UHT became a C# mode of UnrealBuildTool in 5.1. Limitations: it sees only what's marked, it handles templates poorly, and it adds a build step with its own class of errors.

**What reflection drives.** This is the design lesson:

| Consumer | What it reads from reflection |
|---|---|
| Details panel / property editors | Property type, category, `EditAnywhere`/`VisibleAnywhere`, metadata (clamp, tooltips, edit conditions). Custom UI is added via `IDetailCustomization`/`IPropertyTypeCustomization` only where needed. |
| Serialization | Tagged property serialization: properties are saved and loaded by name. Added properties get defaults and removed ones are skipped, which is why assets survive schema changes. Transient properties and values equal to the default aren't written. |
| Garbage collection | `UPROPERTY` object references form the reference graph. Raw, unmarked pointers are invisible to GC. |
| Blueprints | `BlueprintCallable`/`BlueprintReadWrite`/`BlueprintImplementableEvent` expose C++ to graphs. |
| Networking | `Replicated`/`ReplicatedUsing` properties and `Server`/`Client`/`NetMulticast` RPCs. |
| Python | The `unreal` module is generated: "exposes nearly everything that is exposed from C++ to Blueprints". |
| AI agents (5.8) | `UFUNCTION(meta=(AICallable))` on `UToolsetDefinition` subclasses become MCP tools. Doc comments become the schema. |

**Garbage collection.** Mark-and-sweep over the `UPROPERTY` reference graph from a root set, with `TWeakObjectPtr` for non-owning references. Reachability ran within one frame and could hitch. UE 5.4 added experimental **incremental reachability**, which requires `TObjectPtr` write barriers, and the docs still recommend UObject budgets, pooling, or pausing GC in gameplay. Destroyed actors are freed on a later GC pass.

**Class Default Objects (CDOs).** Every `UClass` has a CDO, a constructor-built instance holding its defaults. Blueprint subclasses edit their CDO, and instances copy from it. Serialization stores *deltas from the archetype*. When you change a default, instances update **only if they still hold the old default**, because differing values are assumed intentional. This gives Unreal prefab-like behavior and small level files.

### 2.3 Actors and components, lifecycle, ticking

- **`AActor`** is anything placed or spawned in a level, and it is a container of **`UActorComponent`s** (logic only). **`USceneComponent`** adds a transform and attachment, and `UPrimitiveComponent` adds rendering and collision. An actor's transform is its root component's, so hierarchy lives in components. `AActor` is a heavy base class (networking, input, tick and damage all live on it), which is why Epic built Mass for large counts and Scene Graph for UE6.
- **Lifecycle** (Epic docs):
  - **Load from disk:** `PostLoad` → `PreInitializeComponents` → `InitializeComponent` → `PostInitializeComponents` → `BeginPlay`.
  - **PIE:** actors are *duplicated from the editor world* (`PostDuplicate`), then the same chain.
  - **SpawnActor:** `PostActorCreated` → `OnConstruction` (the construction script, which also runs in the editor whenever the actor is edited) → component init → `BeginPlay`.
  - **Deferred spawn:** set "Expose on Spawn" properties, then call `FinishSpawning`.

  `EndPlay(Reason)` covers destroy, PIE end, level travel, streaming out and quit.
- **Ticking** happens in **tick groups**: `TG_PrePhysics` → `TG_DuringPhysics` (parallel, for logic that tolerates one frame of lag) → `TG_PostPhysics` → `TG_PostUpdateWork`. Each group finishes before the next starts. Within a group, order work with `AddTickPrerequisiteActor/Component`. Tick can be every frame, interval-based or off, and Epic's advice is to disable tick unless it's needed. The split between editor-time `OnConstruction` and runtime `BeginPlay` is very useful for procedural placement.

### 2.4 Gameplay Framework

| Class | Lives where | Purpose |
|---|---|---|
| `UGameInstance` | Every process, not replicated, survives level loads | Session-wide state and owner of GameInstance subsystems |
| `AGameMode(Base)` | **Server only** | Rules, spawning, match flow; creates the other framework actors |
| `AGameState` | Replicated to all | Shared match state (scores, timers, PlayerArray) |
| `APlayerController` | Server + owning client | Input and "will" of a player; possesses a Pawn |
| `APlayerState` | Replicated to all | Per-player data that outlives a Pawn (name, score) |
| `APawn` / `ACharacter` | Replicated | Body in the world. `ACharacter` adds a capsule + `CharacterMovementComponent` (networked, predicted walking/jumping) |
| `AHUD`, camera manager | Owning client | Viewport overlays and view |
| **Subsystems** | Engine / Editor / GameInstance / World / LocalPlayer lifetimes | Auto-instanced singletons tied to an owner's lifetime, exposed to Blueprint and Python automatically |

**Why it exists.** Unreal grew out of networked shooters. The split encodes **authority** (GameMode is server-only), **what replicates to whom**, and **what survives respawn or level travel**. A multiplayer action game gets a networked, predicted character and match flow on day one.

**Pros:** a shared industry vocabulary, multiplayer-correct by construction, and the assumption behind most samples and marketplace content. **Cons:** it fits turn-based, simulation, UI-heavy or non-humanoid games poorly. Beginners routinely don't know where logic belongs (Controller or Pawn? GameMode or GameState?), and `ACharacter` brings a movement model you may not want. Epic's own answers all move away from the fixed hierarchy: **Subsystems**, **Game Features / Modular Gameplay** components injected at runtime (Lyra), and now **Scene Graph**.

### 2.5 Worlds and levels

- **`UWorld`** is the top-level runtime container (editor world, each PIE instance, the game). It owns a **persistent `ULevel`** plus streamed sub-levels, and each `ULevel` owns its actors. A map is a `.umap` package containing a `UWorld`.
- **Classic level streaming:** hand-authored sublevels loaded by volumes or Blueprint. Each sublevel is one binary file with one editor at a time.
- **World Partition** (UE5 default for big worlds): one persistent level on a **runtime grid**. Cells stream by distance from **streaming sources**, and the editor loads *regions* instead of the whole map. Companion systems: **HLODs** (generated distant proxies) and **Level Instances / Packed Level Actors** (prefab-like sub-assemblies).
- **One File Per Actor (OFPA):** each actor saves to its own file under `__ExternalActors__`, so "you do not need to check out the Level file… to make changes to the Actors". This sharply reduces lock contention. Costs: **encoded/hashed filenames** make source-control views opaque (Epic points users to the in-editor View Changelist). Tens of thousands of small binary files hurt Git LFS, files still can't be merged, and missing external-actor files break cooks. OFPA is editor-only: actors are re-embedded into levels when cooked.
- **Data Layers:** toggle sets of actors in the editor and at runtime (e.g. "night variant"). **External Data Layers** let plugins inject content.
- *Traversal stutter* from streaming is still a common complaint about shipped UE5 games. 5.8 adds Fast Geometry Streaming (exp.) and World Partition Insights.

### 2.6 Blueprints

**How they compile and run.** The Kismet compiler builds a skeleton class (so other Blueprints can reference the signatures), then properties and functions. Each node's handler emits intermediate statements, and the **VM backend** turns them into **bytecode** on the generated `UBlueprintGeneratedClass`'s `UFunction`s. The script interpreter runs this bytecode via `ProcessEvent`, calling into C++ through generated thunks. After a compile, live instances are **reinstanced**. Blueprint classes are real `UClass`es, so the standard pattern is a **C++ base class with Blueprint subclasses**: C++ exposes `BlueprintCallable` functions and `BlueprintImplementableEvent`/`NativeEvent` hooks, and designers wire up content and tune values.

**Nativization history.** UE4's Blueprint→C++ cook-time conversion was deprecated in 4.27 and **removed in UE 5.0**. Reported reasons: the output was hard to debug, many nodes couldn't be converted (all-or-nothing packaging failures), and it was slower than handwritten C++ (secondary sources; I found no official Epic rationale). Guidance now: profile, then move hot paths to C++.

| | Strengths | Weaknesses at scale |
|---|---|---|
| Authoring | Non-programmers can ship features. Discoverable APIs (context-sensitive search). Live debugging with breakpoints and watch values in PIE. Very fast for UI, level scripting, prototyping | "Spaghetti" graphs. Refactoring is manual. No text search or grep across logic. Hard to code-review (screenshots, in-editor diff viewer) |
| Collaboration | Each Blueprint can be locked and owned separately | Binary `.uasset`: **no line merge**. Conflicts mean picking one whole file. The built-in merge tool is more of a viewer. Teams rely on exclusive locks |
| Performance | Fine for event-driven glue | Interpreter overhead per node. Often quoted as roughly 10x slower than C++ for heavy loops **(unverified, anecdotal; depends on workload)**. Casting a hard-referenced Blueprint pulls its whole asset graph into memory |
| Dependencies | Easy to reference anything | Hard references in graphs create huge load chains (see 2.8) |

Industry evidence of the limits: Hazelight's open-source **AngelScript** integration for UE5 (in use since 2018) is used daily by 30+ developers. *Split Fiction* has 1.7M+ lines in 16k+ script files, and other games use it too (*THE FINALS*, *ARC Raiders*, *Talos Principle 2*). Epic's UE6 move to Verse is the official version of the same conclusion: gameplay logic belongs in a text language that is safe, fast to iterate on and easy to diff.

### 2.7 C++ modules, plugins, build times, Live Coding

- **Code layout.** Code lives in **modules** (`*.Build.cs` with dependencies, a type such as Runtime/Editor/Developer, and a loading phase), grouped into **plugins** (`.uplugin`, which can also carry content). **UnrealBuildTool** (C#) drives UHT and the compiler with unity builds. Editor builds load modules as DLLs. Licensees get full engine source on GitHub, which developers often praise.
- **Live Coding** (Live++, Ctrl+Alt+F11) patches binaries while the editor or PIE is running. It is solid for function bodies, and **Object Reinstancing** (on by default) handles many reflected-type changes. It doesn't work on console or mobile. **New `UFUNCTION` declarations don't propagate** (the 5.8 MCP docs require a restart), and header and layout changes are the classic "restart the editor" trigger. Legacy **Hot Reload** remains as a fallback, though it has a long community reputation for corrupting Blueprints.
- **Build times** are a perennial complaint: 5+ minutes after a header touch in small projects, and roughly 20 s minimum for a 1-file change in a 200-file project. Studios respond with forward declarations, module splits, distributed builds, and keeping designers in Blueprint "to avoid recompiling". **Iteration time is the #1 thing to beat.**

### 2.8 Assets

- **Packages.** Each `.uasset`/`.umap` is a *package*: a binary container of serialized UObjects with **import and export tables**. Imports are references to objects in other packages.
- **Hard vs soft references:**

| | Hard (`UObject*`, `TObjectPtr`, class refs, Blueprint casts) | Soft (`TSoftObjectPtr`, `FSoftObjectPath`, `TSoftClassPtr`) |
|---|---|---|
| Load behavior | The referenced package loads **automatically and transitively** with the referencer | A path only; you load explicitly (sync or async via `FStreamableManager`) |
| Cook | Always included | Included if discovered through rules, Primary Assets or the Asset Manager |
| Typical failure | "Opening the main menu loads every weapon mesh": memory and load-time blowups from transitive chains | Forgetting to load, or the asset not being cooked |

- **Asset Registry** indexes every package's class, tags (`AssetRegistrySearchable` properties) and dependencies **without loading the assets**. It powers the Content Browser, the Reference Viewer and dependency audits, and it is exactly the kind of index an AI agent should query.
- **Asset Manager** handles **Primary Assets** (IDs, bundles, async loading, chunk assignment). Secondary assets load via references.
- **Data:** `UDataAsset`/`UPrimaryDataAsset` (instances of a reflected class edited in the Details panel), `UDataTable` (rows of a `USTRUCT`, importable from CSV/JSON), CurveTables, and Data Registries.
- **Cooking** turns editor assets into platform runtime data: it strips editor-only data, compiles target shaders, and packages into IoStore (`.utoc/.ucas`) or `.pak`. Derived data is cached in the DDC and Zen. 5.8 adds **incremental cooking (beta)**. Cook times are a top complaint, and Epic's UE6 post promises "Cook times will come down".
- **Import (Interchange)** replaced the legacy FBX importer in 5.4+. It is "file format agnostic, asynchronous, customizable, and can be used at runtime". The architecture is **translators** (file → neutral node graph), then **pipelines** (stackable, scriptable in Blueprint or Python), then **factories** (create assets), with reimport from stored source data. In 5.8, USD import is production-ready for assets and an **FBX path on the open-source uFBX library is experimental**. My inference is that Epic is moving off the Autodesk FBX SDK.

### 2.9 Editor architecture

- **Slate** is Unreal's custom C++ UI framework (declarative syntax, retained widget tree), and the whole editor is built on it. **UMG** (runtime UI, Widget Blueprints) wraps Slate. Slate is powerful but verbose and C++-only. Editor Utility Widgets let you build tool UI in UMG/Blueprint instead.
- **Details panel:** generated from reflection and customizable per type. Most tools need **no UI code**.
- **Editor modes:** `UEdMode` plus the **Interactive Tools Framework** (property sets, gizmos, undoable transactions), used by the Modeling, Landscape and Foliage modes. Scriptable Tools expose these to Blueprint.
- **Undo:** edits are wrapped in `FScopedTransaction`, and objects call `Modify()` to snapshot state. Some operations, such as imports, can't be undone even inside a Python `ScopedEditorTransaction`.
- **PIE:** the editor world is **duplicated** into a new `UWorld` in transient `UEDPIE_<n>_` packages (`DuplicateWorldForPIE` plus a fixup serializer that remaps references). Several instances (server plus clients) can run in one process or as separate processes. **Simulate** runs without a possessed player, and "Keep Simulation Changes" copies edits back. Duplication is fast because nothing reloads from disk, but it has a long tail of edge-case bugs (references not fixed up, component re-registration).
- **Python editor scripting:** editor-only (not PIE or runtime), generated from reflection (docs cite embedded Python 3.11.8). Supports `init_unreal.py` startup scripts, headless commandlets, and **remote execution**, which Send2UE uses. Python is the pipeline-TD automation layer and, in 5.8, the default way to author MCP toolsets.

### 2.10 Brief tour of other systems

| System | Essence |
|---|---|
| **RDG** | Render passes are declared each frame into a graph with their resource reads and writes. RDG culls unused passes, computes barriers and aliases transient memory. A frame-graph design worth copying. |
| **Nanite** | Virtualized micro-polygon geometry with GPU-driven culling. Removes most manual LOD work. Extended to foliage and Mesh Terrain (exp.) in 5.7–5.8. |
| **Lumen / MegaLights** | Dynamic GI and reflections. 5.8 adds **Lumen Lite** (beta, roughly 2× faster). MegaLights became production-ready in 5.8. |
| **Shader/PSO** | Permutation explosion: "compiling 8000+ shaders" on first open and runtime hitches. PSO precaching (5.2+) "prevents most kinds" of runtime shader stutter but needs a project strategy. |
| **Chaos** | Replaced PhysX in UE5: rigid bodies, destruction, cloth, hair, flesh. Mover/ChaosMover is a newer networked movement framework. |
| **Enhanced Input** | Input Actions + stackable Mapping Contexts + Modifiers + Triggers. Unified with Common UI in 5.8. |
| **Replication** | Server-authoritative properties plus RPCs, relevancy and prediction. The new **Iris** system is production-ready for licensees in 5.8. |
| **GAS** | Abilities, Effects, Attributes and Gameplay Tags, with networked prediction. Powerful but famously complex. Gameplay Tags alone are a cheap, broadly useful idea. |
| **Verse / Scene Graph** | Functional-logic language with *transactional* semantics (failed operations roll back). In UEFN since 2023. Scene Graph = hierarchical entities, components (one per type) and prefabs with per-instance overrides. |
| **Mass** | Archetype ECS for crowds and traffic, overhauled in 5.8. It sits *beside* Actors, not instead of them. |
| **PCG, StateTree** | PCG: node-graph procedural placement (production-ready in 5.7). StateTree: general hierarchical state machine. |

### 2.11 Unreal ↔ Blender integration

| Path | Status (2026) | Notes |
|---|---|---|
| **Send to Unreal (Send2UE)** | Originally Epic's, now **community-maintained** ("not affiliated with Epic Games"). The Extensions listing shows v2.4.9 (Aug 2024), tested on Blender 3.6/4.2 and UE 5.3/5.4, and is back in draft after review comments. | Bundles Unreal's `remote_execution.py` plus an RPC module to drive the editor's Python, importing FBX with naming conventions for LODs, collision and sockets. One-way, asset-level. |
| **FBX** | The de facto standard. Blender still uses the Python exporter (`io_scene_fbx` 5.15.0 in 5.2.2, checked locally). UE uses Interchange, with uFBX experimental in 5.8. | Pain: **m vs cm (100× scale)**, armature object becomes an **extra root bone**, leaf-bone junk, non-uniform or zero bone-scale errors, `Apply Transform` breaking skinning, only Principled BSDF plus directly linked textures survive, case-sensitive `UCX_` collision naming. Interchange in 5.4/5.5 recomputed normals by default. |
| **glTF 2.0** | Blender's exporter is first-class (`io_scene_gltf2` 5.2.40 locally, including `export_extras` for custom properties and `export_armature_object_remove`). UE imports via Interchange. | Better PBR transfer for static props. Many still prefer FBX for complex rigs **(unverified as a rule)**. |
| **USD** | UE has USD Stage (live, non-destructive) and Interchange USD import (production-ready for assets in 5.8). Blender 5.2.2's exporter (checked locally) handles armatures, shape keys, UsdPreviewSurface/MaterialX, custom properties, and unit and axis conversion. | The most promising scene-level format, but fidelity needs per-project testing. Epic stressed USD and open standards at State of Unreal 2026. |
| **Datasmith** | **No official Blender exporter.** A community exporter (0xafbf) is listed as deprecated. | Datasmith targets CAD and archviz DCCs (Max, Revit, etc.). |

**The round-trip problem.** Every path is **one-way and asset-granular**. Layout, lighting and gameplay authored in Unreal never flows back to Blender. Blender's procedural data (Geometry Nodes, modifiers, procedural materials) must be **baked** before transfer. Reimport overwrites, so UE-side material tweaks need discipline (Material Instances only). Two unit systems, two material models and two undo histories leave every artist with a mental checklist, which paid add-ons and vendor MCP servers exist to paper over.

### 2.12 Epic's AI tooling and the ecosystem (2026)

| Tool | What it is | Status / reception |
|---|---|---|
| **Epic Developer Assistant (EDA)** / AI Assistant panel | Editor chat panel (5.7+, UE and UEFN): answers, guidance, C++/Verse code generation. A vendor review says it uses an Epic-hosted model with a BYO-key option **(unverified)**. | Experimental, advisory only (it doesn't act on the editor). July 2026 forum feedback complains about refusals and knowledge stuck at UE 5.6. Epic will keep it as an "optional turnkey solution". |
| **Unreal MCP** (`ModelContextProtocol` plugin, 5.8) | MCP server **inside the editor process** over HTTP/SSE at `127.0.0.1:8000/mcp`. `GenerateClientConfig` writes configs for Claude Code, Cursor, VS Code, Gemini and Codex. An optional Terminal plugin runs an agent CLI inside the editor. | **Experimental.** "No authentication layer", loopback only, telemetry on by default. Calls run **serially on the game thread**. Tools only (no Resources or Prompts). **No automatic undo transactions** (per a community guide). |
| **Toolset Registry** | Tools are reflected classes: Python `unreal.ToolsetDefinition` + `@toolset_registry.tool_call`, or C++ `UFUNCTION(meta=(AICallable))`. Type hints and docstrings become the schema. Bundled toolsets cover scene, actors, material instances, objects, Sequencer and animation. **Tool search** exposes just `list_toolsets`/`describe_toolset`/`call_tool`. | A good architecture that reuses reflection. But allowlisting `call_tool` is coarse, with no per-operation permissions. |
| **Community** | chongdashu/unreal-mcp (MIT, roughly 70 tools), ChiR24 (Remote Control API), **VibeUE** (MIT, extends Epic's MCP), Autonomix (MIT). Commercial: Aura, Ludus AI, NeoStack, Nwiro, StraySpark. | Fragmented, of varying quality, and mostly reviewed by vendors. |

**Hands-on reception** (Puget Systems, 2026-07-09, Claude driving UE 5.8 MCP):
- A greybox level (about 5 min) and room-light Blueprint logic both worked, with unrequested extras.
- Player controls ended **broken** after 25 min of attempts.
- A "PCG city" came back as hand-placed meshes: the agent **couldn't build PCG graphs and silently worked around it**.
- Responses took 5–15 min, token use was heavy, and hands-off use reduced the author's understanding of the result.
- Verdict: "I would reach for MCP to accelerate iteration, not to fill a knowledge gap."

Community safety advice: use a disposable branch, read before writing, make one write at a time, never "Save All", and re-read state after a timeout.

### 2.13 What developers love and hate

| Love | Hate |
|---|---|
| Rendering quality out of the box (Nanite, Lumen, MetaHuman) | Iteration: C++ compile and link times, header changes forcing editor restarts, Live Coding limits |
| Full engine source; can fix or extend anything | Shader-compile storms (thousands of shaders on first open, modal "preparing shaders"), PSO and traversal stutter in shipped games |
| Battle-tested networking, Gameplay Framework, GAS | Binary assets: Blueprint merge conflicts, locks, OFPA hashed files, heavy Git LFS |
| Blueprints let designers and artists build gameplay. Excellent visual debugging | Blueprint spaghetti at scale; hard to review, refactor or search |
| Reflection-driven editor: add a property, get UI, serialization, undo and scripting | Gameplay Framework friction for non-shooter games; heavy `AActor` |
| Content ecosystem (Fab), samples (Lyra, City Sample) | Editor weight: huge install, RAM, cook times, crashes on large projects |
| Python automation and Interchange pipelines | Blender/DCC round trip is fragile and one-way. Docs lag the features (repeated in the MCP review) |

---

## 3. Implications for our Blender-based engine

These are opinionated. The guiding principle: Unreal is now spending years (UE6) undoing its early choices around binary assets, Blueprint-only logic, Actor inheritance and C++ iteration. We start clean, so we should design for **text, composition, fast reload and agent-safety** from day one.

### 3.1 Copy

1. **One schema, many consumers (reflection as the spine).** Define every component, property and command once, in a declarative schema: Rust derive macros, or a schema file plus codegen. From that single source generate:
   - Blender UI as generated `bpy.types.PropertyGroup`s and panels. Blender's RNA *is* a reflection system with undo, animation, library-override and Python support built in, so treat it as our "Details panel" and don't build UI by hand.
   - Serialization, both the editor-side format and runtime cooked data.
   - Runtime script bindings.
   - Network replication flags (later).
   - **Agent tool schemas** (the UE 5.8 `AICallable`/toolset pattern).

   Avoid UHT's downside, a bespoke header-parsing step. Use language-native metadata.
2. **CDO/prefab delta semantics.** Store instance data as *overrides against a prefab or class default*. Changing a default updates all instances that didn't override it. In Blender, map prefabs to **linked collections / collection instances + library overrides**, and keep component overrides sparse.
3. **Composition over inheritance: entity + components + prefabs.** This is where Epic is going (Scene Graph). Map a Blender Object to an entity, attached component PropertyGroups to components, and a linked Collection asset to a prefab. Use a separate data-oriented path (Mass-like ECS) only for large counts.
4. **Explicit lifecycle and tick ordering.** Document one lifecycle (`on_editor_construct` → `on_load`/`on_spawn` → `begin_play` → `tick` → `end_play`) with the editor-time "construction script" hook that UE gets so much value from. Use named tick phases (pre-physics / physics / post-physics / late), explicit prerequisites, and default to no tick.
5. **Subsystems instead of a mandatory Gameplay Framework.** Offer lifetime-scoped services (engine / editor / session / world / local-player). Ship the GameMode/Controller/Pawn-style split as an optional *starter template*, not as core types.
6. **PIE-like play, but out of process.** UE duplicates the editor world in-process. We should **launch the runtime as a separate process** from Blender with a scene snapshot, supporting "play from here" and multi-instance (server + clients). This keeps Blender up when the game crashes and avoids PIE's duplication edge cases. Play-time changes are discarded unless explicitly applied as a diff, like UE's Keep Simulation Changes.
7. **Soft references, an asset registry, and a cook step.** Use stable IDs or paths with an explicit load policy, and keep a **registry index** (type, tags, dependencies) that can be queried without opening `.blend` files. Agents need this index too. Cook is an incremental, **content-hash-cached** export to runtime format.
8. **An Interchange-style pipeline with validators.** Blender → *our IR* → runtime assets, through scriptable pipeline steps. Since we own both ends, **skip FBX**: read via `bpy`, or use glTF (`export_extras` carries custom properties), with USD optional for scene composition. Turn the FBX pain list into **pre-flight validators** (unapplied scale, bone scale, extra roots, unsupported material nodes, missing collision).
9. **Pick Blender's conventions.** Meters, Z-up, Blender's axis conventions inside the runtime. Every unit or axis conversion at a boundary is a bug factory, as UE's 100× scale issue shows.
10. **Data-driven input (Enhanced Input model)** and **Gameplay Tags** (hierarchical tags) are cheap, high-value ideas.
11. **A render-graph architecture (RDG-style)** for whatever renderer we build or adopt.

### 3.2 Avoid

1. **Binary, unmergeable gameplay logic.** Gameplay code should be **text** in a hot-reloadable language. Any visual scripting should be serialized as readable text and limited to designer-facing state machines and data flow. Text is diffable, mergeable, reviewable, greppable and, crucially, **what LLM agents are good at**. Hazelight's AngelScript and Epic's own UE6 plan point the same way.
2. **Compile-restart iteration loops.** Keep the engine core precompiled and stable. Put gameplay in a layer that reloads in under a second without restarting (Lua/Luau, C# hot reload, or WASM modules are all candidates for the scripting study). Never require restarting Blender to see a schema change. Generate PropertyGroups and re-register them live.
3. **Monolithic binary level files *and* hashed per-actor files.** `.blend` files are binary and monolithic, which is the same problem UE had before OFPA. Plan for layered storage: keep levels as compositions of linked `.blend` files (per area or collection) **plus a human-readable text sidecar** (or USD-ASCII) that holds entity and component data with readable names. Avoid opaque hashed filenames.
4. **Hard-reference load explosions.** Make the default reference type soft, or at least visible in the editor (show the load cost of a reference).
5. **Shader permutation explosions.** Keep material and shader variants explicit and bounded. Compile async, never block the editor modally, and precache PSOs from the cook.
6. **A shooter-shaped core.** No mandatory Character/Controller/GameMode in the core.
7. **"Agent as autopilot."** The UE MCP hands-on shows the failure modes: silent workarounds, broken systems after long autonomous runs, users losing understanding of their own project.

### 3.3 AI agents: copy Unreal's plumbing, fix its gaps

| Unreal 5.8 MCP | Our design |
|---|---|
| Tools derived from reflected classes and docstrings | Same: tools generated from our schema plus explicit, small, typed commands |
| Tool search meta-tools to keep context small | Same: `list_toolsets` / `describe` / `call` plus a queryable asset registry and scene index |
| No auth, loopback only | Local-only **and** a per-session token. Never exposed beyond localhost |
| No automatic undo | **Every agent call is one undoable transaction** (a Blender undo step via operators) with a structured diff of what changed |
| Coarse `call_tool` allowlist | Permission tiers: **read → propose (dry-run diff) → apply**, scoped to the selection or collection the user granted |
| Agent can silently work around limits | Tools return explicit `unsupported`/`partial` results, and the agent must surface them. Log every action |
| Serial on game thread, 60 s timeouts, 5–15 min responses | Blender data must also be mutated on the main thread, so queue calls through timers or modal operators. Do heavy compute in worker processes, keep tools small, and stream progress |
| EDA trained on old docs, refusals | Ground the assistant in *our* versioned docs and the project's own index. BYO model (Epic also landed on "bring your own models") |

The rule that keeps the developer in charge: **agents propose, humans apply**. The default is a reviewable diff, one step at a time, and the agent explains what it did.

### 3.4 Strategic note

Unreal's weakest seam (the Blender round trip) is our core feature, and its slowest loop (C++ build → editor restart → PIE) is where we can win outright. The things Unreal does uniquely well are photoreal rendering at scale (Nanite, Lumen), battle-tested networking, GAS and the content ecosystem. Those are very expensive to replicate, so we should **not** compete on them in v1.

---

## 4. Sources

**Epic official**
- UE 5.8 release notes: https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5-8-release-notes
- Unreal MCP in Unreal Editor: https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-mcp-in-unreal-editor
- Reflection system: https://dev.epicgames.com/documentation/en-us/unreal-engine/reflection-system-in-unreal-engine
- UObject handling: https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-object-handling-in-unreal-engine
- Unreal Header Tool: https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-header-tool-for-unreal-engine
- Incremental garbage collection: https://dev.epicgames.com/documentation/unreal-engine/incremental-garbage-collection-in-unreal-engine
- Object pointers (TObjectPtr): https://dev.epicgames.com/documentation/unreal-engine/object-pointers-in-unreal-engine
- Actor lifecycle: https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-actor-lifecycle
- Actor ticking: https://dev.epicgames.com/documentation/en-us/unreal-engine/actor-ticking-in-unreal-engine
- Gameplay framework: https://dev.epicgames.com/documentation/en-us/unreal-engine/gameplay-framework-in-unreal-engine
- Subsystems: https://dev.epicgames.com/documentation/en-us/unreal-engine/programming-subsystems-in-unreal-engine
- World Partition: https://dev.epicgames.com/documentation/en-us/unreal-engine/world-partition-in-unreal-engine
- One File Per Actor: https://dev.epicgames.com/documentation/en-us/unreal-engine/one-file-per-actor-in-unreal-engine
- Live Coding: https://dev.epicgames.com/documentation/en-us/unreal-engine/using-live-coding-to-recompile-unreal-engine-applications-at-runtime
- Asset management: https://dev.epicgames.com/documentation/en-us/unreal-engine/asset-management-in-unreal-engine
- Interchange framework: https://dev.epicgames.com/documentation/en-us/unreal-engine/interchange-framework-in-unreal-engine
- Python editor scripting: https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python
- Datasmith supported software: https://dev.epicgames.com/documentation/en-us/unreal-engine/datasmith-supported-software-and-file-types
- UWorld::GetDuplicatedWorldForPIE (4.27 API): https://docs.unrealengine.com/4.27/en-US/API/Runtime/Engine/Engine/UWorld/GetDuplicatedWorldForPIE/index.html
- PIE issues: https://issues.unrealengine.com/issue/UE-20108 , https://issues.unrealengine.com/issue/UE-181665
- UHT C# regression note: https://issues.unrealengine.com/issue/UE-172921
- Scene Graph (UEFN): https://create.fortnite.com/news/scene-graph-is-now-available-as-an-experimental-feature-in-uefn , https://dev.epicgames.com/documentation/uefn/getting-started-in-scene-graph-in-fortnite
- Road to UE6 (403 to fetcher; content via coverage below): https://www.unrealengine.com/news/the-road-to-ue-6
- EDA feedback thread: https://forums.unrealengine.com/t/epic-developer-assistant-feedback/2735725
- Lore: https://github.com/EpicGames/lore

**Coverage and analysis**
- GameFromScratch UE 5.8: https://gamefromscratch.com/unreal-engine-5-8-released/
- GameFromScratch UE 5.7: https://gamefromscratch.com/unreal-engine-5-7-released/
- Digital Production on 5.7 AI Assistant: https://digitalproduction.com/2025/11/12/unreal-engine-5-7-foliage-pcg-and-in-editor-ai/
- State of Unreal 2026 takeaways: https://knowledge.edge-online.com/p/ai-goes-native-in-unreal-engine-the-five-main-takeaways-from-state-of-unreal-2026
- GamesBeat on UE6: https://gamesbeat.com/unreal-engine-6-will-combine-ue5-and-uefn-into-a-unified-engine-state-of-unreal/
- CG Channel on UE6 / 5.8 / Lore: https://www.cgchannel.com/?p=176658
- Ludus analysis (Verse vs Blueprints): https://landing.ludusengine.com/blog/will-verse-replace-blueprints
- Shacknews (Epic AI statements): https://www.shacknews.com/article/149693/unreal-engine-6-generative-ai-models-claude-codex
- Puget Systems MCP hands-on: https://www.pugetsystems.com/blog/2026/07/09/unreal-engine-mcp-hands-on-testing-ai-inside-the-editor/
- Unreal MCP from Codex (community guide): https://dev.to/gamedevtoollab/using-unreal-mcp-in-ue-58-from-codex-setup-toolsets-and-safe-editor-automation-4pon
- AI tools survey (vendor): https://www.seeles.ai/resources/blogs/best-ai-assistants-and-mcp-tools-for-unreal-engine
- StraySpark MCP guide (vendor): https://www.strayspark.studio/blog/mcp-server-game-development-complete-guide-2026
- VibeUE: https://github.com/chillyflow/VibeUE
- Linuxiac on Lore: https://linuxiac.com/epic-games-open-sources-lore-version-control-system/
- Hazelight AngelScript: https://angelscript.hazelight.se/ , https://angelscript.hazelight.se/project/development-status
- GamingOnLinux on Epic's stutter post: https://www.gamingonlinux.com/2025/02/epic-games-devs-talk-about-solutions-to-the-unreal-engine-stuttering-issues-in-games/page=1/
- Shader-compile UI freeze thread: https://forums.unrealengine.com/t/editor-ui-unresponsive-to-input-during-certain-shader-compile-situations/559022
- Blueprint merge conflicts (vendor): https://www.diversion.dev/knowledge-center-articles/how-to-solve-blueprint-merge-conflicts-in-unreal-engine
- OFPA source control (vendor): https://usourcecontrol.com/guides/one-file-per-actor-source-control
- Build-time complaints: https://answers.unrealengine.com/t/c-build-times/350016
- Blueprint nativization (secondary): https://www.strayspark.studio/blog/blueprint-nativization-ue57-when-convert-blueprints-cpp

**Blender ↔ Unreal**
- Send2UE extension listing: https://extensions.blender.org/approval-queue/send2ue
- Send2UE community fork: https://github.com/poly-hammer/BlenderTools (dependencies folder: https://github.com/poly-hammer/BlenderTools/tree/main/src/addons/send2ue/dependencies)
- Send2UE docs: https://poly-hammer.github.io/BlenderTools/send2ue/
- Epic repo migration thread: https://forums.unrealengine.com/t/migrating-optional-epic-games-git-repositories-to-new-github-organization/1718666
- Blender→UE5 export pipeline (Feb 2026): https://sarahhyperdense.substack.com/p/blender-to-ue5-the-complete-export
- Blender 5 → UE pipeline (vendor, Mar 2026): https://www.strayspark.studio/blog/blender-5-unreal-engine-asset-pipeline-2026
- Extra root bone fix: https://bugnet.io/blog/how-to-fix-unreal-skeletal-mesh-extra-root-bone-from-fbx-armature
- Datasmith for Blender (community, deprecated): https://0xafbf.gumroad.com/l/blender-datasmith-content
- Blender USD manual (4.0): https://docs.blender.org/manual/nb/4.0/files/import_export/usd.html
- Local check: Blender 5.2.2 LTS operator introspection (`wm.usd_export`, `export_scene.fbx`, `export_scene.gltf`; add-on versions io_scene_fbx 5.15.0, io_scene_gltf2 5.2.40), run 2026-10-09.
