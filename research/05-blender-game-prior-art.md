# Prior Art: Blender as a Game Engine or Game Editor (as of October 2026)

Research input for a game engine that uses Blender as its editor. Written 2026-10-09. **[unverified]** marks claims I could not confirm. **[inference]** marks my reading of the evidence.

| Project | Model | Status (Oct 2026) | License | Activity signal |
|---|---|---|---|---|
| Blender Game Engine (BGE) | Engine compiled into Blender | Removed in 2.80 (commit Apr 17, 2018) | GPL | Dead |
| UPBGE | Fork of Blender with the BGE restored | 0.50 stable (Jan 4, 2026); 0.53 alpha weekly builds | GPL | 1.8k stars; about 95% of UPBGE-specific commits by one developer |
| Armory3D | Blender add-on plus a separate Haxe/Kha runtime | 2026.2 (Feb 16, 2026); commits through Sep 27, 2026 | zlib (runtime) | 3.3k stars; 3 active committers |
| Range Engine | Fork of an older UPBGE (2.7x-era BGE) | 1.6 LTS; 2.0 "slower pace" | GPL [unverified] | Patreon-gated builds |
| Skein (Bevy) | Blender extension plus a Bevy plugin, linked by glTF | Add-on 0.1.16 (Jun 14, 2026); `bevy_skein` 0.6 for Bevy 0.19; `main` on 0.7.0-rc.1 / Bevy 0.20-rc | MIT or Apache-2.0 | 325 stars; active |
| Blenvy (Bevy) | Blender add-on plus a Bevy crate, linked by glTF | Alpha 1; last commit Feb 16, 2025; Bevy 0.14 | MIT/Apache | 849 stars; stalled |
| Needle Engine | Blender or Unity add-on plus a three.js runtime | Active | Commercial (free for non-commercial use) | Active |
| Verge3D | Blender, Max or Maya add-on plus a three.js-based runtime | 4.13 (Jul 15, 2026) | Commercial | Active |

---

## 1. TL;DR

- **Every in-process approach failed or became a fork.** Blender removed the BGE in 2.80 because it was "no longer under active development or did not fit in the new design." Its only healthy successor, UPBGE, is a full Blender fork that constantly merges Blender `main`, and one developer does nearly all of the game-specific work.
- **Blender's core team will not host a game engine.** Ton (2013): "drop the idea to have an embedded 'true' game engine." Brecht (2019): "There is no current plan to add back a game engine." Our engine has to be an add-on plus a separate runtime.
- **The model that works is: Blender add-on → glTF (plus a namespaced extension) → separate runtime process.** Armory, Needle, Verge3D, Skein, Blenvy, Godot's `.blend` import and Blender Studio's DOGWALK pipeline all use a variant of it.
- **Skein is the best blueprint for mapping custom properties to ECS components.** The add-on fetches the schema from the running game (Bevy `registry.schema` over JSON-RPC) and caches it in the `.blend`. It generates PropertyGroups and UI from that schema, exports `{"crate::Type": {...}}` through the stock glTF exporter's hooks, and Bevy rebuilds components through reflection. The add-on and runtime versions are deliberately independent.
- **Blenvy shows the opposite failure.** It had a large scope (blueprints, material libraries, save/load, auto-export), required the crate and add-on to be version-locked, and had one maintainer. It stalled on Bevy 0.14 in Feb 2025.
- **Live-link prior art is crude.** Armory's "live patch" writes JavaScript snippets to a file that the game polls and `eval`s every frame. Blenvy and Skein re-export glTF and rely on asset hot reload. Ubisoft Mixer (depsgraph diff over TCP) was archived. ID-stable, two-tier live sync is an open opportunity.
- **Blender hooks have sharp edges and keep changing.** `bpy.msgbus` does not fire for objects moved in the 3D viewport. Handlers that modify data can crash Blender. Blender 5.0 separated `bpy.props` storage from ID custom properties.
- **Game data in glTF is being standardized, slowly.** OMI physics, seat, spawn-point and vehicle extensions are Stage 1 proposals, and Godot 4.3+ imports OMI physics. `KHR_physics_rigid_bodies` is still a "Review Draft" (PR open since Jul 2024). `KHR_interactivity` was submitted for ratification on Jul 16, 2026.
- **The GPL boundary is manageable if we draw it deliberately.** Published `bpy` code must be GPL-compatible. A separate runtime that links no Blender code keeps the engine and its games free of the GPL.

---

## 2. The Blender Game Engine (BGE), 2000–2019

### 2.1 Architecture

