# 12: Forking Blender into the GameStudio IDE: how much needs C?

*Research date: 2026-10-09. Source: Blender **v5.2.2**, commit `d13f752e`, shallow clone at `F:\Development-games\blender-upstream`. All paths below are relative to that checkout unless stated otherwise. Binary: the installed 5.2.2 build (hash `d13f752e3b9c`, the same commit).*

*How to read the tags:*
- ***[V]*** means a probe against the installed binary confirmed the claim.
- ***[V-GUI]*** means a short GUI run confirmed it. It lasted about 8 seconds and saved screenshots.
- ***(inferred)*** means I read it from the source but did not run it.
- *Line numbers come from `grep -n` or `sed -n` on the checkout.*

*How the probes were run:*
- *Every probe used `--factory-startup` and set `BLENDER_USER_RESOURCES` to a fresh folder in the session scratchpad. The user's real Blender profile was never written.*
- *The probe scripts (`probe_*.py`, `build_template.py`, `gui_driver.py`) and fixtures are in the session scratchpad under `custom/`. Nothing was added to the project except this file.*

---

## 1. TL;DR

- **The hypothesis mostly holds.** Blender's UI is defined in Python and can be replaced at runtime.
  - In 5.2.2, `bl_ui` registers **26 headers, 674 menus, 1,123 panels and 32 UI lists** **[V]**. That is about 65,000 lines under `scripts/startup/bl_ui/`.
  - Registering a class with an existing `bl_idname` unregisters the built-in one and takes its place (`source/blender/makesrna/intern/rna_ui.cc:885-906` for headers, `:1041-1060` for menus) **[V]**.
  - A GUI run confirmed this for a custom top bar, workspace tabs, a splash screen and a launcher dialog **[V-GUI]**.
- **An app template is the right vehicle for "the IDE".** A template folder can hold:
  - its own `startup.blend` (workspaces and screens);
  - a `userpref.blend` that controls theme, enabled add-ons, keymap, layout locks and the splash flag;
  - a `splash.png`;
  - an `__init__.py` with `register`/`unregister`.

  `--app-template GameStudio` loaded all of these headless **[V]**.
- **Some things need C, but none of them is large.**
  - **Editor-type dropdown:** built in C (`rna_screen.cc:202-238`).
  - **New editor types:** Python cannot register them ("Space does not support registration") **[V]**.
  - **Fixed parts:** the window title (`wm_window.cc:597-696`), the About label and logo, the splash frame, and the config folder names (`GHOST_SystemPaths*.cc`).
  - **Default template at startup:** a saved preference cannot set it. The template name is deliberately cleared when preferences are read (`source/blender/blenloader/intern/readfile.cc:4072-4073`) **[V]**.
- **The minimal C patch is about six small hunks, roughly 30 lines in total.** Everything else is added files (`scripts/…`, `release/extensions/system/…`) and swapped data such as the splash, icons, `.rc`, `Info.plist`, `.desktop` and CMake packaging strings. The existing install rules ship added files without CMake edits (`source/creator/CMakeLists.txt:467-483`, `:1899-1904`).
- **Config isolation is mandatory.** An unpatched fork shares `%APPDATA%\Blender Foundation\Blender\5.2` with the user's real Blender **[V]**.
  - **Without C:** a `portable` folder next to the executable, or `BLENDER_USER_RESOURCES` **[V]**.
  - **With C:** rename two strings in `GHOST_SystemPathsWin32.cc:35,56`, plus the macOS and Linux equivalents. This is the better fix.
- **A project launcher works in pure Python.** You can replace the splash's contents (`WM_MT_splash`), or open a dialog from a timer **[V-GUI]**. A launcher that appears *before* the main window needs C or an external process.
- **"New editors" can be done in Python as fake editors.**
  - A Python `NodeTree` subclass shows up as an editor type and can be assigned to any area **[V]**.
  - Sidebar panels, header overrides and draw handlers then turn that area into an Entity Inspector, AI Chat or Game View.
  - Real editors (new `SpaceType`s) are a **large** C patch that would conflict on every upgrade.
- **CMake trims rendering and I/O features, not editors.**
  - `WITH_CYCLES=OFF` removes Cycles from the build and the UI. On the installed build it is about 200 MB of add-on, kernels and libraries (inferred).
  - `WITH_FREESTYLE=OFF` and the `WITH_IO_*` options hide their UI.
  - **No option removes the Video Sequencer, Movie Clip, Compositor or Grease Pencil editors.** They are built unconditionally (`source/blender/editors/CMakeLists.txt:33-52`).
- **Build blocker:** the clone has no Git LFS content. `release/datafiles/startup.blend` and `splash.png` are 131–132-byte pointer files, and CMake stops at `CMakeLists.txt:93-99`. Run `git lfs pull` first.

---

## 2. Q1: How much of the UI is Python, and can it be replaced?

### 2.1 Where the UI lives

