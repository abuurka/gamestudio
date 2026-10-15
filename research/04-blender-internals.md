# 04: Blender internals and the Python API as a game-editor foundation

*Research date: 2026-10-09. Target: Blender 5.2.2 LTS (Python 3.13.13). Claims tagged **[V]** were verified by running the locally installed Blender 5.2.2 (headless, plus three short GUI runs). Claims tagged **[U]** could not be verified. Everything else comes from the official docs or release notes listed in the Sources section.*

*Test machine for all timings: Windows 11, RTX 4070 (driver 610.88). The display appears to be 75 Hz, which is inferred from the vsync cap. Timings come from single runs and show orders of magnitude only.*

---

## 1. TL;DR

- **Use Blender as the authoring host and run the game in a separate native process.** Python in 5.2.2 is GIL-bound **[V]**, every `bpy` call must happen on the main thread, and a separate process keeps the runtime outside the GPL. A bridge add-on talks to the runtime through exported files and a localhost socket.
- **Library overrides work as a prefab system [V].** A linked collection is the prefab, a collection-instance empty is a cheap read-only instance, and `override_hierarchy_create()` makes an instance editable per property. Source edits propagate on reopen, `Library.reload()` hot-reloads them in about 6 ms, and instances can *add* components through `USE_INSERTION`. There are three catches. Every property must be flagged as overridable. Geometry cannot be overridden. Python writes to linked data are silently thrown away.
- **Use PropertyGroups for component data.** Since 5.0, `bpy.props` storage is separate from user custom properties **[V]**. The glTF exporter writes both to `extras` **[V]**. Its user-extension hooks also let us write a proper vendor extension **[V]**.
- **Modal operators can drive an in-editor loop, but only for previews.** Timer events reach about 1000 Hz with no redraw. With a redraw on every tick, the rate is set by the redraw rate: about 75 Hz on OpenGL (vsync) and about 520 Hz on Vulkan **[V]**. `bpy.app.timers` reaches only about 180 Hz, and timers **never fire in `--background`** **[V]**.
- **`bpy` is not thread-safe, and the threads docs say running threads are unsupported.** The pattern below has a socket thread that never touches `bpy`, a `queue`, and a `bpy.app.timers` pump on the main thread. It gave a median round trip of 13 ms over 100 commands **[V]**. A non-blocking socket polled from the timer avoids threads altogether.
- **For AI agents, change data with `bpy.data`, not `bpy.ops`.** With 5,400 objects in the scene, `bpy.ops.object.empty_add` took **217 ms per call**, while `bpy.data.objects.new` plus a link took 0.017 ms **[V]**. Undo invalidates Python references, so look up targets by a stable ID on every command. `session_uid` stays stable across undo **[V]**. Name lookup is a linear scan **[V]**.
- **Python cannot add new editors.** Registering a `Space` subclass fails with "Space does not support registration" **[V]**. The workarounds are sidebar tabs, a custom `NodeTree` editor, asset shelves, draw handlers on existing spaces, app templates, or an external UI.
- **GPU drawing works, but only through the modern API.** `bgl` and the raw-GLSL `GPUShader(vert, frag)` constructor are gone, and the constructor now raises a TypeError **[V]**. `gpu.shader.create_from_info` works on both OpenGL and Vulkan **[V]**. 5.2.2 still defaults to OpenGL on Windows **[V]**. Vulkan becomes the default in 5.3.
- **Rendering our runtime inside the viewport with a `RenderEngine` is possible but costly.** `view_draw` redraws at the display rate. However, the Python `gpu` API only accepts FLOAT buffers and cannot update a texture in place, so uploading a 1080p frame costs about 9–23 ms **[V]**. A separate runtime window is the better choice for v1.
- **Pin to 5.2 LTS, which is supported until July 2028.** `pip install bpy` needs Python `==3.13.*` exactly, so the Python 3.12 installed on this machine will not work.

---

## 2. Architecture: DNA, RNA, IDs, Main, linking

### 2.1 DNA, SDNA and the .blend format

DNA is the set of C structs in `source/blender/makesdna/DNA_*_types.h`. At build time, `makesdna` parses those headers into a compact binary schema called SDNA. When Blender saves, it writes structs to disk almost exactly as they sit in memory. Each struct gets a block header that records the struct index (SDNAnr), the old in-memory pointer address, the length and the count. The file's own SDNA is stored in a `DNA1` block, so every file describes itself. On load, Blender compares the file's SDNA with its own and converts members that differ. Pointers are fixed up by looking up the stored old addresses. Code in `versioning_*.cc` then handles changes in meaning. This design is why old files open in new Blender versions. It is also why the format is a poor fit as an interchange format for a game runtime: it is a raw memory dump of Blender's internal layout.

What I verified in 5.2.2 **[V]**:

| Item | Observation |
|---|---|
| Header (uncompressed save) | `BLENDER17-01v0502`. "17" is the header size, "01" the format version, and "0502" the Blender version. This 5.0+ layout supports blocks larger than 2 GB. |
| Block header | 32 bytes: `code[4]`, `SDNAnr:int32`, `old:uint64`, `len:int64`, `nr:int64`. My system-Python parser walked the whole file to `ENDB` with this layout. Every ID gets a 2-letter block (`OB`, `ME`, `GR`, …), followed by its `DATA` sub-blocks. `DNA1` held 993 structs. |
| Compression | **On by default since 5.0.** The file starts with the zstd magic bytes `28 B5 2F FD`, and the preference `use_file_compression` is `True`. |
| Save speed | A 500-object scene saved in 8 ms uncompressed (4.2 MB) and 10 ms compressed (237 KB) |

Since 5.0, ID names can be up to 255 bytes instead of 63. 4.5 LTS can read 5.x files, but older versions cannot. Big-endian support has been removed. Tools that parse .blend files directly, such as blender-asset-tracer, had to be updated for the new block header.

**Decision:** Never have the runtime read `.blend` files. Export to our own format, or to glTF/USD, from inside Blender.

