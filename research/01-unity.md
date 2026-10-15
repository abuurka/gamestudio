# How Unity Works (Unity 6.x, as of October 2026)

Research input for a game engine that uses Blender as its editor. Written 2026-10-09. Focus: what affects our design decisions.

| Release | Status / date | Relevant change |
|---|---|---|
| 6.0 LTS | Support ends Oct 2026 | Minimum for Unity AI, CLI, MCP |
| 6.3 LTS | Dec 2025, supported to Dec 2027 | Current production LTS; unified Render Graph; Box2D v3 |
| 6.4 | H1 2026 | ECS packages become core packages; `EntityId` replaces `InstanceID` |
| 6.5 | ~Jun 2026 | Built-in Render Pipeline deprecated |
| 6.6 | ~Aug/Sep 2026 | Domain reload on Play **off by default**; native `Dictionary` serialization; Burst built in |
| 6.7 LTS | Alpha since Jul 2026, due end of 2026 | Last Mono release |
| 7.0 | First alpha Oct 5, 2026 | First CoreCLR editor; "domain reloads are gone" (earlier docs called this "6.8") |

---

## 1. TL;DR

- **One serializer drives everything.** The Inspector, undo, prefab overrides, multi-object editing and the on-disk YAML all come from reflecting over public/`[SerializeField]` fields. That makes tooling cheap to build and the engine hard to change.
- **Identity = asset GUID (in a `.meta` sidecar) + 64-bit local fileID.** A cross-file reference is `{fileID, guid, type}`. Lose or regenerate a `.meta` and every reference to that asset breaks silently. Copy the scheme; fix the fragility.
- **Prefab instances are sparse diffs:** `(target, propertyPath, value)` overrides plus added/removed components and GameObjects. Nesting and variants reuse the same mechanism. The model is good; the merge tooling is weak.
- **Asset pipeline:** source files + `.meta` settings → deterministic importers → cached artifacts in `Library/` (never committed), optionally shared via a cache server.
- **.blend import is a black box.** Unity starts the user's Blender in the background, exports FBX with a bundled script, then imports the FBX. Blender must be installed on every machine, custom data is lost, and Unity's own docs say not to use it in production. **We should do the opposite.**
- **DOTS Baking is the closest Unity analogue to our architecture:** editable authoring objects → stateless, dependency-tracked bakers → binary runtime scenes. It is one-way, editor-only, incremental while editing and a full background bake otherwise. Copy it.
- **Unity's biggest self-inflicted cost is two runtimes (GameObjects and Entities),** which it has spent 2024–2026 unifying. We should have one runtime model from day one.
- **Iteration time is the most persistent complaint.** Mono domain reload takes seconds to minutes per code change. Unity 7.0 replaces it with AssemblyLoadContext reloads, but only targets *parity* with today's speed.
- **Play mode runs in the editor process** and reverts by reloading scenes. A separate runtime process, which our design implies anyway, gives clean reversion and crash isolation for free.
- **AI (2026):** an Assistant with Ask, Plan and Agent modes, permission tiers and checkpoints; Generators for assets; a free CLI, eval and MCP server for external agents. Reception is mixed (paywall, agent can't play-test). Unity's own lesson: build "an assistant that can *verify*," not one that only suggests.

---

## 2. Core object model

**GameObject / Component / Transform.**
- A GameObject is a container (name, tag, layer, active flag) with exactly one Transform. **The hierarchy *is* the Transform parent chain**, so organizing objects and spatially parenting them are the same operation. Blender separates these (collections vs. parenting).
- Components are native C++ types with managed wrappers, or user `MonoBehaviour` scripts. Behaviour lives in components that receive engine callbacks; there are no "systems" in the classic model.
- A destroyed object's managed shell compares `== null` without actually being null ("fake null"), a well-known source of bugs.

**MonoBehaviour lifecycle.**

| Phase | Callbacks (in order) |
|---|---|
| Load | `Awake` (once; runs even if the component is disabled, as long as the GameObject is active) → `OnEnable` → `sceneLoaded` → `Start` (once, before the first `Update`, only if enabled) |
| Fixed step (0..n per frame) | `FixedUpdate` → physics step → `OnTrigger*`/`OnCollision*` → `WaitForFixedUpdate` coroutines |
| Frame | Input events → `Update` → coroutines and `Awaitable`/async continuations → animation → `LateUpdate` (typically cameras) |
| Render / end | `OnGUI`, `OnDrawGizmos`, `WaitForEndOfFrame` |
| Teardown | `OnDisable` → `OnDestroy` |

Ordering rules:
- There is no guaranteed order across GameObjects for the same callback.
- The order between script *types* can be set (Script Execution Order settings or `[DefaultExecutionOrder]`); the order among instances of one script cannot.
- The frame is a PlayerLoop you can customize from C#.
- Developers work around undefined ordering with manager singletons. That puts state in statics, which feeds the domain-reload problem in §7.

**ScriptableObject.** A serializable object stored as a `.asset` file: data with no Transform and no scene. Used for config, item databases and event channels. Many objects can reference one asset, so data is shared instead of copied. Gotcha: changes made in play mode **persist in the editor** but not in builds. Developers love it as "designer-editable data with references," and it is Unity's closest thing to data-oriented authoring.

---

## 3. Scenes and prefabs

- A **scene** is a `.unity` YAML file. Scenes can be loaded additively.
- A **prefab** is a `.prefab` asset. **Nested prefabs** (since 2018.3) keep their links to their own sources. **Variants** inherit from a base, which can itself be a variant: "Overrides in the variant take precedence over the base values."
- **Imported models become read-only "Model Prefabs."** You customize them through variants or overrides. When the source file changes, values you didn't override update and values you did override stick. Every Blender asset in our engine would behave this way.
- **Override types:** property values, added/removed components, added/removed child GameObjects. Root position and rotation are deliberately not counted as overrides. Overrides show as a blue bar in the Inspector and can be applied to or reverted from a chosen nesting level.

**Storage.** A prefab instance in a scene is not a copy. It is one `PrefabInstance` document (class ID 1001) holding a diff:

```yaml
--- !u!1001 &11866470
PrefabInstance:
  m_Modification:
    m_TransformParent: {fileID: 1122852772}
    m_Modifications:
    - target: {fileID: 599465214307944399, guid: 95cf80858ee0c4940b72f62b17aefd41, type: 3}
      propertyPath: m_LocalPosition.x
      value: 39.341
      objectReference: {fileID: 0}
    m_RemovedComponents: []
    m_RemovedGameObjects: []
    m_AddedGameObjects: []
    m_AddedComponents: []
  m_SourcePrefab: {fileID: 100100000, guid: 95cf80858ee0c4940b72f62b17aefd41, type: 3}
--- !u!4 &11866471 stripped
Transform:
  m_CorrespondingSourceObject: {fileID: 599465214307944399, guid: 95cf..., type: 3}
  m_PrefabInstance: {fileID: 11866470}
```

- `target` addresses an object *inside the source prefab* by `(guid, fileID)`.
- `propertyPath` is the same string the editor's `SerializedProperty` uses, so one addressing scheme serves the Inspector, undo and overrides.
- Values are stored as strings.
- **Stripped** objects are placeholders, so other objects in the scene can reference something inside the instance.
- A variant's root is itself a parentless `PrefabInstance`.
- Community-observed, not documented: objects inside nested instances get fileIDs derived by XORing the instance's fileID with the source object's fileID. This keeps them unique when the same prefab is nested more than once.

**Pain points.**
- UnityYAMLMerge has tracker reports of silently letting the last write win on prefab instances, and of keeping both values on variants (marked "Won't Fix").
- Teams lose track of which nesting level an override lives on.
- Children inside an instance can't be restructured without unpacking it, which breaks the prefab link.

---

## 4. Serialization

**Rules.**
- The native serializer is field-based and by-value. It writes public or `[SerializeField]` fields that are not static, const or readonly; properties are never serialized.
- Supported types: primitives, enums, Unity built-ins, `[Serializable]` classes/structs, `UnityEngine.Object` references (stored as references), and arrays/`List<T>` of these.
- Custom classes are inlined, so there are **no nulls, no shared references, no cycles and no polymorphism** (a derived type's extra fields are dropped).
- No nested containers or dictionaries until **Unity 6.6 added native `Dictionary` serialization**, including per-entry prefab overrides.

**`[SerializeReference]`** adds polymorphism, null, shared references and cycles, but only *within one host object*.
- Instances are written to a `references` section with an ID, the fully qualified type name and field values.
- Because the type name is stored, renaming a class needs `[MovedFrom]`. A missing type deserializes as null, but its data is kept.

**YAML format.**
- Each object is its own document with a `--- !u!<classID> &<fileID>` header.
- A same-file reference is `{fileID: N}`. A cross-file reference is `{fileID, guid, type}`: `type: 3` points to an imported artifact in `Library/`, `type: 2` to a serialized asset file.
- Scripts are referenced by **the `.cs` file's GUID** (`m_Script: {fileID: 11500000, guid: …}`), not by class name. Losing a `.meta` therefore produces "Missing Script" everywhere.
- UnityYAML is a custom subset of YAML (no comments, no tags), and Unity says: "Avoid generating or editing Unity YAML files directly."

**How references survive.**
- Every asset and folder gets a `.meta` with a GUID and import settings. The GUID survives moves and renames done in the editor or with the `.meta` moved alongside.
- Importers must give sub-assets (e.g., meshes inside an FBX) stable identifier strings across imports.
- `GlobalObjectId` provides project-wide stable IDs in editor code.

**Serialization also drives code reload.** Domain reload serializes every live object, unloads managed code, reloads it and deserializes. Anything not serializable is lost.

---

## 5. Asset pipeline

| Layer | Mechanism |
|---|---|
| Source | `Assets/` files + `.meta` (GUID, importer settings) |
| Import | Native importers; `ScriptedImporter` for custom extensions; `AssetPostprocessor` hooks |
| Artifacts | `Library/SourceAssetDB` (hashes, GUIDs) and `Library/ArtifactDB` (results, dependencies); the artifact folder was renamed `DataStore` in 6.6. Never committed |
| Sharing | Unity Accelerator (cache server) for teams and CI |
| Runtime loading | `Resources` → AssetBundles → **Addressables** (address/label lookup, catalogs, remote content) → **content directories** (6.6; AssetBundle replacement with automatic de-duplication) |

**Importer contract.**
- `[ScriptedImporter(version, "ext")]` registers an extension; `OnImportAsset(ctx)` emits objects with `ctx.AddObjectToAsset("<stable id>", obj)` and `SetMainObject`.
- Bump `version` whenever importer code changes.
- Declare extra inputs (`DependsOnSourceAsset`, `DependsOnArtifact`); reading `selectedBuildTarget` auto-registers a dependency.
- Import code **must be deterministic** (no timestamps, no reliance on dictionary order), or the cache serves wrong results.

**Pain:** a fresh clone or cleared `Library/` means a full reimport (hours on big projects); platform switches force reimports.

### How Unity imports `.blend` files

1. Unity treats `.blend` as a "proprietary" format and launches the locally installed Blender in the background.
2. Blender runs a bundled `Unity-BlenderToFBX.py` (shipped in `Editor/Data/Tools`) to export an FBX.
3. Unity imports that FBX as a read-only Model Prefab.

| Pain point | Detail |
|---|---|
| Blender everywhere | ".blend files fail to import unless you have the corresponding 3D modeling software installed… everybody working on your Unity project must have the correct software installed." That includes CI |
| Officially discouraged | "Don't use proprietary file formats in production and export to the .fbx format wherever possible" |
| Slow and fragile | Blender cold-starts per import, and the whole file is re-exported on any change. Forum reports of hangs with particular Blender versions; a bug where a non-Blender file association (Notepad) hung the import |
| Lossy | Textures and materials aren't wired up; visibility animation isn't exported. *Unverified third-party claim:* the script sets `bake_anim_use_all_actions=False`, so only the first action imports |
| Axis/scale | Blender's Z-up → Unity's Y-up produces the classic −90° X root rotation. "Bake Axis Conversion" is called buggy on deep hierarchies. FBX's centimetre units cause scale-100 issues |
| Custom data | Only what FBX carries arrives (user properties, read by an `AssetPostprocessor`). No first-class path for gameplay components |

---

## 6. Editor extensibility

The default Inspector walks the object's `SerializedObject`/`SerializedProperty` tree, the same data the YAML writer uses. With no extra code that gives you automatic UI, **undo/redo, prefab-override tracking, multi-object editing** and dirty tracking. Attributes (`[Range]`, `[Header]`, `[Tooltip]`) tweak the generated UI.

| Extension point | Purpose |
|---|---|
| `[CustomEditor]` with `CreateInspectorGUI` (UI Toolkit) or `OnInspectorGUI` (IMGUI) | Replace a component's Inspector; controls bind via `binding-path`; `PropertyField` falls back to default drawers |
| `[CustomPropertyDrawer]` | Custom UI for a field type or attribute, reused wherever it appears |
| `EditorWindow`, Overlays, `EditorTool` | Tool windows and scene tools (since 6.6, tools can live in any custom window) |
| `OnDrawGizmos(Selected)`, `Handles` | Scene-view visualization and interactive manipulators |
| `MenuItem`, `[InitializeOnLoad]`, `AssetPostprocessor`, Graph Toolkit (6.6) | Menus, startup hooks, import hooks, node graphs |

This ecosystem (Odin Inspector, most Asset Store tools) is a big reason Unity won. The costs: two UI frameworks side by side, and editor code that shares the game's domain and pays the same reload costs.

---

## 7. Play mode, domain reload, and the editor/runtime split

- **Same process.** The game runs inside the editor's process and managed domain, so a game crash takes the editor down.
- **Entering play** historically meant a **domain reload** (tear down managed code to reset statics) plus a **scene reload**. On exit, scenes are restored from a backup, so scene edits made during play are lost ("copy component values" is the manual workaround).
- **Exceptions:** assets and ScriptableObjects keep play-mode edits. DOTS SubScenes go the other way: "Unity overwrites all subscene states with the current state on exit."
- **Skipping domain reload** (Enter Play Mode options) leaks statics, static event subscribers and non-serialized fields between sessions; you reset them by hand (`[RuntimeInitializeOnLoadMethod(SubsystemRegistration)]`, `[InitializeOnEnterPlayMode]`).
  - **6.6:** new projects default to "Reload Scene only."
  - **7.0:** domain reload is removed. New attributes (`[AutoStaticsCleanup]`, `[BeforeCodeUnloading]`) and a Project Auditor analyzer help migration.
- **Editor/runtime split.** `UnityEditor` APIs are editor-only, separated by `Editor/` folders, assembly-definition platform filters and `#if UNITY_EDITOR`. The split is enforced by convention and compilation, not architecture, so "works in editor, fails in player" bugs are common.

---

## 8. Scripting runtime

| | Mono (JIT) | IL2CPP (AOT) | CoreCLR |
|---|---|---|---|
| Where | Editor, desktop dev builds | iOS, consoles; preferred for shipping | 7.0 editor (alpha); experimental player in 6.7 |
| Build time | Fast | Slow (C# → C++ → native) | — |
| Perf | JIT warm-up, Boehm GC | Native; aggressive stripping needs `link.xml`/`[Preserve]` | Modern JIT/GC; 7.0 targets only *parity*, optimization planned for the next LTS (2027) |
| Language | C# 9 / .NET Standard 2.1 | Same; BCL moving to .NET 10 | .NET 10 / C# 14 planned, no date |

- **Assembly definitions (`.asmdef`)** limit recompilation to changed assemblies and their dependents, but before 7.0 the *whole* domain still reloads after every compile.
- **Iteration pain.** One forum example: 2 s of compilation became 12–14 s of editor lockup, mostly domain reload. Editor plugins and `[InitializeOnLoad]` work slow it further. Entities 1.2+ was reported to push one project's compiles from 27 s to 210 s. A third-party "Hot Reload for Unity" asset exists because a studio faced 2–3 minute reloads on a 2M-line codebase.
- **CoreCLR (Oct 2026).** 6.7 LTS is the last Mono release. 7.0 uses AssemblyLoadContext-based reload. Faster iteration "needs follow-up work," and live code iteration is "long-term." Breaking changes include no automatic static reset, no `BinaryFormatter`, different static-constructor timing and IEEE-754 float differences.
- **Burst** compiles a struct-only "HPC#" subset to native code via LLVM; it is built into the engine as of 6.6.

---

## 9. DOTS / ECS and the authoring/runtime split

**Runtime.** Entities are IDs; components are unmanaged `IComponentData` structs stored in archetype chunks; systems process queries. Jobs adds safety-checked multithreading, and Burst compiles the hot paths. Physics is Unity Physics (stateless C#) or Havok; rendering is Entities Graphics.

**Authoring (the important part for us).**
- **Baking** "converts GameObject data in the Unity Editor (authoring data) into entities written to Entity Scenes (runtime data)." It is one-way: "Baking only happens in the Editor and never in-game, just like asset importing."
- Authoring components are ordinary MonoBehaviours, conventionally suffixed `Authoring`.
- A **Baker** (`Baker<TAuthoring>`) maps one authoring component to ECS components, and can create extra entities.
  - Bakers must be **stateless**: `Bake` is called many times, in non-deterministic order.
  - They may read external data **only through Baker APIs** (`GetComponent`, `DependsOn`) and must declare dependencies before any early return.
  - Unity records each baker's inputs (to know when to rebake) and outputs (to undo them before rebaking).
- **Baking systems** post-process the whole scene. `TransformUsageFlags` declares which transform data an entity needs, so static objects can drop it.
- **SubScenes** hold the authoring objects ("Unity's core scene system is incompatible with ECS").
  - *Open:* incremental **live baking** updates the runtime world in memory as you edit.
  - *Closed:* a **background full bake** runs in a headless worker, and the binary entity scene streams in at runtime.
- Incremental and full bakes can differ in ordering and chunk layout. Unity 6.6 added `EntitySceneImporterDeterminismChecker.Check()` because non-deterministic bakers inflate patches.

**Where DOTS hurts.**
- Two worlds bridged by "hybrid" companion components (e.g., `Light`, `MeshRenderer`).
- Managed components deprecated in 6.6.
- Boilerplate: an authoring class, a baker and a component per feature.
- Slow compiles; for years ECS lived in a separate package with its own hierarchy window.

**2026 direction ("ECS for all").** ECS became core packages (6.4); `EntityId` is being aligned with the 64-bit Entity; the standard Hierarchy shows entities (6.6). The stated goal is that "every GameObject will have the ability to have ECS component data attached to it," still marked "Planned" with no date. Unity is paying down a decade of two-model debt.

---

## 10. Other subsystems (brief)

| Area | State (2026) | Lesson |
|---|---|---|
| Rendering | Scriptable Render Pipeline (C# drives the frame). URP is the focus; HDRP gets "no new features" (Feb 2026); BiRP deprecated in 6.5, supported to at least end of 2028. Render Graph mandatory for URP custom passes (6.4) | Three incompatible pipelines; one asset developer estimated "50% of their time" went to supporting them all |
| Physics | PhysX (4.1 since 2019.3; no 2026 upgrade found), Unity Physics/Havok for ECS, Box2D v3 low-level 2D API (6.3) | A separate physics stack per object model |
| Input | Input System package replacing the legacy Input Manager | Two systems coexisted for years |
| UI | UI Toolkit (UXML/USS, editor and runtime), uGUI still invested in, IMGUI for legacy editor code | Three UI systems |
| Build | Build Profiles, Scriptable Build Pipeline, Build Analysis/history (6.6) | Content builds separate from player builds is good |

---

## 11. Unity's AI tooling (2026)

**History.** Muse (2023, first-party models) is deprecated. Unity 6.2 (Aug 2025) launched "Unity AI": the Assistant replaced Muse Chat; Generators replaced Muse's sprite/texture/animation tools, adding third-party models from Scenario and Layer AI; and Sentis became Inference Engine. The open beta started May 1, 2026, and the "Unity AI" brand is now being retired in favor of per-tool names.

| Tool | What it does |
|---|---|
| **Assistant** | Project-aware, using unnamed third-party "frontier" models. **Ask** is read-only. **Plan** proposes steps the user approves. **Agent** writes scripts, edits components, creates prefabs and assets, runs editor actions and verifies results. Permission tiers: Read-only / Write scripts only / Full autonomy. Expandable activity log; checkpoints make every change reversible |
| **Generators** | Sprites, textures, materials, sound, animation, terrain layers, skyboxes, static 3D objects. Can be disabled per organization; outputs carry AI-provenance metadata |
| **AI Gateway** | Bring your own agent or model (Claude, GPT…) without spending Unity credits |
| **CLI + Pipeline + MCP** (Jul 2026, beta, free) | `unity` binary with JSON output and exit codes. `unity command eval` runs C# in a live editor or player (Roslyn, no domain reload, token-gated, localhost-only). `[CliCommand]` makes any static method a discoverable command. A plugin adds Unity skills to Claude Code, Codex and Grok |

**Pricing.** Included in Pro, Enterprise and Industry plans. Personal users get a trial, then $10/month for 1,000 credits.

**Reception (May–Aug 2026): mixed.**
- Developers criticized paywalling the MCP server and bring-your-own-key access; the CLI and MCP were later made free.
- Others asked "who is it for?", saw it as aimed at no-code users, and found the credit burn hard to justify.
- A static-analysis vendor raised code-quality concerns.
- A May test found the agent could not play the game, see the Game View in Play Mode or simulate input, so it could only do static checks.
- External agents get blocked by modal dialogs, and the editor sometimes needs window focus before it refreshes.
- Plan mode and scaffolding were praised.
- Unity's CLI post sums up the lesson: "the shift from an assistant that *suggests* to one that can *verify*"; "Unity's AI is one consumer of it, not a replacement for it."

---

## 12. What developers love and hate

| Love | Hate |
|---|---|
| Simple model: add a component, edit its fields | Iteration time (compile + domain reload) |
| Auto-generated Inspector; cheap editor tooling | "Three of everything" (pipelines, UI, input, GameObjects vs ECS); packages stuck in preview or deprecated |
| Prefabs (nested, variants) and ScriptableObjects | Override complexity; YAML merge conflicts; `.meta` discipline; "Missing Script" |
| C# and the ecosystem (Asset Store, tutorials) | Serialization gaps (dictionaries only since 2026) |
| Platform reach, fast prototyping | DOTS power gated behind a verbose second model |
| Extensibility (inspectors, gizmos, windows) | Full reimports, slow IL2CPP builds, shared editor/game process; trust (Runtime Fee announced Sept 2023, rescinded Sept 2024; AI paywall in 2026) |

---

## 13. Implications for our Blender-based engine

Our architecture already has the shape Unity is converging toward: **an authoring tool separate from the runtime.** DOTS Baking and the `ScriptedImporter` contract are the models to study. Recommendations in priority order:

### Copy

1. **Stable identity: `(asset GUID, local ID)` everywhere, never names.**
   - Blender data-blocks are keyed by renameable names. Store a UUID per asset and stable local IDs per object/component *inside the .blend* (ID custom properties), so they survive renames, moves and appends. No sidecar to lose.
   - Bind components to **schema type IDs**, not file paths (avoids "Missing Script"), and support rename aliases (`MovedFrom`-style) from day one.
2. **A Baking-style cook step: Blender → deterministic, cached runtime data.**
   - Stateless per-component bakers with explicitly recorded dependencies: `(inputs hash, baker version) → artifact`.
   - A version bump invalidates the cache, and a **CI determinism check** runs from day one.
   - Two modes: **live incremental** (push deltas to the running game while editing) and **full background** (builds and CI).
3. **Content-hashed artifact cache plus a shared cache server.** Teammates and CI who didn't touch a .blend should never need Blender. Artifacts are never committed.
4. **Schema-driven components with an auto-generated inspector.**
   - Define components once in the runtime language, and generate Blender `PropertyGroup`s and panels from the schema.
   - Allow opt-in drawer and panel overrides, as Unity layers `PropertyDrawer`/`CustomEditor` over defaults.
   - Give every field a **property path**: one addressing scheme for UI, undo, overrides, diffs and AI edits is Unity's best architectural idea.
5. **Prefabs as sparse override lists.** A prefab is a linked Blender collection asset; an instance stores `(target local_id, property_path, value)` plus added/removed components. Support nesting and one variant concept. As with Model Prefabs, linked geometry is read-only and customization happens through overrides.
6. **An agent surface built in from day one, in the spirit of Unity's CLI/Pipeline.**
   - Headless commands with JSON output and exit codes, a self-describing command registry, and a sandboxed eval.
   - **The agent can run the game, query live state, inject input and capture frames**, so it can verify its own work.
   - Ask/Plan/Agent modes with permission tiers, an activity log, and every agent change as an undoable, diffable transaction. This keeps the developer in charge, as the user wants.
   - A free MCP surface with bring-your-own-model support.

### Avoid

1. **Converting .blend to FBX through an opaque Blender process.** Our exporter runs *inside* Blender and knows our component data. Use glTF or our own format for meshes, convert axes in exactly one place, and consider adopting Blender's Z-up as the engine convention to remove the −90° problem entirely.
2. **Two object models.** Pick one runtime model (I recommend ECS-style data) and let Blender objects be authoring-only. Blender then plays the role of Unity's GameObject authoring layer, and baking stays simple.
3. **Running the game in the editor process or depending on domain reload.** Run the runtime as a separate process connected to Blender by a live link. Play-mode changes are discarded by construction, with an explicit "apply back to Blender" action, and a game crash can't kill the editor. Keep gameplay state in the (serializable) world, never in hidden statics, so code can hot-reload at any time. Unity is only retrofitting this in 7.0.
4. **An "almost YAML", unmergeable scene format.** `.blend` is binary and can't be merged. Put collaborative gameplay data (components, overrides, level metadata) in real text sidecars (JSON/TOML) keyed by stable IDs, or make it exportable to them. That serves merges and AI agents alike. Ship a merge tool that reports conflicts instead of letting the last write silently win.
5. **Subsystem fragmentation.** One render-graph renderer, one UI system, one input system. Deprecate only once a replacement is ready.
6. **Serialization narrower than the language.** Support dictionaries, polymorphism, null and shared references in v1.
7. **Modal dialogs or focus dependence on any automation path.** Both block Unity's external agents today.

### Concept mapping (proposed)

| Unity | Our engine |
|---|---|
| GameObject + Transform | Blender Object (authoring) → Entity (runtime) |
| MonoBehaviour fields | Schema components stored as Blender custom properties / PropertyGroups |
| `.meta` GUID + fileID | UUID + local IDs stored inside the .blend, mirrored in the artifact DB |
| Prefab / nested / variant | Linked collection / nested collection instance / override list keyed by local ID |
| ScriptableObject | Typed data assets (text) |
| Importer + `Library/` + Accelerator | In-Blender exporter + content-hashed cache + shared cache server |
| Baker / SubScene / live baking | Component bakers / cooked level / live-link deltas |
| Play mode | Separate runtime process; explicit apply-back |
| Inspector / PropertyDrawer / Gizmos | Generated panels + drawer overrides + Blender gizmo API |
| CLI / Pipeline / MCP / Assistant | Headless command API, self-describing tools, verify loop, permissioned agent modes |

---

## 14. Sources

- Unity 6.6 "What's new": https://docs.unity.com/en-us/engine/6000.6/manual/whats-new/unity66.md
- Unity 6.4 "What's new": https://docs.unity.com/en-us/engine/6000.6/manual/whats-new/unity64.md
- Unity "What's new" index: https://docs.unity.com/en-us/engine/6000.6/manual/whats-new.md
- Unity 6.5 release (GameFromScratch): https://gamefromscratch.com/unity-6-5-released/
- CoreCLR update, October 2026: https://discussions.unity.com/t/coreclr-scripting-and-net-update-october-2026/1738338
- CoreCLR update, June 2026: https://discussions.unity.com/t/coreclr-scripting-and-serialization-update-june-2026/1723299
- Path to CoreCLR, 2026: Upgrade Guide: https://discussions.unity.com/t/path-to-coreclr-2026-upgrade-guide/1714279
- Unity's 2026 Roadmap (Digital Production): https://digitalproduction.com/2025/11/26/unitys-2026-roadmap-coreclr-verified-packages-fewer-surprises/
- Render Pipelines strategy for 2026: https://discussions.unity.com/t/render-pipelines-strategy-for-2026/1710004
- ECS Development Status, Dec 2025: https://discussions.unity.com/t/ecs-development-status-december-2025/1699284
- ECS Development Status for 6.6: https://discussions.unity.com/t/ecs-development-status-for-unity-6-6/1724480
- Baking overview: https://docs.unity3d.com/Packages/com.unity.entities@1.3/manual/baking-overview.html
- Baker overview: https://docs.unity3d.com/Packages/com.unity.entities@1.3/manual/baking-baker-overview.html
- Subscenes overview: https://docs.unity3d.com/Packages/com.unity.entities@1.3/manual/conversion-subscenes.html
- Order of execution: https://docs.unity3d.com/6000.3/Documentation/Manual/execution-order.html
- Domain reloading: https://docs.unity3d.com/6000.3/Documentation/Manual/domain-reloading.html
- UnityYAML: https://docs.unity3d.com/6000.3/Documentation/Manual/UnityYAML.html
- Text serialization format: https://docs.unity3d.com/6000.3/Documentation/Manual/FormatDescription.html
- YAML prefab serialization: https://docs.unity3d.com/6000.6/Documentation/Manual/yaml-prefab-serialization.html
- YAML prefab example: https://docs.unity3d.com/6000.6/Documentation/Manual/yaml-prefab-example.html
- Prefab variants: https://docs.unity3d.com/6000.3/Documentation/Manual/PrefabVariants.html
- Prefab instance overrides: https://docs.unity3d.com/6000.3/Documentation/Manual/PrefabInstanceOverrides.html
- Unpacking prefabs: https://docs.unity3d.com/Manual/UnpackingPrefabInstances.html
- Nested-variant fileIDs (XOR) forum thread: https://discussions.unity.com/t/unity-file-formats-what-does-a-fileid-of-a-variant-of-a-variant-modification-refer-to/885946
- Model prefabs (FBX Exporter docs): https://docs.unity3d.com/Packages/com.unity.formats.fbx@5.0/manual/prefabs.html
- Serialization rules: https://docs.unity3d.com/6000.3/Documentation/Manual/script-serialization-rules.html
- SerializeReference: https://docs.unity3d.com/6000.3/Documentation/ScriptReference/SerializeReference.html
- Asset Database contents: https://docs.unity3d.com/Manual/asset-database-contents.html
- Scripted importers: https://docs.unity3d.com/6000.3/Documentation/Manual/ScriptedImporters.html
- Model file formats: https://docs.unity3d.com/6000.3/Documentation/Manual/3D-formats.html
- Importing from Blender (2017.4): https://docs.unity3d.com/2017.4/Documentation/Manual/HOWTO-ImportObjectBlender.html
- Blender import hangs: https://discussions.unity.com/t/2021-2022-stuck-importing-blend-files/858835 , https://issuetracker-mig.prd.it.unity3d.com/issues/importing-blend-files-freezes-unity-if-the-default-program-to-open-them-is-other-than-blender
- BlenderToFBX derivatives and animation claim: https://github.com/jonathanleang/blendToFbxExporterForUnity , https://fablesalive.gumroad.com/l/UnityBlenderAnimationFix
- FBX axis/scale notes (Uninomicon): https://uninomicon.com/_export/raw/fbx_importing
- Custom Inspector (UI Toolkit): https://docs.unity3d.com/6000.3/Documentation/Manual/UIE-HowTo-CreateCustomInspector.html
- Scripting backends: https://docs.unity3d.com/Manual/scripting-backends-intro.html
- Domain reload slowness: https://discussions.unity.com/t/why-is-domain-reload-sooo-slow-and-will-this-ever-be-fixed/798176 , https://issuetracker.unity3d.com/product/unity/issues/guid/1270910
- Entities compile regression: https://issuetracker-mig.prd.it.unity3d.com/issues/scripts-have-poor-compilation-times-when-using-entities-versions-1-dot-2-0-and-higher
- Hot Reload for Unity: https://premortem.games/2023/02/24/new-tool-dramatically-improves-compiling-times-for-unity/
- UnityYAMLMerge bugs: https://issuetracker.unity3d.com/issues/unityyamlmerge-tool-is-not-detecting-conflicts-in-prefab-instances , https://issuetracker-mig.prd.it.unity3d.com/issues/unityyamlmerge-dot-exe-doesnt-correctly-handle-merge-conflicts-in-modified-properties-on-a-prefab-variant
- BiRP deprecation debate: https://gamedev.net/news/2302-spicy-topic-unitys-killing-birp/
- Low-level 2D physics: https://discussions.unity.com/t/low-level-2d-physics-in-unity-6-3/1683247/1
- Unity 6.3 LTS: https://alternativeto.net/news/2025/12/unity-6-3-lts-arrives-with-hybrid-2d-3d-scenes-box2d-v3-and-cross-platform-toolkit
- Unity AI features: https://unity.com/features/ai
- Ask/Plan/Agent modes: https://unity.com/blog/unity-ai-assistant-ask-plan-agent-mode-explained
- Generators overview: https://docs.unity3d.com/Packages/com.unity.ai.assistant@2.10/manual/about/overview.html
- Open beta coverage: https://aihola.com/article/unity-ai-open-beta
- Unity 6.2 AI launch: https://digitalproduction.com/2025/08/22/unity-6-2-welcomes-ai-but-pace-caution-user-liability-on-copyright/
- Reception: https://www.msn.com/en-us/news/other/unity-ai-beta-draws-mixed-developer-reactions-over-usefulness-and-focus/gm-GM077346D6 , https://tech.yahoo.com/gaming/articles/unity-ai-beta-sparks-backlash-150000279.html , https://unicorn-dev.medium.com/unitys-ai-agent-went-public-the-developers-of-a-static-analysis-tool-on-what-that-means-for-code-71c870ba5ec6
- Meet the Unity CLI: https://unity.com/blog/meet-the-unity-cli
- Unity CLI analysis (Vindler): https://vindler.solutions/blog/unity-cli-agent-automation

**Unverified or weakly sourced:** the XOR fileID derivation (forum); the `bake_anim_use_all_actions` claim (third-party seller); PhysX 4.1 (prior knowledge); the exact 6.6 release date; "outermost override wins" across nested prefab levels (prior knowledge, so not stated as fact above).
