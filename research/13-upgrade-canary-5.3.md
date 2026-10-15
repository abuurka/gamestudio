# 13: Upgrade canary, Blender 5.3 (and `main`) against the GameStudio 5.2.2 fork

*Research date: 2026-10-09. This is the first run of the "weekly canary" in docs/ARCHITECTURE.md §2 and §7. It answers one question: what breaks when GameStudio moves from 5.2.2 LTS to 5.3, and later to 5.5?*

*Sources:*
- *A separate shallow clone at `F:\Development-games\blender-canary`, fetched with `GIT_LFS_SKIP_SMUDGE=1` and without libs:*
  - *`blender-v5.3-release` @ `145d2107`: 5.3.0 beta, committed 2026-10-09;*
  - *`main` @ `84589225`: 5.4.0 alpha;*
  - *the tag `v5.2.2` @ `d13f752e`, fetched only so `git am -3` has the base blobs.*
- *`F:\Development-games\blender-upstream` was not touched.*
- *Paths are relative to the Blender repository root, or to `source/blender/` for paths that start below it.*
- *Line numbers are in the 5.3 branch unless marked otherwise.*

*Nothing here was built or run on 5.3. The patch results are real `git am -3` runs. The API comparison is static, from source. Behaviour claims come from reading the 5.3 source, except one runtime check: Blender 5.2.2 opened 5.3's own factory `startup.blend` (§6).*

---

## 1. TL;DR

- **Patch queue: 5 of the 8 seams apply to 5.3, and the result is identical on `main`.**
  - S01, S02, S04 and S08 apply cleanly. S06 applies through a 3-way merge.
  - **S03, S05 and S07 conflict.**
  - All three conflicts are small. Two of them are the "list tail" collisions the design expected (research/10 §11): upstream appended its own entries at the same tails.
- **5.3 adds a new built-in editor, `SPACE_PROJECT = 24`** (`makesdna/DNA_space_enums.h:1215`).
  - It takes exactly the ID that research/10 §4.3 warned "collides if upstream ever adds a 25th editor".
  - Because S05 uses **64**, the conflict is textual only: keep both enum values. Without that choice, every fork-saved file would have opened as a Project editor in 5.3.
- **S03 needs a real refresh.** `wm_window_title_text()` was rewritten. It now builds the title with `win_title.append(...)` and adds the name of the active Blender project (`windowmanager/intern/wm_window.cc:586,678-684`).
- **The native module (L2) will not compile on 5.3 as it is.** Both breaks are in `editor/native/blender/space_extension.cc`, the adapter layer, as the architecture predicts:
  - three `BLI_*.h` headers were renamed to `.hh` (lines 23-25);
  - `ListBase::first` became `first_`, and `ListBaseT<T>::first()` is now a method (line 49).
- **Python shell (L3): no breaking API change.** A static API diff of the 5.3 sources against the 5.2.2 sources found:
  - across the whole API: 10 types and 13 members removed, 62 types and 96 members added;
  - intersected with the 1,788 Blender usages in `editor/shell/**/*.py`: **0 BREAK, 0 CHANGE, 1 INFO**. The glTF exporter has a new `export_pointclouds` parameter, on by default.
- **Every behaviour the contract tests pin is unchanged in the 5.3 source** (§6). The new upstream behaviour that touches the shell is **Blender Projects**:
  - a Project menu in the top bar;
  - File > Project > New Project…;
  - `bpy.data.project`;
  - the project name in the window title.

  This overlaps GameStudio's own "project" concept and UI (§7.1).
- **Found in passing, and relevant on 5.2.2 today:** S07 defines `ND_SPACE_EXTENSION (23 << 16)`. That is the same value as the existing `ND_REGIONS_ASSET_SHELF` (`windowmanager/WM_types.hh:585`). Every extension-editor redraw notifier therefore also redraws every asset-shelf region, and the reverse (§4.2).
- **Estimated cost of the 5.3 hop:**
  - about half a day for the patches and the adapter;
  - plus whatever a 5.3 build turns up in the glTF instancing rewrite (§7.2) and the contract suite.

  This is well inside the 1–2 week LTS budget.

---

## 2. Method (repeat it with these commands)

