# How Godot Works (Godot 4.x, as of October 2026)

Research input for the architecture of a game engine that uses Blender as its editor. Written 2026-10-09. The focus is on what affects our design decisions, not full coverage of Godot. Claims marked *(unverified)* could not be confirmed from a primary source.

**Version landscape**

| Release | Date | Why it matters here |
|---|---|---|
| 4.0 | Mar 2023 | Rewrite: RenderingDevice/Vulkan renderers, GDScript 2.0, first-class `Signal`/`Callable`, GDExtension, `%` scene-unique nodes |
| 4.3 | Aug 2024 | ufbx FBX importer by default, Direct3D 12, 2D physics interpolation, global groups |
| 4.4 | Mar 2025 | Jolt built in (opt-in), `.uid` sidecars for scripts/shaders, typed dictionaries, game window embedded in editor (Windows/Linux), 3D physics interpolation, Metal |
| 4.5 | Sep 2025 | Abstract classes, variadic functions, shader baker, stencil buffer, AccessKit screen-reader support, embedding on macOS, preload by UID |
| 4.6 | Jan 2026 (exact day unverified) | **Jolt default** for new 3D projects, **unique node IDs** in scene files, `load_steps` deprecated, **LibGodot** (engine embeddable as a library), floating/dockable panels |
| 4.7 | Jun 2026 (~19 Jun per press, unverified) | HDR output, AreaLight3D, new **Asset Store** replaces Asset Library, `Tween.tween_await()` |
| 4.8 | In development | 9.7% of 2026 community poll respondents already use 4.8 dev builds; 63.8% use 4.7 |

---

## 1. TL;DR

