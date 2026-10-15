# 06: Runtime architecture options (where the game runs)

**Date:** 2026-10-09
**Question:** If Blender is the editor, where and how should the game run?
**Method:** I checked official docs, release pages and package registries (npm, crates.io, NuGet, GitHub API) for current versions. I also ran local probes against the installed Blender 5.2.2 LTS in headless mode. Anything I could not confirm is marked **(unverified)**.

---

## 1. TL;DR

- **Recommendation:** Run the game as a separate TypeScript runtime built on Three.js (r186, `WebGPURenderer` with automatic WebGL2 fallback). Use Vite for dev and hot reload. Ship to the browser and, for desktop and Steam, in an Electron shell. Blender stays a pure editor and talks to the runtime through a GPL-licensed add-on.
- **Don't run the game inside Blender** (Option A). On my machine, just moving 500 objects and updating the depsgraph took about 7 ms per frame from Python, before any gameplay, physics or drawing. Blender has no runtime physics stepping, and shipping would mean distributing a GPL application of about 937 MB. UPBGE exists because doing this properly requires forking Blender.
- **Gameplay scripting:** strict TypeScript, using TS 7.0 (`tsgo`) for fast type checks. Script modules hot-swap through Vite HMR while the ECS world state is kept. Python is used only for the Blender add-on and pipeline tooling. Luau or WASM sandboxes can wait until modding is needed.
- **Physics:** Rapier (`@dimforge/rapier3d` 0.21, Sept 2026) behind a thin physics adapter. Its JS build is documented as fully cross-platform deterministic and supports snapshots, which helps replays and agent-run tests. JoltPhysics.js is the upgrade path for vehicles, soft bodies and large worlds. Avoid cannon-es and ammo.js, which have had no releases since 2022.
- **Scene model:** a hybrid. You author Unity-style "components on objects" in Blender. At runtime, an ECS (koota, wrapped behind our own `defineComponent` API) holds gameplay data, and a Three.js scene graph handles rendering and transform hierarchy.
- **Interchange:** glTF 2.0 (`.glb`) per asset, plus a JSON scene and prefab manifest that uses stable UUIDs, versioned component data and script references. Blender's exporter already writes custom properties to glTF `extras` (I verified this locally). Track `KHR_physics_rigid_bodies` and `KHR_collision_shapes`, but don't depend on them: both are still at Review Draft. Don't use USD at runtime.
- **Live link:** JSON-RPC-style messages over a localhost WebSocket, with binary frames for bulk data. Blender sends deltas: transforms, components, and per-object `.glb` re-exports when geometry changes. The runtime sends back state, selection, logs and errors. Measured costs are small: 9 µs to serialize a transform delta and 19 µs for a loopback TCP round trip from Blender's Python. A full-scene re-export takes about 0.9 s for 500 objects, so the link must work in deltas.
- **Play-in-editor:** a separate runtime window (Electron or a browser tab) docked next to Blender, started from a Play button in Blender. Rendering the game inside Blender's viewport (frame streaming) is a later experiment, not part of the MVP.
- **Licensing:** Blender's FAQ says an add-on that uses Blender's Python API must be GPL if distributed. A product that "operates outside of Blender" and "uses no Blender source code or API calls" can be licensed however you want. So the add-on is GPL-3.0-or-later and the runtime and protocol are MIT or proprietary, in a separate repo.
- **Runner-up:** Godot 4.7 as the runtime (Option D). It wins on shipping reach and maturity, but it turns the project into a Blender-to-Godot pipeline with two editors. Bevy 0.20 is the best native option, but its breaking releases (about three a year) and Rust compile loops slow a solo developer working with an AI assistant.

---

## 2. Framing: what matters for this team

The team is one developer plus an AI coding assistant. Blender 5.2 LTS is the only editor. So the runtime choice should be ranked mainly on:

1. **How fast the team can build it**: how much engine we must write, and how quickly a code change shows up in the running game.
2. **How well an AI agent can work in the stack**: how much training data exists, how stable the APIs are, and whether type errors are caught before runtime.
3. **How well it pairs with Blender**: live link, play-in-editor, and fidelity of the data format.
4. **Whether games can ship**, how fast they run, and how mature the ecosystem is.

The local toolchain already covers every option: Node 22.12, Rust 1.93.1, .NET 9.0.305, CMake 4.2, Python 3.12, and Blender 5.2.2 LTS with Python 3.13.13.

### Local measurements (Blender 5.2.2 LTS, headless, this machine)

| Probe | Result |
|---|---|
| Blender's bundled Python | 3.13.13 with the GIL enabled. `numpy`, `asyncio`, `socket`, `selectors` and `multiprocessing.shared_memory` are available. `websockets`, `msgpack` and `zmq` are **not** bundled. |
| glTF exporter | Khronos glTF Blender I/O v5.2.40. With `export_extras=True`, object custom properties are written to node `extras`; both JSON strings and floats came through. Other relevant options: `export_pointer_animation` (KHR_animation_pointer), `export_gpu_instances`, Draco. |
| `depsgraph_update_post` handler | Fires on a location change, with `is_updated_transform=True`. |
| Option A proxy: write location and rotation on 501 objects, then `view_layer.update()` | **7.0 ms per frame**, with no rendering, physics or game logic. |
| Full-scene GLB export, 500 small objects | 0.86–0.90 s per export |
| GLB export of one mesh (51k vertices, 102k triangles) | 151 ms warm, 235 ms cold |
| Raw mesh extraction through `foreach_get` (positions and indices, 1.8 MB) | 33 ms |
| JSON delta for one transform | 9 µs. All 500 objects: 3.3 ms and 60 KB. |
| Loopback TCP round trip from Blender's Python (`TCP_NODELAY`) | 19 µs |
| 720p RGBA8 frame from shared memory into numpy | 0.23 ms as a uint8 copy, 3.9 ms as float32. GPU upload could not be measured headless **(unverified)**. |
| Blender install size | 937 MB |

