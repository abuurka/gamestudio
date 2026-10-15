# GameStudio Editor: architecture of a Blender-based game-engine IDE

*Status: proposed, 2026-10-09. Supersedes the "add-on only" editor design in `research/00-synthesis.md` §3. The runtime, protocol and AI design from that document carry over unchanged.*

*Evidence: research reports written for this decision, plus 04–07. Claims marked [V] were verified against Blender 5.2.2 source or binaries; file and line references are in the reports.*

| Report | Topic |
|---|---|
| [08](../research/08-blender-fork-prior-art.md) | Fork prior art and legal constraints |
| [09](../research/09-embedding-game-view.md) | Game View embedding |
| [10](../research/10-blender-source-native-seams.md) | Native seams, from the 5.2.2 source |
| [11](../research/11-fork-release-engineering.md) | Release engineering |
| [12](../research/12-blender-source-customization.md) | Python-side customization, from the 5.2.2 source |

---

## 0. Summary

GameStudio Editor is built as a **thin, layered distribution of Blender**, the way Brave sits on Chromium and VSCodium on VS Code. It is not a deep fork. Upstream Blender stays almost untouched at a pinned LTS tag, and every GameStudio part sits on top of it in its own layer, with a hard boundary between each pair of layers.

```
 L4  Engine services   separate processes, MIT     runtime (TS), dev server/hub, runtime host (Electron), MCP
 ───────────────────── protocols: SPEC.md (live link) · SURFACE.md (frames + input) ──────────────────────────
 L3  IDE shell         Python, GPL                  app template "GameStudio" + bundled system extensions
 ───────────────────── Host API: gamestudio.host (capability-detecting facade) ───────────────────────────────
 L2  Native module     C++, GPL, out-of-tree         Extension Editor framework, Game View, _gamestudio, adapter
 ───────────────────── Seams: ≤ 15 patches, ~150–250 upstream lines, one purpose each ───────────────────────────
 L1  Upstream Blender  pinned LTS tag (5.2.2), unmodified apart from the seams
```

The upgrade cost lives almost entirely in the seams (L1→L2) and the adapter. A new Blender LTS means:

1. re-apply about 15 small patches to a clean tag;
2. fix compile errors in one adapter directory;
3. run the contract tests.

The research estimate is **1–2 weeks per LTS upgrade** with this structure, against **1–2+ months** for a fork that edits Blender's core files (`research/11` §6.3). Each Blender release changes 4,900–6,400 of its ~8,500 source files, so editing Blender files in place is the trap this design avoids.

**Most of the IDE needs no C at all.** Blender's UI is 674 Python menus, 1,123 panels and 26 headers, and all of them can be replaced from an app template [V]. **Native code is used only where Python cannot reach:**
- branding and config isolation;
- start-up defaults;
- real custom editors;
- a docked, full-speed Game View.

---

## 1. Goals and non-goals

| Goal | How the architecture meets it |
|---|---|
| **G1. Feels like a game-engine IDE.** Project launcher, game workspaces, a docked Game View, inspector, play controls, runtime console, AI assistant. | The L3 shell (app template) defines the whole user-facing experience. L2 adds the editors Python can't create. |
| **G2. Cheap Blender upgrades.** | Patch budget, an out-of-tree native module, an adapter layer, contract tests, and a weekly canary against upstream. |
| **G3. Graceful degradation.** The shell also runs on stock Blender, minus the native features. | The `gamestudio.host` facade detects capabilities, and each feature has a fallback. This keeps a working product even when the fork lags behind upstream. |
| **G4. Games stay free of the GPL.** | The game runtime and its host are separate MIT processes that talk to the editor through documented protocols. They never link Blender code. |
| **G5. Data compatibility.** Files from the fork open in stock Blender, and the other way round. | No changes to existing DNA structs. New editor state lives in one out-of-tree DNA struct. Stock Blender reads such an area as empty and opens it as a 3D Viewport [V by test: `blender/tests/contract/test_file_compat.py`]. `BLENDER_VERSION` is unchanged. |
| **G6. AI helps but never takes over.** | Unchanged from `docs/AI-ASSISTANT.md`. The assistant gets a proper chat editor in the fork. |

