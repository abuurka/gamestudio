# GameStudio for coding agents (MCP)

`@gamestudio/mcp` is an [MCP](https://modelcontextprotocol.io) server that lets a coding agent such as Claude Code or Codex work on a GameStudio game in a closed loop: **build** assets and levels in Blender, **play** the game, **look** at it, **measure** it, and **fix** it. The agent writes the game's TypeScript itself; the server gives it Blender and the running game.

Agents **act directly**. Nothing waits for a click. What protects your work:

- **Undo.** Every scene edit an agent makes is one Blender undo step, named `Agent: <tool>`. Ctrl+Z (or the `undo` tool) reverts it.
- **Checkpoints.** `checkpoint` saves a copy of the scene, unsaved changes included, under `.gamestudio/checkpoints/`; `restore_checkpoint` brings it back.
- **Locks.** Objects you protect (**Protect** in Blender's Agent panel or the object's context menu, or **Lock Selected** in the Assistant panel) are refused by every tool, `run_python` included.
- **Pause.** **Pause** in the Agent panel stops the agent's changes: while you are paused, calls that change the scene or the open file fail with `code: "paused_by_user"`. Reads, including `run_python` with `read_only`, still work.
- **Library backups.** Asset library files (`assets/*.blend`) are written on disk by a background Blender (`asset_add`, and the component tools with `asset`), which undo does not cover. So every such write is backed up first to `.gamestudio/library-backups/`, and `asset_library_restore` puts the previous file back in one call.
- **Localhost and a token.** Blender listens on 127.0.0.1 only and requires a random per-session token.

The in-Blender assistant ([AI-ASSISTANT.md](AI-ASSISTANT.md)) is separate and keeps its propose-and-apply flow.

---

## How it fits together

```
Claude Code ──stdio (MCP)──> gamestudio-mcp (Node, runs in the project folder)
                               │
                               ├─ TCP 127.0.0.1, NDJSON JSON-RPC + token ──> Blender: GameStudio extension, agent bridge
                               │                                             (scene, assets, components, export)
                               ├─ TCP, live-link hub as a "tool" peer ─────> dev server (Vite) ──WebSocket──> games
                               │                                             (play/stop/state/logs, docs/SPEC.md §5)
                               └─ headless Edge/Chrome (puppeteer-core) ───> the game page (playtest, screenshots)
```

- **Agent bridge** (`blender/gamestudio/bridge/`). A socket server inside Blender. Requests run on Blender's main thread, one at a time. With a UI, a timer pumps them every 10 ms; in `--background` Blender, which has no timers, the server's serve loop does.
- **Discovery.** While a scene of the project is open, the bridge writes `<project>/.gamestudio/blender.json` (`pid`, `port`, `token`, `blend`, `project`, `blenderVersion`, `binary`, `background`). It also writes `bridge.json` (the latest session, project or not) and `last-blender.json` (the executable, kept after exit) in the extension's user folder (`<Blender config>/extensions/.user/<repo>/gamestudio/`). Session files are removed when Blender exits.
- **Games.** The server joins the dev server's hub as a `tool` peer. It sends `play.*`, `scene.load` and `state.request` to each game (with `to`), and keeps the games' status and the last 2,000 log lines.

---

## Setup

1. **Install GameStudio and enable the extension in Blender (5.2+).** The **Agent bridge (MCP)** preference (Edit > Preferences > Add-ons > GameStudio > Agent bridge) is **on by default**. The same box shows the port and the number of connected clients.
2. **Register the server with Claude Code.** New projects ship a `.mcp.json`; Claude Code starts the server with the project as its working directory:

   ```json
   {
     "mcpServers": {
       "gamestudio": {
         "command": "node",
         "args": ["node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js"]
       }
     }
   }
   ```

   Inside this repository, packages are hoisted to the root `node_modules`, so `examples/demo-game/.mcp.json` uses `"../../node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js"`. Run `npm install` at the repository root once so the workspace link exists. Projects also ship `.claude/settings.json` with `"enabledMcpjsonServers": ["gamestudio"]` and pre-approved tools.
3. **Optional environment** (set it in `.mcp.json` under `"env"`):

   | Variable | Use |
   |---|---|
   | `GAMESTUDIO_BLENDER` | The Blender executable `launch_blender` starts. Without it, the server uses the path the extension recorded (`bridge.json` / `last-blender.json`), then `$BLENDER`, then the standard install folders. |
   | `GAMESTUDIO_PROJECT` | The project folder, if it is not the working directory. `--project <dir>` does the same. |
   | `GAMESTUDIO_BROWSER` | A Chromium-based browser for headless play (`GS_BROWSER` works too). Default: the system's Edge, Chrome, Chromium, Brave or Vivaldi from the usual install folders (per-user installs and Beta/Dev/Canary builds included), then the Chrome Headless Shell GameStudio's setup downloads when the machine has none (`GAMESTUDIO_BROWSER_DIR`, else the extension's user folder in each Blender profile). `GAMESTUDIO_BROWSER_SYSTEM=0` skips the system browsers. |
   | `GAMESTUDIO_BROWSER_GPU` | `auto` (the default): headless play on the GPU (ANGLE on Direct3D 11 on Windows), checked once per browser; when WebGL2 is missing or software, the browser restarts with SwiftShader and the log says why. `1` forces the GPU, `0` forces SwiftShader. `status` (`headless_game`) and `playtest` results report the `renderer` and `gpu`. |
   | `GAMESTUDIO_MCP_LOG` | Also append the server's diagnostics to this file. `--log <file>` does the same. |
   | `GAMESTUDIO_BRIDGE_DIR` | An extra folder to look for `bridge.json` in. |
   | `BLENDER_USER_RESOURCES` | Honoured like Blender does: discovery looks in that profile too, and a launched Blender uses it. |

Blender does not have to be running. `status` says what is missing, and `launch_blender` starts it. Its default scene is `scenes/<startScene>.blend`; pass `background: true` for a session with no window, which closes when the MCP server exits. `play` starts the dev server through Blender when it is not running.

---

## Codex

[Codex](https://developers.openai.com/codex), OpenAI's coding agent, uses the same server and the same tools. **GameStudio › Open in Codex** installs it when it is missing, with OpenAI's standalone installer (`irm https://chatgpt.com/codex/install.ps1 | iex` in PowerShell on Windows, `curl -fsSL https://chatgpt.com/codex/install.sh | sh` on macOS and Linux: into your user folder, no administrator rights, about 160 MB). It then connects the project and opens a terminal running `codex` in it (`.gamestudio/codex.cmd`, `.command` or `.sh`, with GameStudio's Node.js first on `PATH`). The first time, Codex asks you to sign in, with your ChatGPT account or an OpenAI API key, and whether to trust the folder. The status bar says *Codex connected* once it reaches Blender (Codex introduces itself as `codex-mcp-client`).

What GameStudio writes in the project:

- **`.codex/config.toml`**, Codex's project settings, with the same command and args as `.mcp.json`:

  ```toml
  [mcp_servers.gamestudio]
  command = "node"
  args = ["node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js"]
  startup_timeout_sec = 30
  ```

  On a computer without a Node.js 22.12+ on its `PATH`, `command` is the full path of the Node.js GameStudio set up, as in `.mcp.json`. GameStudio checks it when the project opens, and Save as Template writes `node` again. In a file you already have, it adds this table at the end and later changes only its values: your other settings, comments and layout stay as they are. A `gamestudio` server you start another way (not `node … gamestudio-mcp.js`) is left alone. Codex reads a project's `.codex/` only in a folder you trust, so choose **Trust and continue** when it asks; otherwise the `gamestudio` tools are missing (`/mcp` in Codex lists its servers). Codex starts the server in the folder it runs in: start `codex` in the project folder.
- **`AGENTS.md`**, the instructions, shared with Claude Code (`CLAUDE.md` imports it). When a project's instructions are only in `CLAUDE.md`, the Open in Codex dialog offers to move them to `AGENTS.md`. Unticked, Codex gets a marked copy, which GameStudio refreshes while you leave it unedited.
- **`.agents/skills/`**, a copy of `.claude/skills/`: Codex reads a repository's skills from `.agents/skills/`, Claude Code from `.claude/skills/`. Edit skills in `.claude/skills/`; GameStudio copies them again when the project opens. Each copy has a `.gamestudio-copy.json`. A copy you edit is left as it is, and a folder without that file is yours and never touched.

Codex gives an MCP server only a few environment variables (`PATH`, the home and app-data folders, the temp folder and a few more). Put any variable from the table above under `[mcp_servers.gamestudio.env]`.

### Other agents

Any MCP client can use the server. Start `node node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js` over stdio, with Node.js 22.12+ and the project folder as the working directory (or pass `--project <dir>`). Give the agent `AGENTS.md` as its instructions and `.claude/skills/` as its workflows. To connect Codex by hand, put the table above in the project's `.codex/config.toml`, or in your own `~/.codex/config.toml` with the script's full path and `--project <dir>` in `args`.

---

## Conventions

- **Spaces.** Blender tools use Blender space: Z-up, metres, rotations as XYZ Euler in degrees. Game tools (`game_state`, `playtest`) report runtime space: Y-up, metres, quaternions as `[x, y, z, w]`. Blender `(x, y, z)` is runtime `(x, z, -y)`. Component `vec3` values are always runtime space.
- **Names.** Objects are named by their Blender name or their `gs_id`. Arguments called `objects` or `names` take one name or a list.
- **Paths.** Paths are relative to the project, or absolute.
- **Results.** A result is one JSON text block, plus image blocks for `render_views`, `compare_views`, `playtest` and `game_screenshot`.
- **Errors.** A failed call is a tool result with `isError: true` and `{"error": "<what failed>", "code": "<kind>", "hint": "<how to fix it>"}`, for example:

  ```json
  {"error": "Blender is not running for this project (F:/games/my-game): call launch_blender to open scenes/main.blend (add background: true for a headless session), or open the project's scene in Blender with the GameStudio extension enabled (Agent bridge preference on).", "code": "blender_not_running"}
  ```

  Codes include `blender_not_running`, `blender_error` (Blender refused: an unknown object, an invalid value, a locked object), `blender_timeout`, `invalid_params`, `invalid_arguments`, `devserver_not_running`, `no_game`, `game_not_ready`, `browser_not_found`, `no_project` and `ui_file_not_open`. Invalid arguments are caught before Blender is called, with every problem listed (`"problems": ["arguments.location: needs at least 3 item(s)"]`).

---

## Tools

There are 64 tools. R = read-only, W = changes the scene or game, D = destructive. Every W/D tool that changes the scene is one undo step.

Results are compact by default: the essentials, with a `hint` saying how to get more. `verbose: true` gives everything on `scene_summary`, `list_objects`, `get_object`, `list_kits`, `list_component_types`, `validate_assets`, `export_scene` and `game_state`. Big scenes are summarised rather than truncated (see `scene_summary`).

### Session

| Tool | | Arguments | Notes |
|---|---|---|---|
| `status` | R | none | Project, Blender (file, unsaved changes, scene, object count, mode, component and shader types, kits), dev server, games and their state, headless game, log counts, and `next` suggestions. Never launches anything. |
| `launch_blender` | W | `background?`, `scene?`, `checkpoint?`, `kill_unresponsive?` | Starts Blender for the project and waits for its bridge (~2 s background, ~5 s GUI). It opens `scene`; otherwise the scene that was open when Blender last crashed (as last saved); otherwise `scenes/<startScene>.blend`. `checkpoint` opens the scene and then restores that checkpoint. `kill_unresponsive` stops a Blender of this project that has not answered a 5 s ping, then starts a new one. Refuses when a Blender with this project is already running and answering. |
| `open_scene` | W | `path`, `create?`, `save_changes?`, `discard_changes?`, `background?` | Opens a `.blend`, or makes a new empty one with `create`. Refuses unsaved changes unless told what to do with them. Launches Blender if none is running. |
| `save` | W | `path?` | Ctrl+S, or Save As with `path`. Returns `twin`, the path of the scene twin written next to the `.blend` (see `scene_twin`). Saving the UI file (`ui/ui.blend`) exports its screens, and the result has `ui_export`: `{canvases: [{canvas, file, rev, written, elements}], locales, warnings}` (`written: false` for a screen that did not change; `removed` names screens dropped from the export), or `{error}` when the export failed (the file is saved either way). |
| `checkpoint` | W | `label` | Copies the open scene to `.gamestudio/checkpoints/<id>/` with `checkpoint.json`: `{id, label, created, source, scene, objects, bytes, ...}`. Does not save or change the open file. |
| `list_checkpoints` | R | `limit?` | Newest first. |
| `restore_checkpoint` | D | `id`, `backup?` (default true) | Opens the checkpoint and saves it over the scene it came from. The current state is checkpointed first (`before restore <id>`). |
| `undo` / `redo` | W | `steps?` | Blender's undo stack. The history starts when a file is opened (`launch_blender`, `open_scene`, `restore_checkpoint`) and keeps the last 32 steps (Preferences > System > Undo Steps); `save` keeps it. The first change after opening a file can be undone, in a background session too. Past the start, `undo` says so and points at `restore_checkpoint`. Undo changes Blender, not the last export: the game, and `game_screenshot` in mode `game`, keep what was exported until the next `play`, `playtest` (unless `export: false`) or `export_scene`, which exports the undone state as a full export (the export journal treats undo and redo as a change to anything). With the live link on, a running game also gets the undone transforms and component values right away. |
| `save_template` | W | `id`, `label?`, `description?`, `overwrite?` | Saves the project as a template that New Project lists under "Your templates" ([TEMPLATES.md](TEMPLATES.md)): `src/`, `scenes/`, `assets/`, `.claude/` (custom skills included) and the project files, copied to `Documents/GameStudio/Templates/<id>/` (or `GAMESTUDIO_TEMPLATES`) with the game's name and package paths turned back into placeholders. Writes nothing in the project and is no undo step. Refuses unsaved changes, a built-in id, and an existing template unless `overwrite`; the template it overwrites moves to `.backups/<id>@<time>` (five kept). Returns `path`, `placeholders`, `left_out`, `warnings` and `replaced` (the backup, or null). |

```json
{"name": "checkpoint", "arguments": {"label": "before terrain rework"}}
→ {"id": "20261010-142501-before-terrain-rework", "label": "before terrain rework", "source": "scenes/main.blend", "objects": 25, "bytes": 113726, "path": "…/.gamestudio/checkpoints/20261010-142501-before-terrain-rework", ...}
```

#### Crashes and hangs

The server notices when Blender stops working and says how to get back.

**A crash.** The bridge connection closes and Blender's process is gone. This can happen mid-call, or be found later from the discovery file it left behind. The call fails with `code: "blender_crashed"`, and the `hint` lists the recovery choices that exist:

```json
{"error": "Blender (pid 24456) exited unexpectedly while running run_python: it crashed or was killed.", "code": "blender_crashed",
 "hint": "Recover: launch_blender reopens scenes/main.blend as last saved (04:57); or launch_blender {\"scene\": \"C:/Users/me/AppData/Local/Temp/main_24456_autosave.blend\"} opens Blender's autosave from 05:01, which has unsaved work; then save {\"path\": \"scenes/main.blend\"}; or launch_blender {\"checkpoint\": \"20261010-045720-before-tower\"} starts from the newest checkpoint ('before tower', 04:57).",
 "crash_log": {"path": "…/main.crash.txt", "excerpt": "…"}}
```

- The autosave is offered only when Blender wrote one newer than the saved file. Interactive sessions autosave every few minutes; `--background` sessions never do.
- `crash_log` is included when Blender wrote one.

**A hang.** Blender runs requests one at a time on its main thread, so a long or stuck request delays everything after it.

- A call that gets no answer fails with `code: "blender_timeout"`. Its hint suggests waiting, and offers `launch_blender {"kill_unresponsive": true}`, which loses unsaved changes (autosaves and checkpoints remain).
- `status` pings Blender first. If there is no answer within 5 s, it reports `blender: {"connected": true, "responding": false, "last_unanswered": "<tool>"}` and does not wait on the hung request.
- A call cut short by `kill_unresponsive` fails with `code: "blender_stopped"`.

**Long scripts.** `run_python` interrupts Python code at its `timeout`; the result includes a `split_hint` on how to divide the work. The timeout cannot stop a single long C call. If such a call hangs, use the hang procedure above.

### Scene

| Tool | | Arguments | Notes |
|---|---|---|---|
| `scene_summary` | R | `max_objects?` (100), `verbose?` | Collections (prefabs flagged), every object as a compact row (`name, type, parent?, location, rotation_deg?, scale?, dimensions, components?`; an armature root, an empty or an asset instance gets the size of what it carries, with `dimensions_from: "children"` or `"asset"`; a geometry-nodes object, such as a procedural scatter or road, gets the size of what the nodes make, with `dimensions_from: "instances"` or `"evaluated"` and its `instances` count), totals (evaluated: modifiers and geometry nodes applied; `instances` and `instanced_triangles` add what instances draw) (vertices, triangles, materials), camera, selection, `prefabs`, `animated` objects and component types. Defaults are left out. Above `max_objects` objects the rows are replaced by `by_collection` (counts, types, examples) and the 20 `largest` objects, with a `hint` to drill down with `list_objects`. `verbose` adds faces, materials and collections to each row. |
| `list_objects` | R | `filter?: {name, type, component, collection, parent, selected}`, `limit?` (100), `offset?`, `verbose?` | `name` is a substring or a glob (`Coin*`). `parent: null` returns top-level objects. When there are more, the result has `next_offset` and a `hint`. |
| `get_object` | R | `name`, `verbose?` | Transform (local and world), dimensions and world bounding box (for an object without geometry of its own, such as a rigged character's armature root, a group empty or an asset instance: of what it carries; for a geometry-nodes object: of what the nodes make, instances included, with `instances` and `instanced_triangles`; `dimensions_from` says which), components with every value, mesh stats, materials, modifiers, `kit: {kind, params}` for kit-made objects, `animation` (action and clip names), and `prefab_instance_of` / `in_prefab`. `verbose` adds custom properties, collections, UV maps, material slots, the rotation mode and the full animation (as in `list_clips`). |
| `ground_height` | R | `points: [[x, y], ...]`, `ignore?` | The highest surface under each point (rays cast down from above the scene): `z` and the `object` hit, or `z: null`. Read-only, so it records no undo step. |
| `raycast` | R | `origin`, `direction?` (down), `max_distance?`, `ignore?` | The first hit along a ray: `location`, `normal`, `distance`, `object`, or `hit: false`. |
| `list_clips` | R | `objects?` (default: every animated object) | Per object: the active action, the NLA tracks and strips, and the `clips` the export makes from them, each with `name`, `frames`, `seconds` and `from`. Tracks the exporter skips come back as `warnings`. `exported_clips` gives the clips in the last export. |
| `set_prefab` | W | `collection`, `enabled?` (true) | Marks or unmarks a collection as a prefab (§4.4 of SPEC.md). Running games reload. |
| `set_transform` | W | `name` + `location?`, `rotation_deg?`, `scale?`; or `items: [{name, ..., snap_to_ground?, align?, tilt?}]`; `relative?`, `snap_to_ground?`, `align?`, `tilt?` | `relative` adds to location and rotation and multiplies scale. `snap_to_ground` then moves each object onto the ground below it; give only x and y and let it find z. See "Snapping" below. A running game follows live. |
| `delete_objects` | D | `names`, `with_children?` | Children are kept, unparented in place, unless `with_children`. Component fields that pointed at a deleted object become empty; the result lists them as `cleared_references: [{object, component, field, was}]`. |
| `run_python` | D | `code`, `timeout?` (60 s), `rollback_on_error?` (true), `read_only?`, `label?` | The escape hatch; see below. |

```json
{"name": "set_transform", "arguments": {"items": [{"name": "Coin1", "location": [2, 0, 1]}, {"name": "Coin2", "location": [4, 0, 1]}]}}
{"name": "set_transform", "arguments": {"items": [{"name": "Rock1", "location": [3, 2, 0]}, {"name": "Tree1", "location": [-4, 6, 0]}], "snap_to_ground": true}}
{"name": "set_transform", "arguments": {"name": "Rock_3", "snap_to_ground": true, "tilt": true}}
→ {"updated": [{"name": "Rock_3", "snap": {"align": "lowest", "tilt_deg": 34.58, "ground": 5.2679, "on": "Hill", "moved_z": 0.9729, "contacts": 30, "float_m": 0, "sink_m": 0.123}, "location": [...], ...}]}
   (the same 3 m rock without tilt: align "highest" leaves it floating 1.71 m on the downhill side, "lowest" sinks the uphill side 1.71 m, each with a hint)
{"name": "ground_height", "arguments": {"points": [[0, 0], [4.5, -2]]}}
→ {"points": [{"x": 0, "y": 0, "z": 0.0, "object": "Ground"}, {"x": 4.5, "y": -2, "z": 1.25, "object": "Hill"}]}
{"name": "delete_objects", "arguments": {"names": ["Player"]}}
→ {"deleted": ["Player"], "cleared_references": [{"object": "Camera", "component": "FollowCamera", "field": "target", "was": "Player"}], "hint": "..."}
{"name": "list_objects", "arguments": {"filter": {"component": "Coin"}}}
{"name": "set_prefab", "arguments": {"collection": "Gem"}}
{"name": "list_clips", "arguments": {"objects": ["Hero"]}}
→ {"fps": 24, "objects": [{"object": "Hero", "action": "Idle", "nla_tracks": [{"name": "walk", "mute": false, "strips": [...]}],
   "clips": [{"name": "Idle", "frames": [1, 25], "seconds": 1.0, "from": "active action"}, {"name": "Walk", ..., "from": "NLA track 'walk'"}],
   "exported_clips": ["Idle", "Walk"]}], "actions": [...], "last_export": {"rev": 7, "clips": [...]}}
```

Snapping: `snap_to_ground` rests an object's **underside** on the ground, not its bounding box.

- The underside is the lowest 10 % of the object and its children (a crate's base, a tree's trunk, a character's feet), so a wide canopy over a ledge does not decide where a tree stands. It is sampled in a grid of up to 7 × 7 contacts, a little in from its outline.
- A ray finds the ground under each contact. The object is lifted out of the way while they are cast, so its own faces never block them.
- `align` picks which contact decides:
  - `"lowest"` (default): no contact floats. On a slope, the uphill side sinks into the ground.
  - `"highest"`: no contact sinks. The downhill side floats.
  - `"average"`: halfway between the two.
- `tilt: true` first turns the object to the plane through the ground contacts, keeping its heading, up to 45°. A prop then lies flush on a slope.
- Each snap reports `float_m` and `sink_m`, the gap that is left, and `contacts`. When the ground is uneven by more than 10 % of the object's height (5 cm at least), a `hint` says so. `no_ground_under` counts contacts with nothing below them.

Clips: the export turns two things into glTF clips named after their actions:

- the active action;
- the action of every NLA track with exactly one unmuted strip.

Put each clip on its own NLA track. Games play clips by name, e.g. `ctx.animation.play(entity, 'Walk')` (engine `API.md`).

Prefabs: a collection marked with `set_prefab` is exported as a template, whether or not it is in the scene. Games create copies with `ctx.spawn('Gem', {position: [x, y, z]})`, and collection instances of it in the scene are built from the template.

Live re-export: while a game runs, Blender re-exports and the game reloads when any of these happens:

- geometry or materials change;
- an action is keyed or its keys change;
- an object's active action or its NLA tracks and strips change;
- a prefab changes.

Live edits (moves, component values, deletions) name the level they belong to (`scene`, SPEC §5.4). A game showing another level ignores them, so editing `level2` never moves objects in a game that is on `main`, even when one level started as a copy of the other and they share ids.

Prefab collections that are not in the scene send Blender no change notifications, so they are compared once a second: the marker, members, transforms, data, vertex and face counts, components, materials and animation. Vertex positions are compared only on save. A vertex-only edit of an off-scene prefab therefore reaches the game on the next save or export.

#### `run_python`

Runs Python in Blender, on the main thread, for anything the other tools do not cover: bmesh modelling, modifiers, geometry nodes, `bpy.ops`, lights, the world, animation. The code sees `bpy`, `C` (context), `D` (data), `mathutils`, `Vector`, `Matrix`, `Euler`, `Quaternion`, `math`, and `gs`, the GameStudio add-on (`gs.core.components`, `gs.kits`, ...). The result is the value of a final expression, or of a variable named `result`. Blender objects come back as `{"type", "name"}`, vectors as lists. stdout and stderr are captured (20,000 characters each).

```json
{"name": "run_python", "arguments": {"code": "import bmesh\nme = D.meshes.new('Ramp')\n...\nobj = D.objects.new('Ramp', me)\nC.scene.collection.objects.link(obj)\nobj.name"}}
→ {"ok": true, "result": "Ramp", "stdout": "", "stderr": "", "seconds": 0.004, "undo": true}
```

The call is **one undo step**. If the code raises, or runs past `timeout`, the result has `ok: false`, `error` and `traceback` (the frames of your code), and the scene is **rolled back** to the previous undo step. That step is the state before the call, because every agent tool records one. A timeout adds a `split_hint`: process a few hundred items per call, and use `bpy.data` or bmesh instead of `bpy.ops` in loops.

`read_only: true` is for queries (sampling heights, measuring, counting):

- It records **no undo step**.
- The server compares a fingerprint of the data before and after: objects, transforms, meshes (vertex positions included), materials, components and scene settings.
- If the code changed anything anyway, the call fails with `ok: false`, the change is undone, and `error` names what changed.

**Risks.** This is arbitrary code in the user's Blender with the user's permissions. It can delete data, read, write or delete files anywhere, reach the network, or hang Blender. The timeout only interrupts Python, not a long C call such as a huge boolean or subdivision. If the code changes a protected object, the whole call is rolled back and the result names the object (`locked_changed`). Use it as an escape hatch: prefer the structured tools, keep code short and scene-scoped, never touch files outside the project, and never work around a lock.

### Assets

| Tool | | Arguments | Notes |
|---|---|---|---|
| `list_kits` | R | `kind?`, `verbose?` | The parametric generators (barrel, building, bush, character, collectible, crate, fence, platform, rock, stairs, terrain, tree, ...). By default each comes as `{kind, description, params: "name=default, ..."}`, plus `material_presets` once and the `procedural` builders: about 16k characters in all. Read it once per session, then use `kind` for one kit's schema. `kind` returns that kit's full JSON Schema; material parameters there point at `material_presets` instead of repeating the 27 names. `verbose` returns every schema. A `procedural` section lists the geometry-nodes builders `create_procedural` makes (`kind`, `label`, `about`, and the params each `needs`); `kind` with a builder's name returns its schema too (`fence` is both a kit and a builder: the builder comes as `procedural_builder`). |
| `create_from_kit` | W | `kind`, `params?`, `location?`, `name?`, `collection?`; or `items: [{kind, params?, location?, name?, collection?}]` | A game-ready mesh: metres, origin at the bottom centre, front facing −Y, UVs, materials. `name` names every object the kit makes: it goes to the kit as its `name` param, so `character_rigged` with `name: "Hero"` makes `Hero` (the armature, the root) and `Hero_Mesh`. `items` builds many in one call and one undo step. Top-level fields are defaults for every item. Every item (kind, params, location, name) is checked before anything is built, so a bad item builds nothing: `items[1]: Kit 'terrain' rejected the parameters: terrain.size must be between 2.0 and 200.0, got 240.0. Nothing was built.` The `dimensions` of a rigged character's armature root are the character's. |
| `regenerate_kit` | W | `object`, `params` | Rebuilds a kit object with changed params. It keeps its name, transform, parent and components, and its materials when the slot count is unchanged. |
| `create_procedural` | W | `kind`, `params?`, `name?`, `collection?` | Builds one procedural (geometry-nodes) part of a level, one undo step: `scatter` (forests, rocks, grass over a terrain), `array` (copies along a path), `fence`, `road`, `river`, `cliff`, `rock_wall`, `building`. See "Procedural builders" below. |
| `set_procedural` | W | `object`, `params` | Changes the inputs of a procedural object, one undo step. Values are validated, and nothing changes on an error. The nodes re-evaluate; nothing is rebuilt. |
| `import_asset` | W | `path`, `location?`, `scale?`, `name?`, `collection?` | `.glb`/`.gltf`, `.fbx`, `.obj`. Goes into a collection named after the file unless `collection` is given (`""` keeps the importer's). |
| `apply_material` | W | `objects`, `preset?`, `params?: {base_color, roughness, metallic, emission, emission_strength, alpha}`, `name?`, `slot?` (an index or a list, e.g. `[0, 2]`) | The kits' shared palette: `stone, stone_dark, wood, wood_dark, bark, leaves, pine, grass, dirt, sand, plaster, roof, metal, iron, gold, plastic, rubber, skin, gem, emissive, brick, concrete, copper, snow, ice, water, glass` (aliases `dark_wood, glow, steel, rock, foliage`). Colours are `"#RRGGBB"` or linear `[r, g, b]`. With params only, the base is `plastic`. |
| `validate_assets` | R | `names?` (default `"all"`; or `"selection"`, or names), `max_tris?` (20,000), `summary?`, `verbose?` | A game-readiness report with issues by `severity` (error/warning/info), `check` and `fix`. `"all"` means every mesh and every object with components. The result starts with its `scope`, e.g. `"all: 30 meshes and objects with components"`. Only objects with errors or warnings are listed. When more than 25 have problems, or with `summary: true`, you get `by_check` counts and the 10 `worst` objects instead. `verbose` lists every object with its stats and info notes. Objects with modifiers are checked as evaluated, after modifiers and geometry nodes, so a procedural road's UV map and materials count. An object made only of instances (a scatter) skips the UV and material checks; its sources are checked on their own. |
| `render_views` | R | `target?`, `views?`, `size?` (768), `shading?`, `focus?`, `person?`, `names?` (bool, `"auto"` or a list), `labels?` (bool or a list), `compare?`, `save?` | One labelled contact sheet; see below. |

```json
{"name": "create_from_kit", "arguments": {"kind": "crate", "params": {"width": 1.2}, "location": [3, -4, 0], "name": "SupplyCrate"}}
{"name": "create_from_kit", "arguments": {"items": [{"kind": "rock", "location": [2, 0, 0]}, {"kind": "tree", "location": [5, 3, 0], "name": "Oak"}], "collection": "Props"}}
{"name": "apply_material", "arguments": {"objects": "Lamp", "preset": "emissive", "params": {"base_color": "#66CCFF"}}}
{"name": "create_procedural", "arguments": {"kind": "scatter", "name": "Forest", "params": {"surface": "Hill", "sources": ["Tree_1", "Tree_2", "Rock_1"], "density": 0.03, "keep_out": ["MainRoad"]}}}
→ {"object": "Forest", "kind": "scatter", "collection": "Scatter", "params": {"surface": "Hill", "sources": "Forest Sources", "density": 0.03, ...},
   "stats": {"instances": 100, "by_source": {"ForestBush": 44, "ForestPine": 56}, "triangles": 20912, "own_triangles": 0, "draw_calls": 3}}
{"name": "create_procedural", "arguments": {"kind": "road", "name": "MainRoad", "params": {"curve": [[-30, -20], [0, 0], [30, 18]], "terrain": "Hill", "width": 5}}}
{"name": "set_procedural", "arguments": {"object": "Forest", "params": {"density": 0.05, "seed": 3}}}
```

#### Procedural builders

`create_procedural` and `set_procedural` drive the geometry-nodes toolkit (`blender/gamestudio/procedural/`, `gs.procedural` in `run_python`) with no Python. Each builder makes one object whose geometry comes from a geometry-nodes modifier, so it stays parametric.

- **Objects by name.** `surface`, `sources` (objects or a collection), `curve` (a curve, or `[[x, y], ...]` points that become a smooth path), `path_or_bounds` (cliffs), `terrain` and `keep_out`.
- **Sources.** Scatter and array sources are moved into the hidden `Procedural Sources` collection, so they show only through their instances. Repeated sources export as GPU instances, one draw call per material.
- **Terrain.** Roads and rivers grade or carve the `terrain` through a modifier they add to it.
- **Colliders.** `colliders` picks them. Roads, cliffs and buildings get a mesh collider by default; a road's open ends taper into the ground. Scatters, arrays and fences get none by default. `"hull"`, `"box"` or `"mesh"` put a Collider on the object itself, which the game fits to every instance. `"trunk"` adds an invisible proxy child of trunk cylinders. `"simple"` means trunk for tree kits and mesh otherwise.
- **Results.** Both tools return the object, every input (`params`) and `stats`: instances per source, triangles, and the draw calls the game will make.
- **Undo and errors.** Each call is one undo step, so both can be pre-approved like `create_from_kit`. Bad params fail with what to change, and nothing is left half built.
- **Scripts.** Code that writes geometry-nodes inputs itself (`mod.properties.inputs.<id>.value` in Blender 5.2) must call `obj.update_tag()`; `set_procedural` does.

`validate_assets` checks:

- transform: unapplied scale (warning), negative scale (error), unapplied rotation (info);
- size: real-world scale, under 1 cm or over 1 km;
- origin placement: outside the geometry is a warning; at the bottom centre or the centre is reported;
- normals: inconsistent winding, or inward on closed meshes (error);
- non-manifold edges, open boundaries, loose geometry and zero-area faces;
- triangle count after modifiers, against `max_tris`;
- n-gons;
- UVs: missing, or zero-area UV faces;
- materials: none, empty slots, nodes glTF cannot export, missing texture files, textures without UVs;
- references: component entity fields that dangle: emptied by deleting their target (`delete_objects` or `asset_add` `spawn_only` cleared them, and `cleared_references` listed them), until `set_component` sets them (`null` leaves one empty on purpose) or `remove_component` removes the component; or pointing at an object that is not in the scene. A field that was never set (`null`) is an ordinary empty reference and is not reported.

In the UI file (`ui/ui.blend`), `names: "all"` checks the elements of every canvas, not only the scene on screen, and each report names its `canvas`. UI art is drawn flat (Emission materials by design, docs/GAME-UI.md §3.5), so the mesh, PBR material, UV and transform checks are skipped. What is checked: missing font and image files (also images in materials) as errors, an image empty without an image, and dangling references (`UiBar.fill`, `UiCanvas.focus`) within the element's canvas.

```json
{"name": "Bad", "ok": false, "issues": [{"severity": "error", "check": "normals", "message": "Normals point inward (the closed mesh has negative volume).", "fix": "Edit Mode > select all > Mesh > Normals > Recalculate Outside (or Flip)."}, ...]}
```

#### `render_views`

Agents judge their work by these pictures, so each call returns **one image** that shows what is there and how big it is: a labelled contact sheet.

- **`target`.** Object name(s) (their children are included), `"selection"`, `"collection:<name>"` or `"scene"` (the default). Only the targets are drawn, unless `focus` is set.
- **`views`.** Any of `front, back, left, right, side (= right), top, bottom, iso, camera` (the scene camera). The default is `front, side, top, iso` as a 2×2 sheet. For wide, flat targets such as levels, it is `top, iso`.
- **Framing.** Each view is fitted tightly to the targets' bounds, whatever their size, from a coin to a level. Bands are kept free for the title, rulers and labels.
- **Scale references:**
  - **Orthographic views** (front/side/top/...) have **rulers in world metres** along the left and bottom edges. Graph paper sits behind the objects, the ground line shows in side views, and **yellow dimension lines** give the targets' overall width and height in metres. Each tile title names its axes, e.g. `FRONT (x right, z up)  grid 0.5 m`.
  - **Iso and top views** have a **ground grid** (labelled `grid 1 m` in the title). The iso title also gives the overall size, `W x D x H m`.
  - **A 1.8 m person** (blue) stands front-left of the targets, beside them in every view, when they are between 0.4 m and 80 m. Force it with `person: true` / `false`.
  - The ground is world z = 0 when the targets stand on it or float near it, so a hovering pickup shows its height.
- **`focus: true`** draws the rest of the scene ghosted and outlines the targets in orange: where is it, and what is around it.
- **`names`** labels objects at their tops (`"auto"`: when there are 2–12 targets). A list labels just those objects, e.g. `names: ["Player", "Key_1", "Gate"]` on a whole level; they need not be targets. `labels: [...]` does the same. The result lists them as `labelled`, and those outside every tile as `not_in_view`. `labels: false` removes all text.
- **`shading`.**
  - `"studio"` (default): Workbench studio light, material colours, cavity and outlines; best for reading shapes and colours.
  - `"clay"`: one neutral colour, for shape only.
  - `"material"`: EEVEE look-dev, about 1–2 s.
- **`compare: "<checkpoint id>"`** draws the same views of that checkpoint (left, `BEFORE`) and of now (right, `NOW`), framed identically. The default views are `iso` and `top`. The checkpoint's scene is appended for the call and removed afterwards.
- **`size`.** The sheet width: 768 px by default (four 384 px tiles), 128–2048.
- **`save: true`** also writes the PNG under `.gamestudio/mcp/renders/` and returns its path.
- **Speed.** Views are drawn offscreen with `gpu.types.GPUOffScreen` through a 3D Viewport's draw engine, in the UI and in `--background` (after `gpu.init()`). A sheet takes 0.1–0.5 s; the first one in a session about 1–5 s, while shaders compile. A Workbench render is the fallback.
- **No side effects.** Everything is drawn from a temporary scene that links the objects, with temporary helper objects (grid, person). The user's scenes, settings, selection and undo history are untouched.

```json
{"name": "render_views", "arguments": {"target": "SupplyCrate", "focus": true}}
→ {"backend": "offscreen", "shading": "studio", "layout": [["front", "side"], ["top", "iso"]], "width": 768, "height": 768,
   "bounds": {"min": [2.5, -4.5, 0], "max": [3.5, -3.5, 1], "size_m": [1, 1, 1]}, "person": true,
   "views": [{"view": "front", "grid_m": 1, "ortho": true, "axes": {"right": "x", "up": "z"}, "metres_per_pixel": 0.0089}, ...],
   "legend": "Blender space, Z-up. Rulers and grids are world coordinates in metres ...", "seconds": 0.3}  + 1 image
{"name": "render_views", "arguments": {"compare": "20261010-045720-before-tower"}}
→ {"layout": [["iso before", "iso now"], ["top before", "top now"]], "compare": {"checkpoint": "...", "label": "before tower"}, ...}  + 1 image
```

### Asset libraries and the scene twin

An asset is a collection kept in a project library, `assets/<library>.blend`. It is marked as a Blender asset and, by default, as a prefab. Levels link it and place instances of it, and game code can `ctx.spawn()` it. See `blender/gamestudio/assetlib.py`.

| Tool | | Arguments | Notes |
|---|---|---|---|
| `asset_list` | R | `library?`, `catalog?`, `tag?`, `backups?` | Libraries and assets: `name, library, catalog, tags, description, prefab, objects, components, size`, and `placed` (instances in the open level). `backups: true` also lists the library backups, newest first (`library, id, tool, asset, created, bytes`, and `absent` for a backup taken before the library existed). |
| `asset_add` | W | `objects`, `library?` (`props`), `name?`, `separate?`, `move?`, `spawn_only?`, `catalog?`, `tags?`, `description?`, `prefab?` (true) | Copies the objects, with their children, into the library as one asset (`name`), or one asset per top object with `separate`. A background Blender writes the file, which takes a second or two. Undo does not cover the library file, so it is **backed up first**: the result gives `library_backup` and `restore`, the `asset_library_restore` call that puts the previous file back. `move` also replaces the objects in the open level with an instance, named `<asset>_01` like `asset_place` names them, so it doesn't share a name with the asset's own object. `spawn_only` instead removes them from the level and links the asset as a spawnable template without placing it, so `ctx.spawn('<asset>')` works (one call instead of `asset_add`, `delete_objects` and `asset_spawnable`). The result lists `removed`, `spawnable`, `spawn` and any `cleared_references`. The source's prefab collection, if it is left empty, is removed in the same undo step (`removed_collections`). Otherwise its name would stay taken, and the asset would export as `Grunt [actors.blend]`. `spawn` gives the names the export really uses, and a `warning` says when another prefab in the level takes the name. The change in the open level is one undo step. |
| `asset_place` | W | `asset`, `library?`, `location?`, `rotation_deg?`, `scale?`, `name?`, `collection?` | An instance empty named `<asset>_01`, `_02`, ... Per-copy components go on it. |
| `asset_replace` | W | `instance`, `asset`, `library?` | The instance shows another asset and keeps its name, transform and components. |
| `asset_make_local` | W | `instance` | Editable local copies under the instance empty, for one-off edits; they no longer follow the library. |
| `asset_spawnable` | W | `asset`, `library?` | Links a prefab asset without placing it, so `ctx.spawn('<asset>', {position})` works in game code. `spawn` uses the name the export gives the template, which is `Grunt [actors.blend]` when a local prefab already takes `Grunt`, with a `warning` saying how to free the name. |
| `asset_library_restore` | W | `library`, `backup?` (default: the latest) | Takes a library write back: puts `assets/<library>.blend` (and its twin) back as the backup has it, then reloads it so placed instances and spawns follow. The file it replaces is backed up first, and the result's `undo` is the call that takes the restore back. A backup taken before the library existed removes the file. `backup` is an id or a unique prefix. |
| `asset_reload` | W | `libraries?` | Reloads linked libraries after they changed on disk. |
| `scene_twin` | R | none | The open level as compact text, one line per object, including unsaved changes. Kit objects show `kit: {kind, params}`, and procedural objects `procedural: {kind, inputs}`: what they are built from (surface, sources, curve, terrain), the inputs that differ from the builder's defaults, and extras such as `colliders` or `style`. It is the same format as `<name>.scene.json`, which is rewritten next to the `.blend` on every save and diffs well in git. |

```json
{"name": "asset_add", "arguments": {"objects": ["Crate", "Barrel"], "separate": true, "library": "props", "tags": ["wood"]}}
→ {"assets": [{"asset": "Barrel", "library": "assets/props.blend", ...}, {"asset": "Crate", ...}],
   "library_backup": "…/.gamestudio/library-backups/props/20261010-071512-034-asset-add.blend",
   "restore": "asset_library_restore {\"library\": \"assets/props.blend\", \"backup\": \"20261010-071512-034-asset-add\"}", "note": "..."}
{"name": "asset_place", "arguments": {"asset": "Crate", "location": [4, 2, 0], "rotation_deg": [0, 0, 30]}}
→ {"instance": "Crate_01", "asset": "Crate", "library": "assets/props.blend", "location": [4, 2, 0], "hint": "Per-copy components go on this instance (add_component)."}
{"name": "asset_add", "arguments": {"objects": "Boulder", "spawn_only": true, "catalog": "props/hazards"}}
→ {"assets": [{"asset": "Boulder", "library": "assets/props.blend", ...}], "library_backup": "…", "restore": "asset_library_restore {...}", "removed": ["Boulder"],
   "spawnable": ["Boulder"], "spawn": ["ctx.spawn('Boulder', { position: [x, y, z] })"], "level": "The objects were removed ...", ...}
{"name": "set_component", "arguments": {"asset": "Boulder", "component": "RigidBody", "values": {"linearDamping": 0.2}}}
→ {"asset": "Boulder", "library": "assets/props.blend", "op": "set", "objects": [{"object": "Boulder", "values": {"RigidBody": {..., "linearDamping": 0.2}}, "components": [...]}],
   "library_backup": "…/library-backups/props/20261010-071530-118-set-component.blend", "restore": "asset_library_restore {...}", "note": "...", "instances": [], "spawnable": true, "hint": "Reloaded: ..."}
{"name": "asset_list", "arguments": {"backups": true}}
→ {..., "backups": [{"library": "assets/props.blend", "id": "20261010-071530-118-set-component", "tool": "set_component", "asset": "Boulder", "created": "...", "bytes": 1153024}, ...]}
{"name": "asset_library_restore", "arguments": {"library": "props"}}
→ {"restored": "20261010-071530-118-set-component", "library": "assets/props.blend", "from": {"tool": "set_component", "asset": "Boulder", ...}, "reloaded": true,
   "backup_of_replaced": "…", "undo": "asset_library_restore {\"library\": \"assets/props.blend\", \"backup\": \"20261010-071544-902-asset-library-restore\"}", "hint": "..."}
```

**Changing an asset.** `add_component`, `set_component` and `remove_component` take `asset` (and `library?`) to edit the objects of a library asset in place:

- `objects` then names objects inside the asset. The default is its one top object.
- Values are checked in the open Blender first, so a bad value fails at once. Then a background Blender writes the library, which takes a few seconds (3–6 s for Hill Climb's boulder). Entity fields name objects in the library.
- The open level reloads the library, so placed instances and `ctx.spawn` copies follow on the next export or play.
- Undo does not cover the library file, so it is **backed up first**. The result's `restore` is the `asset_library_restore` call that puts it back.
- On any error the file is not changed.

This replaces opening the library with `open_scene`, editing, saving and reopening the level (4 calls).

### Baked lighting

| Tool | | Arguments | Notes |
|---|---|---|---|
| `bake_lighting` | W | `quality?` (`preview` / `final`; `preview`), `objects?`, `force?` (false), `export?` (true) | Bakes lightmaps for the level's static meshes with Cycles, then exports so connected games reload. `preview` takes seconds; `final` takes longer (minutes on a big level) and is cleaner. Writes generated files only (`.gamestudio/lightmaps/<scene>/` and the export), so it is no undo step and the `.blend` does not change. |

Bake once the level's geometry and lights are final, before `compare_views`, and again after moving static geometry or changing lights (the export report lists `stale` lightmaps).

- **What is baked.** The sky light with its ambient occlusion, the bounce light of every lamp and of the sky, and emissive surfaces. Lamps stay realtime in the game (direct light and shadows), unless the light object has the custom property `gs_lightmap = "baked"`: such a lamp is baked entirely and switched off in the game. Area lights are always baked.
- **Which objects.** Static meshes: no `RigidBody` (or type `fixed`), no `CharacterController`, `Spin` or `Animator`, no animation, armature or shape keys, and nothing above them that moves. The custom property `gs_lightmap = False` leaves an object out. `skipped` lists the meshes left out, with the reason.
- **`objects`** bakes only those objects; the others keep their lightmaps. An unchanged scene is not baked again (`up_to_date: true`) unless `force: true`.
- **Errors** say what to fix, for example "Mark objects static: remove the RigidBody or set its type to fixed" when nothing static is left to bake, or to save the `.blend` inside a GameStudio project.

```json
{"name": "bake_lighting", "arguments": {"quality": "preview"}}
→ {"scene": "main", "quality": "preview", "objects": [{"name": "Ground", "atlas": 0, "size": 512}, ...],
   "atlases": [{"file": "main_ab12_0.png", "size": 1024, "scale": 1.37}], "skipped": [{"name": "Player", "reason": "..."}],
   "baked_lights": [], "texture_bytes": 1048576, "texture_mb": 1.0, "seconds": 6.4, "up_to_date": false,
   "dir": "…/.gamestudio/lightmaps/main", "warnings": [], "export": {"scene": "main", "rev": 12, "entities": 25, ..., "reloaded_games": ["p4"]}}
```

### Comparing Blender and the game

| Tool | | Arguments | Notes |
|---|---|---|---|
| `compare_views` | R | `camera?` (a camera name, or `{from, at, lens?}`), `view?` + `target?`, `size?` ([800, 450]), `engine?` (`eevee`, `cycles`, `workbench`), `samples?` (16), `post_effects?` (false), `save?` | Does the game show what Blender shows? It doesn't change the scene, though it exports first, so the revision goes up. About 10 s. |

The view is the scene camera, another camera object, or a **temporary camera** made for the call and removed afterwards, so you don't have to place one by hand:

- `camera: {"from": [x, y, z], "at": [x, y, z], "lens": 35}`: Blender space. `lens` is in millimetres on a 36 mm sensor; the default is the scene camera's lens, else 35.
- `view: "iso" | "front" | "back" | "left" | "right" | "top"`: frames `target` (object names, children included; default the whole visible scene) from that side.

A temporary camera is not in the export, so the game half builds the same camera in the page from its pose. The pose is in runtime space, with the same vertical field of view, near and far. The result echoes it as `pose: {label, from, at, lens}`.

`compare_views` follows the recipe in `core/compare.py`:

1. Export the scene and note its `rev`.
2. Blender renders the camera's view at `size`, with the scene's lights and world.
3. The game opens in a fresh headless page at the same size and device scale 1, and waits for that `rev`.
4. The game is stopped (its loaded state, before any physics or animation runs), switched to the same camera, and its HUD and page overlays are hidden. Open game UI canvases (menus and HUDs from `ui/ui.blend`) are closed. Its post effects (bloom, vignette, grading) are switched off, because Blender renders none, and the result's `post_effects` names those that were on. `post_effects: true` keeps them.
5. The game page is screenshotted.
6. The two images are compared.

The result:

```json
{"verdict": "different", "verdict_reason": "different, not close: detail_ratio 2.26 is outside 0.5-2: the game image has more fine detail than Blender's (noise, aliasing, shadow acne or banding) (normalized_diff 0.095 alone would allow close).",
 "decided_by": ["detail_ratio"], "normalized_diff": 0.095, "detail_ratio": 2.26, "mean_abs_diff": 0.13,
 "worst_cells": [{"cell": [3, 2], "box": [600, 225, 800, 337], "diff": 0.19}, ...],
 "detail_cells": [{"cell": [1, 2], "box": [200, 225, 400, 337], "detail_ratio": 4.1}, ...], "rev": 4, "camera": "(iso)",
 "pose": {"label": "iso", "from": [...], "at": [...], "lens": 35}, "engine": "BLENDER_EEVEE", "size": [800, 450], "image": "[Blender | game | difference x4]", "note": "..."}  + 1 image
```

Judge it on two numbers:

- **`normalized_diff`**: the difference after matching brightness and contrast. `match` is at most 0.12, `close` at most 0.25.
- **`detail_ratio`**: the game's edge detail over Blender's. `match` needs 0.75–1.33 and `close` 0.5–2. Well below 1 means detail went missing (flat procedural materials, low-res textures); well above 1 means noise, aliasing or shadow acne.

`verdict_reason` says which of the two kept the verdict from the next better one, and `decided_by` lists them. A `normalized_diff` inside the match range can still give `different` when `detail_ratio` is far off. When `detail_ratio` decided, `detail_cells` gives the three grid cells that push it most the way it is off (the most extra or the most missing edge detail), each with its own `detail_ratio`.

`mean_abs_diff` is the plain difference, before brightness and contrast are matched. The game tone-maps with the scene's own view transform and exposure (Render Properties › Color Management, carried in the export), so a good match keeps it small. It grows when the view transform has no three.js equivalent (Filmic becomes ACES in the game; the export warns) or when a look is set, which the game does not carry. `worst_cells` points at the 4×4 grid cells to look at first. `save: true` writes `blender.png`, `game.png`, `side_by_side.png` and `diff.png` under `.gamestudio/mcp/compare/`.

**Export report.** `export_scene` (and the export inside `play` and `compare_views`) reports what happened to the content:

- `size`: total `bytes`, plus the `top` items by size and, with `compress: true`, `compressed` (each GLB before and after the build's compression: what `npm run build` ships). `verbose: true` adds `per_file`: each GLB as Blender's glTF exporter wrote it (`exported`) and as written (`bytes`);
- `warnings`;
- `bake`: `baked` / `cached` counts of procedural materials baked to textures;
- `textures`: textures that were downscaled or converted;
- `instancing`: GPU instancing of repeated geometry-nodes instances;
- `lightmaps`: baked lighting in the export (`objects`, `atlases`, `texture_bytes`), and the `stale` and `missing` lightmaps that `bake_lighting` should bake again;
- `hidden_meshes`.

All of these are compact by default; `export_scene {"verbose": true}` returns the full report.

### Performance

| Tool | | Arguments | Notes |
|---|---|---|---|
| `game_stats` | R | `seconds?` (3), `size?` ([1280, 720]), `scene?`, `use_running?`, `keys?` | Measures what the game costs. It reports the `state` and any menu `screen` it measured; when the game was paused or a screen was open (a title screen), a `warning` says the numbers are that screen's, not the level's, and `keys: ["Enter"]` presses keys first. Read-only: it opens its own headless page with the last export, or samples this server's headless game with `use_running`, and changes nothing. It doesn't export; call `export_scene` first to measure a change. |

It reports:

- `frames`: fps, plus min/avg/p95/max frame time in ms;
- `renderer`: draw calls and triangles per frame (average and maximum), every render pass of the frame counted (the scene and post effects such as bloom), GPU `geometries`, `textures` and shader `programs`, from three.js `renderer.info`;
- `js_heap_mb`, `entities` and physics `bodies`;
- `largest_meshes`: the 10 heaviest meshes by triangles, with their entity names and instance counts;
- `export`: the scene's export size on disk (the GLB, the prefab GLB, and the asset files its manifest names);
- `budgets` and `over_budget`: the project's export budgets from `gamestudio.json` `"export"` (`warnSizeMB`, `maxTextureSize`, `bakeBudgetMB`; defaults 32 MB, 2048 px, 64 MB) against what was measured.

**Headless frame times are only roughly representative.** Headless play draws on the GPU when it can (`renderer.gpu`), with its frame rate capped by the browser; on SwiftShader, the fallback, fps is far below a real GPU (the demo measures about 4.6 fps at 1280×720). Draw calls, triangles, GPU resources and memory are representative either way. Use `game_stats` for relative comparisons: measure, change one thing, `export_scene`, measure again at the same `size`.

```json
{"name": "game_stats", "arguments": {}}
→ {"page": "a fresh headless page", "size": [1280, 720], "seconds": 3, "scene": "main", "state": "playing",
   "frames": {"fps": 4.58, "frames": 14, "min_ms": 213.4, "avg_ms": 218.3, "p95_ms": 227, "max_ms": 227},
   "renderer": {"kind": "webgl", "draw_calls": {"avg": 43, "max": 43}, "triangles": {"avg": 2196, "max": 2196}, "geometries": 22, "textures": 4, "programs": 2},
   "js_heap_mb": 37.9, "entities": 25, "bodies": 14,
   "largest_meshes": [{"entity": "BouncePad", "mesh": "BouncePad", "triangles": 124, "instances": 1}, ...],
   "export": {"bytes": 63500, "glb": 63500, "prefabs": 0, "files": 0},
   "budgets": {"warnSizeMB": {"limit": 32, "actual": 0.06, "over": false}, ...}, "over_budget": [], "note": "Headless Chromium draws with SwiftShader ..."}
```

### Components

Components are defined in TypeScript (`src/components/*.ts`, plus the engine's built-ins) and listed in `.gamestudio/components.json`, which the dev server writes. Values are validated and coerced against that registry. Entity fields take object names. A running game receives changes live.

| Tool | | Arguments |
|---|---|---|
| `list_component_types` | R | `type?`, `verbose?`: every component with its fields as compact `"type = default [min..max] - description"` strings, and every game shader under `shaders`, grouped: `Project` (the project's `src/shaders`) first, with their fields in the same form, then the shader library by category, one line per shader (`name` and the first sentence of its `description`; see "Game shaders" below); `type` returns one component's or shader's raw registry entry, with its fields and whole description; `verbose` returns all raw entries (`shaders` as one list) |
| `add_component` | W | `objects`, `component`, `values?`; or `components: {Name: values, ...}` to attach several at once (all are validated before any is added). `asset?`, `library?`: edit a library asset instead |
| `set_component` | W | `objects`, `component`, `values`, `add_if_missing?`; `asset?`, `library?` |
| `remove_component` | W | `objects`, `component`; `asset?`, `library?` |

With `asset`, these edit the objects of that library asset instead of the open level (`objects` defaults to its one top object). A background Blender writes the library, and the level reloads it. The library is backed up first, and `asset_library_restore` takes the write back; see "Changing an asset" above.

```json
{"name": "add_component", "arguments": {"objects": ["Coin1", "Coin2"], "component": "Spin", "values": {"speed": 3}}}
{"name": "add_component", "arguments": {"objects": "Player", "components": {"RigidBody": {"type": "kinematic"}, "Collider": {"shape": "capsule"}, "CharacterController": {}}}}
{"name": "add_component", "arguments": {"objects": "Coin1", "component": "Spinner"}}
→ isError: {"error": "Unknown component 'Spinner'. Did you mean 'Spin'?", "hint": "Known components: Animator, AudioSource, ..."}
{"name": "set_component", "arguments": {"asset": "Boulder", "component": "RigidBody", "values": {"linearDamping": 0.2}}}
```

#### Game shaders

A game shader is TypeScript that draws a Blender material in the game: a `defineShader` call, listed in the same registry under `shaders`. GameStudio ships a shader library of 33 (water, shoreline foam, lava, foliage and grass wind, snow cover, ice, energy shield, hologram, dissolve, toon, petrification, selection outline, hit flash, a UI shine and gradient and more; [API.md §29](../runtime/packages/engine/API.md#29-shader-library) has the table) in the categories Water, Nature, Magic & Sci-fi, Surfaces, Transformations, Gameplay and Style; the project adds its own in `src/shaders/*.ts`. A project shader with a library shader's name replaces it, so to customise a library shader, copy it into `src/shaders/`. A material names its shader in Blender, with typed values. The export writes this into the scene manifest, and the game applies the shader to every mesh that draws that material, prefab copies included. Blender's viewport keeps showing the material's own nodes, so look at the result in the game (`game_screenshot`, `compare_views`).

`list_component_types` keeps the library short: one line per shader, about 200 bytes each, instead of every field (a fresh project's listing went from about 42 KB to 18 KB). Ask for one shader's fields with `{"type": "<Shader>"}` before you set its values. Extensions older than this listing send the library's fields and no category; the MCP server then reads the categories with a second, verbose call and shortens the entries itself.

| Tool | | Arguments | Notes |
|---|---|---|---|
| `set_material_shader` | W | `material` or `objects` (+ `slot?`), `shader`, `values?` | Sets the game shader of a material, or takes it off with `shader: null`. Pass exactly one of `material` (a material name) and `objects` (the materials in their slots; `slot` is an index or a list of them, default every slot). `values` are validated and coerced against the shader's fields; fields left out keep their values. Colour fields take `"#RRGGBB"` or `"#RRGGBBAA"` (sRGB, as a colour picker shows them; made linear like every UI colour) or linear `[r, g, b, a]` lists; Node refuses a hex string of the wrong length before calling Blender. One undo step for the whole call. The game gets the change with the next export (`export_scene`, `play`). |

An unknown shader name is refused, and the hint names the close matches (`Close matches: SelectionOutline.` for `"Outline"`). While the registry is empty (no dev server has written it yet), the name is stored with a warning instead. Materials linked from a library are read-only, like linked objects. The result lists every material the call changed, with its values and `users`, the objects that use it:

```json
{"name": "list_component_types", "arguments": {}}
→ {..., "components": [...], "shaders": {"Project": [{"name": "Sand", "description": "Dunes with wind ripples", "fields": {"tile": "float = 3.2 [0.01..]", "wet": "float = 0 [0..1]"}}], "Water": [{"name": "Caustics", "description": "Wavering caustic light over the material, ..."}, ...], "Nature": [...], ...}, "hint": "Library shaders show what they do in one line; ..."}
{"name": "list_component_types", "arguments": {"type": "Triplanar"}}
→ {"name": "Triplanar", "description": "...", "category": "Surfaces", "builtin": true, "fields": [{"name": "diffuse", "type": "asset", ...}, {"name": "tile", "type": "float", "default": 3.2, "min": 0.01, ...}, ...]}
{"name": "set_material_shader", "arguments": {"material": "GS_Rock", "shader": "Triplanar", "values": {"tile": 3.2, "wetness": 0.6}}}
→ {"materials": [{"name": "GS_Rock", "shader": "Triplanar", "values": {..., "tile": 3.2, "wetness": 0.6, ...}, "users": ["Cliff", "Boulder_1"]}], "warnings": []}
{"name": "set_material_shader", "arguments": {"objects": "Beach", "slot": 0, "shader": null}}
```

#### Game UI

Game menus and HUDs are built in Blender, in the project's UI file `ui/ui.blend`, with one Blender scene per screen. A screen is called a **canvas**: `Title`, `HUD`, `Pause`, `GameOver`. The game draws the open canvases in WebGL over the 3D view, without tone mapping or post effects. [GAME-UI.md](GAME-UI.md) is the full design.

- **Pixels.** A canvas's orthographic camera frames its design size, 1920×1080 by default. Every position is in canvas pixels: origin top-left, x right, y down. `at` is the centre of a box (panel, image, button, bar, slider, toggle, icon row); text is aligned around it.
- **Elements** are Blender objects: texts, images, flat or image panels (9-slice art too), buttons (a group with a normal and a focused panel and a label), bars, sliders, toggles, icon rows (hearts), 3D models and groups. They carry the built-in UI components (`UiCanvas` on the camera, `UiButton`, `UiAnchor`, `UiText`, `UiBar`, `UiState`, `UiShow`, `UiSlider`, `UiToggle`, `UiSlice`, `UiModel`), so `set_component` sets the fields these tools don't, such as `UiText.fit` and `UiButton.focusScale`.
- **No code for settings and counters.** A slider or a toggle shows and writes a value path: `audio.music`, `audio.sfx`, `audio.master`, `audio.ui` (the volumes, saved for the player), `save.<name>` (saved) or a `ctx.store` path. Icons count a value: hearts are `{"kind": "icons", "value": "lives"}`, and heart *i* shows while `ctx.store.lives` is at least *i*. Every change of a slider or toggle also calls `ctx.ui.on(<its name>)` with `info.value`. Two values are built in and read-only: `game.time`, the game clock (`ctx.time`, seconds), so `"{game.time:m:ss}"` is a clock with no code, and `game.level`, the level's name.
- **Live.** While `ui/ui.blend` is open, a running game gets every change. `save` writes it and exports its canvases to `.gamestudio/export/ui/`, which the game loads at startup; its result's `ui_export` says what was exported (`canvases` with `written` and `rev`, `locales`, `warnings`).
- **Code.** A button press runs its `action`: `close` or `resume` closes the menu, `open` opens the canvas named `target`, `previous` goes back to the menu this one replaced, `restart` restarts, `load` loads the level `target`, and `locale` switches the UI language to `target` (a code such as `"fr"`; `""`: the next language). Then it calls the handler of `ctx.ui.on(<target, else the button's name>)`. Text bodies bind live values: `"Score {score}"` shows `ctx.store.score`, `"{save.best}"` a saved value, `"{time:m:ss}"` a formatted one. A bar's `value` is the same path without braces.
- **Anchors.** On screens of another shape, a top-level element keeps its distance to its `anchor`: by default the screen edge or corner it sits nearest (by thirds), or one of `center`, `top-left`, `top`, `top-right`, `left`, `right`, `bottom-left`, `bottom`, `bottom-right`. `stretch` (`width`, `height`, `both`, `cover`) makes it follow the screen's size.
- **Colours** are `"#RRGGBB"` or `"#RRGGBBAA"` (sRGB, as a colour picker shows them), or linear `[r, g, b, a]` lists. Game shader colour fields in `shader_values` take both forms too (hex is made linear).

| Tool | | Arguments | Notes |
|---|---|---|---|
| `ui_list` | R | `canvas?` | Every canvas of the open UI file with its settings, and its elements with their place (`at`) and `size` in canvas pixels, parents, anchors, visibility, texts, colours and UI components. A text also gives `rect`, `[x, y, w, h]` in canvas pixels: the box its glyphs fill, measured from Blender's evaluated text layout. It is approximate: the game lays the text out with the same font and rules, so its box differs by a few pixels, and a binding is measured as written (`"{score}"`). Use it to place an element next to a title without a screenshot. Panels, buttons, bars and their parts also give their shape, so a new element can match an existing card or pill: `radius`, and for panels `border` and `border_color`, each left out when 0; an image panel gives `image` and its 9-slice `slice`. A rotated element gives `rotation`, in degrees clockwise on the canvas (parents included). Sliders (`kind: "slider"`) give `value`, `min`, `max` and `step`, toggles `value`, icon rows (`kind: "icons"`) `count`, `value`, `image`, `empty`, `icon_size` and `spacing`. A canvas with open/close animations gives `animations` (`{open, close, loop}` in seconds, from its timeline markers) and the `transition` presets that made them, as they still are: a preset whose marker or keys were changed by hand in Blender is left out and named in `transition.edited` (e.g. `["loop"]`). An element with a loop of its own gives `loop`, while its keys are as the tools made them. A 3D model (`kind: "model"`, `type: "model"`) gives `view`, `zoom`, `spin`, `clip`, `light`, `background` (hex), `source` (what it shows, per object: `{"object", "kit"}`, `{"object", "asset", "library"}`, `{"object", "linked": "scenes/main.blend:Player", "kit"?}` for a level's object, `{"object", "collection"}` or `{"object", "type"}`) and `clips` (the animation clips it has, for `clip`). A panel with a game shader gives `shader` and `shader_values` (the values that are not the defaults), the game's title text `project_name: true`. |
| `ui_open` | W | `create?` (true), `save?` (false) | Opens `ui/ui.blend` in Blender, creating it when missing (with one empty menu canvas, `Menu`). Refuses when the open file has unsaved changes unless `save: true`. Returns `{file, canvases}`. `open_scene` goes back to a level. |
| `ui_canvas` | W | `name`, `kind?` (`menu` / `hud`), `open?` (`manual` / `start` / `firstStart`), `pause?` (true), `pause_menu?` (false), `back?` (`none` / `close` / `resume` / `previous` / `restart`), `scale_mode?` (`fit` / `width` / `height` / `fixed`), `backdrop?`, `layer?` (0), `size?` ([1920, 1080]), `focus?`, `transition?` | Creates a canvas, or changes the given settings of one. A `menu` is open one at a time, takes keyboard and gamepad focus and pauses the game; a `hud` stays open under menus. `firstStart` opens once per page load (title screens), `start` on every start and restart (HUDs). `focus` is the button selected first when the menu opens, a button (or slider, or toggle) of that canvas (`null` or `""` clears it: then the first in navigation order). Set it once the buttons exist: Play on a title screen, a safe button such as Back on a settings screen, so Enter never changes a setting by accident. `pause`, `pause_menu`, `back`, `backdrop` and `focus` are menu settings, refused with `kind: "hud"`. The settings are the `UiCanvas` component of the canvas camera, the object `<Canvas> Camera`. `transition?` (`{open?, close?, duration?, loop?}`) animates the canvas (below). Returns the canvas as `ui_list` describes it. |
| `ui_add` | W | `canvas`, `kind`, `name`, `at`, the kind's arguments (below); `parent?`, `anchor?`, `stretch?`, `z?`, `visible?` (true), `loop?` | Creates an element, or updates the element of that name when it is of the same kind. An argument that belongs to another kind is refused, and the hint lists what this kind takes. `anchor` and `stretch` go with top-level elements only. `visible: false` adds it hidden ("Disable in Renders"), for art that game code shows with `ctx.ui.show`, such as a "New best!" text; an update without `visible` keeps it. `loop` gives the element a loop of its own (see "Element loops"). Returns `{canvas, created, element, parts?, removed?}`: `parts` are the pieces the tools made for this element (a button's `Bg`, `Focus` and `Label`, a panel's `Border`), not the elements inside it; `removed` names the parts the change deleted, such as `<name>Border` when an existing bordered panel gets 9-slice art. |
| `ui_set` | W | `canvas`, `name`, any of `at`, `z`, `size` ([w, h]), `body`, `font`, `text_size`, `color`, `text_color`, `focus_color`, `focus_text_color`, `align`, `valign`, `shadow`, `visible`, `anchor`, `stretch`, `action`, `target`, `disabled`, `order`, `sound`, `focus_sound`, `value`, `min`, `max`, `step`, `count`, `parent` (null: top-level), `loop`, `view`, `zoom`, `spin`, `clip`, `light`, `background`, `shader`, `shader_values`, `project_name` | Changes only the given properties. `view`, `zoom`, `spin`, `clip`, `light` and `background` go with 3D models (and `size`, which rescales the model in Blender too); what a model shows changes with `ui_add` (kit, asset or source again). `shader` and `shader_values` go with panels (`shader: ""` removes it), `project_name` with texts. Returns `{canvas, element, parts?, removed?}` as `ui_add` does: a smaller `count` removes icons and slots. `loop` (`pulse`, `bob`, `blink` or `none`) goes with any element (see "Element loops"). `visible: false` makes the element start hidden; game code shows it with `ctx.ui.show`. `shadow` goes with texts. `sound` and `focus_sound` go with any element that has `UiButton`; `""` removes one. `value` goes with bars, sliders, toggles and icon rows, `min`, `max` and `step` with sliders (`max` with bars too), `count` with icon rows; sliders and toggles also take `disabled`, `order` and `sound`, but never `action` (they are not buttons). `size` rebuilds a 9-slice panel's quads, so its corners keep their size: resize panels with `size` (or the Layout box), never by scaling. A button takes `color` (its fill), `text_color` (its label), `focus_color` (the focused fill) and `focus_text_color` (the focused label), and its parts are recoloured as `ui_add` does: the `<name>FocusLabel` part is made or dropped as the focused text colour differs from the label's, and a new `text_color` alone keeps the focused label's colour. `name` can also be a part the tools made (`parts` in `ui_list`, such as `PlayButtonFocus` or `PlayButtonLabel`): that part alone changes, as the panel or text it is, such as a `shader` on a button's focus art. Recolour a whole button through the button, so its parts stay in step. `shader_values` colour fields take hex or linear lists. Examples: `{"canvas": "HUD", "name": "Lives", "count": 5}`, `{"canvas": "Title", "name": "PlayButton", "text_color": "#17171C", "focus_color": "#FFFFFF"}`, `{"canvas": "Title", "name": "PlayButtonFocus", "shader_values": {"color": "#FFFFFF80"}}`. |
| `ui_remove` | D | `canvas`, `name?` | Removes an element, or with only `canvas` the whole canvas (its scene). |
| `ui_strings` | R | `locale?` | Every translatable text of the game UI, each once: `{text, elements: ["Canvas/Name", ...]}` (a button's label and its focused twin share one text; bodies that are only bindings, such as `"{score}"`, are left out; the game's title has `project_name: true`). From the open UI file, or with another file open from the exported canvases (`source`: `"ui/ui.blend"` or `"export"`). With `locale`: each text's `translation` (or `null`), `translated` and `missing` counts (`missing` leaves out the game's title, `project_name: true`: it is listed, but a game's name usually stays as it is; a translation of it still counts as translated), `name`, `file`, `exists`, and `unused`, the table's strings no element shows (strings of `ctx.ui.translate`, or texts changed since). Always `source_locale` and `locales` (`[{code, name, file, strings}]`). See "Translations". |
| `ui_translate` | W | `locale`, `name?`, `strings`, `remove?` | Creates or updates `ui/locales/<locale>.json` (`{name, strings}`): `strings` adds or changes translations, `remove` drops texts, the rest is kept. `name` is the language's own name (`"Français"`), needed when the table is new. Works from any file of the project (it writes JSON, not the scene: no undo step) and sends the table to the game. Returns `{locale, name, file, created, added, changed, removed, strings, missing, unused, exported, sent}`; `missing` counts the UI texts still untranslated, without the game's title. |

| `kind` | Arguments: required **in bold**, defaults in brackets |
|---|---|
| `text` | **`body`**, `size` (font px) [48] (`text_size` says the same), `font` (a `.ttf`, `.otf`, `.woff` or `.woff2`, copied into `assets/fonts/`), `color` [white], `align` (`left`, `center`, `right`, `justify`, `flush`) [center], `valign` (`top-baseline`, `top`, `center`, `bottom`, `bottom-baseline`) [center], `box` ([w, h]: the text wraps at w), `outline` (px), `outline_color`, `shadow` ([x, y] or [x, y, blur] px), `project_name` (true: its body is the game's name, so a game made from a template saved from this one shows its own name there; false unmarks it) |
| `image` | **`path`** (png, jpg or webp; copied into `assets/ui/` when outside the project), `width`, `height` [the image's own size], `opacity` [1] |
| `panel` | **`size`** ([w, h]), `color`, `radius`, `border`, `border_color`, `image` (a png, jpg or webp drawn instead of a colour, stretched; `""` makes it flat again), `slice` (with `image`: `[left, right, top, bottom]` image px, or one number for all four, that keep their size: 9-slice art; no `color`, `radius` or `border` with it), `shader` (a game shader that draws it in the game, such as `"Shine"` or `"Gradient"`; `""` removes it), `shader_values` (its field values; fields not given keep theirs) |
| `button` | **`label`**, `size` [[360, 84]], `action` [`event`; `locale` switches the language], `target`, `font`, `text_size` [40], `color` [`#FFFFFF33`], `text_color` [white], `focus_color` [`#FFD400`], `focus_text_color` [`#17171C`], `radius` [42], `order` (1, 2, 3...: keyboard and gamepad order) [0: by position], `sound` (played when pressed) and `focus_sound` (when selected): a `.ogg`, `.mp3` or `.wav` file of the project, such as `"assets/sfx/click.wav"` [none] |
| `bar` | **`size`**, `value` (a store path such as `"health"`), `max` [1], `color` [`#4CAF50`], `back_color` [`#00000080`], `radius`, `direction` (`right`, `left`, `up`, `down`) [right] |
| `group` | none: an empty that moves, shows and hides its children together |
| `icons` | `count` [3], `value` [`"lives"`], `image` (one full icon) [`"assets/ui/heart.png"`], `empty` (the slot under each icon; `""`: none) [`"assets/ui/heart-empty.png"`], `size` (one icon, px: a number) [52], `spacing` (px) [12]: a row centred on `at`, parts `<name>Slot<i>` and `<name><i>`; icon *i* shows while `value` is at least *i* (`UiShow`). The starter template has heart, star, coin, gem and key icons in `assets/ui/` |
| `slider` | `size` [[360, 28]], `value` [`"audio.music"`], `min` [0], `max` [1], `step` (per arrow key; drags snap to it; 0: smooth) [0.1], `color` (the fill) [`#FFD400`], `back_color` [`#FFFFFF33`], `handle` [true], `handle_color` [white], `radius` [half the thickness], `direction` [right; a vertical slider: `up` with size [28, 360]], `order`, `sound` (played on each change): parts `<name>Bg`, `<name>Fill`, `<name>Handle`. Drag it, or select it and press Left/Right |
| `toggle` | **`value`** (the path it flips, e.g. `"save.subtitles"`), `size` [[96, 48]], `on_color` [`#FFD400`], `off_color` [`#FFFFFF33`], `knob_color` [white], `order`, `sound` (played when flipped): parts `<name>Bg` (the track), `<name>Off` (the knob on the left) and `<name>On` (the lit track with `<name>OnKnob` on the right); the game shows On while the value is truthy |
| `model` | one of `kit` (a kit of `list_kits`, with `kit_params`), `asset` (an asset of the library) or `source` (an object or collection of the UI file, or `"<file>.blend:<Object>"`, an object of a level such as `"scenes/main.blend:Player"`), needed when it is new; `size` (its view) [[320, 320]], `view` (`threeQuarter`, `front`, `side`, `top`) [threeQuarter], `zoom` [1], `spin` (radians per second; 0 still) [0.6], `clip` (an animation clip to loop) [none], `light` (`studio`, `soft`, `flat`) [studio], `background` [transparent]: see "3D models" |

The write tools work on the open UI file. With a level open instead, they fail with code `ui_file_not_open`, whose hint says to call `ui_open` first (`save: true` saves the level before). Each write is one undo step. `ui_strings` and `ui_translate` work from any file.

**3D models.** `{"kind": "model"}` puts a 3D model in a canvas: a gem turning on the title screen, a trophy on the win screen, a character waving on a select screen. It is an empty with `UiModel` whose children are the model, and the game draws them lit, in their own small view of `size` px at the element's place and draw order, with the level's tone mapping. What it shows is one of:

- `kit`: built by a GameStudio kit (`list_kits`), with `kit_params` as `create_from_kit` takes them: `{"kit": "collectible", "kit_params": {"style": "gem"}}`, `{"kit": "character_rigged"}` (with its clips `Idle`, `Walk`, `Wave`...);
- `asset`: an asset of the project's library (`asset_list`), placed as an instance;
- `source`: an object of the UI file, moved under the model with its children (one imported while `ui/ui.blend` is open), or a collection, instanced;
- `source: "<file>.blend:<Object>"`: an object of another `.blend` of the project with its children, such as the level's player, `"scenes/main.blend:Player"` (the starter's, made by the `character` kit, or a `character_rigged` one with its clips). See "The level's character" below.

Giving one of them again replaces the model. The game's camera frames it from `view` (`threeQuarter`: 35° round from the front and 20° up; `front`, `side`, `top`), closer with `zoom` above 1; `spin` turns it, `clip` loops one of its animation clips (`ui_list` lists them as `clips`; a clip the model doesn't have is refused), `light` is `studio` (key, fill and rim), `soft` or `flat`, and `background` fills its view (transparent by default). In Blender the model is seen from above through the canvas camera, scaled so its longest side is 80 % of the view's shorter side; the outline of its view is drawn in the 3D view. `game_screenshot {"ui": "<Canvas>"}` shows it as the game draws it. Its objects are not elements: `ui_list` doesn't list them, and other elements can't be parented to a model (group both instead).

```json
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "model", "name": "Gem", "at": [960, 420], "kit": "collectible", "kit_params": {"style": "gem"}, "size": [360, 360], "spin": 1.2}}
→ {"canvas": "Title", "created": true, "element": {"name": "Gem", "kind": "model", "type": "model", "size": [360, 360], "view": "threeQuarter", "zoom": 1, "spin": 1.2, "clip": "", "light": "studio", "background": "#00000000", "source": [{"object": "GemModel", "kit": "collectible"}], ...}}
{"name": "ui_add", "arguments": {"canvas": "Select", "kind": "model", "name": "Hero", "at": [960, 540], "kit": "character_rigged", "size": [480, 640], "view": "front", "spin": 0, "clip": "Wave"}}
{"name": "ui_set", "arguments": {"canvas": "Select", "name": "Hero", "clip": "Idle", "zoom": 1.2}}
```

**The level's character.** `source: "scenes/main.blend:Player"` shows the level's own player on a title or select screen, without a copy to keep in step:

- The object and its children (a rigged character's armature and mesh) are **linked** from the level, a library link stored relative to `ui/ui.blend`, with their meshes, materials, rig and clips. When the level changes them, the UI file shows the change the next time it opens. Opening it then exports its screens again when the level was saved after their last export, and so does the next `save` of `ui/ui.blend`. Play and `export_scene` from a level don't open the UI file, so they keep its last export until then.
- They are gathered in a collection of the UI file that no screen holds (named like `Player (main.blend)`, shared by the models that show the same object) and **instanced** under the model's empty. Their components (`CharacterController`, `RigidBody`) stay the level's and are ignored by the UI.
- The model shows the object **at its own origin, facing as it was made**: the instance cancels its place and turn in the level, so `view: "front"` sees the face of a kit character that the level turns away from the camera. When the level moves or turns it later, the export cancels the new place, so the model stays centred; the next change to the model through the tools puts it back in Blender too.
- `ui_list` gives it as `source: [{"object": "HeroModel", "linked": "scenes/main.blend:Player", "kit": "character"}]`, with the clips of a rigged character in `clips`.
- Refused: a file outside the project, the open file itself, an object the file doesn't have (the hint lists its objects), a camera or light, and an object with no mesh in or under it. Removing the last model that shows it removes the collection and the links.

```json
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "model", "name": "Hero", "at": [1450, 600], "source": "scenes/main.blend:Player", "size": [360, 480], "view": "front", "spin": 0}}
→ {"canvas": "Title", "created": true, "element": {"name": "Hero", "kind": "model", ..., "source": [{"object": "HeroModel", "linked": "scenes/main.blend:Player", "kit": "character"}]}}
```

**Game shaders on panels.** A panel's `shader` names a game shader that draws it in the game, as `set_material_shader` does for level materials: the library's `Shine` (a highlight sweeping across) and `Gradient` (two colours along an angle) are made for UI. The panel gets a material of its own, a copy of its colour or image material, so the shader never reaches other panels of that colour; a new `color` or `image` keeps the shader and its values. `shader_values` sets its fields (`list_component_types` lists them); a colour field takes `"#RRGGBB"` or `"#RRGGBBAA"` (sRGB, made linear like every UI colour) or a linear `[r, g, b, a]` list. `shader: ""` makes it a plain panel again. A button's focus art is a panel of its own: `ui_set` it by its part name (`PlayButtonFocus`).

```json
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "panel", "name": "PlayShine", "at": [960, 620], "size": [360, 84], "color": "#FFD400", "radius": 42, "shader": "Shine", "shader_values": {"speed": 1.5, "color": "#FFFFFF66"}}}
{"name": "ui_set", "arguments": {"canvas": "Title", "name": "PlayButtonFocus", "shader": "Shine", "shader_values": {"color": "#FFFFFF80"}}}
```

**Translations.** The UI's texts can be shown in other languages, with no code. Each language has a table, `ui/locales/<code>.json`: `{"name": "Français", "strings": {"Play": "Jouer", "Score {score}": "Score : {score}"}}`. Its keys are text bodies exactly as written in Blender, bindings included, and a translation keeps the same bindings (in any order; `ui_translate` refuses one that drops or changes a binding). The language the texts are written in is `gamestudio.json` `locale` (default `"en"`), which has no table. The game starts in the player's language when a table matches it (else the source language), and remembers their choice.

1. `ui_strings` lists the texts to translate, each once with the elements that show it; with `locale`, also their translations so far.
2. `ui_translate {"locale": "fr", "name": "Français", "strings": {...}}` writes the table (texts left out are kept; `remove` drops some) and returns how many texts are still `missing`. The game's title (`project_name: true`) is never counted as missing: a name usually stays as it is. Translate it like any text when it should change. With a level open, `ui_strings` reads the export, which has no mark, and takes a text whose body is the game's name (`gamestudio.json` `name`) for the title.
3. A Language button needs no code: `{"kind": "button", "action": "locale", "label": "{ui.localeName}"}` cycles through the languages and shows the current one's name; `"target": "fr"` switches to one language. `{ui.locale}` is the current code.
4. Strings in code go through `ctx.ui.translate("Crashed!")` and are added to the tables with `ui_translate` like the others (`ui_strings` lists them as `unused`).

```json
{"name": "ui_strings", "arguments": {"locale": "fr"}}
→ {"source": "ui/ui.blend", "source_locale": "en", "locale": "fr", "name": null, "exists": false, "locales": [],
   "texts": [{"text": "Play", "elements": ["Title/PlayLabel", "Title/PlayFocusLabel"], "translation": null}, {"text": "Score {score}", "elements": ["HUD/Score"], "translation": null}, ...],
   "translated": 0, "missing": 9, "unused": {}}
{"name": "ui_translate", "arguments": {"locale": "fr", "name": "Français", "strings": {"Play": "Jouer", "Score {score}": "Score : {score}"}}}
→ {"locale": "fr", "name": "Français", "file": "ui/locales/fr.json", "created": true, "added": 2, "changed": 0, "removed": 0, "strings": 2, "missing": 7, "unused": 0, "exported": {"locales": ["fr"], "changed": true}, "sent": false}
{"name": "ui_add", "arguments": {"canvas": "Options", "kind": "button", "name": "Language", "label": "{ui.localeName}", "at": [960, 700], "action": "locale"}}
```

`ui_add {"kind": "text", "project_name": true}` marks the game's title: when the game is saved as a template (`save_template`), a game made from it shows its own name there.

**Transitions.** `ui_canvas`'s `transition` gives a canvas open and close animations. They are Blender keyframes on the canvas's top-level elements (position, scale and the object colour's alpha) with the timeline markers `open`, `close` and `loop`, so they can be refined by hand in Blender. `open` and `close` are each `fade`, `pop` (from 90 % of the size, with a fade), `slide-up`, `slide-down`, `slide-left`, `slide-right` (a tenth of the screen that way, with a fade) or `none`; `duration` is the seconds of each [0.25]; `loop` (`pulse`, `bob`, `blink`) plays over and over on every top-level element while the canvas is open. Keys left out keep the canvas's current presets (`pop`, `fade` on a canvas without), and `null` removes them (elements' own loops stay). Elements added, moved or resized later through the tools are keyed from their new place; positions are always where elements rest when the canvas is simply open.

**Element loops.** `ui_set {"loop": "pulse"}` (or `ui_add`'s `loop`) makes one element loop on its own while its canvas is open: `pulse` (scale to 104 %), `bob` (8 px up and down) or `blink` (opacity to 35 %), such as a Play button that pulses. Any element works, children too (they loop in their parent's space). It is keyed on that element only, over the canvas's loop range: a canvas without one gets it (and its `loop` marker), one cycle of the slowest element loop, between `open` and `close`, whose presets stay. Each element loop fits the whole cycles that round to its own length. On a top-level element it replaces the canvas's `transition` loop. `"none"` removes it, and the last one takes the loop range with it. A canvas whose `open`, `close` or `loop` markers were made by hand, without `transition`, refuses it: key the loop in Blender, or lay the markers out with `transition` first. Changing the loop range keys every animated element again, so a locked one refuses it.

```json
{"name": "ui_set", "arguments": {"canvas": "Title", "name": "PlayButton", "loop": "pulse"}}
→ {"canvas": "Title", "element": {"name": "PlayButton", "kind": "button", ..., "loop": "pulse"}, "parts": [...]}
```

```json
{"name": "ui_canvas", "arguments": {"name": "Pause", "pause_menu": true, "transition": {"open": "pop", "close": "fade"}}}
→ {"name": "Pause", ..., "animations": {"open": 0.25, "close": 0.25}, "transition": {"open": "pop", "close": "fade", "duration": 0.25}, "elements": [...]}
{"name": "ui_canvas", "arguments": {"name": "Title", "transition": {"open": "slide-up", "loop": "pulse"}}}
{"name": "ui_canvas", "arguments": {"name": "Title", "transition": null}}
```

An Options screen with a music slider and a subtitles toggle, hearts on the HUD and a 9-slice card:

```json
{"name": "ui_add", "arguments": {"canvas": "Options", "kind": "slider", "name": "MusicVolume", "at": [1100, 460], "value": "audio.music"}}
→ {"canvas": "Options", "created": true, "element": {"name": "MusicVolume", "kind": "slider", "value": "audio.music", "min": 0, "max": 1, "step": 0.1, "size": [360, 28], ...}, "parts": [...]}
{"name": "ui_add", "arguments": {"canvas": "Options", "kind": "toggle", "name": "Subtitles", "at": [1100, 580], "value": "save.subtitles"}}
{"name": "ui_add", "arguments": {"canvas": "HUD", "kind": "icons", "name": "Lives", "at": [140, 60], "value": "lives", "count": 3, "anchor": "top-left"}}
{"name": "ui_add", "arguments": {"canvas": "Pause", "kind": "panel", "name": "Card", "at": [960, 540], "size": [640, 520], "image": "assets/ui/panel.png", "slice": [24, 24, 24, 24]}}
{"name": "ui_set", "arguments": {"canvas": "Options", "name": "MusicVolume", "step": 0.05}}
```

`game_screenshot {"ui": "<Canvas>"}` opens a canvas in the headless game, as `ctx.ui.open` would, and takes the picture once it is drawn. Another menu that is open (a title screen) is closed first and its close animation waited for, so two menus never overlap; `ui_closed` in the result names it. The shot then waits until the canvas's open animation has ended (`ui.animating` no longer lists it; an older engine waits its open duration + 100 ms), so it shows the rest pose; `wait_ms` waits longer, into a `loop`. The canvas stays open: a menu pauses the game until it closes, and `stop` closes it. A name the game doesn't know is refused with the canvases it has (`unknown_canvas`). In mode `game`, the result's `ui` is the game's UI state: `screen` (the open menu), `focus`, `buttons` with their centres `x`/`y`, `size`, `open`, `texts` and `hidden`, as `playtest`'s `final.ui` reports them. It is read right after the shot, while the page still has the shot's `size` (a frame is drawn at that size first): `ui.size` is the image's size, and button `x`/`y` are pixels of the returned image.

`set: {"store": {...}, "save": {...}}` sets `ctx.store` and `ctx.save` values in the game before the canvas opens, to shoot screens that show them: a settings screen with a toggle on, a HUD with one heart left, a win screen with a record. `set.save` writes the real save of the headless game's page (its browser profile), which lasts until it is changed again, unlike a playtest's own save. Systems that set store values every frame overwrite them while the game runs.

```json
{"name": "game_screenshot", "arguments": {"ui": "Options", "set": {"save": {"subtitles": true}}}}
{"name": "game_screenshot", "arguments": {"ui": "HUD", "set": {"store": {"lives": 1, "coins": 7}}}}
→ {..., "ui_closed": "Title", "ui": {"screen": null, "open": ["HUD"], "texts": {"HUD/Coins": "x 7"}, ...}}   + the PNG: one heart of three
```

**Button coordinates.** A button's `x`/`y` (here and in `playtest`'s `final.ui.buttons`) are CSS pixels of the page's game canvas, whose size is `ui.size` [w, h]. In `game_screenshot` that is the screenshot's `size` (the page's own size without one), so `x`/`y` are pixels of the returned image. A scripted click lands on the button in a page of that size. For a playtest of another `size`, scale them: `x * size[0] / ui.size[0]` and `y * size[1] / ui.size[1]`. With `ui.size` [960, 540] and a playtest at the default [480, 270], halve them.

A title screen, a HUD and a check:

```json
{"name": "ui_open", "arguments": {"save": true}}
→ {"file": "ui/ui.blend", "canvases": ["Menu"]}
{"name": "ui_canvas", "arguments": {"name": "Title", "open": "firstStart"}}
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "text", "name": "TitleText", "body": "Gem Island", "at": [960, 300], "size": 120, "font": "assets/fonts/Baloo2-Bold.ttf", "outline": 6}}
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "group", "name": "TitleButtons", "at": [960, 675], "anchor": "center"}}
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "button", "name": "Play", "label": "Play", "at": [960, 620], "action": "close", "order": 1, "parent": "TitleButtons", "sound": "assets/sfx/click.wav"}}
{"name": "ui_add", "arguments": {"canvas": "Title", "kind": "button", "name": "Options", "label": "Options", "at": [960, 730], "action": "open", "target": "Options", "order": 2, "parent": "TitleButtons", "sound": "assets/sfx/click.wav"}}
{"name": "ui_canvas", "arguments": {"name": "Title", "focus": "Play"}}
{"name": "ui_canvas", "arguments": {"name": "HUD", "kind": "hud", "open": "start"}}
{"name": "ui_add", "arguments": {"canvas": "HUD", "kind": "text", "name": "Score", "body": "Score {score}", "at": [40, 40], "size": 44, "align": "left", "valign": "top", "anchor": "top-left"}}
{"name": "ui_add", "arguments": {"canvas": "HUD", "kind": "bar", "name": "Health", "at": [260, 100], "size": [400, 28], "value": "health", "max": 100}}
{"name": "ui_add", "arguments": {"canvas": "HUD", "kind": "text", "name": "Hint", "body": "Press E", "at": [960, 980], "radius": 8}}
→ isError: {"error": "\"radius\" does not go with kind \"text\": radius (panel, button, bar, slider).", "code": "invalid_arguments", "hint": "A text takes \"body\", \"size\", ... besides canvas, name, at, parent, anchor, stretch, z, visible and loop. Example: ..."}
{"name": "game_screenshot", "arguments": {"ui": "Title", "size": [960, 540]}}
→ {"url": "http://localhost:5173/", "opened_now": false, "size": [960, 540], "game": {"state": "paused", "scene": "main", "rev": 12},
   "ui": {"screen": "Title", "focus": "Play", "buttons": [{"label": "Play", "name": "Play", "canvas": "Title", "disabled": false, "x": 480, "y": 310, "width": 180, "height": 42}, ...],
          "size": [960, 540], "open": ["HUD", "Title"], "texts": {"HUD/Score": "Score 0", "Title/TitleText": "Gem Island"}}}   + the PNG
{"name": "playtest", "arguments": {"inputs": [{"at": 0.5, "key": "Enter"}], "duration": 1.5}}
→ {..., "screen": null, "final": {..., "ui": {"screen": null, "focus": null, "buttons": [], "size": [480, 270], "open": ["HUD"], "texts": {"HUD/Score": "Score 0"}}}}
{"name": "playtest", "arguments": {"inputs": [{"at": 0, "mouse": "click", "x": 240, "y": 155}], "duration": 1}}
→ the same final.ui: a click at t=0 presses Play like Enter; Play's [480, 310] at ui.size [960, 540], halved for the default [480, 270] page
```

### Game

| Tool | | Arguments | Notes |
|---|---|---|---|
| `export_scene` | W | `all?`, `reload_games?` (true), `verbose?`, `compress?`, `force?` (false) | Writes `.gamestudio/export/<scene>.glb` + `.scene.json` (docs/SPEC.md §4); connected games reload. It only does the work that changed since the last export, and `tier` says which: `reuse` (nothing changed: the files and `rev` stay), `layout` (only transforms, components or scene settings changed: only the manifest is rewritten), or `full`, with a `reason`. `force: true` always does a full export. An export can take up to 15 minutes before the call times out; after a timeout Blender is still exporting and stays busy until it finishes. `compress: true` also measures what `npm run build` ships. `size.compressed` lists each GLB before and after the build's meshopt geometry compression. This is a dry run: the export stays uncompressed, so development stays fast (docs/WORKFLOWS.md, "Smaller builds"). `all: true` exports **every level** for games that change level with `ctx.loadScene` (SPEC §4.7): the open file's scenes here, and every other `scenes/*.blend` in a background Blender, a few seconds each. Those files are only read, never changed or saved. It returns `scenes: [{file, scene, rev, entities}]` and `errors` per file, and each connected game reloads the level it shows. `rev` only ever goes up, past the last export on disk, even after `undo`, `restore_checkpoint` or opening an older copy (which bring back the scene's older counter). So a game or tool that waits for a revision never takes new content for old. From a level, `export_scene` and Blender's Play (`play_in_blender`) also export the UI files whose screens are missing, older than their .blend or made from unsaved changes (docs/GAME-UI.md §4); failures never stop them and come back in both results as `ui_errors: [{file, error}]`. |
| `play` | W | `where?`: `auto` / `headless` / `editor`, `export?` (true), `force?` (false), `size?` ([480, 270]) | Starts the dev server if needed (through Blender), exports, then loads and starts the scene in every game. `auto` uses the open games, or opens this server's headless game when none is open. `editor` uses Blender's own Play (the Game View, or a window the developer sees). `size` is the headless game's page size, kept until changed; a size for the open headless game resizes it. A game that already shows the exported revision is not reloaded. Keep the size small when headless play falls back to SwiftShader, which draws 800×450 at about 3–4 fps. |
| `stop` | W | none | Resets to the loaded scene and halts systems and physics. |
| `pause` | W | `paused?` (true) | `false` resumes. |
| `step` | W | `frames?` (1) | Advances paused games by N fixed steps. |
| `game_state` | R | `names?`, `ids?`, `component?`, `limit?` (50), `peer?`, `verbose?` | Live entities in runtime space: name, id, position and component values (`verbose` adds rotation and visibility). Animated entities also have `animation`: `{graph, state, clip, time, progress, params, playing}` under an animation graph, or `{clip, playing}` (API.md §15). Names that do not exist are listed in `missing`. |
| `game_logs` | R | `since?`, `level?`, `limit?` (200) | Console lines, uncaught errors (with `file`/`line`) and build errors. Pass the previous call's `next` as `since` to get only new lines. |
| `playtest` | W | see below | A scripted run on game time in a fresh headless page: deterministic, with screenshots and tracked entities. |
| `game_screenshot` | R | `wait_ms?`, `size?` (the game's size), `save?`, `mode?` (`game` / `scene`), `view?`, `export?` (true), `ui?`, `set?` | A picture of this server's headless game. It is opened on first use and then kept, and `play`, `stop` and live edits reach it. With `size`, the game is drawn at that size for the shot, then goes back to its own. `save: true` also writes the PNG under `.gamestudio/mcp/screenshots/`. `mode: "scene"` draws the level from a viewpoint without playing (see "Scene shots" below); `view` and `export` go with it. `ui: "<Canvas>"` opens that game UI canvas first, after closing another open menu and until its open animation has ended, and in mode `game` the result has `ui`, the UI state (see "Game UI" above). `set: {store?, save?}` sets `ctx.store` / `ctx.save` values in the game first (`save` writes that page's real save). |

```json
{"name": "game_state", "arguments": {"names": ["Player", "Coin1"]}}
→ {"game": {"peer": "p4", "state": "playing", "scene": "main", "rev": 3, "headless": true}, "space": "runtime (Y-up, metres)",
   "count": 2, "entities": [{"id": "gs_…", "name": "Player", "position": [0, 0.95, 9], "rotation": [0, 0, 0, 1], "visible": true, "components": {...}}, ...]}
```

#### Scene shots

`game_screenshot {"mode": "scene", "view": ...}` shows the level drawn by the game's own renderer from a viewpoint, without playing it. Use it to judge materials in the level, the way the Scene view (the Game toggle in a 3D View header) shows them to the developer. Game shaders, lighting, lightmaps, the sky, tone mapping and post effects look as they do in the game. No title screen, menu or HUD is drawn, and nothing runs: no systems, physics, animation or particles. Geometry that game code builds when it starts does not show. Game shaders' animation (water, sway) is held still, and objects selected in Blender are outlined.

- **`view`** is in Blender coordinates (Z-up, metres), like the camera of `compare_views`. `{"from": [x, y, z], "at": [x, y, z], "lens": 35}` poses a camera at `from` that looks at `at`, with Blender's Z up. `lens` is in millimetres on a 36 mm sensor that spans the wider side of the shot; it defaults to the scene camera's lens, else 35. `{"camera": "<name>"}` looks through a Blender camera instead. Its lens comes from Blender, and its pose from the export the game shows (parents and constraints included), or from Blender when the game doesn't have the camera.
- **Export.** It exports the open scene first, so the shot shows the latest edits (`tier: "reuse"` when nothing changed). `export: false` shows what the game already has.
- **How.** The headless game switches to scene mode for the shot (`view.mode`, SPEC §5.4). It gets the camera as `view.camera` matrices: the Blender pose turned into the game's Y-up space, `(x, y, z) → (x, z, −y)`, with the projection fitted to the shot's size. The tool waits for game shaders' modules and textures and a few frames, takes the shot, then puts the game back as it was. A game in game mode goes back to it, and one that was running plays again from the start (`restored.restarted`). A game that was already in scene mode keeps it and gets its own camera back.
- **Result.** `view` holds the pose and lens used, with `lens_from` or `pose_from`. `export`, `game` (scene and rev) and `restored` follow, plus any `shader_warnings` and `page_errors` the page logged during the shot.

```json
{"name": "game_screenshot", "arguments": {"mode": "scene", "view": {"from": [6, -7, 3], "at": [0, 0, 0.5]}, "size": [800, 450]}}
→ {"mode": "scene", "view": {"from": [6, -7, 3], "at": [0, 0, 0.5], "lens": 50, "lens_from": "scene camera"}, "size": [800, 450],
   "export": {"scene": "main", "rev": 12, "entities": 7, "tier": "reuse"}, "game": {"scene": "main", "rev": 12},
   "restored": {"view": "game", "state": "playing", "restarted": true}}   + the PNG
```

#### `playtest`

`playtest` opens the game in a **fresh** headless Edge or Chrome page (480×270 unless `size` says otherwise) and plays a scripted input timeline. Unless `export: false`, the scene is exported first.

It runs on **game time**. The page opens with the engine's test clock held (`window.__gs.test`, engine `API.md` §19), so nothing runs before the test. The engine then steps its fixed 60 Hz simulation as fast as the machine allows, and draws only the screenshots. All times are **game seconds**. The same script gives the same result on every run, at any page size and machine speed: in the gem-island dogfood, a 12 s route ran identically at 320×180, 480×270 and 800×450, in about 2 s of wall time. `realtime: true` plays on the wall clock instead (the old behaviour, which varies with speed).

| Argument | Meaning |
|---|---|
| `inputs` | The timeline; the shapes are below. |
| `duration` | Game seconds. Default: the last input (releases included) + 0.5. Max 120. Keys still held at the end are released. |
| `screenshots` | Times, e.g. `[0, 1, 2.5]`, or a count spread over the run, e.g. `4`. Default: `[0, duration]`. A shot at 0 is taken after the first step, once the camera follows. Each shot lists the tracked positions at that time. |
| `track` | Entity names (or ids) recorded every step. Default: the entities with `CharacterController`. A name with `*` or `?` is a **pattern**: `"Boulder*"` follows every entity whose name matches, spawned copies included (and authored ones such as `BoulderSpawn`, which have no `spawned_at_s`), each with its own id and `spawned_at_s` / `destroyed_at_s` (see below). A plain name follows one entity at a time; when several copies share it, the result warns and suggests the pattern. |
| `track_max` | Instances listed per pattern (default 20, max 100). `matched` still counts them all. |
| `sample_every` | Game seconds between position samples (min 0.05), e.g. `0.25`. Every tracked entity, pattern instances included, gets `samples: [[t, x, y, z], ...]` without any screenshots. The positions are in metres, rounded to 1 cm. A row appears only when the entity moved 1 cm or more since the previous row, and the last row always appears, so an entity standing still costs two rows. At most 200 per entity and 1000 in all: a longer run is sampled more coarsely, with a warning, and `sample_every_s` gives the step used. |
| `start` | `{position, yaw_deg?, rotation?, name?, space?}`, or a list of them: where an entity starts **for this run only**, to test a late part of a level without editing the scene. Default entity: the player (the first with `CharacterController`). `position` is runtime space unless `"space": "blender"`; `yaw_deg` turns about the up axis; `rotation` is a runtime quaternion `[x, y, z, w]`. A `Respawn`-style component that records its start at load sees the new position. |
| `size` | `[width, height]` of the page and the screenshots. Default `[480, 270]`. It does not change the results. |
| `seed` | Seeds the game's own random stream `ctx.random` and `Math.random` during the run (default 1), so games with randomness repeat too. |
| `scene` | The level to play, as last exported. Default: the level open in Blender, which the playtest exports first, so you test what you just changed. A level that was never exported is refused (`export_scene {"all": true}` exports them all). When the played level is not the one just exported, `warnings` says so. |
| `realtime` | `true`: wall-clock play, as before. The result then has `speed` and a warning when the game ran slower than real time. |
| `state` | `{names?, component?, limit?}`: final entity states to return in `final.entities` (with `animation` for animated entities, as in `game_state`). |
| `export` | `false` plays the last export. |
| `force` | Export with `force: true` first (a full export). |
| `save` | Also writes the screenshots to `.gamestudio/mcp/playtests/`. |
| `set` | `{store?, save?}`: the `ctx.store` and `ctx.save` values the run starts with (`game.test.begin({store, save})`, engine `API.md` §19), e.g. `{"save": {"bestTime": 60}, "store": {"lives": 1}}`. A run has a save of its own, empty unless `set.save` fills it, and never touches the player's. `final.save` is what it holds at the end, so persistence logic can be checked. Game time only: a `realtime` run ignores it, with a warning. |
| `verbose` | All logs (up to 100) and the tracked entities' full final components. |

Each input has `at` (game seconds) and a key or a mouse action:

```jsonc
{"at": 0,   "key": "KeyW", "hold": 2}            // down at 0, up at 2
{"at": 1,   "key": "Space"}                      // a press: down, then up one step (1/60 s) later
{"at": 1,   "key": "Space", "action": "down"}    // action: "down" | "up" | "press" (the default)
{"at": 1.5, "mouse": "move",  "x": 240, "y": 100}           // canvas pixels; default the centre
{"at": 1.5, "mouse": "move",  "dx": 30, "dy": 0}            // relative movement (mouse look)
{"at": 2,   "mouse": "click", "x": 240, "y": 135, "button": "left"}   // mouse: "move" | "down" | "up" | "click" | "wheel"
{"at": 2,   "mouse": "down", "hold": 0.5}         // hold also works for mouse buttons
{"at": 3,   "mouse": "wheel", "dy": 120}
```

**Closed-loop aim** for games aimed with the mouse cursor (top-down or twin-stick shooters):

```jsonc
{"at": 10, "mouse": "aim", "target": "Grunt*", "pick": "nearest", "until": 25}  // pointer on the nearest living Grunt, every step
{"at": 2,  "mouse": "move", "world": [4, 1, -6]}                                // pointer on a world point (runtime space)
```

- `aim` re-picks its target every step, among living entities whose names match `target` (a name or a pattern). `pick` is `"nearest"` to the player (the default) or `"first"`. It projects the centre of the target's mesh through the game's active camera, so `click` inputs then land on it.
- `until` is when to stop: by default the end of the run for `aim`, and one step for a `move` to `world`. `world` takes `space: "blender"` too.
- The result's `aims` gives, for each aim, the steps it aimed, the steps with no target in view, and the `last` target as `name#id`. Games that look with the mouse (pointer lock) read relative movement instead, so this is for cursor aim.

Keys are `KeyboardEvent.code` names: `KeyW`, `KeyA`, `Space`, `ArrowUp`, `ShiftLeft`, `Enter`, `Escape`, `Digit1`. Single letters and digits (`"w"`, `"1"`) and `space`, `up`, `down`, `left`, `right`, `shift`, `ctrl`, `alt`, `enter`, `esc`, `tab` also work.

Inputs reach the game as real DOM events, so systems see what a keyboard gives them. Menus (`ctx.ui` screens and the menus built in `ui/ui.blend`) take them too: `{"key": "ArrowDown"}` / `{"key": "ArrowUp"}` move the focus (arrow keys prefer the buttons in the same row or column) and `{"key": "Enter"}` presses the focused button, or click one where `final.ui.buttons[i]` says it is: `{"mouse": "click", "x": 240, "y": 120}`. A click works from the first step, like a key: `{"at": 0, "mouse": "click", "x": ..., "y": ...}` on a title screen's Play presses it just as `{"at": 0, "key": "Enter"}` does. Button `x`/`y` are CSS pixels of a page of `final.ui.size` [w, h]; in a playtest of another `size`, scale them by `size / ui.size` (see "Button coordinates" under "Game UI"). The engine latches presses: a one-step `press` (or a down and up in the same step) still reads as held for one fixed step, so a tapped Space jumps. Times are rounded to the nearest 1/60 s. A time over 120, such as `"hold": 2000`, is refused with a hint, because times used to be milliseconds.

A full call (the gem-island route, from the real start):

```json
{"name": "playtest", "arguments": {
  "inputs": [
    {"at": 0, "key": "KeyW", "hold": 2.3}, {"at": 0, "key": "KeyA", "hold": 0.9}, {"at": 0.9, "key": "KeyA", "hold": 0.92},
    {"at": 2.3, "key": "KeyW", "hold": 1.15}, {"at": 2.85, "key": "KeyD", "hold": 0.92},
    {"at": 3.8, "key": "KeyD", "hold": 3.6}, {"at": 3.8, "key": "KeyS", "hold": 2.9},
    {"at": 7.5, "key": "KeyA", "hold": 1.03}, {"at": 7.5, "key": "KeyW", "hold": 2.65}, {"at": 8.74, "key": "Space"}
  ],
  "duration": 12,
  "screenshots": [0.75, 9.4, 12],
  "track": ["Player", "Gem_1", "Mushroom"]
}}
```

To route past spawned hazards, follow every copy and sample positions instead of probing with screenshots:

```json
{"name": "playtest", "arguments": {"inputs": [...], "duration": 23.05, "track": ["Player", "Boulder*"], "sample_every": 0.5, "screenshots": 0}}
→ {..., "wall_s": 2.36, "sample_every_s": 0.5, "tracked": [
   {"name": "Player", "start": [0, 0.143, 32], "final": [0.384, 10.383, -29.402], ..., "samples": [[0, 0, 0.14, 32], [0.5, -1, 0.17, 32.02], [1, -2.11, 0.27, 30.67], ...]},
   {"pattern": "Boulder*", "matched": 11, "alive_at_end": 7, "instances": [
     {"id": "gs_c4e0ff3717df", "name": "BoulderSpawn", "start": [0, 11, -13], ...},
     {"id": "rt_1", "name": "Boulder", "spawned_at_s": 2.217, "destroyed_at_s": 16.2, "start": [2.333, 11, -13], "final": [0.313, 1.351, 34.303], ...,
      "path_m": 62.13, "moved_m": 48.319, "samples": [[2.5, 2.33, 10.61, -11.91], [3, 2.32, 10.44, -11.03], ...]}, ...]}]}
```

To test only the last part, start in front of the mushroom instead. This run bounces at 0.467 s, takes `Gem_5` at 1.3 s and stops on the ledge:

```json
{"name": "playtest", "arguments": {"start": {"position": [6.65, 0.15, -0.3]}, "inputs": [{"at": 0, "key": "KeyW", "hold": 1.5}, {"at": 0.1, "key": "Space"}], "duration": 3}}
```

The result is one JSON block followed by one image per screenshot (shortened):

```json
{"url": "http://localhost:5173/", "export": {"scene": "main", "rev": 9, "entities": 32},
 "mode": "game time", "game_time_s": 12, "steps": 720, "wall_s": 1.59, "speed": 7.53, "fps": 452,
 "simulate_ms": 303.8, "render_ms": 30.7, "load_ms": 2240, "size": [480, 270], "inputs": 19,
 "tracked": [
   {"name": "Player", "start": [0, 0, 6], "final": [6.632, 6.004, -8.904], "min": [-8.505, -0.006, -9.557], "max": [11.244, 6.848, 6],
    "path_m": 63.853, "moved_m": 17.383,
    "changes": {"GemHunt.gems": {"start": 0, "final": 5, "min": 0, "max": 5, "count": 5, "first_s": 0.75, "last_s": 9.933},
                "GemHunt.state": {"start": "playing", "final": "won", "count": 1, "first_s": 9.933, "last_s": 9.933}, ...}},
   {"name": "Gem_1", "start": [-4, 1, 2], "final": [-4, 1, 2], ..., "destroyed_at_s": 0.75},
   {"name": "Mushroom", ..., "changes": {"BouncePad.bounces": {"start": 0, "final": 1, "min": 0, "max": 1, "count": 1, "first_s": 9.1, "last_s": 9.1}}}],
 "final": {"state": "playing", "scene": "main", "entity_count": 27, "space": "runtime (Y-up, metres)"},
 "hud": "Gems 5 / 5\nTime 1:21\nAll gems found!\n80 s to spare\nPress R to play again",
 "logs": [{"level": "info", "msg": "[game] Mushroom launched Player at 15 m/s", "t": 9.1}, {"level": "info", "msg": "[game] Won with 80.1 s left", "t": 9.933}],
 "errors": 0,
 "screenshots": [{"index": 0, "time_s": 0.75, "positions": {"Player": [-3.404, 0, 3.059], "Mushroom": [6.5, 0.149, -4]}}, ...]}
```

- `scene` and `rev` are the level shown at the end; `scene_changes` counts the level changes the game made itself (`ctx.loadScene`, `ctx.restart`), which repeat with the run; `screen` is the menu screen open at the end (`null` when none).
- `final.ui` is the menu at the end: `{screen, focus, buttons: [{label, disabled, x, y, width, height}], size, open, texts, hidden}`. `x`/`y` are CSS pixels of the page's canvas, whose size is `size` [w, h]: a scripted click there lands on the button in a page of that size; scale them for another `size`. With game UI canvases (see "Game UI"), a button also has its element `name` and `canvas`, `open` lists the open canvases (and a DOM screen's name), and `texts` maps `"<Canvas>/<Element>"` to what each visible text of an open canvas says, bindings filled in: `{"HUD/Score": "Score 120"}`. `hud` (the page's text) includes those texts too. `hidden` lists, sorted, the `"<Canvas>/<Element>"` elements of the open canvases that are hidden, by code or by "Disable in Renders": after one fall, `final.ui.hidden` is `["HUD/Heart3"]`. `final.vfx` is `{emitters, particles}`, the particles alive at the end. `final.nav` (scenes with navigation) is `{ready, triangles, buildMs, agents}`; each `NavAgent`'s `remaining` (metres of path left, -1 without one) and `target` are in its components.
- `game_time_s` and `steps` are the game time simulated; `wall_s` the real time the run took (screenshots included); `speed` their ratio; `fps` simulated frames per wall second. `simulate_ms` and `render_ms` are the page's CPU time for stepping and drawing.
- `final.save` is what the run's own `ctx.save` holds at the end, by key, sorted (`game.test.saved({internal: true})`), the engine's own `gs.` keys included: `gs.locale`, the UI language a Language button chose, and `gs.audio`, the channel volumes the Options sliders set. So a settings screen's persistence can be checked: `{"bestTime": 42.5, "gs.audio": {...}, "gs.locale": "fr"}`. Start a run from a saved state with `set` (`{"set": {"save": {"bestTime": 60}}}`), then check that a faster finish wrote the new record.
- `tracked[]` has one entry per `track` item, in its order: the exact names first, then the patterns.
- A name's entry is `{name, start, final, min, max, path_m, moved_m, destroyed_at_s?, changes?, samples?}`: per-axis `min`/`max`, `path_m` (metres travelled), `moved_m` (start to final), `destroyed_at_s` (picked up, killed), and `changes`: every component field that changed, with its first and last change. `verbose` adds the final `components`. An entity that is never found has `{name, "found": false}`. With `sample_every`, each also has `samples`.
- A pattern's entry has **no `name`** of its own: it is `{pattern, matched, alive_at_end, listed?, instances: [...]}`, where `matched` counts every entity that matched, `alive_at_end` those still alive, and `listed` (only when `track_max` cut the list) how many `instances` there are. Each instance has `id`, `name`, `spawned_at_s` (left out for entities present at the start), `destroyed_at_s` (when it was destroyed), `start`, `final`, `min`, `max`, `path_m`, `moved_m` and `samples`; read a copy's name as `instances[i].name`. Instances report positions, not component changes. Screenshots list pattern instances alive at that time as `"<name>#<id>"`.
- `logs[].t` is the game time of each console line.
- `warnings` appear when something could make the run misleading: the scene reloaded during the run without the game asking (a re-export or hot reload; a level change the game makes is not one), the game was paused or loading for some steps, a tracked name does not exist or is shared by several copies, a pattern matched nothing, the samples were thinned, or the project's engine has no test clock (the run then falls back to wall-clock time). Patterns and `sample_every` need game time; a `realtime` run ignores them, with a warning.

### Docs

| Tool | | Arguments | Notes |
|---|---|---|---|
| `engine_docs` | R | `topic?` | The engine API reference (`@gamestudio/engine` `API.md`), the same lookup as the assistant's `get_engine_docs`. A topic matches section headings, then section text. Without a topic you get the section list. |

---

## A typical loop

```text
status                               → Blender not running
launch_blender {"background": true}  → pid 4120, scenes/main.blend
scene_summary
checkpoint {"label": "before the bridge level"}
create_from_kit {"kind": "platform", "location": [0, 6, 0], "name": "Bridge"}
set_transform {"name": "Bridge", "snap_to_ground": true}
validate_assets {}                               → scope "all: 31 ...", 0 errors
render_views {"target": "Bridge", "focus": true} → one labelled 2×2 sheet
add_component {"objects": "Bridge", "component": "Collider"}
(write src/systems/bridge-sway.ts with your own editor tools)
playtest {"inputs": [{"at": 0, "key": "KeyW", "hold": 3}], "screenshots": 3}
game_logs {"level": "warn"}
save
```

---

## Security

- **Local only.** The bridge binds 127.0.0.1, on a random port. Each Blender session generates a fresh 128-bit token, and the first request on every connection must present it (`hello`), compared in constant time. A wrong token is refused and the connection closed; any other first line (a line that isn't JSON, a web page's HTTP request, another method) gets one error and the connection is closed.
- **Who can connect.** The token is in `<project>/.gamestudio/blender.json` and in the extension's user folder. Anything running as you can read them, the same trust level as the dev server's `session.json`. Other users on the machine cannot read your profile. The game's dev server does not serve `.gamestudio/` (nor `.claude/`, `.codex/` or `.agents/`), so the game's page and other local web pages cannot read the tokens.
- **Trusted projects.** GameStudio runs a project's own code (its npm scripts and packages, its `tools/` scripts, the game's dev server) only once the project is trusted. Projects you create or copy, your recent projects and projects Blender would auto-run scripts for are trusted; any other project asks once (GameStudio › Trust This Project). A call from an agent never asks: it is refused with a message to pass on to you.
- **Turning it off.** Untick **Agent bridge (MCP)** in the add-on preferences, and the bridge stops and removes its discovery files. Blender never starts the bridge by itself in `--background`. A Blender launched by `launch_blender` starts it even when the preference is off, because it was started for the agent.
- **`run_python`** is full code execution in Blender (see above). In Claude Code, keep it out of the auto-approved tools so you see each call. The template's `.claude/settings.json` pre-approves read tools, checkpoints and play controls, not `run_python`, `delete_objects` or `restore_checkpoint`.
- **What the server touches.** Blender, the dev server's hub on 127.0.0.1, and headless browser pages on the dev server's `localhost` URL. Headless pages use a throw-away browser profile. Nothing goes to the internet.
- **Safety nets.** Locked (protected) objects stay locked to every tool, `run_python` included. **Pause** in the Agent panel refuses every change until you resume. Every scene edit is an undo step; every library write is backed up first (the last 20 per library) and `asset_library_restore` takes it back. `restore_checkpoint` checkpoints the current state first.

---

## For developers

**Bridge protocol.** Newline-delimited JSON-RPC 2.0 over TCP. The first request must be `{"jsonrpc": "2.0", "id": 1, "method": "hello", "params": {"token": "...", "client": "..."}}`, and its reply is `{bridge, blender, pid, background}`. Each tool maps to the bridge method of the same name, except:

- `play` uses `export_scene`, `devserver` and `play_in_blender`;
- `launch_blender`, the game tools and `engine_docs` never reach Blender.

`methods` lists the bridge methods. Error codes:

| Code | Meaning |
|---|---|
| -32700 | parse error |
| -32600 | invalid request |
| -32601 | unknown method (with a "Did you mean" hint) |
| -32602 | invalid params |
| -32603 | a bug in the bridge (with the traceback) |
| -32000 | a tool error (`data.hint` says how to fix it) |
| -32001 | unauthorized |
| -32002 | paused by the user: the person pressed **Pause** in Blender's Agent panel. Calls that change the scene or the open file are refused until they resume; calls that only read still work. The MCP tool error has `code: "paused_by_user"` and `hint: "the user paused the agent in Blender; ask them or wait"`, and `status` reports `blender.paused` (and `blender.locked_objects`) |

**Threading.** The sockets are non-blocking and polled with `selectors` from `bridge.pump()` on the main thread, so there are no threads.

- With a UI, a persistent `bpy.app.timers` callback calls `pump()` every 10 ms while a client is connected, and every 100 ms otherwise.
- In `--background`, `bridge.serve_forever(parent_pid=...)` loops. It also pumps the live link, live edits, play, the dev-server watcher and the registry, which rely on timers. It exits on `quit`, or when the MCP server that launched it exits.
- `runtime/packages/mcp/blender/bootstrap.py` is what `launch_blender` runs inside Blender (`--python`). It enables the extension for the session if needed and starts the bridge.

**Why no MCP SDK.** The server implements the protocol itself: stdio, newline-delimited JSON-RPC, `initialize` with version negotiation (2025-11-25 down to 2024-11-05), `ping`, `tools/list`, `tools/call`, cancellation and progress. A tools-only server needs nothing more. The package is linked into every game project, and keeping it free of dependencies (apart from `puppeteer-core` for headless play) avoids installs and version conflicts there.

**Tests.**

- `python tools/test_blender.py addon -- -k test_bridge` covers the bridge: framing, auth, main-thread execution, discovery files and every method.
- `npm test` runs `runtime/packages/mcp/test/`: the protocol, discovery, tool schemas and error paths against a fake bridge, crash and hang handling with stand-in processes, and the game tools against a real hub with a fake game.
- `.dev/mcp-e2e/` (local, gitignored) holds the end-to-end scripts used during development: a temp copy of the demo, a scratch profile, stock Blender, real crashes and hangs.
- `tools/e2e/e2e.mjs` drives the server against a real Blender and dev server.