---

## 3. Option A: Run the game inside Blender

**How it would work.** A modal operator registers a window timer (`event_timer_add`) and a `SpaceView3D` draw handler, and moves objects through `bpy` on every tick. The game is then drawn by EEVEE or Workbench in the viewport, or by custom `gpu` module batches. Blender's own `operator_modal_draw.py` template shows the pattern. Community reports describe draw handlers that only refresh on input events, which means you must keep forcing redraws.

**Feasibility and framerate.** Everything goes through Python on Blender's single UI thread, with the GIL enabled. My probe measured 7 ms per frame just to write 501 transforms and run the depsgraph update. That is about 42% of a 60 fps budget, spent before any input handling, AI, physics or viewport rendering. EEVEE is built to be a high-quality viewport and renderer, not a game renderer with a fixed frame budget. Timer events are also coalesced into Blender's event loop, so frame pacing is unreliable. Blender gives you keyboard and mouse events, but no gamepad API.

**Physics.** Blender's rigid body world is a cached, timeline-driven simulation. Results are cached as the timeline plays, can be baked, and only exist for frames inside the cache range. It is not an API you can step interactively with game inputs. You would have to load a physics library (such as Rapier's Python bindings) into Blender's Python yourself.

**Shipping.** You cannot ship "the game" without shipping Blender. That means 937 MB, the GPL, and Blender's whole UI around your game. Blender's FAQ says Python scripts that call the Blender Python API must be GPL if you distribute them, so all gameplay code would be GPL. The old Blender Game Engine was removed from Blender in 2.8. **UPBGE** keeps it alive as a full Blender fork: its 0.50 release (Jan 2026) is based on Blender 5.0.1 and adds GPU skinning. The existence of a fork shows that a real in-Blender game loop needs C-level changes. **Armory3D** (zlib license, active, last push Sep 2026) is the clearest example of the alternative: it uses Blender only as the editor and ships a separate Haxe/Kha runtime. In other words, it is an Option B/C design.

**Verdict.** Live link and play-in-editor would be perfect, but the performance ceiling, physics support, shipping and licensing all fail. Use Blender's process only for editing, plus small previews at most, such as running a gameplay script's math on a few objects.

---

## 4. Option B: A native runtime

### B1. Rust (Bevy, Fyrox, or custom on wgpu)

- **Bevy 0.20** was released on 2026-10-08. Releases have come every three to five months, each with breaking changes: 0.17 (Sep 2025), 0.18 (Jan 2026), 0.19 (Jun 2026), 0.20 (Oct 2026). BSN, the new scene format, landed in 0.19, and its syntax is "largely nailed down" in 0.20. A hot-reloadable `.bsn` asset format is still under "What's Next". There is still no official editor; Feathers widgets are described as pieces of a future editor. Asset hot reload works. Rust code hot patching (Dioxus *subsecond*) has been an opt-in experiment since 0.17, limited to ECS systems.
- **Bevy plus Blender is an established community pattern.** **Skein** (`bevy_skein` 0.6.0, Sep 2026, active) fetches Bevy's reflected type registry into Blender, lets you attach components to objects, meshes and materials, and writes them into glTF extras. Bevy then inserts the components when it spawns the scene. The older **Blenvy** describes itself as "Alpha 1" (last push Mar 2026). **Bevy Remote Protocol (BRP)** is a JSON-RPC 2.0 interface for querying and mutating a running app (`world.query`, `world.get_components`, `world.insert_components`). **`bevy_brp_mcp`** exposes BRP to AI agents over MCP. This is good prior art for our own design.
- **Fyrox 1.0** (March 2026; crate 1.0.1) is stable, but it is built around its own editor. Using Blender as the only editor would mean working against that.
- **Custom engine on wgpu 30** gives maximum control, but it is the slowest way to reach a first game.
- **Fit:** the performance ceiling is excellent, it can ship native and WASM builds, and Rust's types help agents. The costs are compile-and-restart loops (hot patching is experimental), API churn that leaves AI training data stale (each Bevy release breaks code written for the previous one), and much less gameplay code in the wild than TS or C#.

### B2. C++ (raylib, sokol, bgfx, custom)

raylib 6.0 (April 2026) is excellent for small games, but it is a library: you would build scene loading, ECS, an asset pipeline and the editor bridge yourself. sokol and bgfx are rendering layers. Hot reload needs DLL swapping or embedded scripting. The ceiling is the highest of all options. But C++ means the most code to write and review, and AI-generated memory-safety bugs are expensive to debug. This option is not recommended for a solo developer.

### B3. C#/.NET (Stride, MonoGame, Silk.NET)