**Non-goals:**
- An in-process game engine. The BGE and UPBGE show why (`research/05`).
- Rewriting Blender's own editors.
- Changing the `.blend` format.
- Shipping non-LTS Blender versions.
- Building a code editor inside Blender. Code goes to VS Code or Cursor, the industry norm (§6.7).

---

## 2. The layers in detail

### L1: Upstream Blender (pinned)

- `editor/upstream.lock` pins the tag, commit and library commit: `v5.2.2`, `d13f752e`, `lib/windows_x64@60d6e96b`.
- Blender's history and sources are **never committed** to our repository. Tooling builds the working tree on demand from a local mirror.
- **We track LTS releases only:** 5.2 LTS (supported to July 2028), then 5.5 LTS (proposed for November 2027). A weekly *canary* applies our patches to `blender-v5.3-release` and `main`, so breakage is visible months before it matters (`research/11` §7).

### Seams: the patch queue (the only edits to upstream files)

A seam is a small, **additive** patch that opens an extension point. It never changes existing behaviour. Each seam is one `git format-patch` file in `editor/patches/`, named by concern, with the structured header defined in §9:

```
S05-rna-extension-space.patch
  Why:         expose SpaceExtension (provider idname + IDProperty state) to RNA/Python
  Hook-for:    gs_native/editor_ext (rna_def_space_extension)
  Upstream:    candidate, as part of a "custom editors" design task
  Test:        blender/tests/contract/test_extension_editor.py
  Remove-when: upstream ships registrable custom editors
```

The catalogue below is what's **implemented** in `editor/patches/`. It was designed from `research/10` §10 and `research/12` §5. Each seam exposes a hook with upstream's default value. GameStudio's values come from `editor/native/CMakeLists.txt` and `editor/build/gamestudio.cmake`, so the patches carry no GameStudio specifics.

| # | Seam | Upstream files | Why native |
|---|---|---|---|
| S01 | CMake hook that adds `GS_NATIVE_DIR` after every Blender module | `source/blender/CMakeLists.txt` | Compiles L2 into the binary |
| S02 | Overridable user, system and cache folder names | `GHOST_SystemPaths{Win32,Cocoa,Unix}.cc`, `appdir.cc` | **Mandatory.** Otherwise the fork shares the user's real Blender profile [V] |
| S03 | Overridable app name (window title, About) and Windows app ID | `wm_window.cc`, `wm_splash_screen.cc`, `CMakeLists.txt` | Branding is C-only [V]. Avoids grouping with Blender in the taskbar. |
| S04 | Overridable default app template | `wm_files.cc` | A saved preference can't set it [V: `readfile.cc:4072`] |
| S05 | `SPACE_EXTENSION = 64` plus a **new** `DNA_space_extension_types.h` | `DNA_space_enums.h`, the new file, the DNA header list | Space types must exist before `startup.blend` is read [V] |
| S06 | Register `ED_spacetype_extension()` and the `_gamestudio` module (behind `WITH_GS_NATIVE`) | `spacetypes.cc`, `ED_space_api.hh`, `bpy_interface.cc` | An external `.pyd` can't reach Blender's C++ API |
| S07 | RNA `SpaceExtensionEditor`, `draw_handler_add` support, `ND_SPACE_EXTENSION` | `rna_space.cc`, `bpy_rna_callback.cc`, `WM_types.hh` | Python access. `rna_space.cc` is the most churned file (108 commits/yr), so the hunks sit at list tails. |
| S08 | Editor-type menu filter hook (editor types and sub-types, e.g. `CompositorNodeTree`) | `rna_screen.cc` | The menu is built in C with no static items [V] |
| (S09) | *Planned:* Vulkan texture import/export, public | `gpu/` | Zero-copy Game View (v1.5) |

- **Today:** 8 patches, **+162 / −19 lines** (32 of them a brand-new file) across 18 files. `gsfork status` prints this budget.
- **Budget: at most 15 seams and about 250 upstream lines.** Raising either number needs a written justification in the seam header.
- **Upstreaming:** S01–S04, S08 and the S05–S07 group (as a "custom editors" design) are all candidates. Every seam that lands upstream disappears from the queue.
- **Branding data is not patched.** Icons, the `.rc` file, `Info.plist`, `.desktop` files, the splash and WiX assets are copied over the upstream tree from `editor/branding/` by the tooling.