| Area | Python (overridable) | What stays in C |
|---|---|---|
| **Top bar** | `bl_ui/space_topbar.py` (897 lines).<br>• `TOPBAR_HT_upper_bar` `:14`<br>• `TOPBAR_MT_editor_menus` `:106`<br>• `TOPBAR_MT_blender` `:128`<br>• File, Edit, Render, Window and Help menus `:157-629`<br>• Workspace tabs, drawn by `template_ID_tabs` at `:36` | • The two C menus `TOPBAR_MT_file_open_recent` and `TOPBAR_MT_undo_history` (`editors/space_topbar/space_topbar.cc:213-223`, `:271-281`)<br>• The tab widget itself<br>• The "+" menu `WORKSPACE_MT_add` (`editors/screen/workspace_edit.cc:586-632`) |
| **3D View** | • `space_view3d.py` (9,548 lines): `VIEW3D_HT_header` `:702`, `VIEW3D_MT_editor_menus` `:1143`, `VIEW3D_MT_view` `:1453`, `VIEW3D_MT_add` `:2679`, `VIEW3D_MT_object` `:2806`<br>• `space_view3d_toolbar.py` (2,439 lines)<br>• `space_view3d_sidebar.py`<br>• Tool definitions in `space_toolsystem_toolbar.py` (4,219 lines) | • Viewport drawing and built-in gizmos<br>• The mode selector `template_header3D_mode` (`editors/space_view3d/view3d_header.cc:139`)<br>• The editor-type button |
| **Properties editor** | • 46 `properties_*.py` files<br>• `space_properties.py`: header `:37`, plus the tab bar `PROPERTIES_PT_navigation_bar` `:75`, which draws `prop_tabs_enum(view, "context")` | • The set of tabs (`ED_buttons_tabs_list`, `editors/space_buttons/space_buttons.cc:199-256`)<br>• The `context` enum (`makesrna/intern/rna_space.cc:579`). Python cannot add tabs. |
| **Splash** | • `WM_MT_splash` (`scripts/startup/bl_operators/wm.py:3445`)<br>• `WM_MT_splash_quick_setup` (`:3362`)<br>• `WM_MT_splash_about` (`:3510`) | The frame (`windowmanager/intern/wm_splash_screen.cc:290-408`):<br>• image `:139-185`<br>• version label `:316`<br>• which menu to draw `:350-359`<br>• About logo `:460` and the "Blender" label `:480`<br>• the `wm.splash` operator `:419-427` |
| **Status bar** | `space_statusbar.py:8-31`, `STATUSBAR_HT_header` (layout only) | The widgets it places: `template_input_status`, `template_reports_banner`, `template_running_jobs`, `template_status_info` |
| **Preferences** | `space_userpref.py` (3,167 lines) | The editor itself |
| **File browser** | `space_filebrowser.py` (1,016 lines, 17 classes): headers and side panels | The file list, thumbnails and navigation (`editors/space_file`) |

The registrable UI types are:
- `Panel` (`rna_ui.cc:1849`)
- `UIList` (`:2028`)
- `Header` (`:2204`)
- `Menu` (`:2272`)
- `AssetShelf` (`:2377`)
- `FileHandler` (`:2533`)

`Space` is **not** registrable. The check is `python/intern/bpy_rna.cc:10469` **[V]**.

**Other C-only pieces:**
- **Editor-type selector.** `template_header()` (`editors/interface/templates/interface_templates.cc:244-247`) calls `ED_area_header_switchbutton` (`editors/screen/area.cc:2989-3009`). That draws the `Area.ui_type` enum, whose items are computed at runtime in `rna_Area_ui_type_itemf` (`rna_screen.cc:202-238`).
  - The items come from `rna_enum_space_type_items` (`rna_space.cc:90`) plus node-tree subtypes (`editors/space_node/space_node.cc:1644-1652`).
  - The probe found **0 static items** on `Area.ui_type`, so there is nothing for Python to filter **[V]**.
- **Region layout.** Each `ED_spacetype_*()` defines its `ARegionType`s in C. Python can only toggle region visibility (`space.show_region_ui` and similar).
- **Header right-click menu:** `ED_screens_header_tools_menu_create` (`editors/screen/screen_ops.cc:6096`).
- All drawing inside editors.

### 2.2 Probe: replacing built-ins (`probe_ui_override.py`, headless) [V]

| Action | Result |
|---|---|
| Re-register `TOPBAR_MT_editor_menus` with a new `draw` | `Info: …has been registered before, unregistering previous`. `bpy.types.TOPBAR_MT_editor_menus is NewClass` → **True**, and the original's `is_registered` → False. |
| Re-register `TOPBAR_HT_upper_bar` | Replaced. Registering the original class object again **restores** it. |
| `unregister_class` on `VIEW3D_MT_editor_menus`, `SEQUENCER_HT_header`, `RENDER_PT_freestyle`, `STATUSBAR_HT_header` | All OK. |
| `TOPBAR_MT_help.append(fn)` | Works. This is the least intrusive way to add items. |
| Register a Python menu named `TOPBAR_MT_file_open_recent` (a C menu) | Refused: `could not be unregistered` |
| Register a Python operator `wm.splash` | Refused: `could not be unregistered`. Operator rule: `rna_wm.cc:1839-1861`. |
| Subclass `bpy.types.Space` | `ValueError … Space does not support registration` |
| Replace `WM_MT_splash` (Python, drawn inside the C splash) | Replaced. |

The same overrides, loaded from an app template, were checked in a GUI run **[V-GUI]**:
- The top bar showed `File | Edit | Project… | [GameStudio menus]`, the template's tabs `Level / Play / Debug / Scripting`, and "GameStudio IDE" on the right.
- The splash showed the template's `splash.png` with the overridden `WM_MT_splash` contents.

**Upgrade advice.** A copied `draw()` is a fork of upstream code. Prefer, in this order:
1. `append`/`prepend`;
2. a thin subclass that calls the original `draw`;
3. a full replacement, kept only for a few top-level classes (`TOPBAR_*`, `WM_MT_splash*`).

`scripts/modules/bl_app_override/__init__.py:34` (`ui_draw_filter_register`) is built for app templates. It wraps every Python `draw()` and filters operators, properties, menus and labels by name, without copying any classes.