Stride 4.4 is a full 3D engine with its own editor. MonoGame 3.8.x is a 2D-first framework. Silk.NET 2.23 is bindings only. AI agents handle C# well, and .NET 9 is installed. But .NET hot reload cannot apply some kinds of edits (**unverified** for 2026), web export is weak, and none of these has a Blender-centric workflow.

**Verdict on B:** Bevy is the only native choice worth keeping as a fallback. It is also the right target if this project later needs a native high-performance runtime. Because our data format (Section 9) is engine-agnostic, a later Bevy port stays possible.

---

## 5. Option C: A web/TypeScript runtime that loads glTF

### Engines (versions checked on npm, 2026-10-09)

| Library | Version | Weekly npm downloads | Notes |
|---|---|---|---|
| **three** | 0.186.1 (r186, 2026-09-24) | ~20.1 M | Rendering library, not an engine. Monthly releases. `WebGPURenderer` falls back to WebGL2 automatically. TSL node materials. Occasional API renames (r183 renamed `PostProcessing` to `RenderPipeline`). |
| **@babylonjs/core** | 9.30.0 (9.0 announced 2026-03-26) | ~0.35 M | A full engine: physics plugin (`@babylonjs/havok`, MIT), inspector, audio, GUI, Frame Graph, clustered lighting on WebGPU and WebGL2, partial KHR_interactivity import. |
| **playcanvas** (engine) | 2.23.1 | ~88 k | MIT engine. Its main workflow centers on PlayCanvas's own hosted editor. |

Three.js has about 57 times Babylon's weekly downloads. It also has the largest body of public example code, and that is what AI assistants learn from. Since we are building our own engine layer (ECS, components, live link), Three.js's lack of opinions is an advantage. Babylon is the credible alternative: it bundles more features, and its team has historically prioritized backward compatibility, which reduces stale-API mistakes by agents **(unverified as a 2026 policy)**. If the team prefers "batteries included" over a bigger ecosystem, Babylon is a reasonable swap. The rest of the architecture would stay the same.

### WebGPU in 2026 (gpuweb implementation status page, edited 2026-10-02)

| Browser | Windows | macOS | Linux | Android / iOS |
|---|---|---|---|---|
| Chrome/Edge (Dawn) | Shipped (113) | Shipped (113) | Intel Gen12+ (144); NVIDIA on Wayland (147); others behind a flag | Android 12+ ARM/Qualcomm/Intel (121) |
| Firefox (wgpu) | Shipped (141) | Apple Silicon (145 on macOS 26; 147 on all versions) | Nightly only; expected in 2026 | Behind a flag |
| Safari | n/a | Shipped (26) | n/a | iOS/iPadOS 26 shipped |

So WebGPU is safe on every desktop target that matters for a Windows-first developer, and Three.js falls back to WebGL2 where it is missing.

### Desktop shipping

- **Electron 44** bundles its own Chromium, so WebGPU behaves the same as Chrome on each OS (on Linux, only supported GPUs), and you have full control over COOP/COEP headers. Those headers are required for `SharedArrayBuffer`, and therefore for multithreaded WASM builds such as Jolt's multithread variants. The cost is a larger download.
- **Tauri 2.12** uses the system webview: WebView2 (Chromium) on Windows, WKWebView on macOS, WebKitGTK on Linux. Downloads are much smaller, but WebGPU availability depends on each platform's webview. A 2023 Tauri issue needed a browser flag on WebView2. Current status per platform is **unverified**, and Linux WebKitGTK is the weakest.
- **Precedent:** CrossCode shipped on Steam on NW.js, and Phaser games ship on Steam through Electron, using Greenworks or similar Steam bindings. Note that `steamworks.js` last released in Aug 2024, so check Steam integration before relying on it.
- **Consoles:** no practical route for an Electron or browser build. This is the main shipping limit of Option C.

### Hot reload and iteration

Vite 8's HMR API (`import.meta.hot.accept`, `dispose`, and `data`, which persists across updates) lets each gameplay system module swap itself while the ECS world, physics world and renderer stay alive. A typical edit-to-visible loop is well under a second. No Rust or C++ option comes close without experimental tooling. TypeScript 7 (the native `tsgo` compiler, 7.0.2) keeps full-project type checks fast enough to run on every agent edit.

### Performance ceiling

JS runs gameplay on one main thread, and GC pauses must be managed with pooling. Physics runs in WASM at near-native speed. WebGPU removes most of WebGL's draw-call overhead. Realistically, Option C fits indie and "AA-lite" 3D: stylized, mid-sized levels, hundreds to low thousands of dynamic bodies, and instanced rendering. It does not fit large open worlds or high-end console-class rendering. Workers plus `SharedArrayBuffer` (allowed in Electron) are the scaling path.

**Verdict on C:** the fastest to build, the best AI fit, the best hot reload, and the easiest live link (WebSocket is built into the runtime, and the dev server can host the link endpoint). Shipping covers web and desktop but not consoles.

---

## 6. Option D: Godot (or another engine) as the runtime, Blender as the only editor