- **Everything is a tree of Nodes, and a "scene" is just a saved subtree.** The same concept covers prefabs, levels, characters, and UI. An instance is one line plus sparse overrides. Composition happens through child nodes, not components, and each node has one script.
- **Two layers: low-level "servers" plus a high-level scene layer.** Servers expose opaque `RID` handles, and nodes are convenience wrappers over them. This split is what lets Jolt drop in as a backend, lets servers run asynchronously, and gives a fast path that bypasses nodes.
- **The text formats (`.tscn`/`.tres`) diff well, but Godot spent years retrofitting stable identity.** It added `.uid` sidecars in 4.4, per-node `unique_id` in 4.6 (only as a *fallback* behind path lookup), and removed `load_steps` in 4.6. **Lesson: stable IDs on day one.**
- **Import pipeline:** source file + committed `.import` settings sidecar + a regenerated, never-committed `.godot/imported/` cache. Copy this.
- **`.blend` import shells out to headless Blender to export glTF**, and keeps it warm through an XML-RPC server. It is lossy (Godot's own priorities page says only "models, textures, lights, and cameras" come through), every machine needs Blender, and failures surface as Python tracebacks. Customization uses name suffixes (`-col`, `-rigid`), custom properties carried as glTF `extras` into metadata, and post-import scripts. With Blender as our editor, we can avoid almost all of this.
- **The editor is a Godot app**, and plugins use the same APIs as games. `@tool` scripts run game code in the editor: powerful, but with no undo. **The game always runs in a separate process.** A debugger link provides the remote tree, live edit, and hot reload. Copy this model.
- **GDScript exists for tight integration**: threads, vector types, refcounting, completion, hot reload. It is the main language for 77.7% of 2026 poll respondents, versus 15.2% for C#. The downside is that it is niche, and LLMs often produce Godot 3 syntax.
- **AI:** the stock editor has no AI features, and third-party MCP servers and plugins fill the gap. On 30 June 2026 the Foundation banned autonomous-agent and "vibe-coded" contributions to the engine. Users are split: in the 2026 poll (n = 11,195), 51.7% use AI somehow, 33.6% are "against it", and 4.9% use an agent with a Godot MCP adapter.
- **Love:** lightweight, fast iteration, scene composition, signals, MIT license. **Hate:** 3D and post-processing gaps, no refactoring tools, Variant binding overhead, fragile re-import of edited imported scenes, second-class C#.

---

## 2. Nodes, scenes and the SceneTree

### The node tree

- A **Node** has a name, a parent, children, groups, process settings, and at most **one script**, which extends the node's C++ class. Features are node types (`MeshInstance3D`, `CollisionShape3D`, `RigidBody3D`, `Timer`, `AnimationPlayer`…). The FAQ says Godot "relies on inheritance instead" of ECS. Juan Linietsky's 2021 article says nodes still give you composition, only at a higher level than ECS components.
- **SceneTree** is the default `MainLoop`. It owns the root viewport/window, groups, pausing, and quitting. A node becomes active when it is attached under the root.
- **Owner:** every node saved in a scene has `owner` set to that scene's root. Nodes added in code without an owner are neither shown in the Scene dock nor saved. Tools and AI agents commonly trip on this.

### Scenes as the composable unit (Godot's answer to prefabs)

| Godot | Unity equivalent | How it is stored |
|---|---|---|
| Scene (`.tscn`) | Prefab *and* Scene (one concept) | A text file holding a node subtree |
| Instanced scene | Prefab instance | `[node … instance=ExtResource("id")]` plus only the overridden properties |
| Inherited scene | Prefab variant | The root node is an `instance=` of the base. You can add nodes and override properties, but you **cannot delete base nodes** |
| Editable children | Overriding nested prefab children | `[editable path="Crate"]` plus `[node parent="Crate/…"]` entries holding only the changed properties |
| "Make Local" | Unpack prefab | Flattens the instance into plain nodes |
| Groups | Tags (many per node) | `groups=["enemies"]` on the node header |
| Autoload | Singleton / DontDestroyOnLoad | Listed in `project.godot`, instanced under `/root` at startup |

**Identity retrofit (4.6).** PR #106837 (reduz) added unique node IDs "to safeguard when a base or instantiated scene changes (nodes renamed, moved or readded), that the hierarchy is still maintained and the node and its overridden properties can be preserved." The PR fixes long-standing bugs where renaming a node in a base scene destroyed overrides in derived scenes. By design the IDs are only a **fallback**: path lookup comes first, and the ID is used only when the path fails. Scenes must be re-saved (Project > Tools > Upgrade Project Files) to gain the IDs.

### Groups and node paths

- **Groups** are string tags, used through `add_to_group()`, `get_nodes_in_group()` and `call_group()`. Since 4.3, "purely organizational" **global groups** can be declared in Project Settings.
- **Node paths** are relative strings: `get_node("Body/Head")`, `$Body/Head`, or `%Head` for **scene unique nodes**, which survive reparenting within their scene. `@onready` resolves a path just before `_ready`. Exported node references are stored as `NodePath`s. String paths in scripts still break silently on rename, because the 4.6 IDs protect only scene inheritance and instancing.

---

## 3. Signals and "call down, signal up"

- The docs say "signals are Godot's version of the observer pattern." In Godot 4, signals and callables are first-class: `timer.timeout.connect(_on_timer_timeout)`, `health_changed.emit(old, new)`. Custom signals are declared with `signal health_changed(old_value, new_value)`, and the docs advise naming them with past-tense verbs.
- Connections made in the editor are serialized into the scene: `[connection signal="died" from="Enemy" to="." method="_on_enemy_died"]`.
- **"Call down, signal up"** is a community convention, not a phrase from the docs. A parent may call methods on the children it owns. Children emit signals and never reach up the tree. Siblings talk through a shared ancestor. The official scene-organization page says the same thing in other words: design scenes "to have no dependencies"; signals are "extremely safe, but should be used only to 'respond' to behavior, not start it"; siblings "should only be aware of their own hierarchies." For global events, an "event bus" autoload is common.
- **Pitfall:** a signal connected in the editor to a node inside an *imported* scene's editable children is dropped when the `.blend` is re-imported (forum report, Godot 4.5.1). The suggested workaround is to connect in code.

---

## 4. Main loop, lifecycle, and pausing

**Frame structure:** input, then zero or more **fixed physics ticks** (`_physics_process(delta)`, 60 Hz by default), then **one** `_process(delta)` at the variable frame rate, then render. `_process` "is not synchronized with physics." **Physics interpolation** (2D in 4.3, 3D in 4.4) smooths fixed ticks on high-refresh displays. Deferred work (`call_deferred`, `queue_free`) runs at the end of the frame.

| Callback | Order | Notes |
|---|---|---|
| `_init()` | On construction | Not in the tree yet |
| `_enter_tree()` | **Top-down** (parent before children) | Every time the node enters the tree |
| `@onready` vars, then `_ready()` | **Bottom-up** (children ready before parent) | Once per node, unless `request_ready()` is called |
| `_physics_process` / `_process` | Tree order (pre-order). `process_priority` overrides it, lower runs first | Toggle with `set_physics_process()` / `set_process()` |
| `_exit_tree()` | Bottom-up | |

**Pausing:** `get_tree().paused = true` stops 2D/3D physics and per-node processing according to `process_mode`:

| `process_mode` | Behavior |
|---|---|
| Inherit (default) | Uses the nearest non-Inherit ancestor's mode. Pausable at the root |
| Pausable | Runs only when the game is not paused |
| When Paused | Runs only while paused (pause menus) |
| Always | Runs regardless of pause |
| Disabled | Never processes |

While paused, "signals still work". Animations, audio and particles pause and resume automatically. The physics servers can be kept running with `set_active`. A pause menu is simply a subtree set to *When Paused*. It is a small, clean design worth copying as is.

---

## 5. Resources and the text formats

- A **Resource** is a refcounted data object: Mesh, Material, Texture, Script, PackedScene, or a custom `class_name X extends Resource` with `@export` fields. The docs say: "When the engine loads a resource from disk, it only loads it once." Every user shares that instance, so editing it affects all of them. Use "Make Unique" or `resource_local_to_scene` to give each user its own copy.
- An **external** resource is its own file. A **built-in / sub-resource** is embedded in the `.tscn`/`.tres` that uses it (clear its path to embed it).
- **Identity:** `res://` paths plus `uid://` ResourceUIDs. A UID lives in the file header for `.tscn`/`.tres`, in the `.import` file for imported assets, and since 4.4 in a **`.uid` sidecar** for scripts and shaders. The 4.4 blog says to commit those sidecars: "they're required to properly sync data (similar to .import files)". Each `ext_resource` stores **both** `uid` and `path`. The UID is the identity and the path is a readable fallback.

**Small `.tscn` example.** This one is hand-written in the 4.6+ format and abridged; it was not saved by the editor:

```ini
[gd_scene format=3 uid="uid://c4k2m8xq1n7ab"]

[ext_resource type="Script" uid="uid://bq3x5r7wq2hkd" path="res://levels/level_01.gd" id="1_lvl"]
[ext_resource type="PackedScene" uid="uid://dyb0v4ln2h3yx" path="res://actors/enemy.tscn" id="2_enemy"]
[ext_resource type="PackedScene" uid="uid://b8k1qk3xg4ty2" path="res://art/crate.blend" id="3_crate"]

[sub_resource type="BoxShape3D" id="BoxShape3D_floor"]
size = Vector3(40, 1, 40)

[node name="Level01" type="Node3D" unique_id=1203948811]
script = ExtResource("1_lvl")

[node name="Floor" type="StaticBody3D" parent="." unique_id=883100231]

[node name="Shape" type="CollisionShape3D" parent="Floor" unique_id=55120934]
shape = SubResource("BoxShape3D_floor")

[node name="Enemy" parent="." unique_id=1690331207 groups=["enemies"] instance=ExtResource("2_enemy")]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 4, 0, -2)
max_health = 50

[node name="Crate" parent="." unique_id=402266871 instance=ExtResource("3_crate")]

[connection signal="died" from="Enemy" to="." method="_on_enemy_died"]

[editable path="Crate"]
```

The sections always come in this order: descriptor, `ext_resource`, `sub_resource`, `node`, `connection`, `editable`. Note that the `.blend` file is referenced directly as a `PackedScene`. The `Enemy` instance stores only its overrides (`transform`, `max_health`).

**A custom data resource (`.tres`):**

```ini
[gd_resource type="Resource" script_class="WeaponData" format=3 uid="uid://cx8a7m2ol3wq1"]

[ext_resource type="Script" uid="uid://d1u5n0w3k7p2s" path="res://data/weapon_data.gd" id="1_wd"]
[ext_resource type="Texture2D" uid="uid://b3vq0l2rj6m4k" path="res://ui/icons/sword.png" id="2_icon"]

[resource]
script = ExtResource("1_wd")
display_name = "Iron Sword"
damage = 12
icon = ExtResource("2_icon")
```

**Why it works with VCS:** the files are INI-like with one property per line, only non-default values are written, and nodes appear in tree order. Local IDs are short random-suffixed strings (`2_eorut`, `CapsuleShape3D_fdxgg`) rather than sequential integers. We infer that this is meant to reduce merge collisions; the docs do not say so. **What still hurts:** large inline array blobs (tilemaps, meshes, animations), reports of UIDs being re-randomized after branch switches in early 4.4, and 4.6's "Upgrade Project Files" rewriting every scene. At export, text resources are converted to binary `.scn`/`.res`.

---

## 6. Scripting

| Option | Who uses it (2026 poll, main language) | Hot reload | Notes |
|---|---|---|---|
| GDScript | 77.7% | Yes: editor and running game ("Synchronize Script Changes") | Gradual typing. Typed code uses "optimized opcodes". JIT/AOT "planned" |
| C# (.NET) | 15.2% | Rebuild needed. In-game hot reload not covered by the docs *(unverified)* | Needs the separate .NET editor build. Android/iOS experimental. **Still no web export in 4.7** |
| C++ via GDExtension (godot-cpp) | 1.7% (+0.6% as engine module/fork) | `reloadable = true` (debug builds) | Shared library loaded at runtime. Builds for 4.1+ run on later 4.x minors, not vice versa |
| Rust via godot-rust (`godot` crate 0.5.5, Aug 2026) | 1.5% | "Even hot-reload is tested!" | "Usable state." Wasm/Android/iOS experimental. Occasional breaking changes |

**Why GDScript exists.** According to the FAQ, Lua was "complex and slow" to bind and Python was hard to embed. Existing VMs also had poor threading support, poor class extension, "horrible interfaces for binding to C++", no native vector types, and GC stalls, and they were hard to integrate with editor completion and live editing. The FAQ sums it up: "We wanted to focus on a great engine, not a great number of integrations." GDScript has since gained typed dictionaries (4.4), abstract classes, and variadic functions (4.5). The priorities page still lists **project-wide rename refactoring**, traits, and namespaces as missing, and calls data-crunching performance "lackluster."

**The binding cost.** GDScript, C# and GDExtension all call the engine through one `ClassDB`/`Variant` interface. Sam Pruden's 2023 analysis measured a C# `IntersectRay` call at about 24 µs, against about 0.49 µs of raw physics work. Part of the overhead is that results come back as a `Dictionary` of Variants. **Design APIs around typed structs, not dictionaries.**

**AI angle.** LLMs "output old syntax with full confidence" (Godot 3 instead of 4). A niche language that changes quickly makes AI assistance worse.

---

## 7. The servers architecture

Godot's official architecture diagram has three layers: **scene** (`/scene`), **servers** (`/servers`, "singleton objects initialized at engine startup"), and **drivers/platform** (`/drivers`, `/platform`). Two cross-cutting parts sit beside them: **core** (Object, ClassDB, Variant, containers, I/O) and **main** (startup and the loop). The editor is built on top of the scene layer. The servers are RenderingServer, PhysicsServer2D/3D (where Godot Physics or Jolt plugs in), AudioServer, NavigationServer2D/3D, DisplayServer, TextServer, and XRServer.

- Servers expose **RIDs**, "opaque handles to the server implementation" that "are allocated and freed manually." Nodes hold RIDs internally.
- Servers "will often run asynchronously for performance", so calling getters stalls them. Writes are cheap; reads are expensive.
- **Why it matters:**
  1. Backends can be swapped behind a stable API. That is how Jolt became the default without node changes.
  2. There is a documented fast path for "tens of thousands of per-frame instances".
  3. Headless builds swap in dummy drivers.
  4. Editor viewports and the game share the same servers.

  The docs admit the scene layer costs complexity, performance, multithreaded control and memory, but say that "in most cases this is not a problem."

---

## 8. The editor

- **Built with the engine's own UI.** The FAQ says the editor is rendered with Godot's Control nodes, "eating its own dog food." The editor and the engine ship in the same binary. **Export templates** are prebuilt engine binaries without the editor, and an export is a template plus a `.pck` of project data.
- **Extension points** live in `addons/<name>/plugin.cfg` plus a script:
  - `EditorPlugin`: docks, main screens, custom types.
  - `EditorInspectorPlugin`: `_can_handle`; `_parse_property` returns true to replace a property's editor.
  - `EditorProperty`, gizmo plugins, and import, post-import and export plugins.
  - `EditorUndoRedoManager` and `EditorScript` (one-off automation).
- **`@tool` scripts** run in the editor and branch on `Engine.is_editor_hint()`. They suit previews and configuration warnings (`_get_configuration_warnings()`). The docs warn: "Modifications in the editor are permanent, with no undo/redo possible." Freeing a node the editor is using can crash it. Nodes added from code need `owner = get_tree().edited_scene_root` to be saved.
- **Separate game process.** Per the docs, "the game always runs in a separate process, no matter the embedding mode", so a game crash does not take down the editor. Embedding (4.4 on Windows/Linux, 4.5 on macOS) is window-system trickery. The **Game** view adds pause, frame advance, game speed, click-to-select of in-game nodes, and camera override.
- **Debugger link:** the Scene dock shows a **Remote** tree of the running game, and the inspector edits it live. **Synchronize Scene Changes** pushes editor edits into the running game (live edit), and **Synchronize Script Changes** hot-reloads scripts. Other tools: profilers, visible collision shapes, and remote deploy with a network filesystem.

---

## 9. Import system and Blender integration

### General pipeline

- The import adds `<asset>.import` next to the source file (commit it). The result is cached in `res://.godot/imported/` (never commit it; it is regenerated if deleted). A changed MD5 checksum triggers automatic reimport. Presets and project-wide import defaults are available. Abridged example:

```ini
[remap]
importer="scene"
type="PackedScene"
uid="uid://b8k1qk3xg4ty2"
path="res://.godot/imported/crate.blend-<md5>.scn"

[deps]
source_file="res://art/crate.blend"
dest_files=["res://.godot/imported/crate.blend-<md5>.scn"]

[params]
nodes/root_type=""
blender/nodes/custom_properties=true
blender/nodes/modifiers=1
_subresources={}
```

- The **Advanced Import Settings** dialog stores per-node, per-mesh, per-material and per-animation overrides in `_subresources`, keyed by node path or resource name. Renaming an object in Blender therefore orphans its settings.
- Supported formats: glTF 2.0 (recommended), `.blend`, FBX (ufbx since 4.3), DAE, OBJ.

### How direct `.blend` import works

The source is `modules/gltf/editor/editor_import_blend_runner.cpp` and `editor_scene_importer_blend.cpp`.

1. Godot launches the Blender executable from `filesystem/import/blender/blender_path` with `--background --python-exit-code 1 --python-expr <script>`.
2. **RPC mode** (the default): the embedded script starts a `SimpleXMLRPCServer` on `127.0.0.1:<rpc_port>` (default 6011) and exposes `export_gltf`. On Blender's main thread it calls `bpy.ops.wm.open_mainfile`, optionally `bpy.ops.file.unpack_all`, then `bpy.ops.export_scene.gltf(**opts)`. The server stays alive for `rpc_server_uptime` after a batch of imports, so one Blender instance serves many files. If RPC fails, Godot falls back to a one-shot **direct mode**.
3. Godot imports the resulting glTF with its normal glTF importer.
4. Import options map onto Blender exporter parameters: `blender/nodes/custom_properties` → `export_extras`, `modifiers` → `export_apply`, `vertex_colors` → `export_vertex_color` (`export_colors` before Blender 4.2), `export_gn_mesh`, `export_animation_mode`, and others. The code branches on the Blender version to follow the exporter's parameter renames.

**Local check on Blender 5.2.2 LTS (this workstation, 2026-10-09):** every exporter parameter Godot passes still exists except the legacy `export_colors`. A cube named `Crate-col` with custom properties exported as `{"extras": {"health": 25, "tag": "breakable"}, "name": "Crate-col"}`. Headless Blender took **4.0–7.1 s to start** over three runs, while exporting the tiny scene took about 1.1 s. Startup cost dominates, which is why Godot keeps Blender warm.

Requirements from the docs: Blender 3.0+ (3.5+ recommended), installed **on every team member's machine**, and not usable from the Android or web editors. The docs still call glTF "recommended".

### Import hints (name suffixes)

Suffixes are case-insensitive and can be written with `-`, `$` or `_`.

| Suffix | Effect |
|---|---|
| `-noimp` | Skip the node or animation |
| `-col` / `-convcol` | Keep the mesh and add a static trimesh / convex collider |
| `-colonly` / `-convcolonly` | Replace the mesh with a StaticBody3D collider. Empties become primitive shapes based on draw type (box, sphere…) |
| `-rigid` | Import as RigidBody3D |
| `-navmesh` | Convert to a navigation mesh |
| `-occ` / `-occonly` | Add an occluder / replace with an occluder |
| `-vehicle` / `-wheel` | VehicleBody3D / VehicleWheel3D |
| `-loop` / `-cycle` | Looping animation clip |
| `-alpha` / `-vcol` | Material transparency / vertex-color albedo |

### Custom data and post-import scripts

- Blender custom properties become glTF `extras`, and since about 4.4 these land in **node, mesh and material metadata** (`get_meta("extras")`). You can process them further with a `GLTFDocumentExtension` or an `EditorScenePostImportPlugin`.
- A **post-import script** (`@tool extends EditorScenePostImport`, `func _post_import(scene): … return scene`) can rewrite the imported tree, for example to attach scripts based on metadata. Paid add-ons such as "Blender To Godot 4 Pipeline" exist because users want this layer.

### What hurts

- **It is lossy by construction.** Godot's priorities page says "only models, textures, lights, and cameras" come through. There are no Geometry Nodes beyond realized instances, no drivers or constraints, and only Principled-BSDF-style materials.
- **Changes made in Godot do not survive re-import well.** Forum reports describe metadata on editable children being wiped, editor-wired signals dropped, and material overrides lost unless materials are extracted to `.tres`. Inherited scenes cannot remove nodes, and splitting one `.blend` into several Godot scenes means breaking the link to the file.
- **Failures are opaque.** Users see "Blender exportation failed" and a glTF-exporter traceback (reported on Godot 4.7 with Blender 5.0, June 2026).
- **It is slow on large projects.** "Importer performance" is still an open priority item.

---

## 10. Brief: rendering, physics, export, license

| Area | State (4.7) |
|---|---|
| Renderers | **Forward+** (desktop, all features, the default on desktop), **Mobile** (most features, the default on mobile), and **Compatibility** (OpenGL, "2D and core 3D", the only option on web). Forward+ and Mobile run on **RenderingDevice**, an abstraction over Vulkan, D3D12 and Metal. Godot itself calls post-processing a "weak spot" and says graphics debugging is "relatively basic" |
| Physics | **Jolt** was built in (opt-in) in 4.4 and became the **default for new 3D projects in 4.6**. Differences: shrink-then-shell margins, no velocity Baumgarte, active-edge ghost-collision removal. Some joint soft limits are unsupported, and running physics on a separate thread is "experimental". 2D uses Godot Physics |
| Export | Per-platform export templates, downloadable per platform in 4.7. Shader baker (4.5). Consoles require third-party middleware such as W4 Games, under platform NDA. The priorities page still lists headless CLI export for CI as unreliable |
| License | **MIT** for the engine (docs are CC BY 3.0). No royalties. Stewarded by the not-for-profit Godot Foundation |

---

## 11. AI tooling in the Godot ecosystem (2026)

- **Stock Godot has no built-in AI**, and the June 2026 coverage found no AI items on the roadmap. Note that this second point comes from a vendor blog.
- **MCP servers.** Several third-party bridges compete; they are listed below.

| Project | Architecture | Notes |
|---|---|---|
| Coding-Solo/godot-mcp (~6.0k ★) | Node.js server that drives the **Godot CLI headless** plus one bundled `godot_operations.gd` taking JSON operations | Run/stop the project, read debug output, create scenes and nodes, manage UIDs. No live editor connection |
| hi-godot/godot-ai (~2.9k ★, v4 needs 4.7+) | stdio → Python server (authenticated HTTP) → **editor plugin over loopback WebSocket** | 46 tools: scenes, nodes, signals, UI, materials, tests. GDScript is parse-validated and hot-reloaded |
| mkdevkit, gdai-mcp, godot-mcp-pro (paid), others | Editor plugin plus a WebSocket/TCP relay | Some route edits through **UndoRedo**; some take screenshots of the running game |

- **In-editor assistants:** Ziva (commercial, still beta; reads runtime errors and takes screenshots), "Godot AI" (MIT, bring your own model, v0.1.0 in April 2026), and AI Assistant Hub. There are also AI-native editors built on Godot (Summer Engine, Orca Engine). Their claims come from vendor marketing and are unverified.
- **Foundation policy (30 June 2026, "Changes to our Contribution Policies"):** "No autonomous AI agent use or vibe coding", which "already leads to an auto-ban." "We require all code to be human authored." AI assistance is limited to "menial things (like code completion, regex, or find and replace)" and must be disclosed. No AI-generated text is allowed in human-to-human discussion. The stated reason is reviewer burden: "AI cannot take responsibility."
- **2026 community poll** (n = 11,195, run July–August 2026, our analysis of the official CSV): 42.6% ask a chat AI for help, 17.9% use agentic coding (Claude Code, Codex…), 4.9% use agentic coding with a Godot MCP adapter, and 3.8% use AI for game testing. Meanwhile **33.6% chose "I don't use AI, and I'm against it"**, and 46.9% report no AI use at all. Of those who use AI, the most common main task is "Ask only, AI does not write code" (42.7%). Only 10.3% say AI writes most of their code, and 3.3% say it writes all of it.

---

## 12. What developers love and hate

| Love | Hate |
|---|---|
| Small download, fast startup, fast iteration | 3D gaps (post-processing, GI, lightmaps, streaming), which Godot's own priorities list admits |
| One "scene" concept, composition by nesting | Overrides on imported scenes break on re-import. Inherited scenes cannot delete nodes |
| Signals, autoloads, built-in tweens | String paths break silently. No project-wide symbol rename |
| GDScript and the built-in editor (63.2% use no external editor) | Variant binding overhead. GDScript is slow for heavy data work |
| Text scenes that work with git (78.8% of respondents use VCS) | Large inline blobs in `.tscn`. UID churn during the 4.4 and 4.6 migrations |
| MIT, no royalties | Consoles only through third parties under NDA. No C# web export. Thin asset ecosystem |
| 84% rate editor-usability progress good or better | Asset import has the lowest satisfaction among core areas (67.6% good or better) |

**Blender is the art tool for 75.5% of all poll respondents, and for 92.7% of those who do 3D regularly.** For Godot users, the Blender bridge is a core workflow.

---

## 13. Implications for our Blender-based engine

### Copy

1. **Make the composable unit a single concept with sparse overrides.** Godot's "a scene is a node subtree; an instance is a reference plus a diff" is simpler than Unity's prefab/scene split. Map it onto Blender: a **collection is a scene or prefab**, a **collection instance is an instance**, and overrides are stored as diffs keyed by stable ID. A separate Blender research note should decide whether library overrides can serve as "editable children".
2. **Give everything stable IDs on day one, and use them as the primary key, not a fallback.** Godot needed `.uid` sidecars (4.4) and per-node IDs (4.6) to fix references that broke on rename or move. Store a UUID in each Blender object's and data-block's custom properties. Write references as `{id, path}`, with the path kept only for humans and as a repair hint.
3. **Use deterministic text sidecars for engine-owned data.** `.blend` is binary and cannot be diffed or merged. Keep our component and gameplay data in Godot-style text files that write only non-default values in stable order, with no global counters (the `load_steps` lesson). Store large binary payloads in separate files so text stays readable.
4. **Separate source, settings, and cache.** Commit the source plus a small settings sidecar (`.import`), and keep cooked outputs in a hash-keyed cache that is ignored by VCS (`.godot/imported`). Treat any data that lives only in the cache as a bug.
5. **Split the runtime into servers and a scene layer.** Build the runtime as handle-based servers (render, physics, audio, navigation) with a thin scene/entity layer on top. That gives swappable backends (Jolt), a fast path, and one clean command API. Both the Blender bridge and AI agents can drive that same API.
6. **Run the game as a separate process with a debug protocol.** Godot shows that a separate process gives crash isolation, a remote tree, live edit, and hot reload. Blender and our runtime will be separate processes anyway. Build the protocol first: a remote scene tree, a property-patch stream from Blender's depsgraph (Godot's "Synchronize Scene Changes"), script hot reload, pause and frame-step, and click-to-select in the game that selects in Blender.
7. **Copy the main loop and pause model exactly.** Fixed-tick physics, variable `process`, interpolation, top-down enter followed by bottom-up ready, and the five `process_mode` values. It is small, proven, and easy to explain.
8. **Use signals with "call down, signal up" as the documented convention.** Serialize connections by **source ID and target ID**, not by path, so they survive re-export.
9. **Copy configuration warnings.** Godot's `_get_configuration_warnings()` maps naturally onto Blender panel warnings. They also give AI agents a machine-readable "what's wrong" list.