```sh
# 1. separate shallow clone (never the build mirror)
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --branch blender-v5.3-release --single-branch --no-tags \
    https://projects.blender.org/blender/blender.git F:/Development-games/blender-canary
cd F:/Development-games/blender-canary
GIT_LFS_SKIP_SMUDGE=1 git fetch --depth 1 --no-tags origin tag v5.2.2            # base blobs for -3
GIT_LFS_SKIP_SMUDGE=1 git fetch --depth 1 --no-tags origin main:refs/remotes/origin/main

# 2. patch queue: first on v5.2.2 (proves the series and creates the intermediate blobs),
#    then on the canary ref; on a failure record the conflict, `git am --abort`, try the next patch
git checkout -B gs-5.2.2 v5.2.2 && for p in $(grep -v '^#' editor/patches/series); do git am -3 --keep-cr $p; done
git checkout -B canary-5.3 blender-v5.3-release && ...same loop...

# 3. API surface, statically from source (no build)
git archive v5.2.2 source/blender/{makesrna/intern,editors,windowmanager,python/intern} scripts/{startup,modules} ... | tar -x -C src/v5.2.2
python editor/tools/api_diff.py source-dump --source src/v5.2.2 --out src-5.2.2.json
python editor/tools/api_diff.py source-dump --source src/v5.3   --out src-5.3.json
python editor/tools/api_diff.py diff src-5.2.2.json src-5.3.json
```

**Why the patches are applied to v5.2.2 first.** In a shallow clone of 5.3, `git am -3` cannot find the blobs that a later patch's `index` lines name. S05's `source/blender/CMakeLists.txt` blob, for example, is the one that exists *after* S01. Without those blobs, S05 fails with "Repository lacks necessary blobs". Applying the series to v5.2.2 in the same clone creates them.

`python editor/tools/gsfork.py canary <ref> --build-check` now does all of this itself:
- it fetches the ref into `refs/gsfork/canary/<ref>` of the mirror, and nothing else there is written;
- it applies the series in a scratch clone that borrows the mirror's objects, after a base pass on the locked commit;
- it tries every patch and reports each as clean, 3-way or conflict, with the conflicting files;
- it checks that native includes still exist;
- it exits 1 on any failure.

Run against `blender-v5.3-release` @ `40ccc2ed`, it reproduces the 5/8 result below. Its header check also flags `BLI_rect.h`, `BLI_string_utf8.h` and `BLI_timer.h` in `editor/native/blender/game_view.cc:38-40`, in addition to §4.1.

---

## 3. Patch queue against 5.3

| Seam | Result | Upstream change it met | Fix |
|---|---|---|---|
| S01 build hook | clean | – | – |
| S02 config folder names | clean | – | – |
| **S03** app name / app ID | **conflict** in `windowmanager/intern/wm_window.cc` | `wm_window_title_text()` rewritten. It appends to `win_title` instead of returning strings. A child window now gets `win_title.append(StringRef("Blender"))` (`:586`). The main window appends the active project's name (`:678-681`), then `" — Blender " + version` (`:683-684`, with an em dash). The `CMakeLists.txt` and `wm_splash_screen.cc` hunks merged. | **Re-implement**: replace the two `"Blender"` literals at `:586` and `:683` with `BLENDER_APP_NAME`, keeping the `#ifndef BLENDER_APP_NAME` default. About 4 lines. The intent is unchanged. |
| S04 default app template | clean | `wm_files.cc:1163` still has the `wm_init_state_app_template` static | – |
| **S05** `SPACE_EXTENSION` DNA | **conflict** in `makesdna/DNA_space_enums.h` | 5.3 appends `SPACE_PROJECT = 24` and `#define SPACE_TYPE_NUM (SPACE_PROJECT + 1)` (`:1215-1217`) at the exact tail S05 edits | **Keep both**: `SPACE_PROJECT = 24,` then `SPACE_EXTENSION = 64`, keeping `SPACE_TYPE_NUM (SPACE_EXTENSION + 1)`. The `CMakeLists.txt` hunk and the new header merged. |
| S06 registration + `_gamestudio` inittab | clean, through a 3-way merge | context drift in `ED_space_api.hh`, `spacetypes.cc` and `bpy_interface.cc` | – |
| **S07** RNA / draw handlers / notifier | **conflict** in `makesrna/intern/rna_space.cc` (4 hunks) and `python/intern/bpy_rna_callback.cc` | Upstream appended `SpaceProject` at every list tail S07 uses: enum item `"PROJECT"` (`rna_space.cc:188`), refine case (`:826`), `rna_def_space_project()` (`:9784`) and its call (`:9833`), and `RNA_SpaceProject` in `rna_Space_refine_reverse` (`bpy_rna_callback.cc:262`). `WM_types.hh` merged. | **Keep both** in each hunk, ours after upstream's. Purely mechanical, but not an automatic union: the `bpy_rna_callback.cc` hunk shares a closing brace. |
| S08 editor-type filter | clean | `rna_screen.cc` +55/−9 elsewhere | – |

