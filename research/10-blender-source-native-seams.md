# 10: Native seams for a GameStudio fork of Blender

*Research date: 2026-10-09. Source: shallow clone of Blender **v5.2.2** (commit `d13f752e`) at `F:\Development-games\blender-upstream`. Every claim cites `file:line` in that checkout. Claims I could not confirm in code are marked **(inferred)**.*

*Path convention: paths that start with `intern/`, `build_files/`, `scripts/` or `CMakeLists.txt` are relative to the repository root. All other paths are relative to `source/blender/`. For example, `editors/screen/area.cc` means `source/blender/editors/screen/area.cc`.*

*Churn data: the clone has a single commit, so `git log` cannot show file history. I counted upstream commits per file on `main` through the Gitea API on projects.blender.org (`/api/v1/repos/blender/blender/commits?path=…&since=…`, reading `X-Total-Count`). Section 11 has the numbers.*

---

## 1. TL;DR

- **A new editor is mostly self-contained.** The status bar is the smallest editor at 165 lines plus a 35-line CMake file. A useful editor with header, sidebar and main regions, RNA and blend I/O needs about 400–700 new lines. It also needs about 10 small edits in roughly 10 upstream files: an enum value, a registration call, CMake lines, an RNA enum item and refine case, and the draw-handler lookup table.
- **Correction (verified 2026-10-09 with a real fork-saved file):** the active area is *not* turned into an Info editor. Stock Blender reads the SpaceExtension block, marks the area `SPACE_EMPTY`, and `ED_area_init` opens it as a **3D Viewport**. Nothing crashes and the other areas are unchanged. The original prediction follows.
- **Stock Blender opens fork files safely.** An area whose space type is unknown is not dropped and does not crash Blender. If the fork stores its space data in a new DNA struct, stock Blender ignores that struct. The area becomes an **Info editor**, and any region types Info lacks are removed with a warning. If the struct is known but the space ID is not, the area falls back to a **3D Viewport**. Saving in stock Blender makes the conversion permanent.
- **One real crash hazard exists.** Workspace tool references (`bToolRef.space_type`) are read without validation, and stock Blender uses that value to index a `SPACE_TYPE_NUM`-sized stack array. A fork must therefore **not register `WorkSpaceTool`s for its new space**.
- **Python cannot register a `Space` because `rna_def_space` never calls `RNA_def_struct_register_funcs`.** A Python-only design would fail anyway: Blender reads the startup file and its areas *before* Python starts. A new space type therefore has to be native. Its *behaviour* can be supplied later by registered providers.
- **The "generic extension editor" design is cheap because the machinery already exists.** Python `Panel`/`Header` classes attach to any space through `bl_space_type`. `bl_context` filters panels by a string, which can carry the provider ID. The editor-type menu already supports runtime-defined sub-types through `space_subtype_item_extend`, the same mechanism that lists Python-registered node-tree types. Persistent `IDProperty` storage on UI data has two precedents: `View3DShading.prop` and `uiList.properties`. Estimated patch: about 110–130 upstream lines across about 11 files, plus about 1,000–1,500 lines of fork-owned code.
- **Do not embed native child windows.** Menus, popups, tooltips and the drag image are regions drawn into the *same* framebuffer as the editors, so an OS child window would cover them. GHOST has no child-window code. Its OS-handle accessor is internal and returns `nullptr` on X11 and Wayland, and Wayland cannot embed a surface from another client at all. **Stream frames into a region texture instead.**
- **GPU:** No API anywhere in `gpu/` imports external memory. Export exists only on Vulkan: `VKTexture::export_memory` is used by OpenXR, and `GPU_pixel_buffer_get_native_handle` is used by Cycles' CUDA/HIP interop. The fast in-process path is the one Cycles' `BlenderDisplayDriver` uses: `GPU_pixel_buffer_map` → memcpy → `GPU_texture_update_sub_from_pixel_buffer`. The Python `gpu` module cannot do this, because it accepts only FLOAT buffers and has no update method.
- **A built-in `_gamestudio` module takes one `_inittab` entry**, following the precedent of Hydra's `_bpy_hydra`. **CMake:** one guarded `add_subdirectory(${GS_NATIVE_DIR} …)` plus one `list(APPEND LIB bf_gamestudio)` in each consumer. Static-library dependencies are transitive, so nothing needs to change in `source/creator`.
- **Redraw at 60 Hz needs no WM patch.** `WM_event_timer_add_notifier` posts a notifier on every tick. A notifier aimed at one space redraws only that editor, and WM timers shorten the main loop's idle sleep so they fire on time.

---

## 2. How an editor (SpaceType) is implemented

### 2.1 The parts of an editor

| Piece | Where | What it is |
|---|---|---|
| Space ID | `makesdna/DNA_space_enums.h:1151-1188` | `enum eSpace_Type : char`, from `SPACE_EMPTY=0` to `SPACE_SPREADSHEET=23`, plus `#define SPACE_TYPE_NUM (SPACE_SPREADSHEET + 1)`. The comment says "Do NOT change order, append on end" (`:1148-1149`). |
| Space data (DNA, saved) | `makesdna/DNA_space_types.h` | Every space struct starts with the `SpaceLink` header: `next/prev`, `regionbase`, `spacetype`, `link_flag`, and padding (`:95-101`). Examples: `SpaceInfo` at `:111-121`, `SpaceStatusBar` at `:1085-1094`. |
| Behaviour (runtime) | `blenkernel/BKE_screen.hh:100-188` | `struct SpaceType`. It holds `create`, `free`, `init`, `exit`, `listener`, `refresh`, `duplicate`, `operatortypes`, `keymap`, `dropboxes`, `gizmos`, `context`, `id_remap`, `foreach_id`, the `space_subtype_*` and `space_name_get`/`space_icon_get` hooks, `blend_read_data`, `blend_read_after_liblink`, `blend_write`, `regiontypes` and `keymapflag`. |
| Regions | `blenkernel/BKE_screen.hh:249-330+` | `struct ARegionType`. It holds `init`, `exit`, `poll`, `draw`, `draw_overlay`, `layout`, `listener`, `message_subscribe`, `cursor`, `context`, the `drawcalls` list (draw handlers), `paneltypes`, `headertypes`, and size and keymap flags. |
| Registry | `blenkernel/intern/screen.cc:340-380` | A `Vector<unique_ptr<SpaceType>>`. `BKE_spacetype_register` refuses duplicate IDs (`:365-375`), and `BKE_spacetype_from_id` searches the list linearly (`:340-348`). |

### 2.2 Walkthrough: the status bar, with the Script editor for the main region

The status bar is the smallest editor (`editors/space_statusbar/space_statusbar.cc`, 165 lines). It has only a header region. The deprecated Script editor (`editors/space_script/space_script.cc`, 220 lines) shows a WINDOW region and ID handling.