### 2.2 RNA: the reflection layer

RNA sits on top of DNA. It is defined in `rna_*.cc` with `RNA_def_*` calls and generated by `makesrna`. Each struct and property carries metadata: type, subtype, unit, range, editable and animatable flags, override flags, and update callbacks. **The UI, animation, drivers, library overrides and Python all go through RNA**, and most of `bpy` is generated from it. A `PointerRNA` carries the data, its type and the owning ID, so that updates tag the right datablock.

Consequences for us:
- Everything is introspectable. For example, `Object.bl_rna.properties["location"]` reports type FLOAT, subtype TRANSLATION, array length 3, unit LENGTH, and animatable **[V]**. AI tool schemas can be generated from RNA.
- RNA paths address data generically: `path_from_id("mass")` returns `"game.components[0].mass"` **[V]**. Overrides, drivers and F-curves use these paths, and our live-link diffs can too.
- `bpy.props` are real RNA properties backed by IDProperties, so they appear in the UI, in undo and in overrides with no extra work.

### 2.3 ID datablocks and the Main database

Every top-level asset is an **ID** (`Object`, `Mesh`, `Material`, `Collection`, `Scene`, `NodeTree`, `Library`, `Action` …). An ID has a type code, a name unique within its type and library, a user count, and an optional `library` pointer. **Main** (`bpy.data`) holds one list per ID type, plus the new `all_ids` iterator in 5.2 **[V]**. Sub-data such as vertices and modifiers belongs to exactly one ID. Embedded IDs, such as a material's node tree, live inside their owner.

Useful facts **[V]**:
- `ID.session_uid` is unique and stable for the session, including across undo. It is **not** saved to the file, and there is no `bpy.data` lookup by session UID.
- Name lookup is a linear scan. `bpy.data.objects.get()` took 0.6 µs for the first of 10,000 objects and 628 µs for the last. Building a `{guid: ob}` dict over 10,000 objects took about 2–3 ms.
- `ob.copy()` duplicates custom properties, so a GUID stored as a custom property gets duplicated as well. We need a check that repairs duplicates.
- Name collisions are resolved automatically (`Dup` becomes `Dup.001`). Never assume a name you assigned is the name that ends up in the file.

### 2.4 Linking, appending, library overrides, and how they compare to prefabs

**Link** brings IDs in from another `.blend` as read-only references. **Append** makes local copies. **Pack** (new in 5.0) stores linked IDs inside the current file as an "archive" library: still read-only, but the file stands alone. A **library override** is a local ID that references a linked ID and stores a list of *operations* on RNA paths, such as `REPLACE` or `INSERT_AFTER`. The values themselves live in the local ID. When the file loads, Blender rebuilds each override from the current library data and replays the operations. The diff that generates operations runs on undo push and on save.

I built a full prefab workflow in headless Blender 5.2.2 **[V]**:

| Step | Result |
|---|---|
| Author `Crate` collection in `prefab_lib.blend`, link it into a level, place 3 collection-instance empties | Works. `depsgraph.object_instances` yields the instanced objects with `is_instance=True`. |
| Write to a **linked** object from Python (`lob["health"]=5`, `lob.location.x=9`) | **No error.** The value changes in memory but is lost on reload. This is a trap for AI agents. |
| `coll.override_hierarchy_create(scene, view_layer, reference=empty)` | Takes 0.28 ms and creates a local override collection linked into the scene. **The instancer empty is left behind** (the UI operator removes it), so we must delete it ourselves. |
| Default override objects | `is_system_override=True`, so they are read-only in the UI. `do_fully_editable=True` gives user-editable overrides. |
| Mesh data of the override | Stays **linked**. Geometry cannot be overridden, which matches the docs. |
| Edit overridden object, save, edit the library, reopen | Location override kept (42). Unoverridden `loot_table`, `scale` and mesh vertex count **propagated from the library**. |
| Custom prop `ob["armor"]` not flagged overridable | **Reverted to the library value** on reload. The docs say non-overridable changes are "restored from the linked reference". |
| `ob.property_overridable_library_set('["health"]', True)` in the library, and `bpy.props` with `override={'LIBRARY_OVERRIDABLE'}` | Overrides kept after reload (`health=250`, `gc.speed=9.0`). The non-flagged `gc.team` reverted. |
| `CollectionProperty(..., override={'LIBRARY_OVERRIDABLE','USE_INSERTION'})` | The override instance could **add** a component. It was stored as an `INSERT_AFTER` operation and survived reload. |
| `override_library.reset()` | Reverts to the library values. |
| Nested prefab (a library that instances another library) | Works. `Library.parent` records the chain. |
| `Library.reload()` after another process edited the library | Takes **6.3 ms**. The level picked up the new values (`armor=77`, `scale=3`) and **kept** its overrides (`health=250`). |
| `bpy.data.libraries.write(path, {ids})` / `bpy.data.temp_data()` / `libraries.load(assets_only=True)` | Writing a prefab file from a selection, reading a file without touching the current Main, and listing assets all work. |

Mapping Unity/Godot prefab concepts to Blender:

| Prefab concept | Blender mechanism | Fit |
|---|---|---|
| Prefab asset | Collection in a library `.blend`, marked as an asset | Good |
| Lightweight instance | Collection-instance empty | Good: cheap and exports as instancing |
| Instance with per-property overrides | Library override (`override_hierarchy_create`) | Good, **if** every property is flagged overridable |
| Add a component on an instance | `CollectionProperty` with `USE_INSERTION` | Good **[V]** |
| Remove or reorder inherited items | Not allowed ("Deletion or re-organization of existing items … is currently forbidden") | Workaround: an `enabled` flag per component |
| Edit geometry per instance | Not possible | Use a separate mesh or variant asset |
| Nested prefabs | Nested collection instances or linked libraries | Works **[V]** |
| Prefab variants | Overrides can chain, so the reference can itself be an override (docs) | **[U]** not tested |
| "Apply overrides to prefab" | No API | We would write it: open the library, apply, save, then `Library.reload()` |
| Live propagation of edits | `Library.reload()` | Works **[V]** |