| Layer | How it worked |
|---|---|
| Origin | Built in 2000 by NaN as a commercial product. Its physics library (SUMO) stayed closed when Blender went open source, so the engine did not work again until 2.37a. Bullet arrived in 2.42. |
| Code | A separate tree, `source/gameengine` ("Ketsji", with `KX_*`, `SCA_*` and `RAS_*` class families), plus `source/blenderplayer`. At game start, `KX_BlenderSceneConverter` copied Blender objects, meshes and logic bricks into engine-side objects through lookup maps. The game ran on its own scene graph, not on Blender's. |
| Logic bricks | Per-object graphs in the Logic Editor. **Sensors** ("primitive event listeners": keyboard, collision, always…) → **Controllers** (AND/OR/expression/**Python**) → **Actuators** ("the only components in the game which are able to" act on the simulation, apart from Python and physics). Per-object **properties** worked as variables, and **states** switched between groups of bricks. |
| Python | A Python controller ran a script or module function. The `bge` API exposed the game objects. |
| Physics | Bullet (rigid and soft bodies, vehicles, constraints), Recast/Detour navmesh (GSoC 2010), Audaspace audio. |
| Rendering | Its own OpenGL rasterizer (GLSL materials from 2.48) that duplicated Blender's viewport drawing. |
| Player | An embedded player ran inside a Blender viewport (P key). `blenderplayer` was a stripped standalone. "Save as Runtime" glued the player and the `.blend` into one executable. |

### 2.2 Why it was removed: what Blender developers actually said

- **2013, Ton Roosendaal (roadmap):** "I propose to make the GE to become a real part of Blender code – to make it not separated anymore… What should then be dropped is the idea to make Blender have an embedded 'true' game engine."
- **April 17, 2018, Dalai Felinto:** commit "Removing Blender Game Engine from Blender 2.8" deleted `source/gameengine`, `blenderplayer` and the logic editor. Blender Internal and the legacy viewport went two days later.
- **May 2018, Ton (bf-committers):** after the backlash, a part-time (about 1.5 days a week) one-year grant went to Benoit Bolsee. It covered a "real-time viewport with physics/event handling", "a new nodal logic system", and export to Godot, Armory and Blend4Web.
- **February 2019, Brecht Van Lommel:** "the interactive mode is explicitly not intended to be a game engine… There is no current plan to add back a game engine." I found no evidence that an interactive mode ever shipped.
- **Blender 2.80 release notes (July 2019):** the removed features "were no longer under active development or did not fit in the new design… We recommend using more powerful, open source alternatives like Godot Engine."

### 2.3 What went wrong

**Technical**
1. **A duplicate engine inside the editor.** The converter, scene graph and rasterizer mirrored Blender's own systems, so every change to Blender's data model or drawing code needed a matching BGE change. Wikipedia: keeping it "in sync with Blender was difficult." When 2.8 moved to an OpenGL 3.3 draw manager and EEVEE, the legacy rasterizer had no route forward. **[inference from the timing of the April 2018 removal commits]**
2. **Partial feature parity.** Modifiers, constraints and drivers only worked in game if someone re-implemented them in the converter.
3. **Python on the hot path.** Anything non-trivial ended up in Python controllers.
4. **Shipping.** Runtimes were desktop-only, and the GPL question (2.4) hung over them.

**Organizational**
1. **A thin volunteer maintainer base** for a subsystem the Foundation's film priorities did not need. It had "largely fallen behind other rising game engines" (Wikipedia). **[inference: no official maintainer count found]**
2. **Never properly funded and never cleanly cut** until the 2.8 rewrite forced the decision. The 2018 replacement grant was part-time, explicitly "not a game engine", and stalled.

### 2.4 Licensing lesson

Blender's license page says published scripts that use `bpy` must be "shared under a GPL compliant license", and that "what you create with Blender is your sole property." The old Blender wiki's BGE licensing guide said a runtime that glued your `.blend` into the standalone player "falls into the category of 'derivatives'… and therefore must be licensed as GPL." The workaround was a tiny starter `.blend` that loaded the real game from outside the executable. This uncertainty put off commercial developers for years. **[inference from 2009–2014 forum and list threads]**

---

## 3. UPBGE (Uchronia Project Blender Game Engine)

**Status.** UPBGE is a full fork of Blender with the BGE restored and modernized. Releases on GitHub: 0.30 (Dec 3, 2021, Blender 3.0 base, first EEVEE-based release), 0.36/0.36.1 (Jul and Aug 2023), 0.44 (May 6, 2025; "full compatibility with Blender 4.5"; experimental Vulkan; Metal on macOS), 0.50 (Jan 4, 2026; includes Blender features "between Blender 4.4.1 and Blender 5.0.1"). Weekly 0.53-alpha builds are published for Windows and Linux x86_64 (build 103, Oct 4, 2026). The repo has 1,825 stars.

**Architecture**
- **Rendering.** Since 0.3, UPBGE uses Blender's own draw manager and EEVEE. Workbench and Grease Pencil engines can also be used. The result is genuinely what-you-see-is-what-you-get between the viewport and the game.
- **Data.** Game objects stay tied to Blender objects and the depsgraph. That is why 0.50 added "Fast AddObject (UPBGE dupli base)": objects that "skip depsgraph integration" spawn much faster but have "fewer unique per-object properties". **[inference: the depsgraph coupling is the cost of reusing EEVEE]**
- **Logic, in three layers.**
  1. Classic logic bricks.
  2. **Python components.** A class inherits `KX_PythonComponent` and declares an `args` dict (float, int, bool, string, enum, vectors, Object/Action/Material references). UPBGE builds the component's UI from `args`, calls `start(args)` once and `update()` every frame "before running logic bricks", and offers a reload button after edits.
  3. **Logic Nodes** (the bundled "uplogic" add-on, originally IzaZed's). Applying a tree generates a `.py` file with one class per node, in dependency order. The manual: "Due to the way Logic Nodes work, they are the most performance-heavy way to create logic."
- **Physics.** Bullet, with Recast/Detour for navigation and Audaspace for audio. 0.50 adds experimental compute-shader GPU skinning (one armature, 4 weights) and a reworked 2D-filter path for OpenGL and Vulkan.

**Shipping.** The bundled "Save As Game Runtime" add-on writes the blenderplayer, the libraries and the data into a folder with an `.exe`. Third-party BPPlayer adds encrypted data packs. VR projects cannot be exported, according to forum reports. The GPL question from 2.4 applies unchanged.

**Strengths.** One application, viewport-identical rendering, and instant play with the P key. The Python components pattern, where a code-declared `args` schema becomes the UI, is a good one.

**Limitations.** It carries the full weight of Blender. It uses a viewport renderer rather than a game renderer (users report animation and material performance problems), targets desktop only, ships GPL runtimes, and pays a constant merge tax. In a sample of 100 "UPBGE:" commits from 2026, 95 were by one developer (`youle31`). Most other commits are upstream Blender merges.

**Community.** Small and mostly hobbyist or education users (BlenderArtists, Discord). I found no notable commercial releases. **[unverified]**

---

## 4. Armory3D

**Status.** Created by Lubos Lenco. Current release 2026.2 (Feb 16, 2026), with commits through Sep 27, 2026, mostly by Moisés Peláez. 3,347 stars, about 374 open issues. Releases were monthly in 2022–2023, then roughly twice a year. The add-on warns unless Blender is 3.3, 3.6, 4.2 or 4.5 LTS ("Armory should be up to 4.5", forum, Feb 2026). Blender 5.x support is unclear: `bl_info` declares 5.2, but 5.x is not on the LTS list.

**Architecture** (from the wiki's Architecture page)

| Layer | Role |
|---|---|
| Blender add-on (Python) | Exports every object to Iron's `.arm` scene format, "structures the export as a Kha game", compiles logic-node trees into Haxe, and maps Cycles/EEVEE node materials onto Armory's own shaders. |
| Haxe → Kha/Kinc → Iron | Haxe game code on Kha, a portable framework (`khamake` builds projects, Krafix cross-compiles shaders), with Iron as the engine core. |
| Krom / ArmorCore | The development runtime: V8 plus Kinc in a separate native window. Play in Blender runs the game in seconds, with no C++ build. |
| Release builds | HTML5, desktop, mobile and consoles through Kha's backends (native code via HashLink/C). |

**Traits** are Armory's components: a Haxe script, a bundled trait (for example a character controller), a logic-node tree compiled to Haxe, or WebAssembly, attached in Blender's Properties panel.

**Live patch: the actual mechanism** (from `blender/arm/live_patch.py` and `LivePatch.hx`)
1. When a session starts, the add-on calls `bpy.msgbus.subscribe_rna` on `Object.location`, `rotation_euler`, `scale`, and light `color` and `energy`. Logic-node editor callbacks also fire on link, property, socket and create events.
2. Each event becomes a **JavaScript string**, for example `var o = iron.Scene.active.getChild("Cube"); o.transform.loc.set(x,y,z); o.transform.dirty = true;`. It is written to `build/debug/krom/krom.patch` with an incrementing patch ID.
3. Every frame, the running game reloads `krom.patch` as a blob. If the ID is new, it calls `js.Lib.eval()` on it.
4. For anything structural, such as edit-mode changes, the add-on re-exports the scene `.arm`, runs `khamake krom --nohaxe --noproject`, and then sends `iron.Scene.patch()` to reload the scene.

It works, but it is fragile: file polling, `eval` of generated code, name-based lookup and only a few watched properties. Users reported crashes and build failures with live patch on (2022). Blender documents that msgbus does **not** fire for objects moved in the 3D viewport, so viewport drags are probably missed. **[inference; not tested]**

**Lesson.** Armory's early "viewport player" ran Krom inside Blender's viewport. It needed a custom Blender build and was dropped in 0.6. The separate runtime with fast Play is the part worth copying.

---

## 5. Range Engine and other BGE descendants

- **Range Engine.** "Built on top of an older release of UPBGE", keeping the 2.79-era BGE (logic bricks and the classic workflow) "with continued support". 1.2 (Sep 2022) moved to Python 3.10. 1.6 shipped in Sep 2024 and the download was then pulled (forum reports). A March 2025 Patreon update promised 1.6 LTS support "until 2026" and said 2.0 is progressing "at a slower pace". Builds are gated to Patreon supporters. Lesson: some users value the stable old UX more than new features, but a fork of a fork does not attract contributors. **[inference]**
- **Blend4Web → Verge3D.** Blend4Web was a GPL-plus-commercial Blender-to-WebGL engine, now listed as discontinued **[date unverified]**. Its creators started Verge3D in 2017: a commercial add-on exporting glTF to a three.js-derived runtime, with "Puzzles" (Blockly-style visual logic). Version 4.13 shipped Jul 15, 2026. This is the commercially sustainable form of the add-on plus web runtime model.

---

## 6. Bevy + Blender: custom properties as ECS components

These projects are the closest match to our design: Blender authors entities, and component data comes from the engine's own type system.

### 6.1 Skein (rust-adventure, Chris Biscardi; repo created Feb 2025)

**Versions.** `bevy_skein` 0.1 through 0.6 cover Bevy 0.15 through 0.19, and `main` is already on 0.7.0-rc.1 for the Bevy 0.20 release candidate. The Blender add-on has its own release cycle (0.1.16, Jun 14, 2026; Blender 4.2+; 0.1.13+ required for Blender 5.0, 0.1.16+ for Blender 5.2). "All versions of `bevy_skein` are… compatible with all versions of the skein Blender addon."

**Mechanism, step by step**

1. **Declare components in Rust** with `#[derive(Component, Reflect)]` and the mandatory `#[reflect(Component)]`. `SkeinPlugin::default()` starts the Bevy Remote Protocol (HTTP JSON-RPC at `127.0.0.1:15702`) in debug builds only. It also adds a `skein/presets` method that returns `Default` values and presets registered with `app.insert_skein_preset(...)`.
2. **Get the schema into Blender.** The "Fetch type registry" operator calls `rpc.discover`, then `registry.schema` (`bevy/registry/schema` on older Bevy), on the running game. It **stores the response in the `.blend`** as Text datablocks (`skein-registry.json`, `skein-presets.json`), so the file can be edited later without the game running.
3. **Generate the UI.** Each reflected type becomes a `PropertyGroup` subclass built at runtime (names over Blender's 64-character limit are hashed), with nested structs as `PointerProperty`s. Components attach to **objects, meshes, materials, cameras, lights, bones and scenes**, and Blender drivers can animate their values.
4. **Export.** A `glTF2ExportUserExtension` hooks the stock exporter (`gather_node_hook`, `gather_mesh_hook`, `gather_material_hook`, `gather_camera_hook`, `gather_joint_hook`, `gather_scene_hook`) and writes directly reflectable JSON:
   ```json
   "extras": { "skein": [ { "test_project::Rotate": { "speed": 1.0 } } ] }
   ```
   Since add-on 0.1.14 with Bevy 0.18+, it can write a proper vendor extension instead:
   ```json
   "extensions": { "BEVY_skein": { "components": [ { "test_components::Player": { "power": 90.9 } } ] } }
   ```
5. **Rehydrate in Bevy.** *Extras path:* an observer fires when `GltfExtras`, `GltfMeshExtras` or `GltfMaterialExtras` is added at spawn, then parses the JSON, runs `ReflectDeserializer` against the `TypeRegistry`, and calls `insert_reflect`. *Extension path:* a Bevy 0.18 glTF extension handler inserts components **while the loader builds the Scene asset**, so "Scenes are 'ready to go'".
6. **Iterate.** Blender 4.2 Collection Exporters (settings stored per collection) plus a "Trigger all Collection Exporters" operator re-export the files, and Bevy's file watcher reloads them.
7. **Refactor headlessly.** `blender --background -b art/tunic.blend -c change_component_path --old_path tunic_bush::BushSensor --new_path api::BushSensor`.

**Design principles** (DESIGN.md, worth adopting almost verbatim):
- "Maintainability is a core concern." Bevy releases every 3–4 months, and every new concept is a maintenance cost.
- "Prefer deep integration over new APIs." Use stock glTF processing, and support Blender's own instancing (collection instances, linked duplicates, library overrides) before inventing new ones.
- "glTF over .blend": "official word is that .blend file parsing isn't a viable target."
- The boundaries are **API formats** (BRP JSON in, a `skein` reflection array out), so either side can be upgraded independently.

### 6.2 Blenvy (kaosat-dev; formerly `bevy_components` + `gltf_auto_export` + `bevy_gltf_blueprints`)

- **Schema.** The crate writes `assets/registry.json` (the whole `TypeRegistry` as JSON schema) at `Startup` on every run. The add-on reads the file, so no live connection is needed.
- **Data.** Component values are stored per object as **RON strings** in a `bevy_components` custom-property map (`{"type::path": "(ron)"}`) and exported as glTF extras. Bevy parses them with `ReflectDeserializer`.
- **Blueprints.** Collections in "library" scenes become blueprint `.glb` files. "Level" scenes reference them through a `BlueprintInfo { path }` component. Material libraries deduplicate textures.
- **Auto-export on save** with change detection. **Hot reload** maps `AssetEvent::<Gltf>::Modified` back to blueprint instances and respawns them. **Save/load** splits static from dynamic entities.
- **Status.** "Alpha 1… quite a few bugs"; "use matching version numbers for the Blender add-on & the rust crate"; Bevy 0.14; last commit Feb 16, 2025.

### 6.3 Comparison

| | Skein | Blenvy | UPBGE components | Needle Engine |
|---|---|---|---|---|
| Schema source | Live game over BRP JSON-RPC; cached in `.blend` | `registry.json` file written at game start | `args` dict in the Python class | TypeScript class compiled by the "component compiler" |
| Storage in Blender | Generated PropertyGroups | RON strings in custom props | Component property panel | Generated component panels |
| Wire format | glTF `extras.skein` or `BEVY_skein` extension (JSON) | glTF extras (RON strings) | None (same process) | glTF `NEEDLE_components` extension |
| Runtime rehydration | Bevy reflection at spawn, or at load (0.18+) | Bevy reflection at spawn | Native | Type name → registered TS class |
| Prefabs | Blender collection instances | Custom blueprint system | Blender collections/instances | Editor-native **[unverified for Blender]** |
| Version coupling | Decoupled by design | Lock-step | Same binary | Package versions |
| Health | Active | Stalled | Active, 1 developer | Commercial |

---

## 7. Other "Blender as level editor" pipelines

### 7.1 Godot
- **`.blend` import.** Godot "transparently" runs the user's Blender (3.0+, 3.5+ recommended) to export glTF, then uses its normal glTF importer. An RPC mode (editor setting `rpc_port`, default 6011) keeps Blender alive between imports for speed. Downsides: everyone on the team needs Blender installed, and it does not work in the Android or web editors.
- **Name-suffix hints:** `-col`, `-convcol`, `-colonly`, `-convcolonly`, `-occ`, `-occonly`, `-navmesh`, `-rigid`, `-vehicle`, `-wheel`, `-noimp`, `-loop`/`-cycle`, `-alpha`, `-vcol`. The docs recommend `GLTFDocumentExtension` for runtime-friendly import. Suffixes are an untyped, one-flag-per-object channel: the anti-pattern that schema-driven components replace.
- **Custom properties.** glTF extras land in `metadata/extras` on nodes, meshes and materials (Godot 4.x).
- **Physics.** Godot 4.3 imports `OMI_physics_body` and `OMI_physics_shape` natively. Blender needs a third-party exporter: the "OMI Physics Body glTF Extension" add-on, or GD3D's "Bless" level-editor add-on **[Bless details unverified]**.
- **DOGWALK (Blender Studio, 2025)** is the Blender project's own Godot game. Simon Thommes (Khronos blog, Oct 16, 2025) listed what worked: per-collection glTF exports (branch → tree → forest instancing), two engine uber-shaders mirrored by Blender node groups (parameters passed by name), and a custom Godot import script that rebuilt nested instances. What hurt: stepped animation keys broke Godot's optimizer (fixed upstream), and "our manual nested-instancing pipeline essentially reimplemented what a standardized extension could provide". UID and cache desyncs under Git LFS also caused trouble. Their advice: keep scope small, export per collection, track draft extensions, and move more work to the engine side.

### 7.2 Unity
Unity imports `.blend` by launching Blender in the background and converting the file through Blender's FBX exporter. That requires Blender on every machine, does not auto-assign textures, and drops custom data (documented in older manuals; current behavior summarized in our `01-unity.md`). It is a convenience, not a level-editing pipeline.

### 7.3 Unreal: Send to Unreal
Epic's free BlenderTools (Jul 2020) now live on as a community fork (poly-hammer, "not affiliated with Epic Games"; 646 stars, active Jul 2026). Send to Unreal is "a one-click solution". Put objects in an `Export` collection, and it infers the asset type, exports FBX or Alembic, and drives Unreal's import through Unreal's Python remote-execution channel (session discovery over UDP multicast, 239.0.0.1:6766 per third-party tools) **[transport details unverified]**. It handles asset push well and has no notion of gameplay components.

### 7.4 Web engines
| Engine | Blender custom properties → runtime | Notes |
|---|---|---|
| three.js | glTF `extras` → `object.userData` (GLTFLoader) | Non-object extras can break `userData`. Names are sanitized (`Object.001` becomes `Object001`). |
| Babylon.js | `ExtrasAsMetadata` loader extension → `node.metadata.gltf.extras` | Nodes, cameras and materials only. Scene, animation and skeleton extras are missing (forum). Babylon also supports `KHR_interactivity` playback. |
| PlayCanvas | No official extras surfacing found **[unverified]** | Users parse the GLB JSON themselves. |
| **Needle Engine** | TypeScript components (`@serializable`) → the component compiler (`@needle-tools/needle-component-compiler`) generates Blender panels → `NEEDLE_components` extension (`builtin_components[]` with `name`, `guid`, `gameObject.node`) → the three.js runtime matches type names to registered classes. "No code is stored in the glTF files — only data." Play goes live "typically in under a second", with Vite HMR for code. | Commercial: free for non-commercial use with a watermark; Pro €49 per user per month. |
| Verge3D | Add-on, Puzzles visual logic, three.js-derived runtime | Commercial. |

Needle mirrors Skein: **code is the schema source, generated editor UI, data-only glTF, and runtime rebuild by type name**, plus a per-component GUID for stable identity across re-exports.

---

## 8. Standardizing game data in glTF

| Extension | Purpose | Status (Oct 2026) | Implementations |
|---|---|---|---|
| `OMI_physics_shape` | Box, sphere, capsule, cylinder, convex, trimesh shapes | OMI Stage 1 proposal | Godot 4.3+ import (export **[unverified]**); third-party Blender exporters |
| `OMI_physics_body` | Motion (static, kinematic, dynamic), collider and trigger per node | OMI Stage 1 proposal | Godot 4.3+ |
| `OMI_physics_joint`, `OMI_physics_gravity` | Joints, gravity volumes | Proposals | Godot (partial) **[unverified]** |
| `OMI_seat`, `OMI_spawn_point` | Sit positions, spawn points | Proposals | Godot, metaverse clients **[unverified]** |
| `OMI_vehicle_body`, `_wheel`, `_thruster`, `_hover_thruster` | Vehicles built on OMI physics | Proposals | Godot demos **[unverified]** |
| `KHR_physics_rigid_bodies` + `KHR_implicit_shapes` | Khronos rigid bodies and shapes | **"Review Draft" in the Khronos registry. PRs #2424 (Jul 2024) and #2370 (Mar 2024) are still open.** The author's stated plan is to ratify. | Blender importer/exporter, Babylon importer, Godot importer (all by the spec author) |
| `KHR_interactivity` | Behavior graphs (events, flow, operations, variables) | Draft merged Jul 15, 2026; **submitted for ratification Jul 16, 2026** | Babylon.js, the Khronos Sample Viewer, UnityGLTF export (Needle), Magic Leap's Unity runtime. Blender not mentioned. |
| `KHR_animation_pointer` | Animate any glTF property | Ratified | Godot 4.4+; others **[unverified]** |

According to Aaron Franke (Godot glTF maintainer, Sep 2025), the OMI and KHR physics specs have converged a lot. OMI is kept as a stable base for vehicle experiments and "will likely" be rebased onto KHR physics after ratification. Both specs have made breaking changes within the last year.

---

## 9. Live-link and hot-reload approaches

| Approach | Example | Transport | Granularity | Problems seen |
|---|---|---|---|---|
| Run the game inside Blender | BGE, UPBGE (P key) | Same process | Total | Forks Blender; the game mutates editor data; GPL |
| Re-export on save + asset hot reload | Blenvy (auto-export with change detection → Bevy `AssetEvent::Modified` → respawn), Skein (collection exporters + Bevy file watcher), Godot `.blend` (re-import) | Files | Whole file or blueprint | Seconds of latency; respawning loses runtime state; entity identity is lost unless IDs are stable |
| Property patches as code | Armory live patch | `krom.patch` file polled each frame, `eval` | Per property | Code injection, name lookups, limited coverage, crash reports |
| Remote procedure calls | Send to Unreal (Unreal Python remote exec), Godot ↔ Blender RPC, Skein ↔ Bevy BRP (`registry.schema`) | UDP/TCP/HTTP JSON-RPC | Per command | Unauthenticated localhost services; version drift |
| Generic datablock sync | Ubisoft Mixer (archived Aug 2022) | TCP to a "broadcaster" server | Whole datablock per change | "Undo / Redo is a important pitfall… strongly adviced not to use it"; partial type coverage; could corrupt scenes |
| Agent bridge | blender-mcp (30k stars) | JSON over TCP, `localhost:9876` | Arbitrary Python | No authentication; `execute_blender_code` runs arbitrary code |

**Blender-side facts that constrain any live link**
- `bpy.msgbus` callbacks are "postponed until all operators have finished executing", fire at most once per property per cycle, and **do not** fire for objects moved in the 3D viewport or for animation changes. A transform link also needs `depsgraph_update_post` and `depsgraph.updates` (`is_updated_transform`, `is_updated_geometry`).
- "If the handler changes data that is accessed by the viewport, this can cause a crash of Blender." Handlers are dropped on file load unless marked `@persistent`.
- Python runs on Blender's main thread, so socket I/O belongs on a worker thread, handing results back through `bpy.app.timers`. **[standard practice; inference]**

---

## 10. Lessons learned

| Theme | What worked | What failed | Recurring pain points |
|---|---|---|---|
| Where the runtime lives | A separate process with fast Play (Armory/Krom, Needle, Skein, Godot) | An engine in Blender's tree (BGE); a viewport-embedded foreign runtime (Armory pre-0.6) | Fork maintenance (UPBGE merges Blender `main` constantly) |
| Data path | glTF plus extras or extensions, the stock exporter with hooks (Skein), per-collection exports (DOGWALK) | Parsing `.blend` directly ("not a viable target"); name suffixes as metadata | Extras are untyped. Each engine surfaces them differently (`userData`, `metadata.gltf.extras`, `metadata/extras`, or not at all). |
| Schema | Runtime type registry → generated Blender UI (Skein, Blenvy, Needle, UPBGE `args`) | Hand-maintained duplicate definitions | Type renames break saved data, so migration tools are needed (Skein CLI) |
| Versioning | Decoupled wire formats (Skein) | Lock-step versions (Blenvy) | Blender API churn (2.80 broke everything; 5.0 removed BGL, split `bpy.props` from ID props, removed the legacy Action API). Armory pins LTS. Bevy releases every 3–4 months. |
| Logic authoring | Code-first components with editor-exposed parameters | Logic bricks did not scale; generated-Python node graphs are the slowest path (UPBGE) | Visual scripting is the feature people ask for and the most expensive to maintain |
| Rendering | Same renderer in editor and game (UPBGE/EEVEE); uber-shaders mirrored as node groups (DOGWALK) | Translating arbitrary Cycles/EEVEE node graphs (Armory: partial) | "Looks different in game" is a constant complaint |
| Performance | C++/Rust runtimes; Python only in the editor | Python per-frame logic (BGE/UPBGE) | Adding objects is slow when coupled to the depsgraph (UPBGE 0.50 workaround) |
| Licensing | Separate non-GPL runtime (Armory zlib, Needle, Verge3D) | GPL blenderplayer runtimes | Add-on code that imports `bpy` must be GPL-compatible if published |
| Live editing | File re-export + asset hot reload: simple and robust | `eval`-based patches; generic datablock sync (Mixer) | Undo/redo, identity across re-export, runtime state loss |
| Sustainability | Small, focused scope (Skein); commercial backing (Needle, Verge3D) | Large scope with one maintainer (Blenvy, Range) | Bus factor of 1 to 3 everywhere in this space |
| Standards | `KHR_animation_pointer`, Godot's OMI physics import | Waiting on physics and interactivity ratification (DOGWALK could not use them) | OMI and KHR physics overlap and both still change |

---

## 11. Implications for our Blender-based engine

**Copy these**
1. **Blender add-on + separate runtime process.** Never compile the runtime into Blender, fork Blender, or embed a viewport player. The BGE died, UPBGE is a one-developer fork, and Armory abandoned its viewport player. Make Play launch or attach to the runtime in under a second, as Krom and Needle do.
2. **The runtime type registry is the single source of truth for components** (the Skein pattern). The editor fetches a JSON schema from the runtime over local RPC, or from a `registry.json` file when the runtime is not running. It **caches the schema in the `.blend`** and generates PropertyGroups and panels from it. Presets and defaults come from code. Do not keep component data in raw ID custom properties: Blender 5.0 already moved that boundary, and generated PropertyGroups give typed UI, undo and drivers.
3. **Ship data through glTF in our own namespaced extension** (`<VENDOR>_components`: `{type_path: value}` entries plus a stable GUID per entity, as Needle does). Use standard extensions wherever one exists (`KHR_lights_punctual`, `KHR_materials_*`, `KHR_animation_pointer`). Hook the **stock** Blender exporter through `glTF2ExportUserExtension` and the runtime's stock loader through extension handlers, rather than writing our own (Skein's maintainability argument).
4. **Model physics on `KHR_physics_rigid_bodies` + `KHR_implicit_shapes`**, read OMI physics on import, and switch losslessly when KHR is ratified. Treat `KHR_interactivity` as the interchange target for any future visual logic instead of inventing a format.
5. **Two-tier live link.**
   - *Fast tier:* property and transform deltas, keyed by **stable GUIDs** (not names), sent as **data, not code** over a versioned, localhost-only JSON-RPC or WebSocket channel. On the Blender side, use `depsgraph_update_post` plus msgbus, debounced, with I/O on a worker thread.
   - *Structural tier:* re-export only the changed collections (Blenvy-style change detection, Blender 4.2 Collection Exporters) and hot-reload them in the runtime with identity preserved.
   - Keep the link one-way, from Blender to the runtime, so undo/redo stays Blender's job. Mixer shows the alternative.
6. **Decouple versions.** Version the schema and the protocol. Any add-on version should work with any runtime version within a protocol major (Skein's rule). Ship a headless migration CLI (`blender --background -c …`) for type renames.
7. **Prefabs are Blender collections and collection instances**, including linked libraries and overrides. Support native instancing before inventing anything.
8. **Code-first logic.** Gameplay lives in runtime-language components with parameters edited in Blender. Defer visual scripting. If we build it, compile it to native systems or `KHR_interactivity`, never to an interpreted per-frame graph.

**Avoid these**
- Parsing `.blend` files outside Blender. The Python API is the stable surface.
- Name-suffix metadata (`-col`, `-rigid`) as a primary channel. Accept it on import only.
- Translating arbitrary shader graphs. Ship a few engine shaders with matching Blender node groups (DOGWALK) and treat the runtime preview as the visual truth.
- `eval`-based patching (Armory) and unauthenticated "run any Python" sockets (blender-mcp).
- Large scope with one maintainer (Blenvy).
- Shipping any Blender binary with games. If the runtime links no Blender code, the engine and games can be MIT/Apache or proprietary. Only the `bpy`-touching add-on must be GPL-compatible. License the protocol and file-format specs separately.

**Process**
- Target Blender LTS (we have 5.2.2 LTS) and run headless add-on tests in CI against every new Blender release. Budget time for each upgrade: 5.0 alone changed property storage, the Action API and the EEVEE identifiers.
- **AI-agent assistance fits this architecture.** A machine-readable schema, diffable glTF/JSON, a headless Blender CLI and a runtime RPC give agents typed levers (compare `bevy_brp_mcp`, an MCP server for inspecting and mutating a running Bevy app). Agents should use the same operators and RPC methods as the developer, producing undoable, reviewable changes, never arbitrary code execution.

**Open questions**
- What is the runtime language and type registry (Rust reflection, C#, C++ with codegen)? That decides how the schema is exported.
- Is an in-Blender runtime viewport worth a fork? Recommendation: no. Use a docked or side-by-side runtime window with a live link.
- Is glTF the shipping format, or only the editor → runtime interchange, cooked to a binary runtime format (compare DOTS baking in `01-unity.md`)?

---

## 12. Sources

**BGE history and removal**
- https://developer.blender.org/docs/release_notes/2.80/removed_features/
- https://code.blender.org/2013/06/blender-roadmap-2-7-2-8-and-beyond/
- https://archive.blender.org/lists/bf-committers/2018-May/049438.html
- https://archive.blender.org/lists/bf-committers/2019-February/049812.html
- https://gamefromscratch.com/blender-game-engine-in-blender-2-8-life-after-death/
- https://devtalk.blender.org/t/ton-roosendaal-blender-2-8-realtime-and-interactive-3d-blender-2-9-blender-game-engine/18073
- https://en.wikipedia.org/wiki/Blender_Game_Engine
- https://docs.blender.org/manual/en/2.79/game_engine/logic/introduction.html
- https://projects.blender.org/archive/blender-archive/raw/commit/0f1e28a13fe28da2eaef47a229f5bd9f192bf2bc/source/gameengine/Converter/KX_BlenderSceneConverter.h
- https://archive.blender.org/wiki/2015/index.php/User:Sculptorjim/Game_Engine/Licensing
- https://www.blender.org/about/license/

**UPBGE**
- https://github.com/UPBGE/upbge (releases, commits via GitHub API)
- https://github.com/UPBGE/upbge/wiki/Release-notes-version-0.50
- https://github.com/UPBGE/upbge/wiki/Release-notes-version-0.44
- https://gamefromscratch.com/upbge-0-3-released/
- https://upbge.org/docs/latest/manual/manual/introduction/deeper_look.html
- https://upbge.org/docs/latest/manual/manual/python_components/introduction.html
- https://upbge.org/docs/latest/manual/manual/logic_nodes/introduction.html
- https://upbge.org/docs/latest/manual/manual/deployment/blender_player.html
- https://blenderartists.org/t/tutorial-how-to-export-games-created-with-upbge-v0-3-using-bpplayer/1382383

**Armory3D**
- https://github.com/armory3d/armory (README, releases, commits, `armory.py`)
- https://github.com/armory3d/armory/wiki/architecture
- https://github.com/armory3d/armory/blob/main/armory/blender/arm/live_patch.py
- https://github.com/armory3d/armory/blob/main/armory/Sources/armory/trait/internal/LivePatch.hx
- https://forums.armory3d.org/t/latest-supported-versions/6051
- https://forums.armory3d.org/t/unofficial-release-notes-for-armory-0-6-full-focus-on-performance/2811
- https://forums.armory3d.org/t/can-i-run-game-in-blender/2994/4
- https://www.blendernation.com/2023/11/18/non-blender-armory3d-release-2023-11-recast-navigation-system-overhaul/

**Range, Blend4Web, Verge3D**
- https://gamefromscratch.com/range-engine-blender-game-engine/
- https://gamefromscratch.com/range-engine-bge-lives-on/
- https://www.patreon.com/rangeengine/posts/some-updates-122959582
- https://blenderartists.org/t/range-engine-1-6-relased-then-took-down-anyone-know-why-or-have-a-copy-to-share/1561855
- https://en.wikipedia.org/wiki/Verge3D
- https://www.blendernation.com/2017/10/06/blend4web-creators-started-new-project-verge3d/

**Bevy workflows**
- https://github.com/rust-adventure/skein (README.md, DESIGN.md, CHANGELOG.md, CHANGELOG-blender.md, `src/lib.rs`, `extension/op_registry_loading.py`, `extension/gltf_export_extension.py`, `extension/property_groups.py`)
- https://bevyskein.dev/
- https://github.com/kaosat-dev/Blenvy (README, `crates/blenvy/README.md`, `tools/blenvy/README.md`, `ronstring_to_reflect_component.rs`, `hot_reload.rs`, Cargo.toml)
- https://lib.rs/crates/bevy_registry_export
- https://docs.rs/bevy_remote
- https://docs.rs/crate/bevy_brp_mcp/0.22.3

**Godot, Unity, Unreal, web**
- https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/available_formats.html
- https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/node_type_customization.html
- https://github.com/godotengine/godot/issues/85446
- https://forum.godotengine.org/t/gltf-import-how-does-one-get-metadata-extras/105711
- https://forum.godotengine.org/t/guide-export-blender-collision-shapes-to-godot-with-gltf-omi-physics/142339
- https://devtalk.blender.org/t/state-of-interoperability-between-godot-and-blender/38559/26
- https://www.khronos.org/blog/project-dogwalk-stress-testing-blender-to-godot-interoperability-with-gltf
- https://godotengine.org/article/godot-showcase-dogwalk/
- https://studio.blender.org/blog/dogwalk-assembling-the-world/
- https://docs.unity3d.com/2017.4/Documentation/Manual/HOWTO-ImportObjectBlender.html
- https://github.com/poly-hammer/BlenderTools
- https://poly-hammer.github.io/BlenderTools/send2ue/
- https://github.com/JoshQuake/BlenderTools
- https://glama.ai/mcp/servers/@radial-hks/MCP-Unreal-Server
- https://github.com/mrdoob/three.js/issues/18445
- https://forum.babylonjs.com/t/how-to-access-extras-property-of-loaded-gltf/22139
- https://forum.playcanvas.com/t/how-to-read-custom-properties/38512
- https://engine.needle.tools/docs/explanation/architecture/technical-overview
- https://engine.needle.tools/docs/blender/components
- https://engine.needle.tools/docs/explanation/core-concepts/component-compiler
- https://needle.tools/eula
- https://needle.tools/pricing

**glTF standards**
- https://github.com/omigroup/gltf-extensions
- https://github.com/omigroup/gltf-extensions/tree/main/extensions/2.0/OMI_physics_body
- https://github.com/KhronosGroup/glTF/pull/2424
- https://github.com/KhronosGroup/glTF/pull/2370
- https://github.com/KhronosGroup/glTF/pull/2293
- https://www.khronos.org/news/press/gltf-interactivity-extension-submitted-for-ratification

**Blender API and live-link infrastructure**
- https://developer.blender.org/docs/release_notes/5.0/python_api/
- https://developer.blender.org/docs/release_notes/4.2/pipeline_assets_io/
- https://docs.blender.org/api/current/bpy.msgbus.html
- https://docs.blender.org/api/current/bpy.app.handlers.html
- https://github.com/ubisoft/mixer
- https://github.com/ubisoft/mixer/blob/master/doc/synchronization.md
- https://github.com/ahujasid/blender-mcp