1. **DNA.** `SpaceStatusBar` contains nothing beyond the `SpaceLink` header (`makesdna/DNA_space_types.h:1085-1094`). Its ID is `SPACE_STATUSBAR = 22` (`DNA_space_enums.h:1181`).
2. **create.** The function allocates the struct, sets `spacetype`, and adds the default regions with `BKE_area_region_new()` (`space_statusbar.cc:34-49`; for the Script editor's header and window regions, `space_script.cc:40-64`). The WM calls it from `ED_area_newspace` (`editors/screen/area.cc:2881`) and `screen_edit.cc:602`.
3. **free / duplicate / init.** The status bar's versions are trivial (`space_statusbar.cc:51-64`). `duplicate` is a `MEM_dupalloc` of the struct. The region lists are copied generically by `BKE_spacedata_copylist` (`blenkernel/intern/screen.cc:514-529`) and freed by `BKE_spacedata_freelist` (`:407-422`).
4. **Region types.** Each region type is an `ARegionType` with `regionid`, `prefsizey`, `keymapflag` (`ED_KEYMAP_UI | ED_KEYMAP_VIEW2D | ED_KEYMAP_HEADER`) and callbacks. The status bar uses the generic `ED_region_header_layout` and `ED_region_header_draw` (`space_statusbar.cc:149-160`). The Script editor's WINDOW region has its own `init`, which sets up View2D and installs a keymap with `WM_keymap_ensure(..., "Script", SPACE_SCRIPT, RGN_TYPE_WINDOW)`, and its own `draw` (`space_script.cc:93-130, 195-204`).
5. **Draw path.** `ED_region_do_draw` sets the viewport and scissor to `region->winrct`, calls `at->draw(C, region)`, and then **always** runs the region type's `REGION_DRAW_POST_PIXEL` draw handlers (`editors/screen/area.cc:488-528`). The handler list lives on the `ARegionType` (`editors/space_api/spacetypes.cc:231-269`).
6. **Listeners and message bus.** The status bar redraws on specific notifiers (`space_statusbar.cc:79-112`) and subscribes to RNA properties through the message bus (`:114-126`).
7. **Registration.** `ED_spacetype_statusbar()` fills a `SpaceType` and calls `BKE_spacetype_register` (`space_statusbar.cc:133-163`). `ED_spacetypes_init()` calls every `ED_spacetype_*()` (`editors/space_api/spacetypes.cc:63-88`). It then loops over all space types to call `gizmos()` and `operatortypes()` (`:141-149`). `ED_spacetypes_keymap` calls each `keymap()` (`:212-217`). `WM_init` calls `ED_spacetypes_init()` (`windowmanager/intern/wm_init_exit.cc:230`). The declarations live in `editors/include/ED_space_api.hh:29-48`.
8. **RNA and Python.** The editor gets an entry in `rna_enum_space_type_items` (`makesrna/intern/rna_space.cc:90-185`; status bar at `:156-161`). That entry is what makes `bl_space_type='STATUSBAR'` valid. `rna_Space_refine` maps space IDs to RNA subclasses (`:759-806`). The status bar and top bar map to plain `RNA_Space` because they have "no type info" (`:797-805`). A typical subclass is `SpaceInfo` (`rna_def_space_info`, `:8214-8247`), which `RNA_def_space` registers (`:9523-9550`). The status bar's UI is plain Python: `STATUSBAR_HT_header(Header)` with `bl_space_type = 'STATUSBAR'` (`scripts/startup/bl_ui/space_statusbar.py:8-9`).
9. **Blend I/O.** `blend_write` writes the struct (`space_statusbar.cc:128-131`). Spaces with pointers also implement `blend_read_data`. Spaces that reference IDs also implement `blend_read_after_liblink` and `foreach_id`, as the Script editor does (`space_script.cc:151-175`). The generic code writes every region and then calls `space_type->blend_write` (`blenkernel/intern/screen.cc:1347-1384`). On read, `direct_link_area` (`:1538-1608`) calls `space_type->blend_read_data`. After linking, `BKE_screen_area_blend_read_after_liblink` (`:1657-1683`) calls `blend_read_after_liblink` and removes any regions whose type no longer exists (`regions_remove_invalid`, `:1639-1655`).

### 2.3 Files and lines a new editor touches

The spreadsheet, the most recent editor (2021), is referenced in 18 files. Most of those references are optional special cases.

| File | Change | Lines | Needed? |
|---|---|---|---|
| `makesdna/DNA_space_enums.h:1182-1184` | New enum value and updated `SPACE_TYPE_NUM` | 2–3 | Yes |
| `makesdna/DNA_space_types.h` | New `SpaceXxx` struct | ~15–25 | Yes, or use an out-of-tree header (§8) |
| `editors/space_xxx/space_xxx.cc` + `CMakeLists.txt` | Implementation | 165–700 + 35 | Yes, new files |
| `editors/CMakeLists.txt:33-52` | `add_subdirectory(space_xxx)` | 1 | Yes |
| `editors/space_api/CMakeLists.txt:21-41` | Link `bf_editor_space_xxx` | 1 | Yes |
| `editors/space_api/spacetypes.cc:70-88` | Call `ED_spacetype_xxx()` | 1 | Yes |
| `editors/include/ED_space_api.hh:29-48` | Declaration | 1 | Yes |
| `makesrna/intern/rna_space.cc` | Enum item, refine case, `rna_def_space_xxx`, call | ~10 + 30–100 | Yes, for Python |
| `python/intern/bpy_rna_callback.cc:199-250` | `rna_Space_refine_reverse` case, so `draw_handler_add` works | 2 | Yes, for Python draw handlers |
| `windowmanager/WM_types.hh:575` | `ND_SPACE_XXX` notifier | 1 | Optional |
| `windowmanager/intern/wm_draw.cc:590-617` | Name in debug `switch` | 1 | Optional (`-Wswitch` warning only; `CMakeLists.txt:1909-1915` errors on other warnings, not on `switch`) |
| `editors/interface/resources.cc:176-187` | Theme lookup | 0 | No. Falls back to the 3D view theme (`:185-187`). |
| `scripts/modules/bl_keymap_utils/keymap_hierarchy.py:180` | Keymap editor entry | 1 | Optional |

**Total: about 9 upstream files and 20–130 changed lines, depending on how much RNA you want, plus the new directory.**

---

## 3. Unknown space types in a .blend file

### 3.1 The read path

1. **DNA level.** At load time, each struct in the file's SDNA is compared with the running Blender's SDNA. A struct name the running Blender doesn't know is flagged `SDNA_CMP_REMOVED` (`makesdna/intern/dna_genfile.cc:640-646`). `read_struct` reads nothing for such blocks (`blenloader/intern/readfile.cc:1836`), and `read_data_into_datamap` puts nothing in the old→new address map for them (`:2776-2799`). Any pointer to such a block resolves to `nullptr`.
2. **List reading.** `BLO_read_struct_list_with_size` follows the `next` pointers (`readfile.cc:6024-6042`). A null at any element ends the list there. **The unknown space and every space after it in that area's `spacedata` list are lost.** Spaces after it are previously used editors kept in the area's history.
3. **Area level** (`blenkernel/intern/screen.cc:1538-1608`):
   - If `area->spacetype` is not registered, it is set to `SPACE_EMPTY`, and the old value is kept in `butspacetype` as a "hint for versioning code" (`:1559-1564`). Only `versioning_280.cc:2403-2420` uses that hint, and only for `SPACE_TIME` and `SPACE_LOGIC`.
   - Regions of an unknown space get `regiondata = nullptr` "so we don't leak" (`:1475-1478`).
   - **If `spacedata` is now empty, which happens when the unknown space was the active one, Blender creates a `SpaceInfo` and sets the area to `SPACE_INFO`** (`:1570-1576`).
   - Any surviving `SpaceLink` with an unregistered ID is set to `SPACE_EMPTY` (`:1588-1590`).
4. **After linking** (`:1657-1683`): spaces with no `SpaceType` have all their regions freed. For known spaces, `regions_remove_invalid` frees regions of unknown types and logs `"Region type %d missing in space type ..."` (`:1639-1655`).
5. **Area initialisation** (`editors/screen/area.cc:2205-2252`): if `area->type` is still null, `area_init_type_fallback(area, SPACE_VIEW3D)` picks an existing 3D view space from the area's history or creates one.
6. Unclaimed data blocks are freed when the address map is cleared (`readfile.cc:329-338`), so nothing leaks. This covers an orphaned `IDProperty` owned by the fork's space.

### 3.2 Outcomes for a fork file opened in stock 5.2

| Fork stores its editor as | Stock Blender result |
|---|---|
| New DNA struct (`SpaceGameView`), **active** in the area | The area becomes an **Info editor**. HEADER and WINDOW regions survive because Info defines both (`editors/space_info/space_info.cc:272,283`). Other region types are removed with a log warning. The fork's state is discarded. |
| New DNA struct, **inactive** (in history) | That space and the spaces after it in the history are silently dropped. The active editor is untouched. |
| Existing DNA struct with an unknown `spacetype` ID | The area falls back to a **3D Viewport** (`area.cc:2237-2244`). |

**Verdict: no crash and no lost area in the editor layout itself. The area is converted, not dropped.** Saving in stock Blender keeps the conversion. When the fork reopens such a file, it sees an ordinary Info or 3D View area.

### 3.3 The one real hazard: workspace tools

`bToolRef.space_type` (`makesdna/DNA_workspace_types.h:96`) is read with no validation (`blenkernel/intern/workspace.cc:157,169-173`). `WM_toolsystem_refresh_screen_window` then executes `space_type_has_tools[tref.space_type] = true` on a `bool[SPACE_TYPE_NUM]` stack array (`windowmanager/intern/wm_toolsystem.cc:894-896`). A fork file that contains a tool for a space ID ≥ 24 therefore triggers **an out-of-bounds stack write in stock Blender**. A smaller example: `wm_dragdrop.cc:269-279` only *asserts* `area->spacetype < SPACE_TYPE_NUM`, but area IDs are already sanitised on read, so that case is safe.

**Rules for the fork:**
1. Never register `WorkSpaceTool`s with `bl_space_type` set to the new editor, or strip those `bToolRef`s in a save-pre handler.
2. Use only the existing `RGN_TYPE_*` values for the new editor's regions.
3. Pick the new space ID deliberately (§4.4).

---

## 4. The generic extension editor (`SPACE_EXTENSION`)

### 4.1 Why `bpy.utils.register_class(MySpace)` fails

`pyrna_register_class` (`python/intern/bpy_rna.cc:10364`) calls `RNA_struct_register(srna)`, which returns `srna->reg` (`makesrna/intern/rna_access.cc:1084-1087`). It then raises `"expected a subclass of a registerable RNA type (%s does not support registration)"` when that is null (`bpy_rna.cc:10464-10473`). `rna_def_space` (`makesrna/intern/rna_space.cc:4123-4146`) never calls `RNA_def_struct_register_funcs`. The types that do register are `Panel`, `UIList`, `Header` and `Menu` (`makesrna/intern/rna_ui.cc:1849, 2028, 2204, 2272`), `Node`/`NodeTree` (`rna_nodetree.cc:9337, 9917`), `RenderEngine` (`rna_render.cc:544`), `Operator` (`rna_wm.cc`) and a few others.

Adding a register function would not be enough. **Load order rules out a pure Python solution:** `WM_init` registers space types (`wm_init_exit.cc:230`), reads the startup or home file including every screen and area (`:283`), and only then starts Python (`:331`). A space type registered from Python would not exist while `startup.blend` is read, so §3 would turn every such area into an Info editor before any add-on runs. **The space type must be native. Only its behaviour can come later.** This is the same split the node editor uses: `SPACE_NODE` is native, and tree types arrive from Python.

### 4.2 Existing machinery we can reuse

| Need | Existing mechanism | Evidence |
|---|---|---|
| Python panels in the editor | `Panel.bl_space_type` resolves through `BKE_spacetype_from_id` and the region type list | `rna_ui.cc:88-110` (`region_type_find`), `:257-300` (`rna_Panel_register`) |
| Per-provider panel filtering | `Panel.bl_context` → `PanelType::context`, checked against a `contexts[]` array that the region's draw passes in. The 3D view passes the mode string. | `blenkernel/BKE_screen.hh:361`, `rna_ui.cc:1971`, `editors/screen/area.cc:3247-3285` (`panel_add_check`), `editors/space_view3d/space_view3d.cc:1392-1396` |
| Python headers and menus | `Header.bl_space_type` → `art->headertypes`, drawn by `ED_region_header_layout` | `rna_ui.cc:2204, 2234`; `area.cc:3876, 4010` |
| Panels-only main region (Preferences-style) | `ED_region_panels_init` / `ED_region_panels_layout` / `ED_region_panels_draw` | `area.cc:3613-3712`; `editors/space_userpref/space_userpref.cc:420-460` |
| Raw GPU drawing from Python | `Space.draw_handler_add(cb, args, region_type, 'POST_PIXEL')` → `ED_region_draw_cb_activate`, which runs after every region draw | `python/intern/bpy_rna_callback.cc:258-348`; `bpy_rna_types_capi.cc:227`; `area.cc:528` |
| Providers in the editor-type menu | `SpaceType::space_subtype_item_extend/get/set` plus `space_name_get`/`space_icon_get`. `Area.ui_type` encodes `(space << 16) \| subtype`. | `makesrna/intern/rna_screen.cc:202-286`; node editor: `editors/space_node/space_node.cc:1632-1670, 1735-1739`; `rna_nodetree.cc:743-773` |
| Persistent provider ID string | `SpaceNode.tree_idname[64]` is the precedent | `makesdna/DNA_space_types.h:876` |
| Persistent provider state | An `IDProperty *` on UI data, written with `IDP_BlendWrite` and read with `IDP_BlendDataRead`, exposed as RNA "system idprops" so `bpy.props` can live there | `View3DShading.prop`: `blenkernel/intern/screen.cc:1272,1280`, `rna_space.cc:1426-1430, 4694`. `uiList.properties`: `DNA_screen_types.h:449`, `screen.cc:1328,1466`, `rna_ui.cc:490, 2029`. Check: `rna_access.cc:803-806` |
| Hiding regions per provider | `ARegionType::poll` | `BKE_screen.hh:258-264`; `editors/screen/screen_edit.cc:698-735` |
| Targeted redraws | An `NC_SPACE` notifier whose `reference` is the `SpaceLink` reaches only that area | `windowmanager/intern/wm_event_system.cc:792-799` |

### 4.3 Minimal design

**DNA** (append to `DNA_space_types.h` or put in an out-of-tree header, §8). Illustrative only:

```cpp
struct SpaceExtension {
  SpaceLink *next = nullptr, *prev = nullptr;
  ListBaseT<ARegion> regionbase = {nullptr, nullptr};
  char spacetype = 0;
  char link_flag = 0;
  char _pad0[6] = {};
  /* End 'SpaceLink' header. */
  char provider_idname[64] = "";   /* like SpaceNode.tree_idname */
  int flag = 0;
  char _pad1[4] = {};
  IDProperty *properties = nullptr; /* provider state, saved */
  void *runtime = nullptr;          /* cleared on write/read */
};
```

**SpaceType callbacks** (fork-owned `space_extension.cc`):
- `create` builds HEADER, UI (sidebar), WINDOW and optionally FOOTER regions.
- `blend_write` writes the struct and `IDP_BlendWrite(properties)`. `blend_read_data` reads `properties` and clears `runtime`. `foreach_id` walks ID pointers inside the IDProperty.
- `space_subtype_item_extend/get/set` list the registered providers and switch between them. `space_name_get`/`space_icon_get` return the provider's name and icon, or "Extension Editor (missing: x)" when the provider is not registered.
- `listener` calls `ED_region_tag_redraw` on `NC_SPACE|ND_SPACE_EXTENSION`.

Each region type calls into the provider:
- **HEADER** uses `ED_region_header`. Python headers check `context.space_data.provider_idname` in their `poll`.
- **UI** uses `ED_region_panels_ex(C, region, ..., contexts={provider_idname, nullptr})`, so a panel is selected by `bl_context = "gamestudio.game_view"`.
- **WINDOW** calls the provider's C++ `draw` if it has one. Otherwise it uses panels filtered the same way. `POST_PIXEL` handlers then run automatically.
- `poll` asks the provider which regions it wants.

**Provider registry** (fork-owned, C++): `ExtensionEditorProvider{idname, ui_name, icon, init, exit, draw_window, poll_region}` stored in a `Vector`. Python registers through `_gamestudio.register_editor_provider(idname, name, icon)` (§7). A later, upstream-quality version could add a registerable `bpy.types.EditorProvider` modelled on `rna_Header_register`, at roughly 300–500 more lines.

**Space ID.** The ID is stored in files as a `char` (`enum eSpace_Type : char`), so it must be ≤ 127. `SPACE_TYPE_NUM` must include it because of `wm_toolsystem.cc:894` and `wm_dragdrop.cc:269`. There are two options. Taking 24 creates the smallest diff, but it collides if upstream ever adds a 25th editor. Taking a reserved high value, such as 64, together with `SPACE_TYPE_NUM (SPACE_EXTENSION + 1)` avoids collisions and costs only a few unused array slots (**recommended**). Upstream has added one editor since 2.80 (the spreadsheet), so either choice rarely conflicts.

### 4.4 Patch size estimate

| Part | Upstream lines | Fork-owned lines |
|---|---|---|
| Enum, `SPACE_TYPE_NUM` | 3 | – |
| DNA struct | 20 (or 1 CMake line) | (20) |
| Register call and declaration, behind `#ifdef WITH_GAMESTUDIO` | 4 | – |
| CMake: `space_api` link, `python` link, `source/blender` hook | 10 | 30 |
| RNA: enum item, refine, `rna_def_space_extension` (provider, system idprops) | 60–80 | – |
| `rna_Space_refine_reverse` | 3 | – |
| `ND_SPACE_EXTENSION` | 1 | – |
| `space_extension.cc` | – | 500–700 |
| Provider registry | – | 150–250 |
| `_gamestudio` Python module | 5 (inittab) | 300–600 |
| **Total** | **≈110–130 lines in ≈11 files** | **≈1,000–1,600** |

### 4.5 Prior art

Nothing has been built or accepted.
- The Python API docs still list "Create new space types" under missing features (docs 4.0–4.2).
- Bastien Montagne (mont29) wrote a wiki stub, last edited 2018-12-16, proposing that scripts could "work in similar way to panels or UILists - but for whole spaces". It calls itself a "rough stub", notes "real design is fully TODO", and warns that "complete space is much more complex than a simple panel".
- The devtalk thread "Add custom spaces in Python API" (2021) got no developer reply. Its suggested workarounds were the custom node-tree template and taking over the Preferences editor.
- An early 2.5-era experiment existed: a 2012 `pyapi_devel_26` branch commit "initial python space extension". I didn't search projects.blender.org exhaustively; its issue pages blocked automated fetches (HTTP 403).

Upstreaming a provider-based extension editor would need a design task first (**inferred**).

---

## 5. Windowing (GHOST) and embedding external content

### 5.1 Native window handles

- `GHOST_IWindow`, the public interface (`intern/ghost/GHOST_IWindow.hh:34`), has **no** OS-handle accessor.
- `virtual void *getOSWindow() const` exists only on the internal base class `GHOST_Window` (`intern/ghost/intern/GHOST_Window.hh:264`). By default it returns `nullptr` (`GHOST_Window.cc:59-62`).
  - Win32 overrides it to return the `HWND` (`GHOST_WindowWin32.cc:369-372`). There is also `getHWND()` (`GHOST_WindowWin32.hh:108`).
  - Cocoa overrides it to return the `NSWindow*` (`GHOST_WindowCocoa.mm:524-527`).
  - **X11 and Wayland do not override it.** X11 offers `Window getXWindow()` (`GHOST_WindowX11.hh:119`) and `Display *getXDisplay()` (`GHOST_SystemX11.hh:191`). Wayland offers `wl_surface_get()` (`GHOST_WindowWayland.hh:190`) and `wl_display_get()` (`GHOST_SystemWayland.hh:268`).
- The WM stores the window as `win->runtime->ghostwin` and always casts it to `GHOST_IWindow*` (for example `windowmanager/intern/wm_window.cc:1558`). Nothing in `windowmanager/`, `editors/` or `gpu/` reads a native handle.

**Seam if needed:** move `getOSWindow` into `GHOST_IWindow` with a `nullptr` default, add X11 and Wayland overrides, and add `void *WM_window_native_handle_get(const wmWindow *)`. That is about 25 lines in 5 low-churn files (§11). Alternatively, fork code can include the internal header and `static_cast<GHOST_Window*>`, which needs no patch but is fragile.

### 5.2 Rectangles and DPI

- `ARegion::winrct` holds window-space pixel coordinates with the origin at the **bottom left**. The bounds are inclusive: `winx = BLI_rcti_size_x(&winrct) + 1` (`editors/screen/area.cc:2402-2409`; struct at `makesdna/DNA_screen_types.h:809-864`). An area's rectangle is `area->totrct` (`wm_event_system.cc:3879-3891`).
- OS client coordinates are converted by `y = (win->sizey - 1) - y`, then scaled by `getNativePixelSize()` (`wm_window.cc:1557-1574`).
- Only Cocoa sets a native pixel size other than 1, from the Metal layer's backing scale (`GHOST_WindowCocoa.mm:803`; default 1.0 in `GHOST_Window.hh:227-233`).
- On Windows the process is per-monitor DPI aware (`GHOST_SystemWin32.cc:171`), so `winrct` is in physical pixels that match Win32 client pixels. The UI scale comes from `getDPIHint()` → `U.dpi` (`wm_window.cc:735-795`).
- With client-side decorations on Wayland (`WITH_GHOST_CSD`), the drawable rectangle is offset (`wm_window.cc:3154-3165`).
- To place a child at the top left in OS pixels: `x = winrct.xmin / f` and `y_top = (sizey - 1) - winrct.ymax / f`, where `f` is the native pixel size **(inferred from the conversion above)**.

### 5.3 Event dispatch

- `WM_main` loops: `wm_window_events_process` → `wm_event_do_handlers` → `wm_event_do_notifiers` → `wm_draw_update` (`windowmanager/intern/wm.cc:598-621`).
- For each event, `wm_event_do_handlers` does the following:
  1. Sets the context area and region by hit-testing `area->totrct` and `region.winrct` (`wm_event_system.cc:3879-3907, 4337-4339`).
  2. Runs window modal handlers (`:4347`).
  3. On `MOUSEMOVE`, updates the active region (`:4369-4376`).
  4. For each area containing the cursor, runs the handlers of the region under the cursor, chosen by `ED_area_find_region_xy_visual` (`:4192-4215, 4157-4184`).
  5. Runs area handlers (`:4400-4423`), then window handlers (`:4426-4433`).

  A region type receives events through the handlers its `init` installs, such as keymaps or `ED_KEYMAP_UI`.
- **A native child window would bypass all of this.** Its input never enters Blender's queue, so Blender shortcuts stop working while it has focus. When it takes focus, the top-level window gets `WM_ACTIVATE` and `WM_KILLFOCUS`. GHOST re-grabs focus when the window "loses focus to nowhere" (`intern/ghost/intern/GHOST_SystemWin32.cc:2138-2160, 2279-2287`), and the WM clears modifier state on deactivate (`wm_window.cc:1819-1826`). Cross-process `SetParent` is legal but attaches input queues transitively. Raymond Chen compares it to "juggling chainsaws".

### 5.4 Existing child-window code

There is **none**.
- Win32 creates only top-level or owned windows (`GHOST_WindowWin32.cc:109-121`, with the parent passed as the *owner*).
- Cocoa uses `addChildWindow` only for dialogs (`GHOST_WindowCocoa.mm:463`).
- The Win32 IME works through IMM on the main `HWND` (`GHOST_ImeWin32.cc:56`; API at `GHOST_IWindow.hh:365-384`).
- XR renders into OpenXR swapchains, not OS windows (§6.2).
- The XR "mirror" is drawn *inside* a 3D view region and redrawn every loop (`wm_draw.cc:564-575`). That is the precedent to follow.

### 5.5 Popups and menus share the window surface

`wm_draw_window_offscreen` draws each area, then draws **menus, which are `screen->regionbase`, into their own offscreen buffers** (`wm_draw.cc:1063-1109`). `wm_draw_window_onscreen` blits area regions, draws overlays, screen edges and callbacks, then **blends menus on top**, followed by gestures, the drag image and the software cursor (`:1111-1234`). Everything is composited into one GPU surface. An OS child window would sit above all of it, covering menus, tooltips, and drop-downs from the editor's own header.

On Wayland, cross-client embedding is not possible. `xdg-foreign` only parents toplevels, and a proposal to embed foreign surfaces was not adopted (wayland-protocols issue #74).

**Recommendation: render the game as a texture inside a region (§6). Treat child windows as a Windows-only experiment at most.**

---

## 6. GPU: drawing an external texture in a region

### 6.1 Import and export inventory

| Capability | OpenGL | Vulkan | Metal |
|---|---|---|---|
| Import external memory into a `gpu::Texture` | **No** | **No** | **No** |
| Export texture memory | No | `VKTexture::export_memory(OPAQUE_FD / OPAQUE_WIN32)` needs `GPU_TEXTURE_USAGE_MEMORY_EXPORT` (`gpu/vulkan/vk_texture.cc:551-584`; flag `gpu/GPU_texture.hh:775-776`; result struct `gpu/vulkan/vk_memory.hh:15-28`). Internal API only. | No |
| Pixel-buffer native handle (public) | GL PBO id (`gpu/opengl/gl_texture.cc:891-896`) | Opaque Win32 handle or FD of a `VkBuffer`; the caller closes it (`gpu/vulkan/vk_pixel_buffer.cc:57-80`) | `MTLBuffer*` with unified memory (`gpu/metal/mtl_texture.mm:2590-2606`) |
| Device identity | `GL_EXT_memory_object` UUID and LUID query only (`gpu/opengl/gl_backend.cc:341-360`) | UUID and LUID | UUID |

The public API is `GPU_pixel_buffer_get_native_handle` (`GPU_texture.hh:1351-1367`), with device IDs from `GPU_platform_uuid()` and `GPU_platform_luid()` (`GPU_platform.hh:99-104`). The Vulkan device enables `VK_KHR_external_memory_win32` or `_fd` when available (`intern/ghost/intern/GHOST_ContextVK.cc:1785-1788`). The entry points are loaded at `gpu/vulkan/vk_device.cc:217-220`.

### 6.2 Precedents: OpenXR and Cycles

- **OpenXR on Vulkan.** Blender exports its framebuffer colour attachment (`gpu/vulkan/vk_context.cc:554-650`). GHOST imports it into the runtime's device with `VkImportMemoryWin32HandleInfoKHR` or `VkImportMemoryFdInfoKHR` and copies it into the swapchain image (`intern/ghost/intern/GHOST_XrGraphicsBindingVulkan.cc:786-890`; mode selection at `:470-553`).
- **OpenXR on OpenGL under Windows.** OpenGL is blitted into D3D11 through `WGL_NV_DX_interop` (`GHOST_ContextD3D.cc:113-260, 352`).
- **Cycles.** `BlenderDisplayDriver` passes the pixel-buffer handle to CUDA, HIP or oneAPI for zero-copy writes (`intern/cycles/blender/display_driver.cpp:571-621`). Its CPU path maps the buffer (`:550`), unmaps it (`:564`) and uploads with `GPU_texture_update_sub_from_pixel_buffer` (`:482-490`) into a texture made by `GPU_texture_create_2d` (`:145-150`).

**In every case Blender is the exporter and the other party imports.** A runtime process could import a Blender-exported Vulkan buffer the same way. That requires passing handles across processes and a sync primitive Blender does not expose **(inferred)**.

### 6.3 Uploading RGBA8 efficiently

C++ code can do this:
- Create a texture with `GPU_texture_create_2d(name, w, h, 1, TextureFormat::UNORM_8_8_8_8, usage, nullptr)` (`GPU_texture.hh:818-824`; format at `gpu/GPU_format.hh:37`).
- Update it in place with `GPU_texture_update(tex, GPU_DATA_UBYTE, data)` or `GPU_texture_update_sub(...)` (`GPU_texture.hh:963, 976-985`; implementation `gpu/intern/gpu_texture.cc:522-536`). On Vulkan each such call creates a fresh staging buffer (`vk_texture.cc:467-503`). OpenGL uses `glTexSubImage2D` (`gl_texture.cc:322, 349`).
- **The cheapest in-place path:** keep a persistent `GPUPixelBuffer` with `GPU_pixel_buffer_create` and `GPU_pixel_buffer_map` (Vulkan buffers stay mapped, `vk_pixel_buffer.cc:50-55`), memcpy the frame in, unmap, and call `GPU_texture_update_sub_from_pixel_buffer` (`GPU_texture.hh:1324-1383`). This is Cycles' path.
- Draw the texture inside the region's `draw` with `GPU_SHADER_3D_IMAGE` and `immRectf_with_texco` (`gpu/GPU_shader_builtin.hh:161`, `gpu/GPU_immediate_util.hh:23`).
- To give the texture to Python, use `BPyGPUTexture_CreatePyObject(tex, true)` (`python/gpu/gpu_py_texture.hh:41`).

Python cannot do this. `GPUTexture(...)` accepts only FLOAT buffers (`python/gpu/gpu_py_texture.cc:254-258`), and the type has no update method (`:766-785`). This matches the 9–23 ms per 1080p frame measured in research doc 04.

### 6.4 How the viewport draws a RenderEngine

`draw/engines/external/external_engine.cc:231-268` creates the engine on first use with `view_update`. It then sets pixel space and calls `type->view_draw`. Redraws are driven by `RE_ENGINE_DO_DRAW`, which `wm_draw_update_test_window` polls on every loop through `wm_region_test_render_do_draw` (`windowmanager/intern/wm_draw.cc:532-561, 1541-1611`).

**Recommendation for v1:**
1. The runtime writes RGBA8 frames into OS shared memory.
2. `_gamestudio` copies the latest frame into a mapped `GPUPixelBuffer` and uploads it in the extension editor's WINDOW draw (or in a `RenderEngine.view_draw` if we stay inside the 3D view).
3. This needs **no upstream GPU patch**. Copy cost is about 8 MB per 1080p frame, roughly 1–3 ms **(inferred)**.

Zero-copy is a later, Vulkan-only seam. It would add a public `GPU_texture_export_memory()` wrapping `VKTexture::export_memory`, plus an exported semaphore, at about 150–300 lines in `gpu/` **(inferred)**.

---

## 7. Built-in Python C-API modules

- Built-in modules are listed in `static _inittab bpy_internal_modules[]` (`python/intern/bpy_interface.cc:320-351`): `mathutils`, `_bpy_path`, `blf`, `bl_math`, `imbuf`, `bmesh`, optional `manta`/`aud`/`_cycles`, `gpu`, `idprop` and optional `_bpy_hydra`. They are added with `PyImport_ExtendInittab(bpy_internal_modules)` before Python initialises (`:435`).
- `_bpy` is created differently: `BPy_init_modules` builds it with `PyModule_New("_bpy")` and inserts it into `sys.modules` (`python/intern/bpy.cc:760-787`).
- **Precedent for an optional, separately compiled module: Hydra.**
  - `render/hydra/python.cc:194-216` defines `PyModuleDef module = {"_bpy_hydra", ...}` and `PyObject *BPyInit_hydra()`.
  - `bpy_interface.cc:316-318, 348-350` declares it and adds it to the table behind `#ifdef WITH_HYDRA`.
  - `python/intern/CMakeLists.txt:347-352` appends `bf_render_hydra` to `LIB` and defines `WITH_HYDRA`.
- Built-in is required. Blender links statically and hides symbols (`source/creator/CMakeLists.txt:1964`), so an external `.pyd` cannot call Blender's C++ API **(inferred from the link setup)**.

**Minimal fork patch:** 5 lines in `bpy_interface.cc` (a forward declaration and an `#ifdef WITH_GAMESTUDIO {"_gamestudio", BPyInit_gamestudio}`) and 3 lines in `python/intern/CMakeLists.txt`. The define can come from `target_compile_definitions(bf_gamestudio INTERFACE WITH_GAMESTUDIO)`, because `blender_link_libraries` uses plain `target_link_libraries`, which propagates interface definitions (`build_files/cmake/macros.cmake:351-425`). The module's Python package (`gamestudio/…`) ships as an add-on, extension or app template, so no upstream files are involved.

---

## 8. CMake

- The top level adds `intern`, `extern`, then `source`, then `source/creator` (`CMakeLists.txt:2433-2467`; minimum CMake version 3.21 at `:29`).
- `source/blender/CMakeLists.txt` lists the DNA headers in `SRC_DNA_INC` (`:5-101`) and adds each module directory (`:113-172`).
- `blender_add_lib` creates a static library, links its `LIB` list through `blender_link_libraries`, sets `LINK_INTERFACE_MULTIPLICITY 3` for cyclic dependencies, and appends to a global `BLENDER_LINK_LIBS` property that nothing reads (`build_files/cmake/macros.cmake:427-493`).
- **The executable links only a few roots** (`source/creator/CMakeLists.txt:11-26, 338, 1958-1964`). Everything else arrives transitively. For example, `bf_python` comes through blenkernel (`blenkernel/CMakeLists.txt:652-660`), and `bf_editor_space_api` comes through `bf_rna` (`makesrna/intern/CMakeLists.txt:444-451`). **Any library listed in the `LIB` of a linked library reaches the binary.**
- `makesdna` generates `dna_includes_all.h` from whatever is in `SRC_DNA_INC` (`makesdna/intern/CMakeLists.txt:30-42`). A fork DNA header can therefore live out of tree.

**Minimal hook.** Insert this in `source/blender/CMakeLists.txt` after `SRC_DNA_OTHER_INC` (`:105-111`) and before `add_subdirectory(makesdna)` (`:149`):

```cmake
set(GS_NATIVE_DIR "" CACHE PATH "Out-of-tree GameStudio native module")
if(GS_NATIVE_DIR)
  list(APPEND SRC_DNA_INC ${GS_NATIVE_DIR}/dna/DNA_gamestudio_types.h)
  add_subdirectory(${GS_NATIVE_DIR} ${CMAKE_CURRENT_BINARY_DIR}/gamestudio)
endif()
```

Then add `if(GS_NATIVE_DIR) list(APPEND LIB bf_gamestudio) endif()` to `python/intern/CMakeLists.txt` (for the inittab) and to `editors/space_api/CMakeLists.txt` (for `ED_spacetype_extension()`). CMake resolves target names at generate time, so the order of `add_subdirectory` calls doesn't matter **(inferred, standard CMake behaviour)**.

The out-of-tree `CMakeLists.txt` calls `blender_add_lib(bf_gamestudio ...)`. It uses absolute include paths such as `${CMAKE_SOURCE_DIR}/source/blender/editors/include` and links `bf::windowmanager`, `bf::gpu`, `bf::blenkernel` and `bf::dna`. **Total: about 10 upstream CMake lines across 3 files.**

---

## 9. Event and redraw hooks

- **Main loop.** See §5.3 (`wm.cc:598-621`). When idle, `wm_window_events_process` sleeps 5 ms (`wm_window.cc:2198-2228`). **WM timers shorten that sleep** so they fire on time (`:2183-2193`).
- **Timers that post notifiers.** `WM_event_timer_add_notifier(wm, win, NC_SPACE|ND_SPACE_EXTENSION, 1.0/60)` (`wm_window.cc:2545-2565`) posts `WM_main_add_notifier` on every tick (`:2160-2162`). `wm_event_do_notifiers` passes it to region listeners (`wm_event_system.cc:596, 760-822`). Start the timer when the game view becomes active and remove it with `WM_event_timer_remove_notifier` (`wm_window.cc:2616`). Animation playback uses the same pattern at scene FPS (`editors/screen/screen_edit.cc:1965`).
- **Tick callbacks with no notifier.** `BLI_timer_register` (`blenlib/BLI_timer.h:20-38`), the backend of `bpy.app.timers`, runs each loop from `wm_event_timers_execute` (`wm_event_system.cc:566-579`). Unlike WM timers, these do not shorten the idle sleep **(inferred from the code above)**, which explains the roughly 180 Hz limit measured in research doc 04.
- **Redraw by polling** is how `RenderEngine` and the XR mirror work (`wm_draw.cc:532-575`). A generic `ARegionType::needs_redraw` callback in `wm_draw_update_test_window` would cost about 10 lines in `BKE_screen.hh` and `wm_draw.cc`, and could plausibly be upstreamed. It isn't needed for v1.
- **Thread rule.** An IPC thread must not touch WM. It should set an atomic "frame ready" flag that the 60 Hz timer or the region draw checks.
- **Message bus.** `WM_msg_subscribe_rna_*` redraws the editor when specific RNA properties change (`space_statusbar.cc:114-126`).
- **Frame cap.** Redraw rate is ultimately capped by buffer swaps: about 75 Hz with vsync on OpenGL, per research doc 04.
- **Input.** Forward events from the WINDOW region's handlers, or from a modal operator, to the runtime. Use `WM_cursor_grab_enable/disable` for mouse look (`windowmanager/intern/wm_cursors.cc:509, 567`).

---

## 10. Seam catalogue

| # | Seam | Upstream files touched | Upstream lines | Upgrade risk | Upstreamable? |
|---|---|---|---|---|---|
| S1 | CMake out-of-tree hook (`GS_NATIVE_DIR`, DNA header, link) | `source/blender/CMakeLists.txt`, `python/intern/CMakeLists.txt`, `editors/space_api/CMakeLists.txt` | ~10 | Low (12, 15 and 2 commits per year) | Maybe, as a generic "extra modules" hook |
| S2 | `_gamestudio` built-in module | `python/intern/bpy_interface.cc` | ~5 | Low; the table rarely changes | No (fork-specific) |
| S3 | New space ID and `SPACE_TYPE_NUM` | `makesdna/DNA_space_enums.h` | ~3 | Low–medium; the file churns (30/yr) but the enum tail rarely does | Only with S6 |
| S4 | `SpaceExtension` DNA | `DNA_space_types.h` (27/yr), **or** an out-of-tree header through S1 | 0–20 | Low if out of tree | Only with S6 |
| S5 | Registration call | `editors/space_api/spacetypes.cc`, `editors/include/ED_space_api.hh` | ~4 | Low (12 and 3 per year) | Only with S6 |
| S6 | RNA: enum item, refine, `SpaceExtensionEditor` with provider and system idprops | `makesrna/intern/rna_space.cc` | 60–80 | **Highest churn (108/yr).** Keep hunks small. Put the def function at the end of the file. | Possibly, as a "custom editors" design task |
| S7 | `draw_handler_add` for the new space | `python/intern/bpy_rna_callback.cc` | 3 | Low (8/yr) | With S6 |
| S8 | Notifier `ND_SPACE_EXTENSION` | `windowmanager/WM_types.hh` | 1 | Low; append only | With S6 |
| S9 | Debug name and `-Wswitch` | `windowmanager/intern/wm_draw.cc` | 1 | Low | With S6 |
| S10 | Public native window handle | `intern/ghost/GHOST_IWindow.hh`, `GHOST_Window.hh`, `GHOST_WindowX11.{hh,cc}`, `GHOST_WindowWayland.{hh,cc}`, `windowmanager/WM_api.hh`, `wm_window.cc` | ~25 | Low for GHOST (5–9/yr); WM files churn but the hunks are additive | Plausible (small, generic). **Skip unless child windows are pursued.** |
| S11 | Vulkan texture export, public (zero-copy, later) | `gpu/GPU_texture.hh`, `gpu/intern/gpu_texture.cc`, `gpu/intern/gpu_texture_private.hh`, Vulkan backend, maybe GHOST semaphores | 150–300 | Medium (13/yr in the header; the backend churns more) | Plausible; useful for interop beyond XR |
| S12 | Generic region "needs redraw" poll | `blenkernel/BKE_screen.hh`, `windowmanager/intern/wm_draw.cc` | ~10 | Medium (39 and 42 per year) | Plausible. **Not needed; use WM timers.** |
| – | Fork-owned: `space_extension.cc`, provider registry, `_gamestudio`, GPU upload, Python UI | none (out of tree) | 0 (1,000–1,600 fork lines) | Breaks only on internal API renames such as `ListBaseT` or `gpu::TextureFormat` | n/a |

**Minimum viable fork (S1–S9):** about 90–130 upstream lines across about 11 files. Most hunks are one to three lines at list tails, and no hunk changes existing behaviour. **Rules:** no workspace tools in the new space (§3.3), and only existing region types.

---

## 11. Upstream churn (commits on `main` touching the file)

| File | Last 24 months | Last 12 months |
|---|---|---|
| `makesrna/intern/rna_space.cc` | 199 | 108 |
| `editors/screen/area.cc` | 163 | 79 |
| `windowmanager/intern/wm_event_system.cc` | 159 | 74 |
| `windowmanager/intern/wm_window.cc` | 128 | 59 |
| `windowmanager/WM_api.hh` | 120 | 66 |
| `windowmanager/intern/wm_draw.cc` | 82 | 42 |
| `source/creator/CMakeLists.txt` | 76 | 38 |
| `intern/ghost/intern/GHOST_WindowWayland.cc` | 64 | 36 |
| `blenkernel/intern/screen.cc` | 59 | 31 |
| `editors/interface/resources.cc` | 58 | 26 |
| `windowmanager/WM_types.hh` | 57 | 24 |
| `makesdna/DNA_space_types.h` | 55 | 27 |
| `blenkernel/BKE_screen.hh` | 55 | 39 |
| `python/intern/bpy_interface.cc` | 46 | 21 |
| `makesdna/DNA_space_enums.h` | 45 | 30 |
| `python/intern/CMakeLists.txt` | 36 | 15 |
| `source/blender/CMakeLists.txt` | 30 | 12 |
| `gpu/GPU_texture.hh` | 28 | 13 |
| `editors/space_api/spacetypes.cc` | 24 | 12 |
| `intern/ghost/intern/GHOST_WindowX11.cc` | 18 | 9 |
| `intern/ghost/GHOST_IWindow.hh` | 16 | 5 |
| `intern/ghost/intern/GHOST_Window.hh` | 16 | 6 |
| `python/intern/bpy_rna_callback.cc` | 14 | 8 |
| `editors/space_api/CMakeLists.txt` | 9 | 2 |
| `editors/include/ED_space_api.hh` | 6 | 3 |
| `editors/CMakeLists.txt` | 3 | 1 |

Commit counts measure activity, not conflicts. Merge conflicts depend on whether upstream edits the same few lines, and the proposed hunks sit at list tails that change only when upstream adds an editor or module. Upstream has added one editor since 2019 (the spreadsheet, 2021).

---

## 12. Sources

**Source code** (Blender v5.2.2, `d13f752e`, `F:\Development-games\blender-upstream`). Every file cited above was read directly. The main ones:
- `source/blender/editors/space_{statusbar,script,node,userpref,api}/`
- `source/blender/blenkernel/{BKE_screen.hh,intern/screen.cc,intern/workspace.cc}`
- `source/blender/blenloader/intern/readfile.cc`, `source/blender/makesdna/intern/dna_genfile.cc`
- `source/blender/makesrna/intern/{rna_space,rna_screen,rna_ui,rna_access}.cc`
- `source/blender/python/intern/{bpy_rna,bpy_rna_callback,bpy_rna_types_capi,bpy_interface,bpy}.cc`, `source/blender/python/gpu/gpu_py_texture.cc`
- `source/blender/windowmanager/intern/{wm,wm_event_system,wm_draw,wm_window,wm_init_exit,wm_toolsystem,wm_dragdrop}.cc`
- `intern/ghost/` (`GHOST_IWindow.hh`, `GHOST_Window*`, `GHOST_SystemWin32.cc`, `GHOST_ContextVK.cc`, `GHOST_ContextD3D.cc`, `GHOST_XrGraphicsBindingVulkan.cc`)
- `source/blender/gpu/` (`GPU_texture.hh`, `vulkan/vk_texture.cc`, `vk_pixel_buffer.cc`, `vk_context.cc`, `opengl/gl_backend.cc`)
- `intern/cycles/blender/display_driver.cpp`, `source/blender/draw/engines/external/external_engine.cc`, `source/blender/render/hydra/python.cc`
- `build_files/cmake/macros.cmake`, `CMakeLists.txt`, `source/blender/CMakeLists.txt`, `source/creator/CMakeLists.txt`

**Web:**
- devtalk, "Add custom spaces in python API" (2021): https://devtalk.blender.org/t/add-custom-spaces-in-python-api/19971
- B. Montagne, "Custom Spaces" (wiki stub, 2018): https://archive.blender.org/wiki/2024/wiki/User:Mont29/Foundation/UI/Custom_Spaces.html
- Python API "missing features" list: https://docs.blender.org/api/4.2/info_quickstart.html
- 2012 `pyapi_devel_26` space-extension commit: https://lists.blender.org/pipermail/bf-blender-cvs/2012-June/047398.html
- BlenderArtists, "Why have there been no new types of windows since the 2.5 refactor": https://blenderartists.org/t/why-have-there-been-no-new-types-of-windows-since-the-2-5-refactor/679784
- Raymond Chen, "Is it legal to have a cross-process parent/child or owner/owned window relationship?": https://devblogs.microsoft.com/oldnewthing/20130412-00/?p=4683
- wayland-protocols #74, "Allow embedding foreign wl_surfaces": https://gitlab.freedesktop.org/wayland/wayland-protocols/-/issues/74
- Churn counts: Gitea API on projects.blender.org, `/api/v1/repos/blender/blender/commits?path=…&since=2024-10-01` and `since=2025-10-01`, queried 2026-10-09 (script at `…/scratchpad/churn.sh`).
- Companion doc: `research/04-blender-internals.md` (measured Python GPU upload costs, timer rates, the `Space` registration failure).