**Assessment:** Overrides are a solid basis for prefabs. They were designed for reusing production characters and sets. They are also strict and fragile at the edges: resync is needed when the library hierarchy changes, override rules for invalid properties are dropped silently, and edit mode cannot be used on overrides. Our add-on should hide the raw overrides behind a small "Prefab" API. That API should create overrides fully editable, flag every component property as overridable, remove the instancer, and show which properties are overridden.

### 2.5 Collection instancing

`ob.instance_type='COLLECTION'` with `ob.instance_collection=coll` instances a whole collection. This is the cheap path for placing a prefab: nothing is copied, and the depsgraph expands instances at evaluation time **[V]**. Geometry Nodes can also generate instances. Both show up in `depsgraph.object_instances`, each with `matrix_world` and `is_instance`. The glTF exporter has `export_gpu_instances` (the `EXT_mesh_gpu_instancing` extension) and a full-hierarchy collection option **[V]: the parameters exist**.

---

## 3. Depsgraph and evaluation

Original data is what the user edits. Evaluated data is a copy-on-evaluation result with animation, drivers, constraints, modifiers and Geometry Nodes applied. Rules from the docs:
- Use `ob.evaluated_get(depsgraph)` for final state and `ob_eval.original` to get back. Selection only exists on originals.
- `ob_eval.to_mesh()` returns a temporary mesh. Free it with `to_mesh_clear()` and never keep a reference to it.
- Iterate `depsgraph.object_instances` without storing the items. `depsgraph.update()` invalidates evaluated references.

Measured **[V]**:

| Operation | Time |
|---|---|
| `evaluated_get`+`to_mesh` on Suzanne with Subsurf level 3 (507 → 31,658 verts), first evaluation | 45 ms |
| `foreach_get("co")` into numpy, 31k verts | **1.8 ms** |
| Same values read with a Python loop | 32.5 ms (18× slower) |
| `dg.update()` after a transform change | 0.04 ms |
| Move 500 objects (Python loop) | 1.7 ms. The following `dg.update()` took 2.3 ms. |

`bpy.app.handlers.depsgraph_update_post(scene, depsgraph)` fires after each evaluation. Its `depsgraph.updates` items carry `id`, `is_updated_transform`, `is_updated_geometry` and `is_updated_shading` **[V]**. This is the right hook for a live link: debounce it, then send diffs keyed by GUID.

---

## 4. Window manager, operators, timers, handlers, msgbus

**Operators** are the unit of user action. `poll(cls, context)` gates availability and greys out UI. `invoke(context, event)` is the interactive entry point and can open dialogs or start a modal. `execute(context)` does the work and runs again for redo. `modal(context, event)` receives events until it returns `FINISHED` or `CANCELLED`. In 5.2 `bl_options` can contain `REGISTER, UNDO, UNDO_GROUPED, BLOCKING, MACRO, GRAB_CURSOR(_X/_Y), DEPENDS_ON_CURSOR, PRESET, INTERNAL, MODAL_PRIORITY` **[V]**. `UNDO` pushes an undo step after `FINISHED`, and `REGISTER` adds the redo panel and the info log entry. Many operators depend on context: `bpy.ops.ed.undo()` fails `poll()` without a window **[V]**, so calls from timers need `context.temp_override(window=…, area=…, region=…)`.

**Can a game loop run in the viewport?** I measured it in GUI runs **[V]**:

| Loop driver | Achieved rate |
|---|---|
| Modal op with `event_timer_add(0.001)`, no redraw | ~995 Hz (OpenGL), ~910 Hz (Vulkan) |
| Same, with `area.tag_redraw()` every tick | **~75 Hz OpenGL** (vsync-locked), **~523 Hz Vulkan** |
| `event_timer_add(1/60)` | 59.2 Hz |
| `bpy.app.timers` callback returning `0.0` | ~181 Hz (the idle loop sleeps between iterations) |
| `RenderEngine.view_draw` calling `tag_redraw()` continuously | ~70 Hz (OpenGL) |
| `bpy.app.timers` under `--background --python` | **Never fires.** The script exits first. |

So yes, a modal operator can tick at display rate. It is still a poor place for a real game loop. Python is slow per entity and single-threaded, every change goes through the depsgraph into the next undo step, and input capture is limited. Use it only for "simulate in editor" previews, with a snapshot and restore around them.

**`bpy.app.handlers` in 5.2** **[V]**: `load_pre/post/post_fail`, `save_pre/post/post_fail`, `undo_pre/post`, `redo_pre/post`, `depsgraph_update_pre/post`, `frame_change_pre/post`, `animation_playback_pre/post`, `render_*`, `object_bake_*`, `composite_*`, `blend_import_pre/post`, `load_factory_startup_post`, `version_update`, `exit_pre` (new in 5.1), `translation_update_post`, `xr_session_start_pre`. Decorate handlers with `@bpy.app.handlers.persistent` so they survive file loads.

**msgbus** (`bpy.msgbus.subscribe_rna`) fires for RNA changes made through **Python and UI buttons**. I verified this: a 3 s modal loop that wrote `location` on every tick got 227 notifications, about one per write **[V]**. It does **not** fire for moving objects in the viewport or for animation (docs). Callbacks are deferred until operators finish and are coalesced. Subscriptions are cleared on file load, so re-subscribe in `load_post`. **For a live link, `depsgraph_update_post` is the reliable signal**, and msgbus suits UI-level reactions.

---

## 5. UI extensibility

What Python can register in 5.2 **[V]**: `Panel`, `Menu`, `Header`, `UIList`, `Operator`, `Macro`, `PropertyGroup`, `AddonPreferences`, `RenderEngine`, `NodeTree`/`Node`/`NodeSocket`, `Gizmo`/`GizmoGroup`, `WorkSpaceTool`, `AssetShelf`, `FileHandler`, `KeyingSetInfo`, `USDHook`.