**`main` (5.4 alpha)** gives exactly the same result, with byte-identical conflict hunks. `main` was branched for 5.3 only recently, so the 5.5-specific churn has not started yet (§8).

**Lessons for the seam design:**
- Every conflict is either a list tail (S05, S07) or a function upstream rewrote (S03). No seam changed meaning.
- `git rerere` will replay the S05 and S07 resolutions on every later canary run.
- S03 is the most fragile seam. It edits string literals inside a function that upstream rewrites for product reasons (projects, client-side decorations).
  - A one-line upstream hook would remove S03's `wm_window.cc` hunk entirely: a `BKE_appdir_program_name()`-style accessor, or a `BLENDER_APP_NAME` define used in both places.
  - That hook is the best upstreaming candidate in the queue.

---

## 4. Native module (L2): compile breaks to expect

`editor/native/blender/` is the adapter layer, the only code that includes Blender headers. A 5.3 build fails there and nowhere else. That is the architecture working as intended.

### 4.1 Header renames and the ListBase API

| GameStudio code | 5.2.2 | 5.3 | Fix |
|---|---|---|---|
| `space_extension.cc:23-25` `#include "BLI_listbase.h"`, `"BLI_string.h"`, `"BLI_string_utf8.h"` | exist | **renamed** to `BLI_listbase.hh`, `BLI_string.hh`, `BLI_string_utf8.hh` (git sees the renames: `blenlib/{BLI_listbase.h => BLI_listbase.hh}`, …). Upstream's own includes moved with them, for example `blenkernel/intern/appdir.cc`. | Include the `.hh` names. To build against both versions, use a two-line shim header in the adapter (`#if __has_include("BLI_listbase.hh")`). |
| `space_extension.cc:49` `static_cast<SpaceExtension *>(area->spacedata.first)` | `ListBase { void *first, *last; }` | `ListBase { void *first_, *last_; }` (`makesdna/DNA_listBase.h:37`), with typed accessors `ListBaseT<T>::first()` (`:102-108`) and `first_as<U>()` | Use `area->spacedata.first_as<SpaceExtension>()` on 5.3. A small adapter function `first_space(area)` hides the difference. |

The other headers the module includes still exist with the same names in 5.3: `BKE_context.hh`, `BKE_screen.hh`, `BLO_read_write.hh`, `DNA_screen_types.h`, `DNA_space_types.h`, `DNA_userdef_types.h`, `ED_screen.hh`, `ED_space_api.hh`, `MEM_guardedalloc.h`, `RNA_access.hh`, `RNA_enum_types.hh`, `UI_interface_c.hh`, `UI_resources.hh`, `WM_api.hh` and `WM_types.hh`. The function signatures the module calls were not compiled here, so other errors may still turn up.

The `SpaceLink` header the S05 struct must mirror is unchanged in 5.3 (`makesdna/DNA_space_types.h:96-103`). The S05 struct types `link_flag` as `char` where upstream uses `eSpace_Link_Flag`. Both are one byte, so the layout matches; matching upstream's type would be tidier.

### 4.2 Notifier value collision in S07 (already present on 5.2.2)

S07 adds `#define ND_SPACE_EXTENSION (23 << 16)` (`editor/patches/S07-…patch:102`). 5.2.2 and 5.3 already define `#define ND_REGIONS_ASSET_SHELF (23 << 16)` (`WM_types.hh:577` in 5.2.2, `:585` in 5.3).

**The effect is in both directions:**
- `asset_shelf_region_listen()` redraws on `NC_SPACE` with `wmn->data == ND_REGIONS_ASSET_SHELF` (`editors/asset/intern/asset_shelf.cc:334`). So every `_gamestudio.tag_redraw()` (`editor/native/blender/python_module.cc:47`) also redraws every asset-shelf region.
- `extension_region_listener` (`space_extension.cc:155`) redraws on every asset-shelf notifier.

Once the Game View posts this notifier at 60 Hz, every visible asset shelf is redrawn 60 times a second.