### Avoid

1. **Don't run Blender as a black-box subprocess to export glTF.** That is the core of Godot's Blender pain: 4–7 s cold starts, lossy glTF, opaque tracebacks, renamed exporter parameters, and Blender required on every machine. Our exporter runs **inside** the live Blender session and reads the evaluated depsgraph incrementally. For CI and headless cooks, keep a **warm Blender worker pool**, which is the same idea as Godot's RPC server.
2. **Don't encode semantics in object names** (`-col`, `-rigid`). Use typed components edited in a Blender panel and stored in custom properties. Optionally parse Godot's suffixes when importing third-party assets, since many artists already know them.
3. **Don't key per-object import overrides by name** (Godot's `_subresources`). Key them by ID.
4. **Don't let engine-side edits fight the source.** Godot's re-import breakage comes from editing a derived artifact. Blender should be the single source of truth for everything placed in the world. Engine-only data should be attached by ID, and re-export must be idempotent.
5. **Don't invent a scripting language.** GDScript's reasons were sound when Godot was built. Today a niche language costs AI assistance quality and ecosystem. Choose a mainstream language with good hot reload, and design typed, struct-based bindings (the Pruden lesson).
6. **Don't give editor-time code unrestricted, un-undoable access.** Godot's `@tool` warnings ("no undo/redo possible", editor crashes) apply to Blender add-ons too. Route every scripted or agent edit through Blender's undo stack.
7. **Don't treat CI as an afterthought.** Godot still lists headless export for CI as unreliable. Design cook and export as a CLI first.