- **Sidebar (N-panel) tabs**: `bl_space_type='VIEW_3D'`, `bl_region_type='UI'`, `bl_category="Game"` **[V]**.
- **Data**: attach a `PointerProperty(type=MyGroup)` to Object, Scene or Collection, and use `CollectionProperty` with a `UIList` for lists. Ad-hoc custom properties (`ob["k"]`) hold ints, floats, strings, arrays, dicts and ID references, with UI metadata set through `id_properties_ui()` **[V]**. Since 5.0, `bpy.props` values are no longer reachable through `ob["prop"]`. They are under `ob.bl_system_properties_get()` **[V]**.
- **Layout helpers**: `UILayout.panel()` for collapsible sub-panels, `progress()`, and in 5.2 `link()` and **`textbox()` for multi-line text** **[V]: these exist**. `textbox()` is enough for a basic AI prompt box.

**Python cannot create new editors (SpaceTypes).** `register_class` on a `Space` subclass raises "Space does not support registration" **[V]**. Workarounds, from most to least native:
1. **Sidebar tabs and pop-overs** in existing editors, such as an inspector or component editor in the 3D View N-panel.
2. **A custom `NodeTree` type** turns the Node Editor into our own editor, and the tree type appears in the editor's tree-type menu **[V]**. Sverchok and Animation Nodes have shipped this way for years.
3. **Draw handlers and a modal operator** on an existing space (Image Editor, Text Editor or 3D View) give a fully custom-drawn canvas with `gpu` and `blf`. This means rebuilding widgets by hand.
4. **AssetShelf** for a prefab palette in the viewport **[V]: registers headless**.
5. **App template** (`scripts/startup/bl_app_templates_*/<Name>/startup.blend` + `__init__.py`) **[V]: the directory layout exists**. It brands Blender as "our editor" with preset workspaces.
6. **An external UI process**, such as a web or Qt app, over the same socket bridge. This is the only option for genuinely new, rich tools, and I recommend it for the AI agent console if the in-panel UI is not enough.

---

## 6. Custom node trees (visual scripting) and Geometry Nodes

A custom tree needs registered classes for the `NodeTree`, its `NodeSocket` types (`draw`, `draw_color_simple`) and its `Node` types (`poll`, `init`, `draw_buttons`, `update`, `insert_link`). **[V]**: I built a `GSLogicTree` with `On Start` and `Print` nodes, a custom "Flow" socket plus the builtin `NodeSocketFloat`, a link between them, and an interface socket added with `tree.interface.new_socket(...)`. The tree is an ID in `bpy.data.node_groups`, so it saves, links, gets overridden and becomes an asset like any other datablock.

**Viability as a logic-graph editor:** good for *editing*, but there is no execution. We get the editor UX, undo, copy/paste, assets and linking for free. **Blender never evaluates a Python node tree.** The runtime compiles or interprets the serialized graph, which suits an editor-only Blender. Limits: node bodies only support `UILayout` widgets, and there are no custom zones. Custom group nodes are **[U]**. Python callbacks get slow once a graph reaches thousands of nodes.

**Geometry Nodes** is a closed C++ evaluator. Python can create GN trees, add `NODES` modifiers **[V]** and set inputs (in 5.2 through `modifier.properties.inputs.<id>.value`), but it **cannot add Python nodes to GN**. Use GN for procedural level content and read the result from the evaluated depsgraph (instances and meshes) at export time.

---

## 7. The `gpu` module, Vulkan and Metal

The `gpu` module is the only drawing API: `bgl` was removed in 5.0. The pattern is `gpu.types.GPUShaderCreateInfo` → `gpu.shader.create_from_info`, `gpu_extras.batch.batch_for_shader`, `SpaceView3D.draw_handler_add(cb, (), 'WINDOW', 'POST_VIEW'|'POST_PIXEL')`, and `gpu.types.GPUOffScreen` for off-screen rendering or `draw_view3d()` camera previews.

Verified in 5.2.2 **[V]**:
- The default backend is **OpenGL** on Windows: `preferences.system.gpu_backend == 'OPENGL'` with items `OPENGL, METAL, VULKAN`. `--gpu-backend vulkan` switched to Vulkan, and the same `create_from_info` shader with a `GPUStageInterfaceInfo` varying compiled and drew on both backends.
- `gpu.types.GPUShader(vert_src, frag_src)` now raises `TypeError: cannot create 'GPUShader' instances`. Raw-GLSL shaders are gone, so `create_from_info` is mandatory, and we write only the shader *bodies*. Builtin shader names changed: `3D_UNIFORM_COLOR` is gone, and `UNIFORM_COLOR`, `FLAT_COLOR`, `SMOOTH_COLOR`, `IMAGE`, `POLYLINE_UNIFORM_COLOR` and `POINT_UNIFORM_COLOR` work.
- **`gpu.init()` (new in 5.2) gives a GPU context in `--background`.** It took 0.7 s. A shader compiled in 7 ms, an off-screen triangle draw took 0.025 ms, and pixel readback was correct. Headless thumbnail and preview baking for an asset pipeline is now possible.
- `GPUOffScreen.draw_view3d` at 640×360 (Solid shading) took 2.6 ms per frame (GUI run).
- Compute shaders are always supported (`compute_shader_support_get` is deprecated as always True).
- `GPUTexture(data=…)` **only accepts `Buffer('FLOAT', …)`**: RGBA8 from a UBYTE buffer is rejected, and there is no in-place update method.

```python
info = gpu.types.GPUShaderCreateInfo()
info.vertex_in(0, 'VEC3', "pos")
info.push_constant('MAT4', "mvp"); info.push_constant('VEC4', "color")
info.fragment_out(0, 'VEC4', "FragColor")
info.vertex_source("void main(){ gl_Position = mvp * vec4(pos, 1.0); }")
info.fragment_source("void main(){ FragColor = color; }")
shader = gpu.shader.create_from_info(info)        # works on OpenGL and Vulkan [V]
```