**Fix:** move `ND_SPACE_EXTENSION` to a high, unused value, for the same reason the space ID is 64. `NOTE_DATA` is `0x00FF0000`, so the value can go up to 255, for example `(200 << 16)`.

---

## 5. Python API surface: what the shell uses

`editor/tools/api_diff.py` scans `editor/shell/**/*.py` with Python's `ast` module. At the time of the run it found 1,788 usages in 43 files. They include:
- `bpy.types.X`;
- `bpy.ops.a.b` with its keyword arguments;
- `bpy.props`, `bpy.app.*`;
- attribute chains resolved through RNA types (`bpy.context.preferences.view.ui_scale`);
- operator and menu ids in UI strings;
- `icon=` values;
- `bl_*` class attributes;
- methods overridden from upstream classes.

The tool resolves each usage against the old dump and checks every resolved fact in the new one.

### 5.1 5.2.2 → 5.3 (source dumps)

| | Whole API (unfiltered) | Touching the shell |
|---|---|---|
| Types | −10 / +62 | 0 |
| Struct members | −13 / +96 | 0 |
| Operators | −2 / +47 | 0 |
| Icons | −0 / +9 | 0 |
| **Findings** | | **0 BREAK, 0 CHANGE, 1 INFO** |

**The one INFO:** `bpy.ops.export_scene.gltf(...)` has a new parameter `export_pointclouds`, default `True` (`scripts/addons_core/io_scene_gltf2/__init__.py:610-614`). `core/export.py:279` relies on the defaults of the parameters it does not pass, so a scene containing point-cloud objects will now export them as nodes.
- `inject_ids` matches nodes by object name, so point-cloud objects would also receive `gs_id` extras and appear as entities.
- **Decide explicitly:** add `"export_pointclouds": False` to `GLTF_SETTINGS`, or support point clouds in the runtime.

**What upstream removed, none of it used by the shell:**
- types: `NodesModifierPanel(s)`, `VIEW3D_MT_add_object`, `TIME_PT_jump`, the sculpt auto-masking panels, two Sequencer colour-tag classes;
- members: `UserAssetLibrary.path`, `SpaceOutliner.use_sort_alpha`, `NodesModifier.panels`, `PreferencesSystem.geometry_nodes_stack_limit`, five `ThemeSpaceGeneric`/`ThemeSpaceGradient` header colours, and others;
- operators: `node.duplicate_compositing_node_group`, `sequencer.select_inverse`.

**Additions worth knowing about:**
- `UndoStack` and `UndoStep` are now in RNA. They are potentially useful for the assistant's "one named undo step per action" rule.
- `BlenderProject`, `SpaceProject`, `ProjectAssetLibrary` and the `project.*` operators.
- `wm.tool_set_by_id_hold`.

### 5.2 How much of the API the static mode covers, and how the tool was validated

The static source dump resolves **432 of the 459** facts that the binary dump of 5.2.2 resolves for the shell. The missing 27 are:
- C-implemented Python modules: `mathutils`, `bmesh`, `gpu.platform`;
- operator properties added by `WM_operator_properties_*` helpers (`filepath`, `use_empty`);
- `bpy.app.timers.register` keywords;
- two `width` parameters;
- `Library.filepath`.

A 5.3 binary dump is needed to cover these: `python editor/tools/api_diff.py compare --old <5.2.2> --new <5.3>`.

**Binary-mode validation on installed builds:**

| Comparison | Whole API | Touching the shell |
|---|---|---|
| 5.2.2 → 5.2.2 (three dumps) | the dump files are byte-identical | 0 findings |
| 5.1.2 → 5.2.2 | types −5/+117, members −50/+132, operators +39, icons +16 | 0 BREAK, 0 CHANGE, 4 INFO |
| 5.2.2 → 5.1.2 (fallback direction) | – | `UILayout.textbox` removed, at `assistant/ui.py:90-91` |

- **The four 5.1→5.2 INFO findings** are new operator parameters whose defaults we inherit: glTF `export_meshopt_compression_enable` and `export_meshopt_extension`, and `wm.save_(as_)mainfile` `show_save_modified_images_dialog`.
- **The 5.2→5.1 textbox finding** is reported as INFO, because the shell guards the call with `if hasattr(layout, "textbox")`.
- **A dump with deliberate mutations was also checked.** It flagged, with `file:line` evidence:
  - the removal of `Object.matrix_world`, `DepsgraphUpdate.is_updated_transform`, `TOPBAR_HT_upper_bar.draw_right` and `bpy.app.online_access`;
  - an invalid `PLAY` icon;
  - a removed glTF keyword;
  - a changed glTF default.