### L2: Native module `gs_native` (C++, out of tree)

`editor/native/`, built as `bf_gamestudio` through S1. It is never copied into Blender's tree.

| Directory | Role |
|---|---|
| `blender/` | **The adapter layer, the only code allowed to include Blender internal headers** (BKE, ED, WM, GPU, RNA, DNA):<br>• `space_extension.cc`: the Extension Editor, one generic `SpaceType` driven by providers (§3)<br>• `python_module.cc`: `_gamestudio`<br>• later, the Game View's Blender side<br>When upstream renames an internal API, only this directory changes. |
| `core/` | Plain C++ with no Blender headers: the provider registry (`providers.hh`). |
| `gameview/` | Blender-independent C++: the surface receiver (socket, D3D11 bridge, triple buffer) and the host launcher (§4). |
| `CMakeLists.txt` | Sets the identity values for S02–S04, builds `bf_gamestudio` when the S05 seam is present, and wires `WITH_GS_NATIVE` into the two consumers. |

The only new DNA, `SpaceExtension`, is a new upstream-side file added by seam S05. `makesdna` resolves headers inside its own directory, so the header cannot live outside the Blender tree.

Size estimate: **1,000–1,600 lines** for the editor framework plus the Python module (`research/10` §4.4), and about 1,500 more for the Game View. All of it is owned by us and never conflicts with upstream.

### L3: IDE shell (Python)

`editor/shell/`, shipped inside the build:

- **`app_template/GameStudio/`**. The template is the IDE. It carries:
  - `startup.blend` with the workspaces Level, Model, Materials, Animate, Procedural and Debug. The file is *generated* by `editor/shell/tools/build_template.py`, so it is reproducible, diffable and upgradable, and never hand-saved. It holds stock editors only; each workspace carries a layout spec that the template applies on the fork the first time the workspace is shown (Level: Inspector and Console; Debug: Game View, Console, Project, Inspector), so stock Blender never meets unknown areas.
  - `userpref.blend`, also generated, with the theme, keymap, layout locks, enabled extensions and splash flag.
  - `splash.png`.
  - `__init__.py`. Its register and unregister hooks install the top bar and menu overrides, the launcher, editor-visibility policy and the File > New list.
- **System extensions** in `release/extensions/system/` (shipped with no CMake edits [V]):
  - `gamestudio` is today's add-on, unchanged in behaviour: components, IDs, export, live link, Play, and the assistant.
  - `gamestudio_ide` holds the editor providers (Inspector, Assistant chat, Runtime Console, Project view) and the `gamestudio.host` facade.
- **Rules:**
  - Override upstream UI from the template, in this order of preference: `append`/`prepend`, then a thin subclass that calls the original `draw`, then full replacement. Full replacement is reserved for a handful of top-level classes such as `TOPBAR_HT_upper_bar` and `WM_MT_splash`.
  - Use `bl_app_override.ui_draw_filter_register` to hide items without copying classes (`research/12` §2.2).
  - **Never edit `scripts/startup/bl_ui/*.py` in the patch queue.** They change in every release; `space_view3d.py` alone had +188/−121 lines in 5.1→5.2.

### The Host API: `gamestudio.host`

This is the abstraction that keeps the shell independent of how it is hosted. All shell code calls `host`, never `_gamestudio` or version-specific `bpy` details directly:

```python
from gamestudio import host

host.info()            # {"blender": (5, 2, 2), "gamestudio": "0.3.0", "native": True, "gpu_backend": "OPENGL"}
host.has("extension_editors")   # False on stock Blender
host.has("game_view")

host.editors.register(InspectorProvider)    # native: real editor; stock: falls back to a 3D View sidebar tab
host.game_view.open(area=None)              # native: docked Game View; stock: detached window (today's behaviour)
host.compat.system_props(obj)               # wraps bpy differences across Blender versions (e.g. 5.0 props split)
```