- **Status:** Godot 4.7.2-stable (2026-08-18). 4.6 (Jan 2026) made Jolt the default 3D physics for new projects and introduced LibGodot, which lets other applications embed the engine. 4.4 added game embedding: the game still runs as a separate process, shown in a floating or embedded window. This works on Windows, Linux and Android, but not macOS.
- **Blender bridge:** Godot imports glTF `extras` as node, mesh and material metadata (`get_meta("extras")`). It also imports the OMI_physics_body extension through `GLTFPhysicsBody`. A community Blender add-on writes OMI physics shapes, because Blender's exporter does not.
- **Limits for this project:** "Blender as the only editor" is not really achievable. Godot's editor is still needed for import settings, UI, scripts, export presets and debugging, so you live in two editors and reimport on every change. Live-pushing Blender edits into a running Godot game needs a custom plugin with a WebSocket server, written in GDScript or C#. C# projects still can't export to the web in 4.7. AI agents often mix up Godot 3 and Godot 4 APIs. Most importantly, the product becomes a Blender-to-Godot pipeline rather than "a game engine on top of Blender."
- **Fit:** the most mature and widest shipping (desktop, mobile and web for GDScript; consoles through third parties), with good hot reload of GDScript. **Choose D if shipping a game quickly matters more than owning the engine.**

---

## 7. Weighted comparison

Scores run from 1 (poor) to 5 (excellent). Weights reflect the solo-plus-AI context.

| Criterion (weight) | A: In Blender | B1: Bevy (Rust) | B2: C++ custom | B3: C#/.NET | **C: TS + Three.js** | D: Godot runtime |
|---|---|---|---|---|---|---|
| Dev velocity, solo + AI (25%) | 3 | 2 | 1 | 3 | **5** | 4 |
| AI-assistant fit: training data, types, API stability (15%) | 3 | 3 | 3 | 4 | **5** | 3 |
| Hot reload (15%) | 4 | 3 | 2 | 3 | **5** | 4 |
| Live link and play-in-editor with Blender (10%) | 5 | 4 | 3 | 3 | **5** | 3 |
| Export and shipping story (10%) | 1 | 4 | 4 | 3 | 4 | **5** |
| Performance ceiling (10%) | 1 | **5** | **5** | 4 | 3 | 4 |
| Ecosystem maturity for this use (10%) | 2 | 3 | 3 | 2 | 4 | **5** |
| Licensing and control (5%) | 1 | 5 | 5 | 5 | 5 | 5 |
| **Weighted total** | **2.75** | **3.25** | **2.75** | **3.25** | **4.60** | **4.00** |

Two things would change the ranking. If console shipping becomes a hard requirement, D (or B1) moves ahead of C. If the team decides it wants to ship games rather than build an engine, D wins.

---

## 8. Gameplay scripting language

| Language | Pros | Cons | AI-assistant angle |
|---|---|---|---|
| **TypeScript** (recommended) | Same language as the engine, so no FFI layer. Vite HMR. Huge ecosystem. Strict types plus zod schemas can describe components once and drive both runtime validation and the Blender UI. | GC; one main thread. | TS became GitHub's most used language in Aug 2025. GitHub links this to AI-assisted coding and cites a study finding 94% of LLM compile errors were type-check failures, the kind a compiler catches. The type checker gives an agent a cheap correctness signal. |
| Python | Familiar to Blender users; already used for the add-on. | No good embeddable runtime for browsers or games: Pyodide is heavy, CPython embedding is large and slow for per-frame logic. It would split the codebase across two languages. | Plenty of training data, but dynamic typing gives agents a weak correctness signal. |
| Lua / Luau | Small, fast, sandboxable; Luau (MIT, C++) adds gradual types and has `luau-analyze` and an LSP. A WASM build (`luau-web` 1.5) exists. | It is a second language and needs bindings to maintain. Luau's new type solver has had memory problems in Roblox reports. | Good Roblox-era training data, but mostly Roblox APIs. |
| Rust | Fastest, safe. | Compile loop. Hot reload is experimental. | Compiler feedback is excellent, but churn in engine APIs hurts. |
| C# | Strong types; agents handle it very well. | Needs a .NET runtime and is weak on the web. | Excellent. |
| WASM (any language) | Sandboxed, portable modding. | Tooling and debugging are heavier, and calls across the WASM boundary add overhead. | Depends on the source language. |

**Decision:** TypeScript for engine and gameplay. Python for the Blender add-on, exporters and batch tools. Keep a seam for a sandboxed modding language (Luau or WASM) after v1, if user-generated content becomes a goal.

---

## 9. Physics engines

| Engine | Status (Oct 2026) | Fit |
|---|---|---|
| **Rapier** | Rust `rapier3d` 0.36.0 (2026-09-25). JS `@dimforge/rapier3d(-compat/-simd-compat)` 0.21.0, same week. The rapier.js code moved into the main monorepo in Jul 2026. Apache-2.0. | **Primary.** First-class TypeScript API. Documented as "fully cross-platform deterministic" in WASM/JS, with `world.takeSnapshot()`. Has a character controller (per docs navigation). Very widely used: about 10 M weekly downloads of the compat package. |
| **Jolt** | C++ 5.6.0 (2026-07-11; adds GPU compute and hair). `jolt-physics` (JoltPhysics.js) 1.1.0 (2026-07-11). Builds: `wasm`, `wasm-compat`, `asm` and multithread variants. MIT. Godot's default 3D physics engine. | **Upgrade path.** More complete (vehicles, soft bodies, double precision), but memory is managed manually (`Jolt.destroy`), and multithread builds need cross-origin isolation. |
| **Havok** (`@babylonjs/havok` 1.3.14, MIT) | Babylon's physics plugin. | Only if we switch to Babylon. |
| **PhysX 5** | `physx-js-webidl` 2.8.0 (Sep 2026) binds PhysX 5.11. | Capable but large, with a verbose binding API. Not needed. |
| **Bullet / Ammo.js** | `ammo.js` 0.0.10 (2022). | Stale. Avoid. |
| **cannon-es** | 0.20.0 (Aug 2022). | Unmaintained. Avoid for new work. |
| **Box3D** (Erin Catto) | New C engine, MIT, alpha, released in Jul 2026; repo active. | Promising, but there is no JS/WASM build yet **(unverified)**. Watch it. |