---

## 6. Contract behaviours, re-checked in the 5.3 source

These are the behaviours `editor/tests/contract/` pins on 5.2.2. The contract suite itself needs a 5.3 binary; this table records what the source says now.

| Behaviour (contract test) | 5.3 evidence | Status |
|---|---|---|
| `RestrictBlend` blocks `bpy.data` during registration (`test_registration`) | `scripts/modules/_bpy_restrict_state.py`: 0-line diff. `addon_utils.py`: comment-only change. | unchanged |
| Re-registering a Menu or Header replaces it (`test_ui_overrides`) | `rna_ui.cc:331,748,888,1047` keep "has been registered before, unregistering previous". The diff is the `ListBaseT` refactor only. | unchanged |
| Upstream classes the template overrides | `space_topbar.py`: `TOPBAR_HT_upper_bar` `:14`, `draw_left` `:25`, `draw_right` `:40`, `TOPBAR_MT_editor_menus` `:106`, `TOPBAR_MT_file` `:160`, `TOPBAR_MT_help` `:651`. `bl_operators/wm.py`: `WM_MT_splash` `:3542`, `WM_MT_splash_about` `:3607`. | unchanged, but `TOPBAR_MT_editor_menus.draw` gained a Project menu (§7.1) |
| `--app-template` loading; a saved preference cannot set the template | `wm_files.cc:1327-1391` (search and load order); `readfile.cc:4200` "Don't read the active app template" | unchanged |
| Factory workspaces that `build_template.py` renames | **Run on 5.2.2:** 5.3's `release/datafiles/startup.blend` (from LFS) has the same 11 workspaces, including Layout, Modeling, Shading, Animation, Geometry Nodes and Scripting. `versioning_defaults.cc` renames none. | unchanged |
| System extension repo `bl_ext.system.*` | `appdir.cc`: header renames only. `preferences.cc`: asset-library and project additions. | unchanged |
| Timers never fire in `--background` | `bpy_app_timers.cc` +2/−2; `creator.cc` +21/−14 with no event-loop change for background mode | unchanged (by reading) |
| Unknown space struct opens as an Info editor (`test_file_compat`) | `blenkernel/intern/screen.cc:1684-1708` keeps the `SPACE_EMPTY` / `butspacetype` / `SpaceInfo` fallback. `regions_remove_invalid` is at `:1772`. | unchanged |
| Hazard: workspace tools for unknown spaces | `wm_toolsystem.cc:903-905` still indexes `space_type_has_tools[SPACE_TYPE_NUM]` without a bounds check | unchanged. Keep the "no `WorkSpaceTool` for `EXTENSION_EDITOR`" rule. |
| glTF camera and light −90° X correction (`test_gltf_export`) | No change to the correction code in `blender/exp/nodes.py` or `tree.py`. The exporter goes from 5.2.40 to **5.3.36** (85 files, +4,221/−1,810). | very likely unchanged; the golden test confirms on a build |
| glTF node names equal object names | not touched in the diff | unchanged (by reading) |
| `bl_system_properties_get`, `override={'LIBRARY_OVERRIDABLE'}`, `UILayout.textbox` parameters, `bpy.app.online_access`, `bl_options` `UNDO` | static API diff: no change to any fact the shell uses | unchanged |
| Python version | `build_files/build_environment/cmake/versions.cmake`: 3.13.13 on 5.2.2, 5.3 and `main` | unchanged |

---

## 7. Upstream changes that matter to GameStudio beyond the API

### 7.1 Blender Projects (new in 5.3)

5.3 introduces a "project" concept of its own:
- `bpy.data.project` (`makesrna/intern/rna_main.cc:743`);
- a new editor, `SPACE_PROJECT` / `SpaceProject`, called "Project Settings";
- `project.new_project`, `project.open_blend_in_project` and asset libraries per project;
- `TOPBAR_MT_editor_menus` draws `TOPBAR_MT_project` while a project is open (`space_topbar.py:124-125`);
- the File menu gains a Project submenu (`:201`, `TOPBAR_MT_file_project` at `:341`): "New Project…" and "Open Blend in Project";
- the window title shows the project's name (`wm_window.cc:678-681`).