Backend outlook: 4.5 reached Vulkan feature parity, 5.0–5.2 kept OpenGL as the default, and **5.3 makes Vulkan the default on Windows (except ARM) and Linux**. macOS uses Metal. Code written against `create_from_info` and `gpu.state` is backend-neutral. Anything that reaches native GL handles is not.

---

## 8. `bpy.types.RenderEngine`: our runtime inside the viewport?

A Python `RenderEngine` implements `render()` for F12 renders and `view_update(context, depsgraph)` / `view_draw(context, depsgraph)` for the viewport's Rendered mode. Class flags in 5.2 include `bl_use_eevee_viewport`, `bl_use_gpu_context`, `bl_use_shading_nodes_custom` and `bl_use_materialx` **[V]**. A test engine drew a full-region triangle and redrew at about 70 Hz when it called `self.tag_redraw()` every frame **[V]**.

To show *our* renderer, the frames have to come from somewhere:
- **Re-implement our renderer with the `gpu` module.** That duplicates the runtime's renderer, so it is limited and slow to build.
- **Stream frames from the runtime process.** This is Malt's approach: its bridge returns a buffer that `view_draw` wraps in a `GPUTexture` and draws as a quad. Measured upload **[V]**: about **9–23 ms per 1080p RGBA16F frame** and about 5 ms at 720p. FLOAT only, with a new texture every frame. Python has no zero-copy GPU texture sharing, so it would need a native module per backend **[U]**.
- Overlays and gizmos should still draw on top of a custom engine's output, as they do with Cycles. This is **[U]** for Python engines, and so is depth compositing with an external image.

**Verdict:** A RenderEngine streaming frames is viable for a 720p "game view" preview at 30–60 fps. It is not viable as the main play mode. For v1, launch the runtime in its own window and live-link it. Leave in-viewport rendering for later, possibly through a native extension.

---

## 9. Threading and concurrency

From the docs: "Python threads cause Blender to crash in hard to diagnose ways", and threads that keep running after the script finishes are unsupported. The officially recommended route is `multiprocessing`. In practice, the rule is: **a background thread may do pure I/O but must never touch `bpy`. All `bpy` work happens on the main thread through `bpy.app.timers`.** This is the pattern I verified (100 commands, median 13.3 ms, p95 14.1 ms round trip **[V]**):

```python
import bpy, json, queue, socket, threading
CMDQ = queue.Queue()                        # (command dict, reply queue)

def _serve(port=8765):                      # background thread: stdlib only, NEVER bpy
    srv = socket.create_server(("127.0.0.1", port))
    while True:
        conn, _ = srv.accept()
        with conn, conn.makefile("rwb") as f:
            for line in f:                  # newline-delimited JSON
                reply = queue.Queue(maxsize=1)
                CMDQ.put((json.loads(line), reply))
                f.write((json.dumps(reply.get(timeout=30)) + "\n").encode()); f.flush()

def _pump():                                # main thread via bpy.app.timers
    for _ in range(64):                     # bound work per tick -> UI stays responsive
        try:
            cmd, reply = CMDQ.get_nowait()
        except queue.Empty:
            break
        try:
            reply.put({"ok": True, "result": dispatch(cmd)})   # dispatch() uses bpy.data
        except Exception as e:
            reply.put({"ok": False, "error": repr(e)})
    return 0.01                             # reschedule in 10 ms

def register():
    threading.Thread(target=_serve, daemon=True).start()
    bpy.app.timers.register(_pump, persistent=True)   # survive File > Open
```

Notes:
- **A variant without threads**, which avoids the documented risk entirely: open a non-blocking socket and poll it with `selectors` (timeout 0) inside `_pump`. Latency is the same, with no thread. I recommend it as the default for the add-on.
- In `--background` mode timers never run **[V]**, so a headless worker runs its own `while True: poll(); handle()` loop in the script.
- Heavy computation (pathfinding bakes, LLM calls, asset processing) belongs in another process. Hand back plain data, then apply it with `bpy` on the main thread.

---

## 10. Extensions platform (4.2+) and legacy add-ons

| | Extension (4.2+) | Legacy add-on |
|---|---|---|
| Metadata | `blender_manifest.toml`: `schema_version="1.0.0"`, `id`, `version` (semver), `name`, `tagline` (≤64 chars, no trailing punctuation), `maintainer`, `type="add-on"`, `blender_version_min≥"4.2.0"`, `license=["SPDX:GPL-3.0-or-later"]` **[V]: bundled template** | `bl_info` dict |
| Module name | `bl_ext.<repo>.<id>`. Use `__package__` and relative imports only | Top-level module name |
| Dependencies | `wheels = ["./wheels/x.whl"]`, bundled unmodified from PyPI. `platforms` with `extension build --split-platforms` produces per-OS zips | Vendor them yourself |
| Writable storage | `bpy.utils.extension_path_user(__package__, create=True)` | Anything goes |
| Network | Declare `[permissions] network = "reason"` and check **`bpy.app.online_access`** (False by default in a factory setup **[V]**) | Not policed |
| Permissions | `files`, `network`, `clipboard`, `camera`, `microphone`, each with a one-line reason **[V]: template**. They are shown to users. Enforcement or sandboxing is **[U]**: nothing indicates one, and Python runs with full process rights. | n/a |
| Status in 5.2 | Recommended | Deprecated but still installable ("Install legacy Add-on") |

Wheel gotcha, from reading the 5.2 source in `_bpy_internal/extensions/wheel_manager.py` **[V]**: all extensions' wheels go into **one shared** `extensions/.local/lib/python3.13/site-packages`. Duplicate packages are deduplicated by name, and **the highest version wins**. Two extensions that pin different versions of `websockets` will collide. Prefer stdlib or vendored pure-Python code for the bridge. Commands: `blender --command extension build | validate`. The official platform only accepts GPL-compatible licenses.

## 11. `bpy` as a standalone module