---

## 3. Q2: App templates

### 3.1 Discovery and loading

Search order is the same in C (`blenkernel/intern/appdir.cc:1092-1120`) and Python (`scripts/modules/bpy/utils/__init__.py:523-550`):
1. `{USER_SCRIPTS}/startup/bl_app_templates_user/`
2. each path in `BLENDER_SYSTEM_SCRIPTS` (separated by `;` on Windows), under `startup/bl_app_templates_system/`
3. the install's `{ver}/scripts/startup/bl_app_templates_system/`

Upstream ships five templates there: 2D_Animation, Sculpting, Storyboarding, VFX and Video_Editing.

**Loading (`windowmanager/intern/wm_files.cc`, `wm_homefile_read_ex`):**
- **Choosing the template:**
  - The command-line override wins over `U.app_template` (`:1257-1263`).
  - `BKE_appdir_app_template_id_search` finds the template folder (`:1362-1367`).
- **`startup.blend`:**
  - The user's saved copy `{config}/{template}/startup.blend` is used if it exists.
  - Otherwise the template's own file is used (`:1376-1393`). In that case the load counts as a factory startup, so `load_factory_startup_post` fires (`:786-788`).
- **`userpref.blend`:** the template's file is read and *swapped* into `U` (`:1467-1495`). It can change only these fields (`blenkernel/intern/blender.cc:438-457`):
  - `themes`, `uistyles`, `uifonts`
  - `addons`
  - `user_keymaps`, `user_keyconfig_prefs`, `keyconfigstr`
  - `gizmo_flag`, `app_flag` (layout locks)
  - three `uiflag` bits: save prompt, **splash disable**, navigate gizmo
  - `ui_scale`
- **Python:** `bl_app_template_utils.reset()` calls the module's `register()` (`scripts/modules/bl_app_template_utils.py:37-79`, `:152-177`). When the user switches template, the old module's `unregister()` runs first.
- **Splash image:** `{template}/splash.png` replaces the image (`wm_splash_screen.cc:144-152`).
- **Saving preferences:** while a template is active, saving writes the template's part to `{config}/{template}/userpref.blend` and the rest to `{config}/userpref.blend` (`blenkernel/intern/blendfile.cc:1680-1729`) **[V]**. Both files were created in the run C1 below.

### 3.2 Probe: `--app-template` [V]

`build_template.py` built a "GameStudio" template from factory data:
- workspaces renamed to Level, Scripting, Play and Debug, with the rest removed;
- a red theme widget colour;
- `prefs.apps.show_corner_split` and `show_edge_resize` set to False;
- `ui_scale` 1.1;
- two extra add-ons enabled: a legacy add-on and a System-repo extension;
- a generated `splash.png`.

`probe_app_template.py` then reported the following.

| | Factory (no template) | `--factory-startup --app-template GameStudio` |
|---|---|---|
| `prefs.app_template` | `''` | `'GameStudio'` |
| Workspaces | 11 stock | `Debug, Level, Play, Scripting` |
| Template hooks fired | none | `register` → `load_post` → `load_factory_startup_post` |
| `TOPBAR_MT_editor_menus.__module__` | `bl_ui.space_topbar` | `bl_app_templates_system.GameStudio` |
| Theme `wcol_regular.inner` | (0.33, 0.33, 0.33) | (0.8, 0.1, 0.1) |
| Layout locks (corner split / edge resize) | True / True | False / False |
| Enabled add-ons | 8 stock | 8 stock + `gamestudio_stub` + `bl_ext.system.gamestudio_ext` (both registered) |

`--factory-startup` still loads the template's *factory* files. The comment at `wm_files.cc:1372-1373` says so explicitly.

### 3.3 Making the build start in the template by default

- **No compile-time or CMake setting exists.** `app_template` and `APP_TEMPLATE` do not appear in any CMake file.
- **A saved preference does not work.** Run C1 used `--app-template GameStudio` and saved preferences. Run C2 started with no arguments and came up with `app_template=''` and stock workspaces **[V]**. The cause is `readfile.cc:4072-4073`: *"Don't read the active app template, use the default one."*
- **Option 1, no C:** always launch with `--app-template GameStudio`.
  - Put it on the installer shortcut or the `.desktop` `Exec` line.
  - `blender-launcher.exe` forwards its command line (`source/creator/blender_launcher_win32.c:98-105`).
  - The flag is parsed in the settings pass (`source/creator/creator_args.cc:1910-1922`, registered at `:3347`).
- **Option 2, a one-line C patch:** initialise the static at `wm_files.cc:1159-1162` to `{"GameStudio", true}`.
  - `WM_init` already passes it to the first home-file read (`wm_init_exit.cc:280`).
  - File > New → General still works because choosing a template explicitly clears the override (`wm_files.cc:2914-2925`).

### 3.4 Hiding "General / 2D Animation / Sculpting"

- **File > New list and splash "New File" column:** pure Python.
  - `TOPBAR_MT_file_new.app_template_paths()` lists the folders (`space_topbar.py:218-240`).
  - `draw_ex()` hard-codes "General" (`:262`).
  - The template can re-register `TOPBAR_MT_file_new` with its own draw.
- **The C "+" workspace menu:** it lists workspaces from *every* template's `startup.blend` (`workspace_edit.cc:601`). To hide those entries, either:
  - **delete** the five folders under `scripts/startup/bl_app_templates_system/` in the fork, which is a data-only change; or
  - leave out `new="workspace.add"` when drawing the tabs.