**Decision:** put a `PhysicsBackend` interface between ECS and physics: bodies, colliders, queries, character controller, step, snapshot. Implement Rapier first. Physics authoring data in Blender (body type, mass, collider shape) should map to `KHR_physics_rigid_bodies` and `KHR_collision_shapes` field names where possible, so the data stays portable.

---

## 10. ECS vs scene graph vs hybrid

- **Pure scene graph** (Three.js `Object3D` with behaviors on nodes, like Godot): simplest for transform hierarchies, but data and logic get mixed together, and it is hard to serialize, diff and network.
- **Pure ECS**: excellent for data-oriented systems, serialization and agent-readable state dumps, but awkward for hierarchies and for third-party Three.js code.
- **Hybrid (recommended):**
  - In Blender, objects carry **components** (Unity-style), edited in a schema-driven N-panel.
  - At runtime, every Blender object becomes an **entity** with components in the ECS. A render-sync system mirrors `Transform` and `Renderable` onto Three.js objects, and Three.js keeps the parent/child hierarchy.
  - Gameplay lives in **systems**: TS modules, each hot-reloadable.

| Library | Version and activity | Notes |
|---|---|---|
| **koota** (pmndrs) | 0.6.6 (Apr 2026); ~43 k weekly downloads; repo active | TS-first. Traits can be SoA or AoS. Relations, `Not`/`Or` query filters, change events, optional React bindings. **Recommended**, behind our own API. |
| bitECS | 0.4.0 (Dec 2025); ~14 k weekly | Very fast SoA. 0.4 reworked the API (a legacy module keeps 0.3), so agents may mix the two versions. A good alternative if profiling demands it. |
| miniplex | 2.0.0 (Jul 2023) | Inactive. Avoid. |
| Becsy | 0.16.1 (Oct 2026); ~1.3 k weekly | Multithreading-oriented, but little adoption. |
| Bevy ECS | Ships with Bevy 0.20 | Only relevant under B1. |
| flecs | v4.1.6 (Jun 2026) | Excellent C/C++ ECS. Only relevant for a native runtime. |

We expose our own `defineComponent(name, zodSchema)`, `defineSystem`, `query` and `spawnPrefab`. Agents then learn one small, documented API of ours, and the backing library can be swapped.

---

## 11. Interchange format: Blender to runtime

| Option | Carries meshes and animation | Carries game data (components, prefabs, script refs) | Runtime cost in JS | Verdict |
|---|---|---|---|---|
| **glTF 2.0 + extras** | Excellent; Blender's exporter is first-party (Khronos I/O v5.2.40). | Yes, through `extras`: custom properties go into `extras` (verified locally). Free-form data, no schema. | Native loaders everywhere (`GLTFLoader`, glTF-Transform 4.5.1 for optimization). | **Use for assets.** |
| glTF + standard extensions | — | Physics: `KHR_physics_rigid_bodies` and `KHR_collision_shapes` are **Review Draft**. `OMI_physics_body` is imported by Godot. `MSFT_rigid_bodies` is a frozen predecessor. Blender doesn't export any of them natively; there is a GPL add-on for KHR physics. `KHR_interactivity` is **ratified** (it is a node-graph logic format, not a replacement for scripts). | — | Mirror physics data into a KHR-shaped extension later. Don't block on it. |
| USD | Rich, layered. Blender exports custom properties to `userProperties`. | Good for composition. | No mature JS/WASM USD runtime; heavy. | Not for the runtime. Possibly a future interop export. |
| **Custom JSON manifest + glTF meshes** | Defers to glTF | Full control: stable IDs, schema versions, prefab overrides, script refs, scene settings. Diffable and easy for agents to edit. | Trivial | **Use for scenes and prefabs.** |

**Decision: two layers.**

1. **Assets:** `.glb` files, one per Blender collection or prefab, or one per object while a live edit is in progress. Components are also mirrored into `extras.gs` so a glTF file is self-describing.
2. **Scene and prefab manifest** (`*.scene.json`): entity UUIDs (a `gs_id` custom property the add-on assigns at creation, because Blender names are not stable IDs), parent links, asset references (`assets/crate.glb#node`), component data validated against the component schema version, prefab instances with overrides, and script references (`"script": "src/systems/door.ts#DoorSystem"`).

---

## 12. Live link and play-in-editor

### Topology