PyPI `bpy` 5.2.2 was released on 2026-09-15 and requires **`Requires-Python ==3.13.*`**. Wheels exist for win-x64, win-arm64, manylinux_2_28 x64 and macOS 11+ arm64, at about 1.2 GB in total. "Each Blender release supports one Python version". Versions outside the LTS window are removed from PyPI but stay on download.blender.org. There is no UI. It is good for CI tests of our add-on logic, headless export or bake workers, and agent sandboxes. The Python 3.12 on this machine cannot install it: use a 3.13 venv, or `blender -b --python` with the bundled 3.13.13. Whether `gpu.init()` works inside the pip module is **[U]**.

---

## 12. Data export: glTF, USD, collection exporters

**glTF** (`io_scene_gltf2` v5.2.40, bundled, with 111 operator parameters **[V]**):
- `export_extras=True` writes **user custom properties and registered `bpy.props` values** (via `bl_system_properties_get()`) into node `extras`. Only values that were explicitly set exist as IDProperties, so defaults are missing **[V]**. Example: `"extras": {"spawn_group": 3, "gs": {"speed": 3.0}}`.
- **User-extension hooks**: the exporter scans every *enabled add-on* module for `glTF2ExportUserExtension(s)`, `glTF2_pre_export_callback` and `glTF2_post_export_callback` **[V]: source**. There are about 50 hooks (`gather_node_hook`, `gather_mesh_hook`, `gather_material_hook`, `gather_scene_hook`, `gather_gltf_extensions_hook`, `pre_export_hook`, `post_export_hook`, the animation hooks and others). I verified a hook that writes a vendor extension **[V]**:

```python
class glTF2ExportUserExtension:            # top level of our (enabled) add-on module
    def __init__(self):
        from io_scene_gltf2.io.com.gltf2_io_extensions import Extension
        self.Extension = Extension
    def gather_node_hook(self, gltf2_object, blender_object, export_settings):
        gs = blender_object.gs
        gltf2_object.extensions = gltf2_object.extensions or {}
        gltf2_object.extensions["GS_components"] = self.Extension(
            name="GS_components", extension={"script": gs.script, "speed": gs.speed}, required=False)
# -> "extensions": {"GS_components": {...}}, "extensionsUsed": ["GS_components"]
```
  I tested this as a legacy add-on module. Discovery under an extension's `bl_ext.*` module name is **[U]**, but the lookup goes through `preferences.addons` and `sys.modules`, so it should work.
- Speed: 500 objects and 50 unique meshes (UV spheres) exported to GLB in **0.96 s** (3.4 MB) **[V]**. Draco and gltfpack options are present.

**USD** (`bpy.ops.wm.usd_export`): the same scene took 0.53 s **[V]**. `export_custom_properties` writes `userProperties:<name>` attributes, but **registered PropertyGroup data was not exported** **[V]**. A `USDHook` subclass can be registered for custom writing. `pxr` is bundled, so Python can post-process the stage.

**Collection exporters** (4.2+): `coll.exporters.new("IO_FH_gltf2")`, set `export_properties.filepath`, then `bpy.ops.collection.export_all()` **[V]**. Each prefab or level collection stores its own export settings, which makes one-click "export all prefabs" easy.

**Fast programmatic export, recommended:** keep glTF plus our extension as the interchange and debug format. For the live link and fast iteration, write our own exporter that walks the evaluated depsgraph, pulls arrays with `foreach_get` into numpy (1.8 ms for 31k verts **[V]**) and streams binary chunks keyed by GUID, sending only `depsgraph.updates`.

---

## 13. Asset Browser and asset libraries

Any ID can be an asset: `id.asset_mark()`, `asset_data.description/tags/catalog_id/author/license`, `preferred_import_method` **[V]**. A library is a directory of `.blend` files plus `blender_assets.cats.txt` catalogs. Import methods in 5.2 are `LINK`, `APPEND`, `APPEND_REUSE` and `PACK` **[V]**. 5.2 adds **online asset libraries** (`UserAssetLibrary.remote_url`, `use_remote_url`, `asset.asset_download`) **[V]: properties and operators exist**, gated by "Allow Internet Access". Python can register an `AssetShelf` with `asset_poll` filtering, for example to show only collections **[V]**. `libraries.load(path, assets_only=True)` lists a file's assets without loading them **[V]**.

**Fit as a content browser:** good, with LINK as the default import method for prefabs, which feeds §2.4. Previews, catalogs, search and drag-and-drop come for free. Missing pieces: assets that are not IDs (scripts, audio cues) and "find references". Our bridge can index those itself with `file_path_foreach` and `libraries.load(...).libraries` (both 5.2) **[V]**.

---

## 14. Undo from Python and stale references

- An operator with `bl_options={'REGISTER','UNDO'}` pushes one undo step on `FINISHED`. Outside operators (timers, sockets), call `bpy.ops.ed.undo_push(message="AI: add 3 enemies")` yourself, with a window context. In the background, `undo_push` returns FINISHED but `ed.undo` fails `poll()` **[V]**. The default depth is 32 steps **[V]**.
- Global (memfile) undo re-reads changed IDs, so **Python references can die**. GUI tests **[V]**:
  - A reference to the changed object raised `ReferenceError: StructRNA of type Object has been removed`.
  - A reference to an unchanged object survived in one run and was invalidated in another.
  - `session_uid` stayed identical across undo and redo.
  - The docs say to "assume that undo and redo always invalidates all bpy.types.ID instances".
- `remove()` invalidates references cleanly **[V]**. Mode switches reallocate mesh data. Writes to linked data are silently lost **[V]**.
- Override diffs are computed at undo push and at save. `override_library.operations_update()` forces one **[V]**.
- Changes made from msgbus callbacks are not included in undo steps (docs).

**Rules for AI agents:**
1. Address everything by a persistent GUID: a custom property, or a PropertyGroup field with a duplicate check. Within a session, `session_uid` also works.
2. Resolve the GUID at the start of every command and never cache `bpy_struct` objects across commands.
3. Rebuild the GUID→ID cache in `undo_post`, `redo_post`, `load_post` and `depsgraph_update_post`.
4. Wrap each agent action in exactly one named undo step.
5. Refuse writes when `id.library` is set (linked data) or when `override_library.is_system_override` is set.