---

## 4. Q3: Hiding and reshaping without C

| Goal | Pure Python? | How (citation) |
|---|---|---|
| Hide editor types in the editor-type menu | **No** | Items are built in C (`rna_screen.cc:202-238`). Only `TOPBAR` and `STATUSBAR` are skipped (`:217`). Python can only:<br>• remove the Shift+F1…F12 shortcuts `screen.space_type_set_or_cycle` (`scripts/presets/keyconfig/keymap_data/blender_default.py:735-745`);<br>• replace `template_header()` in the 26 Python headers with a custom whitelist menu that sets `area.ui_type`;<br>• add a `msgbus` or timer guard that reverts areas switched to a forbidden type (inferred). |
| Remove workspace tabs / set the default workspaces | Yes | Draw the top bar without `template_ID_tabs`, or ship only the wanted workspaces in the template `startup.blend` **[V]**. The active workspace saved in that file opens first. `window.workspace = …` is deferred to the event loop (`rna_wm.cc:969-983`) and did not apply in `--background` **[V]**. |
| Remove menus and panels | Yes | `unregister_class`, re-registration, `append`/`prepend`, or `bl_app_override.ui_draw_filter_register` **[V]**. |
| Hide Properties tabs | Yes, per editor | The `SpaceProperties.show_properties_*` toggles (20 of them; `space_buttons.cc:170-191`) **[V]**. *Adding* a tab is C. |
| Hide add-on UI per workspace | Yes | `WorkSpace.use_filter_by_owner` and `owner_ids` (`bl_ui/properties_workspace.py:50-63`) **[V]**. |
| Lock the layout | Yes | `prefs.apps.show_corner_split`, `show_edge_resize`, `show_regions_visibility_toggle` (`rna_userdef.cc:7576-7600`, flags at `DNA_userdef_types.h:232-236`). The template `userpref.blend` carries them through `app_flag` **[V]**. |
| Theme | Yes | Template `userpref.blend` (`themes` swap) **[V]**, XML presets in `scripts/presets/interface_theme/`, or direct RNA edits **[V]**. The *factory* theme is C data (`release/datafiles/userdef/userdef_default_theme.c`), which you don't need to touch. |
| Keymaps | Yes | `wm.keyconfigs.addon.keymaps.new(...)` **[V]**, or ship a whole preset (`scripts/presets/keyconfig/*.py`). The full default map is Python data in `keymap_data/blender_default.py`. The template `userpref.blend` selects it through `keyconfigstr` (`blender.cc:442-447`). `keyconfig_init` always loads "Blender" first (`bpy/utils/__init__.py:852-868`). |
| Window title | **No** | Built in `wm_window_title_text` (`wm_window.cc:597-696`): `"Blender"` at `:610` and `" - Blender {version}"` at `:693`. `Window` has no title property **[V]**. A Windows-only `ctypes` `SetWindowTextW` hack did change it **[V-GUI]**, but Blender rewrites the title on file-read, save and data-changed notifiers (`wm_event_system.cc:636-643`). Not recommended. |

---

## 5. Q4: Branding points

The paths and lines below were collected by a sub-agent from the same checkout. I spot-checked 18 of them and all matched.

| Item | Where | Kind |
|---|---|---|
| **Window title** | `windowmanager/intern/wm_window.cc:610` (child windows), `:693` (main suffix) | C, 2 lines |
| **About dialog** | • `wm_splash_screen.cc:480` `IFACE_("Blender")`<br>• Logo `ICON_BLENDER_LOGO_LARGE` `:460`, drawn from `release/datafiles/icons_svg/blender_logo_large.svg`<br>• Operator name "About Blender" `:503`<br>• Text and links in Python: `bl_operators/wm.py:3510-3552` | C 1–2 lines, plus data and Python |
| **Splash** | • `release/datafiles/splash.png`, embedded by `editors/datafiles/CMakeLists.txt:1076`<br>• Runtime overrides with no code change: template `splash.png` (`wm_splash_screen.cc:144-152`), `BLENDER_CUSTOM_SPLASH` (`:156`), `BLENDER_CUSTOM_SPLASH_BANNER` (`:197`)<br>• Splash links: `wm.py:3445-3507` | Data or Python |
| **Version** | • `blenkernel/BKE_blender_version.h:23-33`<br>• String built at `blenkernel/intern/blender.cc:104-144`<br>• Parsed by CMake (`build_files/cmake/macros.cmake:959-1014`) into the "5.2" folder name (`:1008`)<br>**Keep it as is.** It drives `.blend` versioning (`BLENDER_FILE_VERSION`, `:32`) and extension compatibility. Show the fork's own version in the title and About instead (inferred design choice). | — |
| **App icon / Windows metadata** | `release/windows/icons/winblender.ico` and `winblender.rc:12-31`, which holds:<br>• `CompanyName "Blender Foundation"`<br>• `ProductName "Blender"`<br>• `OriginalFilename "blender.exe"`<br>Linked by `source/creator/CMakeLists.txt:159-169` | Data |
| **macOS** | `release/darwin/Blender.app/Contents/Info.plist`:<br>• `:35` executable<br>• `:40,:43` icons<br>• `:45` `org.blenderfoundation.blender`<br>• `:49` name<br>`OUTPUT_NAME Blender` at `source/creator/CMakeLists.txt:1700` | Data/CMake |
| **Linux** | • `release/freedesktop/blender.desktop:2,82,83,89`<br>• X11 class `intern/ghost/intern/GHOST_SystemX11.hh:30-31`<br>• Wayland app id: CMake `WITH_GHOST_WAYLAND_APP_ID` (`CMakeLists.txt:357`) | Data/CMake/C 2 lines |
| **Windows AppUserModelID / taskbar name** | `CMakeLists.txt:173,175` (`BLENDER_WIN_APPID`, `…_FRIENDLY_NAME`) | CMake |
| **Executable / install** | • `add_executable(blender …)` at `source/creator/CMakeLists.txt:338`<br>• Layout `TARGETDIR_VER "5.2"`, `blender.shared` at `:415-421`<br>• `project(Blender)` at `CMakeLists.txt:81`<br>• Installer: `build_files/cmake/packaging.cmake:89-90` (install dir and registry key), `:116-117` (shortcut), WiX files in `release/windows/installer_wix/`<br>• Renaming `blender.exe` also touches hard-coded names in `blender_launcher_win32.c:98`, `blenlib/intern/winstuff.cc:205,209`, `system_win32.cc:547-560` and `GHOST_SystemWin32.cc:2951` | CMake/data. Renaming the exe adds C in 5 places. |
| **User and system config dirs** | • Windows: `intern/ghost/intern/GHOST_SystemPathsWin32.cc:35` (system, ProgramData) and `:56` (user, AppData), both `"\\Blender Foundation\\Blender\\"`<br>• macOS: `GHOST_SystemPathsCocoa.mm:33`<br>• Linux: `GHOST_SystemPathsUnix.cc:45,104,109`<br>• Cache: `blenkernel/intern/appdir.cc:220-230` | C, about 8 lines |
| **Menus and links** | • `TOPBAR_MT_blender` "Blender" (`space_topbar.py:115-117,128-129`)<br>• Help URLs (`:600-629`)<br>• `WM_OT_url_open_preset` URLs (`wm.py:1114-1157`) | Python |