### AI: assist, don't take over

- **Ship one first-party agent API** with documented, versioned, typed tools covering: query the scene by ID, read and write components, read configuration warnings, run, pause, and step the game, read runtime errors, and take screenshots. Godot's ecosystem shows the cost of leaving this to third parties: about ten competing MCP servers, each covering different things. Copy hi-godot's security model: a loopback-only connection with authentication.
- **Every agent change must be undoable, visible as a diff, and confirmable.** Some Godot MCPs already route edits through UndoRedo; that is the minimum standard. Text sidecars make agent changes reviewable in git.
- **Make AI opt-in and easy to switch off.** A third of Godot's community is actively against AI, and almost half use none. For external contributions, a disclosure policy like Godot's is a reasonable default.

### Concept mapping (proposed)

| Godot | Our engine (Blender-based) |
|---|---|
| Node / scene subtree | Blender object + typed components / collection |
| Instanced scene, editable children | Collection instance + ID-keyed override diff |
| `uid://` + `.uid` + `unique_id` | UUID in custom properties, primary from day one |
| `.tscn` / `.tres` | Text sidecar per collection/data asset, plus binary blobs |
| `.import` + `.godot/imported` | Export-settings sidecar + ignored, hash-keyed cook cache |
| Servers + RIDs | Runtime servers + handles, also used as the agent and bridge API |
| Remote tree / live edit | Depsgraph → runtime patch stream; runtime → Blender selection |
| EditorPlugin / inspector plugin | Blender add-on panels / property UIs |
| Godot MCP plugins | First-party agent tool server |

