# 00: Synthesis and architecture decisions

> **Update, 2026-10-09:** the *editor* half of this design has been superseded by [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md). GameStudio Editor is now a thin, layered distribution of Blender: a pinned LTS, a small seam patch queue, an out-of-tree native module and a Python IDE shell. The runtime, protocol and AI decisions below still stand. Five later reports back that change:
>
> | Report | Topic |
> |---|---|
> | [08](08-blender-fork-prior-art.md) | Fork prior art and legal constraints |
> | [09](09-embedding-game-view.md) | Embedding the Game View |
> | [10](10-blender-source-native-seams.md) | Native seams, from the 5.2.2 source |
> | [11](11-fork-release-engineering.md) | Release engineering |
> | [12](12-blender-source-customization.md) | Python-side customization, from the 5.2.2 source |

*Date: 2026-10-09. Sources: the seven research reports in this folder. Each was written by a separate research agent working in parallel, and several of them verified claims against the Blender 5.2.2 LTS installed on this machine. This document turns their findings into the decisions GameStudio is built on.*

| # | Report | Question it answers |
|---|---|---|
| 01 | [Unity](01-unity.md) | How does a component-based engine with an editor/runtime split handle identity, prefabs, serialization and baking? |
| 02 | [Unreal](02-unreal.md) | What does a reflection-driven engine get right, and what is Epic replacing in UE6? |
| 03 | [Godot](03-godot.md) | How do scenes, nodes, text formats, the servers split and the editor-as-app work, and how does Godot import from Blender? |
| 04 | [Blender internals](04-blender-internals.md) | What can Blender's Python API do, and where are the hard limits? |
| 05 | [Blender game prior art](05-blender-game-prior-art.md) | Why did the BGE die? What do UPBGE, Armory, Skein, Blenvy and Needle teach us? |
| 06 | [Runtime options](06-runtime-options.md) | If Blender is the editor, where should the game run? |
| 07 | [AI agents](07-ai-agents.md) | How do we add AI that helps without taking over? |

---

## 1. The ten findings that shaped the design

1. **Every attempt to run the game inside Blender failed or forked Blender.**
   - The BGE was removed in 2.80. UPBGE is a full fork, and one developer does nearly all of its game work.
   - Armory dropped its in-viewport player. Blender's core developers have said in writing there is "no current plan to add back a game engine". (05)
   - Measured on this machine, moving 500 objects from Python costs about 7 ms per frame before any game logic runs, and Blender's rigid-body simulation can't be stepped live. (06)
2. **The model that works is a Blender add-on that exports glTF to a separate runtime process.** Armory, Needle, Verge3D, Skein, Godot's `.blend` import and Blender Studio's own DOGWALK project all work this way. (05)
3. **The runtime's type registry should be the single source of truth for components**, and the editor UI is generated from it. Skein, Needle and UPBGE's `args` all do this. Blenvy's lock-step versioning is the counter-example. (05)
4. **Stable IDs from day one.** Unity keys everything by GUID and fileID. Godot spent years retrofitting UIDs. Names are not identity: GLTFLoader even rewrites them. (01, 03, 05)
5. **An authoring/runtime split with a one-way "bake" works well.** Unity DOTS Baking is the clearest example. Blender is purely authoring, and the export step is our baker. (01)
6. **One schema drives everything.** Unreal's `UPROPERTY` drives the Details panel, serialization, GC, Blueprints, networking and, in 5.8, AI tool schemas. Our component schema drives the Blender panels, the scene format, runtime validation and the AI tool arguments. (02)
7. **Blender's API has sharp, verified edges.** (04)
   - `bpy` is not thread-safe, and timers never fire in `--background`.
   - msgbus misses viewport moves, so the live link uses `depsgraph_update_post`.
   - Undo invalidates Python references, but `session_uid` survives.
   - `bpy.ops` costs 217 ms per call in a 5,400-object scene against 0.017 ms through `bpy.data`.
   - Since 5.0, `bpy.props` storage is separate from custom properties.
   - Python can't add new editor types.
8. **Library overrides work as a prefab system.** The full workflow was verified on 5.2.2, provided every property is flagged `LIBRARY_OVERRIDABLE`. Unflagged properties silently revert. (04)
9. **AI in engines has converged on ask/plan/act modes plus MCP, and undo is the weak spot in all of them.** Developer sentiment is falling: in GDC 2026, 52% said generative AI is hurting the industry. Blender MCP's "execute any Python" design has two 2026 CVEs. (07)
10. **The GPL boundary is manageable.** Blender's FAQ allows software that "operates outside of Blender" and makes no Blender API calls to use any license. So the add-on is GPL, while the runtime, and therefore shipped games, can be MIT or proprietary. (04, 05, 06)

---

## 2. What we take from each engine

| | Take | Leave |
|---|---|---|
| **Unity** | Components on objects as the mental model. GUID identity. Baking as an editor-only conversion. A schema-generated inspector. Play mode that reverts on stop. | Two parallel object models (GameObjects and DOTS). `.meta` sidecars that silently break references when lost. Domain-reload wait times. |
| **Unreal** | Reflection metadata as the single source for editor, serialization and AI tools. Agents use the same typed tools as the developer. Soft references. | Deep actor class hierarchies, which UE6 is replacing with entities, components and prefabs. Binary assets. Long C++ build loops. An unauthenticated MCP server with no undo. |
| **Godot** | Scenes as composable prefabs. VCS-friendly text formats. Running the game as a separate process with remote inspection. A server/handle layer under the scene layer. | Meaning encoded in name suffixes (`-col`, `-rigid`). Starting a headless Blender for each import. A custom scripting language. |
| **Blender** | Its modeling and layout tools, collections and instancing, library overrides for prefabs, the asset browser, the undo stack, RNA-backed property UIs, and the stock glTF exporter. | Running gameplay inside Blender. Parsing `.blend` files outside Blender. Arbitrary Python execution for agents. |