---

## 15. Breaking API changes in 5.0, 5.1 and 5.2 that affect us

| Version | Change | Impact on us |
|---|---|---|
| 5.0 | `bpy.props` storage separated from custom props. `ob['addon_prop']` no longer works. New `get_transform/set_transform`, the `READ_ONLY` option, `property_unset()`, `bl_system_properties_get()` | Design component storage around RNA access only |
| 5.0 | **`bgl` removed**, `Image.bindcode` removed, raw-GLSL shader creation removed | `gpu` + `create_from_info` only **[V]** |
| 5.0 | .blend: large-bhead format, compression on by default, 255-byte names, packed linked IDs | Never parse .blend in the runtime |
| 5.0 | Legacy Action API removed (`action.fcurves` etc.), replaced by slots and channelbags (`anim_utils.action_get_channelbag_for_slot`) | **Animation exporter must use the layered Action API** |
| 5.0 | `AssetHandle` removed (use `AssetRepresentation`). `template_asset_view` removed (use the asset shelf) | Asset UI via `AssetShelf` |
| 5.0 | mathutils buffer protocol with float32 buffers, so numpy may get non-contiguous matrices. `rna_info` and other bundled modules made private | Check numpy interop |
| 5.1 | **Python 3.13**. `exit_pre` handler. `bpy.app.cachedir`. `template_ID_session_uid` | Python 3.13 everywhere |
| 5.2 | GN modifier inputs: `modifier["Socket_2"]` → `modifier.properties.inputs.<id>.value` | Any GN parameter UI or AI tool must use the new path |
| 5.2 | `gpu.init()` in background. `UILayout.textbox/link`. `bpy.data.all_ids`, `file_path_foreach` options, `libraries.load` exposes `input.libraries`, `Window.screenshot` returns pixels | Headless previews. Agent screenshots of the UI (`Window.screenshot`) |
| 5.3 (in development) | Vulkan default on Windows and Linux | Test the `gpu` code on both backends now |

---

## 16. Licensing

- Blender is GPL (GPL-2.0-or-later, distributed as GPL-3.0). Blender's FAQ: "If you share or publish Python scripts they have to be made available licensed as GNU GPL as well". Add-ons may be sold, but they are GPL to the buyer. **Our Blender add-on (bridge, panels, exporters) will be GPL** if we distribute it.
- The same FAQ gives four conditions under which separate software keeps its own license: it "operates outside of Blender", "uses no Blender source code or API calls (including Python API)", "produces data for Blender to operate on", and "executes Blender to read and operate on the data". That wording covers a separate runtime or tool that reads exported files. A **live bidirectional socket protocol is not explicitly addressed**. The FSF GPL FAQ generally treats programs that communicate over pipes and sockets as separate works unless they exchange complex internal data structures intimately. **[U]**: the FSF page returned HTTP 429 and is paraphrased from memory, so have counsel confirm.
- Output is yours: "What you create with Blender is your sole property", including .blend files and exports.
- **Design consequence:** the runtime must never `import bpy` or link Blender code. It reads only our format or glTF, and the socket protocol should be a documented, generic message format that is not tied to Blender's structs, so another DCC could implement it. The `bpy` pip module is also GPL, so the same rule applies to any tool that imports it.

## 17. Performance limits of Python in Blender

Measured **[V]**:
- RNA access costs about 3–4 µs per property write: 500 `location` writes took 1.7 ms, and 5,000 `matrix_world` reads took 3.1 ms.
- `bpy.ops` cost grows with scene size: 1.9 ms per call in an empty scene, 217 ms with 5,400 objects. `bpy.data` creation stays at about 0.015 ms.
- Name lookup is linear.
- `foreach_get` is about 18× faster than a Python loop.
- The GIL is always on.

Rules:
- Move bulk arrays with `foreach_get`/`foreach_set` and numpy.
- Never call `bpy.ops` in loops.
- Keep handlers O(changed IDs) by iterating `depsgraph.updates`.
- Push heavy work to other processes.

---

## 18. Implications for our Blender-based engine

**1. Architecture: Blender is the editor and the runtime is a separate native process.**
Build a GPL add-on, "GS Bridge", plus an independent runtime in Rust or C++ that never imports `bpy`. Reasons: the GPL boundary (§16), the single-threaded Python with a GIL (§9, §17), crash isolation, and freedom to ship the runtime on any platform. "Play" starts the runtime in its own window. The add-on streams scene diffs over localhost. Do **not** pursue a UPBGE-style fork: it would put us on Blender's release treadmill and make everything GPL.

**2. Component data goes in PropertyGroups and is overridable by default.**
Attach `Object.gs` as a `PointerProperty`, with components as a `CollectionProperty` declared `override={'LIBRARY_OVERRIDABLE','USE_INSERTION'}` and every field `LIBRARY_OVERRIDABLE`. If a field lacks the flag, its prefab overrides are **silently reverted** (§2.4). Generate the component classes from a schema file shared with the runtime, so that the editor UI, the RNA metadata, the AI tool schemas and the runtime deserializer all come from one source. Store a persistent GUID per object and repair duplicates after copy and paste.

**3. Prefabs are linked collections plus overrides, wrapped in our own API.**
Placing a prefab creates a collection-instance empty. "Make editable" calls `override_hierarchy_create(..., do_fully_editable=True)` and deletes the leftover instancer. Saving a prefab writes the library and calls `Library.reload()`. We need to design for these limits: geometry is never per-instance, inherited components cannot be removed (use `enabled` flags), resync is needed when the prefab hierarchy changes, and Python writes to linked data must be blocked in our tools. Prototype "apply overrides back to prefab" early. It is the feature Unity users will expect, and Blender does not provide it.

**4. Export: glTF plus a vendor extension as the contract, and a binary live link for speed.**
Use the glTF user-extension hooks for `GS_components`, `GS_prefab_ref` and `GS_logic_graph` (§12). Configure collection exporters per level and prefab. For play-in-editor, use `depsgraph_update_post` (not msgbus, which misses viewport moves), debounce through a timer, and send GUID-keyed diffs with `foreach_get` arrays.