```
Blender 5.2 (editor)                               Runtime (separate process)
+------------------------------+                   +-------------------------------------+
| GPL add-on "gs_link"         |  ws://127.0.0.1   | Vite dev server + link hub          |
|  - depsgraph_update_post ->  |<=================>|  (Node, MIT)                        |
|    diff by gs_id             |  JSON-RPC text    |        ^                            |
|  - socket I/O thread + queue |  + binary frames  |        | same WS protocol           |
|  - bpy.app.timers drains     |                   |  Game window (Electron or browser)  |
|    queue on main thread      |                   |  Three.js + ECS + Rapier            |
|  - exports changed .glb      |---- files ------->|  assets/ watched; HMR for src/      |
+------------------------------+                   +-------------------------------------+
                                                        ^
                       MCP server (thin adapter) -------+  AI agent: inspect, set, step, screenshot
```

### Protocol sketch (v0)

- Transport: a WebSocket on localhost, with a `hello` handshake carrying protocol version and project ID. The hub listens; Blender and the game window are clients. All of `bpy` must be called on Blender's main thread, so the add-on does socket I/O on a background thread and drains a queue every ~20 ms from `bpy.app.timers`. Blender doesn't bundle `websockets`, so either vendor the wheel (Blender extensions support bundled PyPI wheels per platform through `blender_manifest.toml`) or write a minimal client on the bundled `socket` and `selectors` modules.
- Blender to runtime: `scene.open`, `entity.upsert` / `entity.remove`, `transform.batch` (binary Float32 matrices keyed by `gs_id`), `component.set`, `asset.reload` (path and content hash), `play.start` / `stop` / `pause` / `step`, `select`.
- Runtime to Blender: `state.snapshot` (selected entities' live component values, used for "apply play-mode values back to Blender"), `select`, `log`, `error` (with stack trace and source location), `stats` (fps, frame time, body count).
- Same messages for agents: an MCP adapter exposes `inspect_entity`, `query`, `set_component`, `step`, `screenshot` and `tail_logs` over the same hub. This copies Bevy's BRP and `bevy_brp_mcp` pattern. Agents assist; nothing gets written back into the `.blend` file without the developer's action in Blender.

### Latency budget (from the local probes)

| Edit type | Path | Expected cost |
|---|---|---|
| Move, rotate or scale an object | depsgraph handler → delta (9 µs) → WS (loopback ~19 µs) → apply next frame | Dominated by frame time: **one to two frames (~16–33 ms)**. Throttle to about 30 Hz while dragging and send only changed `gs_id`s. A full 500-object batch is 3.3 ms and 60 KB, so a full batch is fine as a fallback. |
| Change a component value | N-panel → `component.set` | Same as above |
| Geometry or material edit | Debounced (~250 ms) per-object `.glb` export (150–235 ms for a 51k-vertex mesh) → `asset.reload` | **~0.3–0.6 s** |
| Live edit-mode mesh streaming (later) | `foreach_get` → binary frame (33 ms for 1.8 MB) | ~50 ms; positions and indices only, more work for normals and UVs |
| Full scene re-export | 0.86–0.90 s for 500 objects | Only on Play or scene open; never per edit |
| Code change | Vite HMR on the system module | Sub-second, with ECS state preserved |

### Play-in-editor options

1. **A separate window, docked next to Blender (MVP).** Play in Blender starts or focuses the Electron runtime, sends `scene.open`, then streams deltas. A crash can't take Blender down. Godot 4.4 also keeps the game in a separate process for the same reason.
2. **Window reparenting into Blender's window** (Win32 `SetParent`, the same idea as Godot's embedding). Possible on Windows, but fragile with Blender's GHOST window layer and DPI handling **(unverified)**. Defer.
3. **Frame streaming into a Blender viewport** (runtime renders offscreen into shared memory; a Blender draw handler uploads a `GPUTexture`). The CPU side is cheap (0.23 ms for a uint8 copy, 3.9 ms for float32 conversion at 720p), but GPU upload cost and input forwarding are **unverified**. It adds latency and puts load on Blender's UI thread. Treat it as a v1-plus experiment for "preview in viewport", not the main play mode.

---

## 13. Licensing: the GPL boundary

From Blender's official license page and FAQ:

- "What you create with Blender is your sole property." The output of Blender, in the form of `.blend` files, "is considered program output, and the sole copyright of the user." Exported glTF and JSON are output too.
- Python scripts "have to be made available licensed as GNU GPL as well, if they use Blender Python API calls" when shared or published.
- On add-ons that work with a separate product: "You have full freedom to license your software product however you wish if and only if: It operates outside of Blender. Uses no Blender source code or API calls (including Python API). Produces data for Blender to operate on. Executes Blender to read and operate on the data."

**What this means for the architecture:**

- The **Blender add-on** (`gs_link` plus the exporters) uses `bpy`, so it is GPL-3.0-or-later when distributed. Keep it in its own repo or package.
- The **runtime, link hub, protocol spec and game code** run in separate processes. They read exported files and talk over a socket, use no Blender code, and never import `bpy`, so they can be MIT, Apache or proprietary. This is the "operates outside of Blender" pattern the FAQ describes.
- **Rules:** never copy Blender source or shader code into the runtime. Never ship Blender with a game. Keep the protocol defined in the runtime repo, and have the add-on implement it.
- This is not legal advice. Get a lawyer to review it before a commercial release.

---

## 14. Recommendation and phased plan

### Recommended stack