---

## 3. Decisions

| Area | Decision | Why (report) |
|---|---|---|
| Editor | **Blender 5.2 LTS, extended by a GPL add-on.** Panels live in the 3D View sidebar ("Game", "Assistant"). | Supported until July 2028. Python can't add new editors, so sidebar tabs are the native surface. (04) |
| Runtime | **Separate TypeScript process: Three.js r186 + Rapier 0.21, run by Vite 8**, MIT. Ships to web and desktop. | Fastest for a solo developer with an AI assistant, sub-second hot reload, a deterministic physics core, and the best AI training-data coverage. Scored 4.60/5, against 4.00 for Godot-as-runtime. (06) |
| Scripting | **Strict TypeScript.** Data-only components with `defineComponent` in `src/components/`, and logic with `defineSystem` in `src/systems/`. | One language from engine to gameplay. The type checker gives an agent a cheap correctness signal. (06) |
| Scene model | **Hybrid: components authored on Blender objects, a small ECS at runtime, and the Three.js graph for rendering.** | Familiar authoring with data-oriented, agent-readable runtime state. (06) |
| Components | Defined in TS. The dev server writes `.gamestudio/components.json`, and the add-on **generates typed PropertyGroups** (`Object.gsc_<Name>`) from it, with every field flagged `LIBRARY_OVERRIDABLE`. | The runtime registry is the source of truth, and the overridable flags keep prefab overrides working. (04, 05) |
| Identity | `obj["gs_id"]` = `gs_` + 12 hex characters, with duplicates repaired after Shift+D. The runtime maps entities **only** by ID. | Names aren't stable, and GLTFLoader rewrites them. (01, 03, 04, 05) |
| Interchange | **`<scene>.glb` from Blender's stock exporter, post-processed so `extras` holds only `gs_id`, plus a `<scene>.scene.json` manifest** carrying component data. | Stock exporter, data in a diffable manifest. `export_extras` would also dump our PropertyGroups. (04, 06) |
| Live link | **Localhost hub inside the Vite dev server.** Blender connects over TCP/NDJSON with a token, games over WebSocket. Blender sends deltas (transforms at ≤30 Hz, component sets), and the game sends logs, errors, stats and state. | Measured at 19 µs per loopback round trip. Ideas taken from Skein's BRP, and lessons from Armory's `eval` live-patch. (05, 06) |
| Play | **Play in Blender exports, starts or reuses the dev server, and opens the game in a separate app-style window.** Stop resets to the loaded state. | A crash can't take Blender down, and viewport streaming costs 9–23 ms per 1080p frame. (04, 06) |
| Prefabs | **Next milestone:** collections and linked libraries with overrides, wrapped in our own API. | Verified to work, with documented catches. (04) |
| AI | **Opt-in assistant.** Typed tools only. **Write tools stage proposals, and the developer applies each one with a click as one undo step.** Includes risk scoring, staleness checks, `ai_lock`, an audit log, Ask/Propose modes, a Mentor role and Teach mode. External agents use the same tools through MCP. | The industry has converged on this shape, undo is everyone's weak spot, and comprehension drops when developers simply delegate. (07) |

---

## 4. How the AI helps without taking over

Each principle comes from evidence in report 07:

| Principle | Mechanism in GameStudio |
|---|---|
| The developer is the author | Every write tool returns a *proposal*. Only the Apply button, an operator with `UNDO`, changes the scene. Only Apply writes files, after a backup, and Revert restores them. |
| Read-only by default | The assistant is off until enabled. Ask mode has no write path. The Mentor role has no write tools at all. |
| No silent overwrites | Proposals fingerprint the objects and files they're based on. If either changes, Apply asks for confirmation. File edits require exact, unique text matches. |
| Bounded scope | `ai_lock` on objects. Paths confined to the project, excluding `node_modules`, `.gamestudio` and `.git`. No network, shell or arbitrary-Python tools. |
| Risk shown up front | Risk comes from what the operations do (deletes, renames, disk writes, suspicious code patterns), never from the model's own claims. High-risk changes need a second confirmation. |
| Grounded, not invented | The engine's `API.md` and the project's `ASSISTANT.md` are in the cached system prompt. Tools return real schemas. The type check runs on demand. |
| Untrusted data stays data | The system prompt treats names, files and logs as data. The assistant has no egress except the model API. |
| Learning, not dependency | Teach mode, an "Explain" button on every proposal, and the Mentor role. |
| Accountability | `.gamestudio/ai/audit.jsonl` records every prompt, tool call, proposal and decision. Cost is shown per chat. |

---

## 5. Known gaps and the next milestones

1. **Prefabs** through collections and library overrides (§3), including "apply overrides to prefab".
2. **Per-object GLB re-export** for geometry edits. Today a geometry change re-exports and reloads the whole scene.
3. **Physics data in `KHR_physics_rigid_bodies` and `KHR_collision_shapes`** once ratified. Both are still Review Draft. (05)
4. **Animation**, using the layered Action API, since the legacy API was removed in 5.0. **Audio.** **UI.** **Save/load.** (04, 06)
5. **Desktop packaging** with Electron, and a checked Steam path. (06)
6. **Visual logic** as a custom `NodeTree` compiled to systems or `KHR_interactivity`. Never interpreted per frame. (04, 05)
7. **AI eval set** of about 30 engine tasks to measure invented APIs, acceptance rate and cost per accepted proposal. (07)