### Option to evaluate: Godot as our runtime

Since 4.6, **LibGodot** lets another application embed the engine and control its startup and main loop (Linux, Windows and macOS for now). The engine is MIT-licensed. A Blender front end driving a Godot-based runtime process would give us rendering, physics, export templates, and Jolt at once. The costs are inheriting Godot's 3D gaps and its path-based scene model. **Licensing note:** Blender's license FAQ treats add-ons that use its Python API as GPL. A separate-process runtime that we talk to over a socket keeps the runtime's license independent. Have this confirmed legally *(not re-verified here)*.

---

## 14. Sources

- Godot 4.5 / 4.6 / 4.7 release pages: https://godotengine.org/releases/4.5/ · https://godotengine.org/releases/4.6/ · https://godotengine.org/releases/4.7/
- Godot 4.7 press coverage: https://gamedev.net/news/godot-47-released-r4011/ · https://godotengine.org/article/release-candidate-godot-4-7-rc-1/
- TSCN file format (4.7): https://docs.godotengine.org/en/4.7/engine_details/file_formats/tscn.html
- UID changes in 4.4: https://godotengine.org/article/uid-changes-coming-to-godot-4-4/
- Unique node IDs PR #106837: https://github.com/godotengine/godot/pull/106837 · proposal #6291: https://github.com/godotengine/godot-proposals/issues/6291
- Scene organization: https://docs.godotengine.org/en/stable/tutorials/best_practices/scene_organization.html
- Using SceneTree: https://docs.godotengine.org/en/stable/tutorials/scripting/scene_tree.html
- Idle and physics processing: https://docs.godotengine.org/en/stable/tutorials/scripting/idle_and_physics_processing.html
- Pausing games: https://docs.godotengine.org/en/stable/tutorials/scripting/pausing_games.html
- Signals: https://docs.godotengine.org/en/stable/getting_started/step_by_step/signals.html
- Groups (global groups): https://docs.godotengine.org/en/4.4/tutorials/scripting/groups.html
- Scene unique nodes: https://docs.godotengine.org/en/4.6/tutorials/scripting/scene_unique_nodes.html
- Resources: https://docs.godotengine.org/en/stable/tutorials/scripting/resources.html
- Import process: https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/import_process.html
- 3D formats / .blend import: https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/available_formats.html
- Import hints: https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/node_type_customization.html
- Import configuration / post-import scripts: https://docs.godotengine.org/en/stable/tutorials/assets_pipeline/importing_3d_scenes/import_configuration.html
- Blend runner source: https://raw.githubusercontent.com/godotengine/godot/master/modules/gltf/editor/editor_import_blend_runner.cpp · importer options: https://raw.githubusercontent.com/godotengine/godot/master/modules/gltf/editor/editor_scene_importer_blend.cpp
- RPC issue #85446: https://github.com/godotengine/godot/issues/85446
- glTF extras → metadata (forum): https://forum.godotengine.org/t/gltf-import-how-does-one-get-metadata-extras/105711
- Re-import pain (forum): https://forum.godotengine.org/t/metadata-lost-on-editable-children-when-reimported-from-blender/122749 · https://forum.godotengine.org/t/model-reimport-signal-disconnected/130619 · https://forum.godotengine.org/t/blend-file-is-xed-out-in-godot/140394
- FAQ (GDScript motivations, ECS, license, editor): https://docs.godotengine.org/en/stable/about/faq.html
- Why isn't Godot ECS (2021): https://godotengine.org/article/why-isnt-godot-ecs-based-game-engine/
- Static typing: https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/static_typing.html
- C# docs: https://docs.godotengine.org/en/stable/tutorials/scripting/c_sharp/index.html · C# web export forum: https://forum.godotengine.org/t/cant-export-to-web/141491
- GDExtension: https://docs.godotengine.org/en/4.4/tutorials/scripting/gdextension/what_is_gdextension.html · https://docs.godotengine.org/en/stable/tutorials/scripting/cpp/about_godot_cpp.html
- godot-rust: https://github.com/godot-rust/gdext · https://godot-rust.github.io/book/ · https://crates.io/crates/godot
- Pruden, "Godot is not the new Unity" (Sep 2023): https://sampruden.github.io/posts/godot-is-not-the-new-unity/
- Using servers: https://docs.godotengine.org/en/stable/tutorials/performance/using_servers.html
- Architecture diagram: https://docs.godotengine.org/en/stable/engine_details/architecture/godot_architecture_diagram.html
- Running code in the editor (@tool): https://docs.godotengine.org/en/stable/tutorials/plugins/running_code_in_the_editor.html
- Inspector plugins: https://docs.godotengine.org/en/stable/tutorials/plugins/editor/inspector_plugins.html
- Debugging tools: https://docs.godotengine.org/en/stable/tutorials/scripting/debug/overview_of_debugging_tools.html
- Game embedding: https://docs.godotengine.org/en/stable/tutorials/editor/game_embedding.html
- Renderers: https://docs.godotengine.org/en/stable/tutorials/rendering/renderers.html
- Jolt: https://docs.godotengine.org/en/stable/tutorials/physics/using_jolt_physics.html
- Priorities: https://godotengine.org/priorities/
- Consoles / W4: https://www.w4games.com/w4consoles
- Contribution policy (30 Jun 2026): https://godotengine.org/article/contribution-policy-2026/ · coverage: https://www.theregister.com/ai-and-ml/2026/07/01/godot-says-bye-bye-ai-bans-vibe-coded-contributions/5265344
- Community poll 2026 + CSV: https://godotengine.org/article/godot-community-poll-2026/ · https://godotengine.org/storage/data/GodotCommunityPoll2026.csv
- MCP servers: https://github.com/Coding-Solo/godot-mcp · https://github.com/hi-godot/godot-ai · https://github.com/mkdevkit/godot-mcp · https://github.com/3ddelano/gdai-mcp-plugin-godot · https://github.com/youichi-uda/godot-mcp-pro
- AI plugins: https://godotengine.org/asset-library/asset/5067 · https://forum.godotengine.org/t/ziva-sh-ai-godot-development-agent/131964 · https://www.summerengine.com/blog/summer-vs-ziva (vendor) · https://www.summerengine.com/blog/best-ai-for-gdscript (vendor)
- Local verification: Blender 5.2.2 LTS glTF exporter probe and startup timing on this workstation, 2026-10-09.