**Minimal rebrand edit set:**
1. **Config dirs:** about 8 lines in the three `GHOST_SystemPaths*` files plus `appdir.cc:220-230`. This keeps the fork from touching the real Blender profile.
2. **Title and About:** 3 lines in `wm_window.cc:610,693` and `wm_splash_screen.cc:480`.
3. **Resource swaps:**
   - `splash.png`, `blender_logo_large.svg`
   - `.ico` / `.icns` / `.svg`
   - `winblender.rc`, `Info.plist`, `blender.desktop`
4. **CMake strings:**
   - `CMakeLists.txt:173-175` (app id)
   - `packaging.cmake:89-90,116-117` (installer)
   - optionally the Wayland app id
5. **Python strings:** `TOPBAR_MT_blender`, Help and splash links. These are best overridden from the template instead of editing upstream files.
6. **Leave alone:**
   - the executable name;
   - `BLENDER_VERSION`;
   - the `.blend` magic `"BLENDER"` (`blenloader/intern/writefile.cc:1570,1587`).

---

## 6. Q5: Startup flow and where a launcher fits

**`source/creator/creator.cc`:**
1. Create the argument parser (`:513`).
2. `ARG_PASS_ENVIRONMENT` (`:521`): handles `--env-system-*`.
3. `BKE_appdir_init` (`:530`).
4. `ARG_PASS_SETTINGS` (`:548`): handles `--background`, `--factory-startup` and `--app-template` (`creator_args.cc:3121,3347-3348`).
5. Initialise subsystems.
6. `ARG_PASS_SETTINGS_GUI` (`:603`).
7. **`WM_init`** (`:608`).
8. `ARG_PASS_FINAL` (`:627`): `--python` and the `.blend` file argument.
9. Background mode exits here (`:649-659`). Otherwise `WM_init_splash_on_startup` (`:666`) runs, then `WM_main` (`:668`).

**`WM_init` (`windowmanager/intern/wm_init_exit.cc:201`):**
1. GHOST init.
2. Operator and space types (`:213-230`).
3. **`wm_homefile_read_ex`** (`:283`): reads preferences, then `startup.blend` (template or factory), then template preferences. The main window is set up from the startup file's window manager here, in C.
4. GPU and UI init.
5. **`BPY_python_start`** (`:331`). `import bpy` runs `utils.load_scripts()` (`scripts/modules/bpy/__init__.py:70`), which registers every module in each `scripts/startup/` dir, including `bl_ui`.
6. `WM_keyconfig_init` (`:363`).
7. **`wm_init_scripts_extensions_once`** (`:367`): the app template's `register()`, then add-ons and extensions.
8. **`wm_homefile_read_post`** (`:372`): fires `load_post` and `load_factory_startup_post` (`wm_files.cc:779-788`).

**Splash:** `wm_init_splash_show_on_startup_check` (`:375-395`) returns false if `USER_SPLASH_DISABLE` is set, or if a `.blend` was passed (unless no preferences exist). Otherwise `WM_init_splash` calls `"WM_OT_splash"` by name (`:416`).

**Launcher options, from least to most C:**