**Consequences for the shell:**
- **The template hides upstream's Project menu.** It replaces `TOPBAR_MT_editor_menus` outright, so the Project menu silently disappears from GameStudio's top bar. Decide whether to show it, or to hide Blender projects on purpose.
- **Two "New Project" entries.** File > New Game Project… (prepended by the template) sits next to File > Project > New Project… (upstream). Users will confuse them. Either hide upstream's submenu with `bl_app_override.ui_draw_filter_register`, or map GameStudio projects onto Blender projects.
- **An opportunity.** Blender projects carry per-project asset libraries and variables. A GameStudio project folder could *be* a Blender project, which would give prefab asset libraries per game for free. Worth evaluating before 5.5.
- **The editor-type policy (S08)** must decide whether `PROJECT` is shown.

### 7.2 glTF exporter: collection-instance traversal rewritten

In `blender/exp/tree.py:425-441`:
- children of an instanced collection are now walked object by object; an object whose parent is in the same collection is skipped;
- objects parented to the instancer are no longer flagged `is_children_in_collection`.

**Why it matters:** `core/export.py inject_ids` and `core/convert.py` (the baked `-instance_offset` on instancers) both encode how the exporter lays out collection instances.

**What tests it:**
- `editor/tests/addon/test_convert.py` builds exactly this case: `Instancer` and `InstancerChild`.
- The contract tests pin the camera and light conventions.

**Run both first on a 5.3 build.** This is the most likely real regression of the hop.

### 7.3 GPU backend preference

5.3 migrates stored `OPENGL` and `VULKAN` preferences to a new `USER_GPU_BACKEND_DEFAULT` (`blenloader/intern/versioning_userdef.cc:864-871`). The CPU-bridge Game View design is backend-neutral (ARCHITECTURE §4), and `host/compat.py gpu_backend()` reads the active backend, not the preference. No action is needed. The Vulkan default itself was already planned for.

---

## 8. Outlook for 5.5 LTS

`main` is 5.4.0 alpha (`BKE_blender_version.h`: `504`, `alpha`). Against `main`:
- the patch queue gives the same 5/8 result, with identical conflicts;
- the static API diff gives the same single INFO;
- the only additional raw change is `PreferencesExperimental.use_collection_importer`, which was removed.

**What this means:**
- **The 5.5 churn has not started.** Run the canary weekly (ARCHITECTURE §2) so it is seen as it lands.
- **The `ListBase` and `.hh` header migration is clearly ongoing.** Expect more renames of the BLI/BKE headers the adapter uses. Isolate them behind one `blender_adapter/compat.hh` before 5.5, not after.
- **Blender Projects will mature through 5.4 and 5.5.** The overlap in §7.1 needs a product decision, not just a code fix.

---

## 9. Recommended actions, in priority order

1. **Fix the S07 notifier collision now.** It affects 5.2.2: set `ND_SPACE_EXTENSION` to an unused high value (§4.2).
2. **Add a version-agnostic adapter shim** for `BLI_*.h` vs `.hh` and for `ListBase::first` vs `first()`/`first_as<T>()` (§4.1). That makes the native module build on 5.2.2 and on 5.3+.
3. **Refresh S03 on the canary branch and record the resolutions with `git rerere`.** Do the same for the S05 and S07 keep-both resolutions. Propose a `BLENDER_APP_NAME`-style hook upstream; it removes S03's most fragile hunk.
4. **Decide on `export_pointclouds`.** Add it to `GLTF_SETTINGS` explicitly (§5.1).
5. **Decide how GameStudio projects relate to Blender projects** (§7.1). Until then, filter upstream's File > Project submenu in the template, so there is only one "New Project".
6. **Add the API diff to the canary.** `gsfork canary` now uses a scratch clone, tries every patch and has `--build-check` (§2). The remaining step is to run `api_diff.py source-dump` / `diff` after the patches.
7. **Once a 5.3 build exists**, run, in this order:
   1. `run_contract.py`;
   2. the add-on suite (especially `test_convert`);
   3. `api_diff.py compare` on the two binaries.

---

## 10. Limits of this canary

- No 5.3 binary was built, because a fork build was running on this machine. Runtime behaviour is inferred from the source, and from one 5.2.2 run on 5.3 data (the factory startup file).
- Static API dumps miss C-implemented Python modules and helper-added operator properties (§5.2). Both dumps miss the same things, so they produce no false differences, but changes in those areas would also go unseen.
- Only the 5.2.2 → 5.3 diff was examined in depth. `main` was checked for patch application and API surface only.