| Layer | Choice | Why it beats the alternatives for this team |
|---|---|---|
| Runtime | **TypeScript + Three.js r186** (`WebGPURenderer` with WebGL2 fallback), Vite 8 for dev | Fastest to build. Best AI training-data coverage. Sub-second hot reload. Live link uses built-in WebSocket. |
| Desktop shell | **Electron** (Tauri as an optional slim build once its WebGPU support is checked per OS) | The same bundled Chromium on every OS. On Linux, WebGPU is limited to supported GPUs and other machines use the WebGL2 fallback. Control over COOP/COEP for threads. |
| Scripting | **Strict TS** systems and components; `tsgo` type checks; zod component schemas | One language from engine to gameplay. Types give agents a cheap correctness signal. Schemas drive the Blender UI. |
| Physics | **Rapier** behind `PhysicsBackend`; Jolt as an optional second backend | Deterministic, supports snapshots, TS-native API, actively released. |
| Scene model | **Hybrid**: components in Blender, ECS (koota behind our API) for gameplay, Three.js graph for rendering | Familiar Unity-style authoring, data-oriented runtime, and agent-readable state. |
| Interchange | **glTF 2.0 `.glb` assets + `*.scene.json` manifests**, stable `gs_id` UUIDs, mirrored `extras.gs`; KHR physics fields later | Uses Blender's first-party exporter. Diffable data that agents can edit. Stays portable for a later native or Godot target. |
| Live link | **Localhost WebSocket**, JSON-RPC-style, binary transform batches; Node hub; MCP adapter | Measured costs are microseconds per message. The same channel serves the developer, the debugger and AI agents. |
| Licensing | GPL add-on ↔ MIT runtime, separate repos | Follows Blender's FAQ. |

### Phased build plan

**Phase 0 (spike, about 1–2 weeks).** Exit test: move a cube in Blender and see it move in the game window in under 50 ms.
- Add-on: assign `gs_id` values, add an "Export scene" command (glb plus manifest), and implement a WS client with a timer-drained queue.
- Runtime: Vite and Three.js load the manifest and glbs, plus the link hub. Apply `transform.batch`.
- Confirm on this machine: WebGPU in Electron, the Rapier compat build, and the HMR pattern for a system module.

**MVP (about 6–8 weeks after Phase 0).** Exit test: build a small 3D game (for example, a physics puzzle room) entirely in Blender plus TS, and ship it to itch.io (web) and as a Windows Electron build.
- Component registry: TS `defineComponent` with zod → JSON Schema → generated Blender N-panel UI, with values stored in custom properties and the manifest.
- ECS (koota wrapper), render sync, input (keyboard, mouse, gamepad API), camera, and Rapier bodies and colliders authored in Blender.
- Play, Stop and Pause from Blender. Live transform and component edits. Per-object glb re-export on geometry change. Code HMR.
- Runtime errors shown in Blender (an info panel and the system console) with file and line.
- Headless mode: the simulation runs in Node with no rendering, for unit tests and agent-run checks; Rapier determinism makes them repeatable.

**v0.5 (about +2–3 months).** Exit test: a second, larger game reuses prefabs, and an agent can diagnose a gameplay bug through MCP alone.
- Prefabs: Blender collections and collection instances → prefab glb plus overrides in the manifest.
- Asset pipeline: glTF-Transform (meshopt or Draco, KTX2), content hashing, incremental export.
- Animation: Blender actions and NLA → glTF animations, plus an animation state component. Audio. HTML/CSS UI overlay. Save and load through ECS serialization.
- Pulling runtime state back: an inspector in Blender and "apply play-mode values".
- MCP adapter: inspect, query, set, step, screenshot, logs.

**v1 (about +3 months).** Exit test: a third party can install the add-on and the runtime template and ship a game.
- Physics data mirrored to `KHR_physics_rigid_bodies` and `KHR_collision_shapes` (tracking the spec). Optional Jolt backend. Navmesh (`recast-navigation` 0.43).
- Profiler overlay, a build command (web, Electron for Windows, macOS and Linux), a checked Steam integration path, project templates and docs.
- Experiments: embedded viewport preview (frame streaming), live edit-mode mesh streaming, Tauri slim builds.

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| Three.js API churn (monthly releases, occasional renames) | Pin the version. Upgrade on purpose once a quarter. Keep Three.js behind a render-sync layer. Give agents the pinned docs. |
| Web performance ceiling | Instancing and batching, WASM physics, and workers with `SharedArrayBuffer` in Electron. The engine-agnostic manifest keeps a native (Bevy) runtime possible later. |
| No consoles | Accept for v1. If consoles become a requirement, revisit D (Godot) or B1 (Bevy) using the same Blender add-on and data format. |
| Blender Python API changes across versions (5.0 had breaking changes) | Target the 5.2 LTS only. Keep add-on logic thin and test it headless (`blender -b --python`). |
| Agents overreaching | Agents act through MCP tools and code diffs. Writing to the `.blend` file and the manifest stays a developer action. |

### Why this beats the alternatives for this team

Option A fails on frame budget, physics, shipping and the GPL. Bevy's breaking releases, which have come every three to five months, cost migration time and leave the AI's knowledge stale. C++ and C# mean far more engine code, or working against an engine's own editor. Godot makes Blender one editor out of two. The TypeScript runtime gives the shortest path from a Blender edit, or an agent's code edit, to a type-checked, hot-reloaded result on screen. Its data formats keep the native and Godot options open.