- **Capability detection, not version sniffing.** A feature checks `host.has(...)`.
- **Compatibility shims live in one module**, `host/compat.py`, and are the Python counterpart of `blender_adapter/`. A `bpy` change is fixed there once.
- **Contract-tested** against both the fork build and stock Blender in CI, so we always know the fallback still works.

### L4: Engine services (separate processes, MIT)

These are unchanged in principle from today. The one addition is the runtime host:

| Process | Role |
|---|---|
| **Dev server and hub** (Node, `@gamestudio/devserver`) | Vite, the component registry, the live-link hub ([SPEC §5](SPEC.md)) |
| **Runtime host** (Electron, new `@gamestudio/host`) | Runs the game. **Docked mode:** a hidden offscreen `BrowserWindow` with GPU-shared textures, sending frames to the Game View. **Detached mode:** a normal window. This is also the **desktop shipping shell** for games. |
| **Game** (TS engine in the host's renderer) | Unchanged |
| **MCP bridge** | Unchanged. External agents stage proposals only. |

---

## 3. Custom editors: one native editor type, many providers

Python cannot register a `Space` [V]. Even if it could, Blender reads `startup.blend`'s areas **before** Python starts, so a Python-defined editor would be lost on every launch (`research/10` §4.1). The editor *type* must be native, but its *behaviour* can come from providers registered later. This is how the Node Editor works: `SPACE_NODE` is native and the tree types arrive from Python.

**`SPACE_EXTENSION` (ID 64)** is a single generic editor:

```
SpaceExtension { SpaceLink header; char provider_idname[64]; IDProperty *properties; void *runtime; }
```

- **Regions:** HEADER, UI (sidebar), WINDOW and FOOTER. Only existing `RGN_TYPE_*` values are used.
- **Providers** are registered from Python (`host.editors.register`) or from C++ (the Game View). Each one supplies a name, an icon, region polls, an optional native `draw_window`, and panels/headers selected with `bl_context = "<provider_idname>"`. This reuses Blender's own panel filtering [V].
- **The editor-type menu** lists each provider as a sub-type of "Extension Editor", using the `space_subtype_item_extend` mechanism that node-tree types already use [V].
- **Persistent state** goes in `properties` (an `IDProperty`), so `bpy.props` work. The precedents are `View3DShading.prop` and `uiList.properties`.
- **Redraw at 60 Hz:** `WM_event_timer_add_notifier` posts a notifier aimed at this one space. No window-manager patch is needed [V].
- **Opening fork files in stock Blender:** these areas become 3D Viewports with no crash. `research/10` §3 predicted Info editors from code reading; the fixture test showed the area is read as empty instead.
- **One hazard** (`research/10` §3.3): never register `WorkSpaceTool`s for `SPACE_EXTENSION`. A contract test plus a `save_pre` guard enforces this.

**Providers planned:**

| Provider | Content | Built with |
|---|---|---|
| `gamestudio.game_view` | The running game, docked | C++ draw (GameViewHost) |
| `gamestudio.inspector` | Entity + component editor (today's Game panel, promoted) | Python panels |
| `gamestudio.assistant` | AI chat: transcript, proposals, diff preview | Python panels + `textbox` + `blf` draw handler |
| `gamestudio.console` | Runtime logs and errors, clickable `file:line` | Python panels |
| `gamestudio.project` | Project files: scenes, scripts, prefabs | Python panels; aligns later with upstream's new `space_project` (Blender `main`, June 2026) |

---

## 4. The docked Game View

**The constraint** (`research/09`, `research/10` §5.5): Blender draws every menu, popup and tooltip into the same GPU surface as the editors. A native child window or webview over an area would cover them on every OS, and Wayland can't embed another process's surface at all. Godot needs four platform-specific implementations and still disables embedding in the single-window mode that Blender always uses.

**The design:** the game renders offscreen in its own process. Its frames reach a Game View region as a texture.

```
 Runtime host (Electron, MIT)                         GameStudio Editor (GPL)
 ┌──────────────────────────────────┐   surface      ┌───────────────────────────────────────────┐
 │ offscreen BrowserWindow (WebGL2/ │   channel      │ Game View (SPACE_EXTENSION provider)       │
 │ WebGPU) → paint(sharedTexture)   │ ─────────────► │  GameViewHost ─ FrameImporter               │
 │ ring of 3 shared BGRA8 textures  │ {slot,frameId} │   v1:  CpuBridgeD3D11 → GPU_texture_update  │
 │ input layer ◄─ input.* events ───┼◄────────────── │   v1.5: VkD3D11Import / GlNvDxInterop       │
 │ SDL3 gamepads                    │ cursor.grab    │  draws texture in WINDOW region             │
 └──────────────────────────────────┘                └───────────────────────────────────────────┘
```

- **Producer.** Electron 44 offscreen mode with `useSharedTexture` was **measured on this machine at 240 fps at 1080p** using 4–6% of a core [V]. The CPU-bitmap mode tops out at 105 fps (`research/09` §4B).
- **v1 consumer: the CPU bridge.**
  1. A helper thread in the editor opens the D3D11 handle and copies the frame to a staging texture.
  2. The main thread runs `GPU_texture_update_sub(UBYTE)` with a `bgra` swizzle.

  It calls only Blender's public GPU API, so it **works on OpenGL (5.2) and Vulkan (5.3+) with no backend code**. Estimated cost: 2–6 ms per 1080p frame, with 1–2 frames of extra latency, to be measured in spike 1.
- **v1.5 consumers: zero-copy.** Vulkan first, through `VK_KHR_external_memory_win32` with a D3D11 texture handle and a keyed mutex or shared fence, since Vulkan is the default from 5.3. OpenGL follows through `WGL_NV_DX_interop2`, reusing the `GHOST_ContextD3D` plumbing that is already there for OpenXR [V]. This adds seam S12. **The importer falls back to the CPU bridge automatically** when import fails.
- **macOS and Linux (v2):** IOSurface→`MTLTexture`, for which Blender already has a wrapper, and DMA-BUF.
- **Input.**
  - Click the Game View to capture input, as in Unreal. The release chord is Shift+F1.
  - Losing window focus releases capture.
  - Input arrives as engine-level `input.*` events, not Chromium events.
  - Pointer lock becomes `cursor.grab`, which the editor handles with `WM_cursor_grab_enable` and relative deltas, because pointer lock doesn't work in offscreen mode [V].
  - Gamepads are read by the runtime host through SDL3, gated on capture.
- **Detach:** the same host in windowed mode, which is today's behaviour. Godot treats floating as its default, so this is a first-class mode, not a fallback.
- **Process lifetime:** a Windows Job Object (kill-on-close), so an editor crash takes the game with it. A game crash only shows "Game stopped (crashed) - Restart" in the view.
- **Protocol:** `docs/fork/SURFACE.md` (MIT, engine-agnostic) covers `surface.configure`, `surface.frame`, `surface.release`, `input.*`, `focus`, `cursor.*` and `detach`. A future native runtime (Bevy or Godot) would simply be another producer.

---

## 5. Repository and tooling

```
gamestudio/                            one repository; licence per directory
  editor/                              GPL-3.0-or-later: the Blender-based IDE
    upstream.lock                      tag + commit + lib commit
    patches/series, S01-*.patch …      the seams (§2)
    native/                            gs_native (L2): blender/ (adapter), core/, gameview/
    shell/scripts/startup/bl_app_templates_system/GameStudio/   the IDE (L3) app template
    shell/extensions/system/gamestudio/  the system extension: components, export, live link,
                                         assistant, projects, providers, gamestudio.host
    shell/tools/build_template.py      generates the template's startup.blend / userpref.blend
    branding/                          icons, .rc, Info.plist, .desktop, WiX, splash
    tests/contract/ tests/ui/ tests/fixtures/
    tools/gsfork/                      the fork CLI (Python, stdlib only)
  runtime/                             MIT: engine, devserver, host (Electron), protocol docs
  examples/  docs/  research/
```

**`gsfork` CLI.** It is the one tool for every fork operation, so the upgrade procedure is the same commands every time:

| Command | Does |
|---|---|
| `gsfork sync` | Updates the local upstream mirror (`F:\Development-games\blender-upstream`) and libs to `upstream.lock` |
| `gsfork apply` | Creates a clean worktree at the locked tag, runs `git am -3` on the series, overlays `branding/`, and links `native/` and `shell/` |
| `gsfork export` | Turns commits on the worktree branch back into `patches/` (`git format-patch`), so developers edit with git, not by hand |
| `gsfork build [--dev]` | Runs `make … ninja sccache 2022` with `-DGS_NATIVE_DIR=…`, then `ninja install` |
| `gsfork run` | Starts the built editor. During development the shell runs from source through `BLENDER_SYSTEM_SCRIPTS`, so Python edits need no rebuild |
| `gsfork test` | Runs the contract, shell, UI-smoke and e2e tests against the fork build, and the shell tests against stock Blender |
| `gsfork canary <ref>` | Applies the series to upstream `<ref>` (`main`, `blender-v5.3-release`) and reports which patches and builds fail |
| `gsfork upgrade <tag>` | The guided LTS upgrade (§7) |
| `gsfork package` | Builds the ZIP and, with `--installer`, a per-user Inno Setup installer that bundles Node.js and the runtime; writes the GPL source archives. `--verify` installs, tests and uninstalls it (`docs/fork/RELEASING.md`) |

---

## 6. Feature map: where each IDE feature lives

| Feature | Layer | Stock-Blender fallback |
|---|---|---|
| Project launcher (choose or create a game) | L3 (splash override or launcher dialog [V-GUI]) | Same |
| Game workspaces (Level / Model / Materials / Animate / Procedural / Debug) | L3 template `startup.blend` + layout specs [V] | Same workspaces with stock editors |
| Top bar with Play / Pause / Step / Stop (F5, F6, F7, Shift+F5), the project menu, and game state in the status bar | L3 header override + add-on keymap [V-GUI] | Same |
| Theme, keymap, layout locks | L3 template `userpref.blend` [V] | Same |
| Hide irrelevant editors (VSE, Compositor, Movie Clip, …) | S11 + L3 policy | Shortcuts removed, menus filtered (partial) |
| Branding: name, title, icons, About, separate config | S8, S9, `branding/` | n/a (stock is Blender) |
| Inspector, Assistant, Console, Project editors | S1–S7 + L2 + L3 providers | 3D View sidebar tabs (today's UI) |
| Docked Game View | L2 + runtime host | Detached window (today) |
| Component system, export, live link, Play | L3 `gamestudio` extension (today's code) | Same |
| AI assistant and MCP | L3 + L4 (today's code) | Same |
| Code editing | **External** (VS Code / Cursor / custom command): opens the project at file:line; clickable runtime and build errors in the Console; New Component / New System templates | Same |
| Shipping games | L4: Vite web build and the Electron desktop build | Same |

### 6.7 Why code editing stays external

Unity and Unreal both delegate code to external IDEs. Godot's built-in editor is a common complaint (`research/03`). Blender's Text Editor is not a code editor. TypeScript already has excellent tooling in VS Code and Cursor, and the AI assistant already reads and proposes code. We integrate rather than rebuild:
- open the file at a given line;
- surface `tsc` diagnostics in the Console;
- later, add a VS Code extension that talks to the hub.

---

## 7. The upgrade procedure (new Blender LTS)

Rehearsed continuously by the canary, so it is never a surprise:

1. **Bump the lock:** `gsfork upgrade v5.5.0` writes a candidate `upstream.lock` and syncs the mirror and libs.
2. **Patches:** `git am -3` the series.
   - Seams sit at list tails, so expect 0–3 small conflicts. Fix them on the worktree, then run `gsfork export`.
   - `git rerere` replays known resolutions.
3. **Build:** compile errors appear only in `editor/native/blender_adapter/`, by construction. Fix them there.
4. **Contract tests** (`blender/tests/contract/`):
   - RNA API dump diff (`tests/python/rna_info_dump.py`) intersected with the identifiers the shell uses;
   - every Blender behaviour we depend on: restricted registration, UI override semantics, template loading, depsgraph handler fields, the glTF exporter's TRS conventions, PropertyGroup override flags, `SpaceExtension` read and write, stock-Blender fallback for fork files;
   - the existing 117 add-on tests and the 21-check e2e run.
5. **Data compatibility:** open golden `.blend` files from the previous release, re-export, and compare manifest hashes.
6. **UI smoke:** launch, draw each provider, Play/Stop, and screenshot diffs.
7. **Release** `gs-v<x>`: signed packages and the GPL source archive.

**Budget:**
- **LTS→LTS:** 1–2 weeks.
- **Patch releases of the same LTS** (5.2.2→5.2.3): about half a day (bump the tag and lib pin, rebuild, run the tests).
- **The 6.0 API break (2028, proposed):** about twice the LTS budget.

---

## 8. Data and compatibility guarantees

- **No edits to existing DNA structs.** The only new DNA is `SpaceExtension`, in an out-of-tree header.
- **Space ID 64** is chosen deliberately, away from upstream's next IDs (`research/10` §4.3).
- **`BLENDER_VERSION` stays upstream's.** `GAMESTUDIO_VERSION` is separate, so extension compatibility keyed on Blender versions still works.
- **Game data** lives in `bpy.props` PropertyGroups and `gs_id` custom properties, which stock Blender preserves.
- **Fork-saved files open in stock Blender.** Extension areas become 3D Viewports and nothing crashes. This is an automated test against a file saved by the fork (`blender/tests/fixtures/fork_extension_editor.blend`).
- **Separate config directories** (S8): the fork never touches `%APPDATA%\Blender Foundation\Blender\5.2` [V].
- **Pinned tools:** the exported scene format and the protocols are versioned (`SPEC.md`, `SURFACE.md`), and the runtime host and engine are pinned per project, so an editor update never silently changes a shipped game.

---

## 9. Licensing and naming

Sources: `research/08` §3, which quotes blender.org's trademark policy, Blender's license FAQ and the FSF GPL FAQ (updated 2026-01-13).

| Topic | Rule for GameStudio |
|---|---|
| **Name** | The product is **"GameStudio"**, described as *"based on Blender's open source code"*. Blender's policy allows that wording without permission. Not allowed: "Blender" in the product or company name (no "Blender GameStudio" or "GameStudio for Blender"), a `blender-*` domain, the Blender logo as our icon or splash mark, slogans, or anything implying endorsement. Keep Blender's credits and copyright notices, and add ours. |
| **Editor licence** | The fork, `gs_native`, the shell and everything bundled that imports `bpy` are **GPL-3.0-or-later**. Every binary release ships its **Corresponding Source**, *including the build scripts* (GPLv3 §1): the upstream tag, `patches/`, `native/`, `shell/`, `branding/` and `tools/gsfork`. `gsfork package` writes this archive. |
| **Runtime licence** | The runtime, dev server, runtime host and protocols are **MIT**. Shipping them in the same installer is a lawful **aggregate** (GPLv3 §5). |
| **Keeping the runtime outside the GPL** | The FSF test looks at both the *mechanism* and the *semantics* of communication. "Using shared memory to communicate with complex data structures is pretty much equivalent to dynamic linking." So, by design:<br>• the runtime never links Blender code and never reads Blender's structures;<br>• it exchanges only documented formats: glTF, the JSON manifest, NDJSON deltas, and **flat image frames with a documented header** (`SURFACE.md`);<br>• the editor is complete without the runtime installed, so it is not "specifically designed to require" it.<br>The risk is low for sockets and files, and low to medium for shared-texture image frames (`research/08` §3.3). Have counsel review before a commercial release. |
| **Internal use** | The GPL only applies on distribution. Private builds for yourself carry no obligations. |

### Why this structure and not a "real" fork

`research/08` measured how far each living fork has drifted from upstream:

| Fork | Upstream C/C++ files changed | Python UI | Sync method | State |
|---|---|---|---|---|
| Bforartists (UI fork) | 464 files, +21k/−5k lines | 110 files, +31k/−8k (edits `bl_ui` in place) | Weekly merges of Blender `main` | Alive; ships Blender alpha code in its "stable" releases |
| UPBGE | 238 files (26 DNA headers) | — | 123 merges of `main` since April, one developer | Alive; bus factor 1 |
| Goo Engine | 159 files (16 DNA headers), plus a vendored legacy EEVEE | — | Rebase per release | **Five releases behind.** Blocked for about a year by the EEVEE rewrite |
| Fluid Designer, Mechanical Blender | — | — | Froze | Dead |

The forks that stay current over years all use a **pinned upstream plus a small, named patch queue**, with new code in their own files:

| Fork | Patch queue |
|---|---|
| VSCodium | 70 patches |
| LibreWolf | 65 patches |
| Electron | 142 patches, each with a written reason |
| Brave | Patches kept to one-line hooks into `chromium_src` overrides |
| Floorp | Hot-reloaded TypeScript features over a thin patch set |

That is this architecture.

**Patch header format** (from Electron and Brave practice, `research/08` §4). It also lets an AI assistant *re-implement* a patch's intent when the surrounding code has moved, rather than guess at conflict markers:

```
Why:         <one sentence>
Hook-for:    <gs_native or shell function this seam calls>
Upstream:    <issue/PR link> | not upstreamable: <reason>
Test:        <contract test that proves it works>
Remove-when: <condition, e.g. "upstream custom-editor API lands">
```

AI-proposed patch refreshes during upgrades are applied **only when the contract tests pass**, the same human-in-the-loop rule the assistant follows everywhere else.

---

## 10. Phased plan

Every phase ends in something usable, and the stock-Blender path keeps working throughout.

| Phase | Scope | Exit test | Needs |
|---|---|---|---|
| **0. Toolchain and a stock build** (≈1 week) | VS 2022 C++ workload, Ninja, sccache; `gsfork sync/apply/build` with **zero** patches; the contract suite runs on the result | `gsfork build` produces a working Blender 5.2.2 from our pipeline. Incremental rebuild under 2 min | **You:** install the VS 2022 "Desktop development with C++" workload (needs admin) |
| **1. Distribution shell** (≈2 weeks) | Restructure the repo (`editor/`), the app template (workspaces, top bar with Play, launcher, theme), `gamestudio.host`, extensions as system extensions; seams S8, S9, S10, S11; branding overlay | `GameStudio.exe` opens the launcher, then a project, then the Level workspace. Stock Blender with `--app-template` gives the same shell minus branding | Phase 0 |
| **2. Extension Editor framework** (≈2–3 weeks) | Seams S1–S7, `editor_ext`, `_gamestudio`, providers: Inspector, Assistant, Console | Providers survive save and load. A fork file opens in stock Blender (automated). No workspace tools in the extension space | Phase 1 |
| **3. Docked Game View** (≈3–4 weeks) | `@gamestudio/host` (Electron, docked and detached), `SURFACE.md`, GameViewHost + CPU bridge, input capture, Job Object | 1080p at display refresh, < 3 ms main-thread cost per frame, on both the OpenGL and Vulkan backends. Menus draw over the view | Phase 2 |
| **4. Release engineering** (≈2 weeks) | Self-hosted CI, nightly builds, canary vs 5.3 and `main`, MSI and ZIP, signing, GPL source archive | An upgrade rehearsal onto `blender-v5.3-release` measures the real cost | Phases 0–3 |
| **5. Later** | Zero-copy importers (S12), macOS and Linux, OSR web panels, Hub launcher with per-project editor versions, upstreaming seams | — | — |

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Internal C++ API churn breaks L2 | All internal use goes through `blender_adapter/` (lint-enforced). LTS-only releases. The weekly canary catches breakage months ahead. |
| Seam conflicts grow over time | Budget of 15 patches and about 250 lines, additive list-tail hunks, and an upstreaming effort for the generic seams |
| Electron offscreen-texture regressions | Pin the Electron version. The CPU-bitmap path is a fallback. Detached mode always works. |
| Vulkan default in 5.3 | The v1 CPU bridge uses only the backend-neutral GPU API |
| Build infrastructure cost (14 GB libs, ~25 GB per build) | Self-hosted runner on this PC, persistent sccache. 287 GB is free on F: |
| The fork lags behind upstream | Graceful degradation: the shell runs on stock Blender (G3) |
| GPL boundary for the runtime | Separate processes and documented, engine-agnostic protocols. Counsel review before a commercial release. |
| A one-developer project (Blenvy and UPBGE both have a bus factor of 1) | Keep the delta small, the tooling scripted and the docs current, and make the canary report the cost of every upstream change |