| Option | C? | Status |
|---|---|---|
| A. Replace `WM_MT_splash` (and `_quick_setup`) from the template. The splash becomes the launcher, keeping the template's `splash.png` and a C-drawn version label. | none | **[V-GUI]** |
| B. Set `show_splash=False` in the template's `userpref.blend`. A `load_post` handler registers a one-shot `bpy.app.timers` callback that calls `bpy.ops.gamestudio.launcher('INVOKE_DEFAULT')` → `invoke_props_dialog`. | none | Handlers fire at startup **[V]**. Timer → dialog **[V-GUI]**. Guard against `load_post` firing on every file load. `load_factory_startup_post` only fires while the user hasn't saved their own startup file. |
| C. A "Launcher" workspace saved as active in the template's `startup.blend`: a single full-window area, for example a custom NodeTree editor with panels. It switches to Level after the user picks a project. | none | (inferred) |
| D. An external launcher process (for example the existing Node tooling) runs `gamestudio.exe <project>/main.blend --app-template GameStudio`. Passing a file suppresses the splash. | none | (inferred) |
| E. Call `"GAMESTUDIO_OT_launcher"` instead of `WM_OT_splash` at `wm_init_exit.cc:416`, falling back to the splash if it isn't registered. | 1–3 lines | (inferred) |
| F. A true launcher *before* the main window, for example a GHOST window created before `startup.blend`'s windows exist. | large | (inferred). Not worth it. |

`wm.splash` itself cannot be overridden from Python **[V]**, but nothing requires it to be.

---

## 7. Q6: Bundling scripts and extensions

The install copies the whole `scripts/` tree to `{ver}/scripts` (`source/creator/CMakeLists.txt:467-483`) and `release/extensions/` to `{ver}/extensions` (`:1899-1904`). **New files under those folders ship with no CMake edits.**

| Route | Location in the fork | Always on? | User can disable? | `AddonPreferences` | Verified |
|---|---|---|---|---|---|
| Startup module | `scripts/startup/gamestudio_*.py`, or `BLENDER_SYSTEM_SCRIPTS/startup` | Yes, every launch (`bpy/utils/__init__.py:326-336`) | No toggle exists | No | **[V]** `always_on.register` ran with and without a template |
| "Hidden core" add-on | `scripts/addons_core/gamestudio`, plus a one-line edit to `_addons_hidden_core` (`scripts/modules/addon_utils.py:46-52`) | Yes, enabled first and persistent (`:65-72`) | Hidden in the UI. `preferences.addon_disable` still works for the session, and it comes back on restart. | **No**: the comment at `:41-45` says hidden core add-ons can't store preferences | **[V]**, simulated by adding to the set from a startup module |
| System extension | `release/extensions/system/gamestudio/` with `blender_manifest.toml` → `{ver}/extensions/system`, module `bl_ext.system.gamestudio` (`blenkernel/intern/preferences.cc:343-347,427-429`) | Only if enabled | Yes | Yes | **[V]** with `BLENDER_SYSTEM_EXTENSIONS` |
| Enable through the template's `userpref.blend` `addons` list | template folder | While the template is active | Yes. A disable is saved to `{config}/GameStudio/userpref.blend` | Yes | **[V]** |
| Factory default list | `blenkernel/intern/blendfile.cc:1526-1541` | For new profiles | Yes | Yes | (inferred). This is C. |
| `--addons gamestudio` | launcher args (`creator_args.cc:3388`) | Per launch | Yes | Yes | (inferred) |

**Recommendation:**
1. Ship the existing add-on unchanged as a **System extension**. It is already in extension format: `blender/gamestudio/blender_manifest.toml`.
2. Enable it in the **GameStudio template's `userpref.blend`**.
3. Put the IDE chrome (top-bar overrides, launcher, policy hooks) in the **template's `__init__.py`**, so it is on whenever the IDE is.

Preventing users from disabling the add-on is not needed. Disabling it only removes GameStudio panels, and File > Defaults > Load Factory GameStudio Settings brings it back. If a hard guarantee is wanted, move `prefs.py` to a JSON file in the config dir and use the hidden-core route.

Path behaviour of the environment variables:
- `BLENDER_SYSTEM_SCRIPTS` takes several paths. Each one adds `startup/` modules, an `addons/` dir, `presets/` and app templates (`bpy/utils/__init__.py:424-431`, `appdir.cc:1107-1108`) **[V]**.
- `BLENDER_SYSTEM_EXTENSIONS` moves the System repository (`appdir.cc:730-741`) **[V]**.

---

## 8. Q7: Portable and separate configuration

**User path precedence** (`appdir.cc:496-537`):
1. `BLENDER_USER_RESOURCES`
2. `{exe_dir}/portable/`
3. GHOST `getUserDir` → `…/Blender Foundation/Blender/5.2`

Per-folder variables override within that: `BLENDER_USER_CONFIG`, `_SCRIPTS`, `_DATAFILES`, `_EXTENSIONS` (`appdir.cc:649-760`).

**System path:** `BLENDER_SYSTEM_RESOURCES`, then GHOST `getSystemDir` (`C:\ProgramData\Blender Foundation\Blender\5.2`), then `{exe_dir}/5.2` (`:559-594`). For scripts, the system path is checked **before** the bundled local folder (`:712-719`). An unpatched fork would therefore pick up anything an administrator put in Blender's ProgramData folder. That is another reason to rename the GHOST paths.

**Command line:** only `--env-system-datafiles`, `--env-system-scripts`, `--env-system-python` and `--env-system-extensions` exist (`creator_args.cc:3077-3085`). There is no `--env` and no `--env-user-*`.

**Probes (`probe_paths.py`) [V]:**