---

## Sources

**Blender and licensing**
- https://www.blender.org/about/license/
- https://www.blender.org/support/faq/
- https://docs.blender.org/manual/en/latest/advanced/extensions/python_wheels.html
- https://docs.blender.org/manual/en/latest/addons/scene_gltf2.html
- https://projects.blender.org/blender/blender/pulls/118938
- https://projects.blender.org/archive/blender-archive/src/commit/05f3f11d553e97f27fc88b1dada9387acdb768a6/release/scripts/templates_py/operator_modal_draw.py
- https://blenderartists.org/t/how-to-do-modal-timer-with-draw-handler/664104
- https://docs.blender.org/manual/pt/5.0/physics/rigid_body/world.html
- https://github.com/LuxCoreRender/BlendLuxCore/issues/1049
- https://github.com/UPBGE/upbge/releases
- https://alternativeto.net/software/upbge/news
- https://armory3d.org/engine
- https://github.com/armory3d/armory

**Native runtimes**
- https://bevy.org/news/
- https://bevy.org/news/bevy-0-20/
- https://gamefromscratch.com/bevy-0-17-released/
- https://bevyskein.dev/
- https://github.com/rust-adventure/skein
- https://github.com/kaosat-dev/Blenvy
- https://docs.rs/bevy_remote
- https://docs.rs/crate/bevy_brp_mcp/0.22.3
- https://fyrox.rs/blog/
- https://gamefromscratch.com/after-7-years-of-development-fyrox-1-0-is-here/
- https://crates.io/crates/bevy, https://crates.io/crates/fyrox, https://crates.io/crates/wgpu, https://crates.io/crates/rapier3d, https://crates.io/crates/bevy_skein (crates.io API)
- https://en.wikipedia.org/wiki/Raylib
- https://www.nuget.org/packages/Stride.Engine, https://www.nuget.org/packages/MonoGame.Framework.DesktopGL, https://www.nuget.org/packages/Silk.NET (NuGet API)

**Web runtime**
- https://github.com/mrdoob/three.js/releases
- https://threejs.org/manual/en/webgpurenderer
- https://app.cinevva.com/news/2026-03-18-threejs-r183-render-pipeline
- https://blogs.windows.com/windowsdeveloper/2026/03/26/announcing-babylon-js-9-0/
- https://forum.babylonjs.com/t/current-support-of-gltf-interactivity/62621
- https://github.com/playcanvas/engine
- https://github.com/gpuweb/gpuweb/wiki/Implementation-Status
- https://v2.tauri.app/reference/webview-versions/
- https://github.com/tauri-apps/tauri/issues/6381
- https://vite.dev/guide/api-hmr
- https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/SharedArrayBuffer
- https://phaser.io/news/2025/03/publishing-web-games-on-steam-with-electron
- https://github.com/greenheartgames/greenworks
- https://api.npmjs.org/downloads/point/last-week/ (npm download counts for three, @babylonjs/core, playcanvas, rapier, jolt-physics, cannon-es, koota, bitecs, miniplex, becsy)
- https://www.npmjs.com/package/three, /electron, /@tauri-apps/cli, /vite, /typescript, /luau-web, /@gltf-transform/core, /recast-navigation, /steamworks.js (npm registry)

**Godot**
- https://github.com/godotengine/godot/releases
- https://godotengine.org/releases/4.6/
- https://godotengine.org/releases/4.4
- https://docs.godotengine.org/uk/4.x/tutorials/editor/game_embedding.html
- https://forum.godotengine.org/t/gltf-import-how-does-one-get-metadata-extras/105711
- https://docs.godotengine.org/en/stable/classes/class_gltfphysicsbody.html
- https://forum.godotengine.org/t/guide-export-blender-collision-shapes-to-godot-with-gltf-omi-physics/142339
- https://forum.godotengine.org/t/is-there-an-update-on-exporting-c-projects-to-web/128821

**Interchange, physics and ECS**
- https://github.com/KhronosGroup/glTF/blob/main/extensions/README.md
- https://forum.babylonjs.com/t/gltf-physics-babylon/43923
- https://github.com/eoineoineoin/glTF_Physics_Blender_Exporter
- https://rapier.rs/docs/user_guides/javascript/getting_started_js
- https://rapier.rs/docs/user_guides/javascript/determinism
- https://github.com/jrouwe/JoltPhysics.js
- https://github.com/jrouwe/JoltPhysics/releases
- https://www.npmjs.com/package/physx-js-webidl, /cannon-es, /ammo.js, /@babylonjs/havok
- https://phoronix.com/news/Box3D-Open-Source-3D-Physics
- https://github.com/erincatto/box3d
- https://www.npmjs.com/package/koota, /bitecs, /miniplex, /@lastolivegames/becsy (npm registry)
- https://bestofjs.org/projects/koota
- https://gittrend.io/repo/NateTheGreatt/bitECS
- https://github.com/SanderMertens/flecs

**Languages and AI**
- https://github.blog/news-insights/octoverse/octoverse-a-new-developer-joins-github-every-second-as-ai-leads-typescript-to-1/
- https://github.com/luau-lang/luau
- https://devforum.roblox.com/t/new-luau-type-solver-causing-extremely-high-memory-usage/4110630