**5. AI agents: assist through a narrow, undoable command API.**
Run the MCP or agent server **outside** Blender and connect it to the add-on's socket. Commands run on the main thread through the timer pump and follow the five rules in §14. In particular, every agent action becomes one named undo step, so the developer can undo it with Ctrl+Z. Mutations go through `bpy.data`, never `bpy.ops`.

Keep read tools (scene graph, RNA-derived schemas, `Window.screenshot`, off-screen camera renders) separate from write tools, and default to "propose, then apply". For batch work, run headless workers (`blender -b`, or `bpy` on 3.13) with their own loop.

**6. Editor UI: native panels first, a custom node editor for logic, and an external app for richer tools.**
Put the inspector and component editing in a "Game" N-panel tab. Build logic graphs as a custom `NodeTree` that the runtime compiles. Use an AssetShelf for the prefab palette, an app template for branded workspaces, and `UILayout.textbox` for a minimal agent prompt. If the agent UX needs threads, diffs, or rich markdown, build it as an external web UI on the same bridge, since Python cannot add an editor.

**7. Rendering: use Blender's viewport plus `gpu` overlays, with the game in its own window.**
Draw gameplay gizmos (colliders, triggers, nav meshes, AI paths) with `draw_handler_add` and `create_from_info` shaders, and test them on both OpenGL and Vulkan now, because 5.3 switches the default. Do not build v1 around a RenderEngine that streams frames: 9–23 ms per 1080p upload, FLOAT only. Revisit it later through a native-module texture-sharing path if an in-viewport game view becomes a must.

**8. Versions: target 5.2 LTS (to July 2028) and Python 3.13.**
Run CI with `bpy==5.2.*` on Python 3.13. Keep a smoke test against 5.3 for Vulkan and API drift. Ship as an **extension** with stdlib-only or vendored dependencies, to avoid the shared-wheel version collisions (§10). Declare `network` and `files` permissions, and honour `bpy.app.online_access`. A localhost-only bridge arguably still needs the `network` declaration, so declare it.

---

## 19. Sources

Official docs, release notes and source:
- https://developer.blender.org/docs/release_notes/5.0/python_api/
- https://developer.blender.org/docs/release_notes/5.0/core/
- https://developer.blender.org/docs/release_notes/5.1/python_api/
- https://developer.blender.org/docs/release_notes/5.2/
- https://developer.blender.org/docs/release_notes/5.2/python_api/
- https://developer.blender.org/docs/release_notes/5.2/assets/
- https://developer.blender.org/docs/release_notes/5.2/core/
- https://developer.blender.org/docs/release_notes/5.3/eevee/
- https://developer.blender.org/docs/features/core/
- https://developer.blender.org/docs/features/core/dna/
- https://developer.blender.org/docs/features/core/rna/
- https://developer.blender.org/docs/features/core/overrides/library/
- https://developer.blender.org/docs/features/core/undo/
- https://docs.blender.org/api/current/bpy.types.Depsgraph.html
- https://docs.blender.org/api/current/bpy.app.timers.html
- https://docs.blender.org/api/current/bpy.msgbus.html
- https://docs.blender.org/api/current/info_gotchas_crashes.html
- https://docs.blender.org/api/current/info_gotchas_threading.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/getting_started.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/addons.html
- https://docs.blender.org/manual/en/latest/advanced/extensions/python_wheels.html
- https://pypi.org/project/bpy/
- https://www.blender.org/about/license/
- https://www.blender.org/support/faq/
- https://github.com/KhronosGroup/glTF-Blender-IO/tree/main/example-addons
- https://github.com/bnpr/Malt and https://raw.githubusercontent.com/bnpr/Malt/Development/BlenderMalt/MaltRenderEngine.py (prior art for streaming frames into a RenderEngine)
- https://git.autourdeminuit.com/autour_de_minuit/blender-asset-tracer/src/branch/main/blender_asset_tracer/blendfile/header.py (third-party parser that handles the LargeBHead8 block header)
- https://phoronix.com/news/Blender-5.0-Released (secondary: Vulkan status in 5.0)
- Local source read: `5.2/scripts/addons_core/io_scene_gltf2/__init__.py` and `blender/com/extras.py` (hook discovery, extras), `5.2/scripts/modules/_bpy_internal/extensions/wheel_manager.py` (shared site-packages, version dedupe), `5.2/scripts/templates_toml/blender_manifest.toml`

Not fetched: https://www.gnu.org/licenses/gpl-faq.html returned HTTP 429, so its position in §16 is paraphrased and marked **[U]**.

### Claims verified against the local Blender 5.2.2 LTS

The probe scripts are in the session scratchpad: `probe1_basics.py` through `probe10_insertion.py`.

Every **[V]** tag in this document was verified locally. In summary:
- **Runtime:** 5.2.2 LTS, Python 3.13.13, GIL build.
- **File format:** .blend header and block-header layout, `DNA1`, zstd compression by default.
- **Registration:** `Space` cannot be registered. The other UI and engine types register.
- **Properties:** `bpy.props` storage is separate from custom properties.
- **Prefab workflow:** the whole override workflow in §2.4, including `USE_INSERTION`, `Library.reload()`, and Python writes to linked data being lost.
- **Evaluation:** depsgraph timings and handler fields.
- **Loop and threading:** handler list, operator flags, modal, timer and RenderEngine rates on OpenGL and Vulkan, msgbus on Python writes, no timers in the background, socket-to-timer latency.
- **Undo:** stale references and `session_uid` stability.
- **GPU:** behaviour, removals and costs.
- **Export:** glTF extras and user-extension hook, USD custom properties, collection exporters.
- **Assets:** asset API surface.
- **Costs:** `bpy.ops` vs `bpy.data`, linear name lookup, GUID duplication on copy.
- **New in 5.2:** UI and data functions.