| Setup | `resource_path('USER')` | `user_resource('CONFIG')` |
|---|---|---|
| Default | `C:\Users\<user>\AppData\Roaming\Blender Foundation\Blender\5.2` | `…\5.2\config` |
| `BLENDER_USER_RESOURCES=<dir>` | `<dir>` (**no** `5.2` subfolder) | `<dir>\config` |
| …plus `BLENDER_USER_CONFIG=<cfg>` | `<dir>` | `<cfg>` (scripts stay in `<dir>\scripts`) |
| Copy of the install with an empty `portable\` next to `blender.exe` | `<copy>\portable` | `<copy>\portable\config`. `save_userpref` wrote `portable\config\userpref.blend`. |
| Portable folder **and** `BLENDER_USER_RESOURCES` | the environment variable wins | — |

`bpy.app.portable` reports the build flag `WITH_INSTALL_PORTABLE` (False here). It does not reflect the runtime folder.

**For the fork:** rename the GHOST user and system dirs (section 5). Use `portable` only for zip or dev builds, because Program Files is not writable per user.

---

## 9. Q8: Ranked inventory

| Feature | Verdict | Reason and reference |
|---|---|---|
| Custom top bar and menus | **Pure Python** | Re-register `TOPBAR_HT_upper_bar` and `TOPBAR_MT_*`, or `append`. Only the two C menus need their parent menu replaced (`space_topbar.cc:213-223,271-281`). **[V-GUI]** |
| Game workspaces: Level, Scripting, Play, Debug | **Pure Python/data** | Template `startup.blend` **[V]**. Delete the stock templates to clean up the "+" menu (`workspace_edit.cc:601`). |
| Custom theme | **Pure Python/data** | Template `userpref.blend` `themes` swap (`blender.cc:440`) **[V]** |
| Keymaps | **Pure Python/data** | Add-on keyconfig **[V]** or a preset plus `keyconfigstr` (`blender.cc:447`) |
| Project launcher (over the main window) | **Pure Python** | `WM_MT_splash` override or timer → dialog **[V-GUI]**. A pre-window launcher is large C or an external process (§6). |
| Project content browser | **Pure Python, limited** | An area set to `ui_type='FILES'` or `'ASSETS'` **[V]**, plus `prefs.filepaths.asset_libraries.new(name, directory)` **[V]** and `AssetShelf` (`rna_ui.cc:2377`). A custom grid is C (`editors/space_file`). |
| Entity inspector as its own editor | **Python as a fake editor**; a real one is **large C** | Register a `NodeTree` subclass; it appears as an editor type and `area.ui_type="GS_EntityTree"` works **[V]** (`space_node.cc:1644-1652`). Add sidebar panels polled on `tree_type`. A plain 3D View N-panel tab also works. A real editor is a new `SpaceType` plus DNA, RNA, enum item and versioning. |
| AI chat editor | **Python as a fake editor** | Same NodeTree trick or a sidebar tab. 5.2 has a multi-line `UILayout.textbox()` (`makesrna/intern/rna_ui_api.cc:1562-1574`, present in the binary **[V]**) for input. Scrolling chat history and rendering would use `blf`/`gpu` draw handlers (`SpaceNodeEditor.draw_handler_add` exists **[V]**) or a Text block. |
| Dockable Game View | **Python is possible but slow**; fast needs **medium/large C** | Fake editor plus a POST_PIXEL draw handler, but `GPUTexture` has no in-place update **[V]**: methods are `read`/`clear` only, and a 1080p upload costs about 9–23 ms (research/04). Options: a separate runtime window (current design), or a C patch for a texture-update or shared-texture API in `python/gpu` (medium), or a real Game `SpaceType` (large). Reparenting the runtime's HWND with `ctypes` is Windows-only (inferred). |
| Hiding irrelevant editors (Compositor, VSE, Grease Pencil) | **Small C patch** | Filter `rna_Area_ui_type_itemf` (`rna_screen.cc:217`) and the node-tree subtype list, about 10–20 lines. Python can only remove the shortcuts and redraw headers (§4). No CMake option removes them (`editors/CMakeLists.txt:33-52`, `space_api/spacetypes.cc:70-87`). |
| Branding | **Small C patch plus data** | About 11 C lines (§5) plus resource and CMake string swaps |
| Default to the GameStudio template | **Python-only via launcher args**, or **1-line C** | `readfile.cc:4073` stops it from being a preference **[V]**. Patch `wm_files.cc:1159-1162`. |
| Isolated user config | **Small C patch** (or `portable` / env var) | `GHOST_SystemPaths*.cc` (§5, §8) **[V]** |
| Build-time feature removal | **CMake only** | See below |

**CMake options (`CMakeLists.txt`):**

| Option | Line | Effect |
|---|---|---|
| `WITH_CYCLES` | `:600` | **Shrinks the build and the UI.**<br>• `intern/CMakeLists.txt:31-33` skips the add-on, which installs to `scripts/addons_core/cycles` (`intern/cycles/CMakeLists.txt:14`).<br>• `wm_files.cc:523-525` removes it from preferences.<br>• `WITH_CYCLES_OSL` is forced off (`:1340-1342`).<br>• Installed size today: the add-on folder is 72 MB and `cycles_kernel_oneapi_aot.dll` is 40 MB. The OSL DLLs (72 MB) and Embree (26 MB) are also Cycles-related (inferred). |
| `WITH_FREESTYLE` | `:515` | Shrinks and hides UI: `bl_ui/__init__.py:101-102`, `bl_operators/__init__.py:52-53`, freestyle scripts excluded (`source/creator/CMakeLists.txt:460-465`). |
| `WITH_IO_WAVEFRONT_OBJ`, `_PLY`, `_STL`, `_FBX`, `_GREASE_PENCIL` | `:459-463` | Hide their File menu entries (`space_topbar.py:371-415` check `bpy.app.build_options`). There is no `WITH_IO_CSV`. |
| `WITH_USD`, `WITH_HYDRA`, `WITH_MATERIALX` | `:439`, `:445`, `:442` | About 55 MB of USD libraries plus MaterialX. The menu entries hide. |
| `WITH_ALEMBIC` | `:436` | Library size; the menu entries hide. |
| `WITH_CODEC_FFMPEG` | `:432` | About 43 MB of DLLs. Video output disappears. |
| `WITH_OPENIMAGEDENOISE` | `:315` | About 64 MB. |
| `WITH_MOD_FLUID`, `WITH_MOD_OCEANSIM`, `WITH_MOD_REMESH` | `:419-421` | Fluid UI checks `build_options.fluid` (`properties_physics_fluid.py:117`). |
| `WITH_BULLET`, `WITH_LIBMV`, `WITH_OPENVDB`, `WITH_POTRACE`, `WITH_XR_OPENXR`, `WITH_INPUT_NDOF`, `WITH_INTERNATIONAL` | various | Smaller cuts. **Keep `WITH_BULLET` if rigid-body baking matters.** |

`build_files/cmake/config/blender_lite.cmake:12-64` shows the full set that can be turned off together. No option disables EEVEE, the sequencer, motion tracking UI, the compositor or Grease Pencil.

---

## 10. Sources

**Blender v5.2.2 source** (`F:\Development-games\blender-upstream`, commit `d13f752e`):

*Startup and command line*
- `source/creator/creator.cc`
- `source/creator/creator_args.cc`
- `source/creator/blender_launcher_win32.c`
- `source/creator/CMakeLists.txt`

*Window manager*
- `source/blender/windowmanager/intern/wm_init_exit.cc`
- `source/blender/windowmanager/intern/wm_files.cc`
- `source/blender/windowmanager/intern/wm_splash_screen.cc`
- `source/blender/windowmanager/intern/wm_window.cc`
- `source/blender/windowmanager/intern/wm_event_system.cc`

*Kernel and file loading*
- `source/blender/blenkernel/intern/appdir.cc`
- `source/blender/blenkernel/intern/blender.cc`
- `source/blender/blenkernel/intern/blendfile.cc`
- `source/blender/blenkernel/intern/preferences.cc`
- `source/blender/blenkernel/BKE_blender_version.h`
- `source/blender/blenloader/intern/readfile.cc`

*RNA and Python bindings*
- `source/blender/makesrna/intern/rna_ui.cc`
- `source/blender/makesrna/intern/rna_ui_api.cc`
- `source/blender/makesrna/intern/rna_screen.cc`
- `source/blender/makesrna/intern/rna_space.cc`
- `source/blender/makesrna/intern/rna_wm.cc`
- `source/blender/makesrna/intern/rna_userdef.cc`
- `source/blender/makesrna/intern/rna_nodetree.cc`
- `source/blender/python/intern/bpy_rna.cc`

*Editors*
- `source/blender/editors/screen/area.cc`
- `source/blender/editors/screen/screen_ops.cc`
- `source/blender/editors/screen/workspace_edit.cc`
- `source/blender/editors/space_buttons/space_buttons.cc`
- `source/blender/editors/space_node/space_node.cc`
- `source/blender/editors/space_topbar/space_topbar.cc`
- `source/blender/editors/CMakeLists.txt`
- `source/blender/editors/space_api/spacetypes.cc`
- `source/blender/makesdna/DNA_userdef_types.h`

*GHOST (platform layer)*
- `intern/ghost/intern/GHOST_SystemPathsWin32.cc`
- `intern/ghost/intern/GHOST_SystemPathsCocoa.mm`
- `intern/ghost/intern/GHOST_SystemPathsUnix.cc`
- `intern/ghost/intern/GHOST_SystemX11.hh`

*Python scripts*
- `scripts/startup/bl_ui/*.py`
- `scripts/startup/bl_operators/wm.py`
- `scripts/modules/bl_app_template_utils.py`
- `scripts/modules/bl_app_override/__init__.py`
- `scripts/modules/addon_utils.py`
- `scripts/modules/bpy/__init__.py`
- `scripts/modules/bpy/utils/__init__.py`
- `scripts/presets/keyconfig/keymap_data/blender_default.py`

*Build and release files*
- `CMakeLists.txt`
- `build_files/cmake/packaging.cmake`
- `build_files/cmake/macros.cmake`
- `build_files/cmake/config/blender_lite.cmake`
- `intern/CMakeLists.txt`
- `intern/cycles/CMakeLists.txt`
- `release/windows/icons/winblender.rc`
- `release/darwin/Blender.app/Contents/Info.plist`
- `release/freedesktop/blender.desktop`

**Probes** run against `F:\Program Files\Blender Foundation\Blender 5.2\blender.exe` (5.2.2, `d13f752e3b9c`). The scripts are in the session scratchpad under `custom/`.

| Probe | What it checked |
|---|---|
| `probe_ui_override.py` | Re-registration, refusals, enum items, layout locks, theme, keymaps |
| `build_template.py` and `probe_app_template.py` | `--app-template`, template preferences, add-ons, the saved-preference default |
| `probe_paths.py` | Environment variables and `portable` on a scratch copy of the install |
| `probe_editors.py` | NodeTree as an editor type, Properties tabs, asset libraries, class census |
| Hidden-core probe | `_addons_hidden_core` behaviour |
| `gui_driver.py` | One isolated GUI run: screenshots of the top bar, splash and launcher, plus the OS window title |

**Related project research:** `research/04-blender-internals.md` (Python cannot add a `Space`; cost of a GPU texture upload).
