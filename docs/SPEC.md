# GameStudio: technical contracts (v1)

This file defines the formats and interfaces shared by the three parts of the engine. Each part has its own source tree, and all three must follow these contracts exactly. If one part needs a contract to change, update this file first.

| Part | Path | Language | License |
|---|---|---|---|
| Runtime + dev server + link hub | `runtime/` | TypeScript (Three.js, Rapier, Vite) | MIT |
| Blender add-on (editor) | `blender/gamestudio/` | Python 3.13 (Blender 5.2 LTS) | GPL-3.0-or-later |
| AI assistant (part of the add-on) | `blender/gamestudio/assistant/` | Python | GPL-3.0-or-later |

The runtime never imports Blender code. The add-on never imports runtime code. They share only files on disk (`components.json`, scene exports) and the link protocol. This keeps the runtime and shipped games outside the GPL (see `research/06-runtime-options.md` §13).

---

## 1. Game project layout

```
my-game/
  gamestudio.json          # project config (required; marks the project root)
  package.json             # depends on @gamestudio/engine and @gamestudio/devserver
  vite.config.ts           # plugins: [gamestudio()]
  index.html
  ASSISTANT.md             # optional: project conventions given to the AI assistant
  src/
    main.ts                # createGame(...)
    components/*.ts        # defineComponent(...) only: no DOM or Three.js side effects
    systems/*.ts           # defineSystem(...)
    shaders/*.ts           # defineShader(...): game shaders for Blender materials (§3, §6.5); no texture loads at module load
  scenes/*.blend           # authored in Blender: one level per file (§4.7)
  .gamestudio/             # generated; gitignored
    components.json        # component and game-shader registry (§3), written by the dev server
    session.json           # live dev-server session (§5.1)
    export/<scene>.glb
    export/<scene>.prefabs.glb     # prefab templates (§4.4), when the file has prefabs
    export/<scene>.scene.json
    export/files/**                # copies of files named by asset fields (§4.5)
    bake/<key>/                    # baked procedural materials (§4.6), reused by later exports
    bake/textures/                 # downscaled / WebP textures (§4.6)
    ai/audit.jsonl         # AI audit log
    ai/backups/            # backups made before AI file edits
```

`gamestudio.json`:

```json
{
  "name": "demo-game",
  "template": "starter",
  "engine": "0.1.0",
  "formatVersion": 1,
  "startScene": "main",
  "link": { "editorPort": 7710, "gamePort": 7711 },
  "export": { "maxTextureSize": 2048, "webp": false, "bake": "auto" },
  "compression": { "geometry": "meshopt", "textures": "webp" },
  "post": { "bloom": true, "vignette": true }
}
```

`export` is optional; §4.6 lists its keys. `compression` is optional too, and §4.6 describes it. `post` is optional: screen effects for every scene, with the fields of the `PostEffects` component (§6.3); the dev server passes it to the game (§7.4), and a scene's `PostEffects` component replaces it for that scene.

`template` is optional: the id of the project template New Project made the project from (the folder name in `blender/gamestudio/project_templates/` or in the user templates folder, e.g. `"shooter"`; see TEMPLATES.md), written once when the project is created. Open in Claude Code and Open in Codex add missing agent files (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.mcp.json`) from that template, composed with its base as New Project composes them; a project without it, or with a template this GameStudio does not have, gets the starter's. The same template's skills keep an existing project's `.claude/skills/` current when it opens (TEMPLATES.md, Instructions for coding agents). The runtime ignores it.

`engine` is optional: the `@gamestudio/engine` version New Project made the project with, written once when the project is created (Save as Template leaves it out). Nothing reads it yet; it is there so a later GameStudio can tell which engine a project was written for. The runtime ignores it.

The project root is the nearest ancestor directory of the `.blend` file that contains `gamestudio.json`.

---

## 2. Entity identity

- Every exported Blender object has a stable ID, stored as the ID custom property `obj["gs_id"]`.
- **Format:** `"gs_"` followed by 12 lowercase hex characters, for example `gs_3f2a9c01b7de`.
- **Assignment:** the ID is assigned when a component is first added, when the scene is exported, or by the "Assign IDs" operator.
- **Duplicates:** Blender's duplicate operators copy the property, so the add-on must detect and repair duplicated IDs. The object whose `obj["gs_id_owner"]` matches its current name keeps the ID, and every other object with that ID gets a new one. The add-on writes `obj["gs_id_owner"] = obj.name` whenever it assigns an ID. Renaming an object makes the owner check fail, but a rename alone never causes a duplicate, so only real copies get new IDs. In-session tracking by `session_uid` is preferred when available.
- **How the ID reaches the runtime:** custom properties reach the glTF file as node `extras` (`export_extras=True`), so Three.js exposes the ID as `object.userData.gs_id`. The runtime maps nodes to entities by this ID only. It never matches by name, because GLTFLoader rewrites names, for example turning `Cube.001` into `Cube001`.

---

## 3. Component registry: `.gamestudio/components.json`

The runtime defines components (§6.2) and game shaders (below) in TypeScript. The dev server writes the registry file at startup and again whenever `src/components/**` or `src/shaders/**` changes. The add-on reads this file and generates its UI from it.

```json
{
  "format": "gamestudio.registry",
  "formatVersion": 1,
  "generatedAt": "2026-10-09T12:00:00.000Z",
  "components": [
    {
      "name": "Spin",
      "description": "Rotates the entity continuously.",
      "category": "Demo",
      "builtin": false,
      "source": "src/components/spin.ts",
      "fields": [
        { "name": "speed", "type": "float", "default": 1.0, "min": 0, "max": 100, "description": "Radians per second" },
        { "name": "axis", "type": "vec3", "default": [0, 1, 0] }
      ]
    }
  ],
  "shaders": [
    {
      "name": "Swell",
      "description": "Waves that move with game time",
      "category": "Water",
      "builtin": false,
      "source": "src/shaders/swell.ts",
      "fields": [
        { "name": "height", "type": "float", "default": 0.15, "min": 0, "unit": "distance" }
      ]
    },
    {
      "name": "Triplanar",
      "description": "Photo scans projected in world space, ...",
      "category": "Surfaces",
      "builtin": true,
      "fields": [
        { "name": "diffuse", "type": "asset", "default": "", "extensions": [".webp", ".png", ".jpg", ".jpeg"], "description": "Colour scan (albedo). ..." },
        { "name": "tile", "type": "float", "default": 3.2, "min": 0.01, "unit": "distance", "description": "Metres per texture repeat" }
      ]
    }
  ]
}
```

### Field types

| `type` | JSON value | Blender property | Extra keys |
|---|---|---|---|
| `float` | number | `FloatProperty` | `min`, `max`, `step`, `unit` (`"none"`, `"distance"`, `"angle"`, `"time"`) |
| `int` | integer | `IntProperty` | `min`, `max` |
| `bool` | boolean | `BoolProperty` | |
| `string` | string | `StringProperty` | `multiline` (bool, UI hint only) |
| `enum` | string (one of `options`) | `EnumProperty` | `options: string[]` (required) |
| `vec2` | `[x, y]` | `FloatVectorProperty(size=2)` | `min`, `max` |
| `vec3` | `[x, y, z]` | `FloatVectorProperty(size=3)` | `min`, `max` |
| `color` | `[r, g, b, a]`, linear 0–1 | `FloatVectorProperty(size=4, subtype='COLOR')` | |
| `entity` | `gs_id` string or `null` | `PointerProperty(type=bpy.types.Object)` | |
| `asset` | project-relative path string | `StringProperty(subtype='FILE_PATH')` | `extensions: string[]` |

- Field names match `^[a-z][a-zA-Z0-9_]{0,40}$`.
- Component names match `^[A-Z][A-Za-z0-9]{0,40}$` and are unique.
- Vectors are in **runtime space**: Y-up, right-handed, metres. The add-on doesn't convert them; they are shown as authored.
- `description`, `category` and `builtin` are optional. `builtin: true` marks components that ship with the engine (§6.3).

### Game shaders

A game shader draws a Blender material in the game. It is defined in TypeScript with `defineShader(name, {description?, category?, fields, apply?, mesh?, load?, preview?})`, in `src/shaders/*.ts` (the files directly in the folder, not subfolders; the dev server option `shadersDir` moves it, as `componentsDir` moves `src/components`), or comes with the engine (the shader library, §6.5). A material names its shader and values in Blender, and the export lists it in the manifest's `materials` (§4.2).

- `shaders` is optional within `formatVersion` 1. The dev server always writes it (the library's shaders, plus the game's); readers treat a missing key as `[]`.
- An entry has the keys of a component entry: `name`, `description?`, `category?`, `builtin`, `source?` (the file that defines it) and `fields`, sorted by name. `preview` is not in the registry.
- **Library shaders** (`builtin: true`, no `source`) are registered whenever `@gamestudio/engine/schema` loads, so every registry lists them. Their categories are exactly `Water`, `Nature`, `Magic & Sci-fi`, `Surfaces`, `Transformations`, `Gameplay` and `Style`. A project file that defines a library shader's name overrides it: the later definition wins, and the entry has `builtin: false` and that file as `source`.
- Shader names follow the component name rule, in a namespace of their own: a shader and a component may share a name.
- Fields use the same JSON and types as component fields, except `entity`, which a shader cannot have.
- The dev server evaluates the component files first, then the shader files, and attributes each component and each shader to the file whose evaluation defined it. A file that fails to load is reported as an error (in the dev server's log and to editors).

---

## 4. Scene export

Exporting a scene from Blender produces two files in `<project>/.gamestudio/export/`, plus `<scene>.prefabs.glb` when the file has prefabs (§4.4) and copies of asset files under `files/` (§4.5).

### 4.1 `<scene>.glb`

Written by Blender's stock glTF exporter (`bpy.ops.export_scene.gltf`) with these settings:

- `export_format='GLB'`
- `export_extras=False`
- `export_yup=True`
- `export_apply=True` (modifiers applied)
- `export_cameras=True`
- `export_lights=True`
- `use_visible=False`
- `export_animations=True`
- `export_animation_mode='ACTIONS'`, `export_merge_animation='ACTION'` (the default), `export_anim_slide_to_zero=True`, `export_frame_range=False`, `export_force_sampling=True`, `export_anim_single_armature=False`, `export_skins=True`, `export_morph=True`, `export_morph_animation=True`

**Animation clips.** With these settings, Blender 5.2's exporter writes one glTF animation per action, named after the action. It writes:

- each object's active action;
- the action of every NLA track that has exactly one unmuted strip. This includes muted tracks, such as the `[Action Stash]` track that Blender's Stash button creates.

It skips:

- NLA tracks with several unmuted strips. The add-on logs a warning for these.
- actions that are on no object. This is why `export_anim_single_armature` is off: on its own, it would add every bone action in the file to a lone armature.

An action used by several objects becomes one animation with channels for all of them. Clips start at 0 s and are sampled at every frame of the scene's frame rate. Skins and shape-key animation are included. The exporter evaluates the scene at frame 0 for the rest pose and then restores the current frame.

**Authoring convention:**

- Store each clip as its own action, named after the clip.
- Give each action its own NLA track on the animated object; for a character, that is the armature.
- Keep clips in place.

**Armature clips are sampled by GameStudio** (`core/clips.py`). The exporter samples a clip by stepping the whole scene through its frames, re-evaluating every object (skinning included) for each frame of each action; on a level with a dozen rigged characters that is minutes. Instead, a glTF user-extension hook (`gather_actions_hook`) takes the clips of eligible armatures out of the exporter's run, and GameStudio reads their F-curves directly and splices the clips into the GLB. The user's data is not changed.
- **Eligible:** an armature whose pose bones have no enabled constraint with influence, without drivers on the object or its armature data, whose bones inherit rotation and scale fully and use local location, and whose clip animates only bone location, rotation (quaternion, Euler in any order, axis-angle) and scale. Everything else, and every clip of a non-armature object, stays with the exporter; the export result lists those armatures in `clips.stock` with the reason.
- **Same output:** one animation per action with channels for every armature that plays it, every bone's translation, rotation and scale sampled at every frame of the action's range, starting at 0 s, in the exporter's order. A constant channel keeps its first and last keys. Values match the exporter's within 1e-6, except that channels a clip does not key hold the rest pose (the exporter leaves the current pose in them for the clip that is already active).
- **Once per skeleton:** rigs that share armature data and a clip sample it once and share its accessors.
- **Cached:** in `.gamestudio/cache/clips/<key>.clip`, keyed by a hash of the clip's F-curves (keys, handles, interpolation, modifiers, mute, extrapolation), the frame range and frame rate and the skeleton (bone names, parents, rest matrices, connection, inheritance and rotation modes), plus the encoded bytes in memory for the session (up to 128 MB). Files unused for 30 days are deleted when new ones are written.

**Post-processing:** after export, the add-on rewrites the GLB's JSON chunk. The BIN chunk is copied unchanged and the JSON chunk is padded with spaces to a multiple of 4 bytes. The add-on sets `node.extras = {"gs_id": "<id>"}` on each node that represents a scene object. The Blender exporter names nodes exactly after the objects, so nodes are matched by name. The add-on does not descend into the children of collection-instance nodes, because those are instance contents, not entities.

We turn off `export_extras` because it would also dump every registered `bpy.props` value, including our component PropertyGroups and their ID pointers, into `extras` (verified in `research/04` §12). The manifest is the single carrier of component data.

The exporter must never change the user's file. Run it with the current scene active, and leave the selection unchanged.

**Prefab instances.** Collection instances of a prefab collection (§4.4) keep only their own node in `<scene>.glb`. The add-on removes the instance contents that the exporter wrote under them, along with the animation channels that targeted those contents. The runtime builds the instance from the template instead. Real children (objects parented to the instance) stay.

### 4.2 `<scene>.scene.json`

```json
{
  "format": "gamestudio.scene",
  "formatVersion": 2,
  "scene": "main",
  "rev": 7,
  "exportedAt": "2026-10-09T12:00:00.000Z",
  "blenderVersion": "5.2.2",
  "glb": "main.glb",
  "settings": {
    "gravity": [0, -9.81, 0],
    "background": [0.05, 0.05, 0.08],
    "activeCamera": "gs_3f2a9c01b7de",
    "sky": { "horizon": [0.52, 0.77, 0.89], "zenith": [0.04, 0.26, 0.71], "ground": [0.25, 0.32, 0.25] },
    "fog": { "color": [0.52, 0.77, 0.89], "near": 12, "far": 62 },
    "toneMapping": "neutral",
    "exposure": 0
  },
  "entities": [
    {
      "id": "gs_3f2a9c01b7de",
      "name": "Player",
      "type": "MESH",
      "parent": null,
      "components": {
        "RigidBody": { "type": "kinematic", "mass": 1 },
        "CharacterController": { "speed": 5, "jumpSpeed": 6 }
      }
    },
    {
      "id": "gs_8d1e44a0c3b2",
      "name": "HeroRig",
      "type": "ARMATURE",
      "parent": "gs_3f2a9c01b7de",
      "components": { "Animator": { "clip": "Idle", "loop": true, "speed": 1, "playOnStart": true, "fade": 0.25 } },
      "animations": ["Idle", "Walk"]
    },
    { "id": "gs_51c0d9e2aa17", "name": "Coin.001", "type": "EMPTY", "parent": null, "components": {}, "prefab": "Coin" }
  ],
  "animations": [
    { "name": "Idle", "duration": 2.0, "entities": ["gs_8d1e44a0c3b2"] },
    { "name": "Walk", "duration": 1.0, "entities": ["gs_8d1e44a0c3b2"] }
  ],
  "prefabs": [
    {
      "name": "Coin",
      "glb": "main.prefabs.glb",
      "offset": [0, 0, 0],
      "entities": [
        { "id": "gs_0c4f7a2b9e10", "name": "CoinBody", "type": "MESH", "parent": null,
          "components": { "Spin": { "speed": 2, "axis": [0, 1, 0] } } }
      ],
      "animations": []
    }
  ],
  "materials": {
    "GS_Sand": { "shader": "Triplanar", "values": { "diffuse": "assets/scans/sand_diff.webp", "tile": 3.2, "wet": 0.6 } }
  },
  "assets": {
    "assets/sfx/coin.ogg": "files/assets/sfx/coin.ogg?v=3f2a9c01b7",
    "assets/scans/sand_diff.webp": "files/assets/scans/sand_diff.webp?v=8c01d4e2f9"
  }
}
```

- `entities` lists every exported object: all objects in the scene, including ones without components. Each needs an `id`.
- `type` is Blender's `obj.type`: `MESH`, `EMPTY`, `CAMERA`, `LIGHT`, `CURVE`, and so on.
- `parent` is the parent's `gs_id`, or `null`.
- `components` maps component name to a full value object. Every field in the schema is present, with defaults filled in. Entity fields hold `gs_id` strings or `null`.
- `rev` increases by one on each export of the scene that writes files (a `reuse` export, which writes nothing, keeps it): it is one more than the larger of the scene's custom property `gs_rev` and the `rev` of the manifest already on disk, and is stored back into `gs_rev`. So it never repeats, even after an undo, a checkpoint restore or an export from another Blender process. The runtime appends `?rev=N` to the GLB URL to bust caches.
- `settings.gravity` comes from Blender's scene gravity, converted to Y-up. `settings.background` comes from the world color if it can be read, otherwise from the default.
- `settings.activeCamera` is the `gs_id` of `scene.camera`, or `null`.
- `settings.sky` (optional): the gradient of a world made by the `sky` kit, from `world["gs_sky_colors"]` (`horizon`, `zenith`, `ground` as sRGB `#RRGGBB`), converted to linear RGB. The runtime draws it as the background (ground below the horizon; horizon at the horizon; 55 % horizon + 45 % zenith at 0.2 of the way up, as the kit's colour ramp; the zenith overhead) and tints its hemisphere fill light with it. Absent: a flat `background`.
- `settings.fog` (optional): linear fog, present when the world's mist is on (`world.mist_settings.use_mist`, or `use_pass_mist` on the exported view layer). `near` is `mist_settings.start`, `far` is `start + depth` (metres); `color` is `world["gs_fog_color"]` (`#RRGGBB`), else the sky's horizon, else `background`.
- `settings.toneMapping` and `settings.exposure`: the scene's Color Management, which the game follows (§6.5) so that it tone-maps like Blender. `toneMapping` is `"neutral"`, `"agx"`, `"aces"` or `"none"`, from `scene.view_settings.view_transform`: Khronos PBR Neutral → `neutral`, AgX → `agx`, Standard → `none`, ACES 1.3 → `aces`. Transforms three.js does not have map with a warning: Filmic and Filmic Log → `aces` ("Filmic has no three.js equivalent; the game uses ACES"), ACES 2.0 → `aces`, any other (False Color, Raw) → `none`. A look other than None is not carried, with a warning. `exposure` is `view_settings.exposure`, in stops.
- `animations` lists the clips in `<scene>.glb`:
  - `name` is the action name.
  - `duration` is in seconds, from the largest sampler input.
  - `entities` lists the entities whose nodes the clip animates. A channel that targets a bone or another non-entity node counts for the nearest entity above it.
- `entity.animations` is present on animated entities and gives the names of their clips.
- `entity.prefab` is present on collection instances of a prefab and gives the prefab name (§4.4).
- `entity.drawn` is present, and `false`, on objects whose mesh the game does not draw but keeps: collision-only objects (a `Collider`, drawn as Wire or Bounds) and display-only objects that have components (§4.6). The runtime builds their colliders from the mesh and moves their meshes (and the non-entity nodes under them) to three.js layer 31, which cameras do not render. Child entities are drawn as usual. Template entities carry it too.
- `prefabs` lists the prefab templates (§4.4).
- `materials` (optional): the game shaders (§3) of the materials the GLBs draw with, keyed by the material's name exactly as written in the GLBs (the scene GLB and the prefab GLB; export copies, such as baked materials, keep the original name). Only materials that a mesh of an exported GLB uses and whose Blender material names a game shader (`Material.gs.shader`) are listed; the key is absent when there are none. `shader` is the shader's name and `values` holds every field, defaults filled in, as in `components`; `asset` fields hold project-relative paths, which `assets` maps (§4.5). A shader the registry does not define is listed with the values `{}`, and the export warns. When several exported materials share a name (a local and a linked one), the local one's settings are written (else the first library's), with a warning when the others' differ: the runtime binds by name (§6.5).
- `assets` maps asset paths, as written in the `asset` fields of components and game shaders, to the exported copies (§4.5). The URLs are relative to the manifest.
- `files` (optional) maps each GLB the manifest references (the scene GLB and the prefab GLBs) to the first 16 hex digits of its SHA-1. Runtimes key their caches on it: a GLB whose hash did not change is neither downloaded nor parsed again. The engine requests a hashed GLB as `<glb>?h=<hash>`, which the dev server serves with an immutable cache header once it has checked the hash against the file (and `X-Content-SHA1` with what it sent). Without `files`, GLBs are requested with `?rev=` and never cached.
- `entity.transform` (optional, scene entities only): `{ "p": [x, y, z], "r": [x, y, z, w], "s": [x, y, z] }`, local to the parent and Y-up, the same convention as a live `entity.transform` item (§4.3, §5.4). It is present on objects that moved after their GLB was written: a `layout` export (below) rewrites only the manifest and leaves the GLBs byte-identical. It wins over the node's transform in the GLB; live edits still apply on top.

**Compatibility.** `formatVersion` 2 adds `animations`, `prefabs`, `assets`, `entity.prefab` and `entity.animations`. `settings.sky` and `settings.fog` are optional additions within version 2: runtimes that do not know them draw the flat `background` without fog, and a runtime reading a manifest without them does the same. So is `entity.drawn`: an older runtime draws those meshes (collider meshes have no material, so they show in the default grey). `files` and `entity.transform` are optional additions too. A runtime without `files` downloads and parses every GLB as before. So are `materials`, `settings.toneMapping` and `settings.exposure`: an older runtime draws every material with its Blender look and tone-maps with ACES at exposure 0, and a runtime reading a manifest without them does the same. A runtime without `entity.transform` shows a moved object where its GLB put it until the next full export, so the add-on and the engine are updated together. A runtime reads version 1 files as having no clips list, no prefabs and no assets. It still plays any animations the GLB contains. Runtimes warn about versions newer than they know but load what they understand. Version 2 is a separate number because a version-1 runtime would show prefab instances empty: their contents moved to the template.
### 4.3 Coordinate conversion (Blender Z-up → runtime Y-up)

The glTF exporter handles the conversion for geometry. Live transform deltas (§5) must convert the same way:

- position `(x, y, z)` → `(x, z, -y)`
- scale `(x, y, z)` → `(x, z, y)`
- rotation quaternion `(w, x, y, z)` → `(w, x, z, -y)`, sent as `[x, y, z, w]` (Three.js order)
- Transforms are **local to the parent**, computed as `parent.matrix_world.inverted() @ obj.matrix_world`. This way Blender's `matrix_parent_inverse` never leaks into the runtime.
- **Cameras and lights:** the glTF exporter applies an extra correction to camera and light nodes, a rotation of −90° about X in local space. Live deltas must apply the same correction, so that the transform from a delta matches the exported node's transform exactly. An automated test checks this: delta transform equals GLB node transform within 1e-4.

### 4.4 Prefabs

- **Marker:** a collection is a prefab when it has the ID custom property `gs_prefab` with a true value. The add-on API is `export.is_prefab(collection)` and `export.set_prefab(collection, value=True)`. The prefab name is the collection name. When several marked collections share a name (a local and a linked "Crate", or two libraries), the local one (else the first library by path) keeps it and the others export as their full name, `Crate [props.blend]`, with a warning; each instance references its own collection's template.
- **Template export:** every prefab collection in the file is exported to `<scene>.prefabs.glb`, whether or not it is linked into the scene. The add-on does this with a second exporter run over a temporary scene that links the prefab collections, has the scene's frame rate and frame, and is deleted afterwards. The user's scenes, objects and collections are left untouched.
  - Template nodes get `extras.gs_id` like scene nodes. Template ids are checked for duplicates together with the scene's objects.
  - Contents of prefab instances nested inside a template are removed, as in the scene. The runtime builds them.
  - Objects whose parent is outside the collection, or that are disabled in viewports, are not exported. The add-on logs a warning for them.
- **`prefabs[]` entries:**
  - `name`.
  - `glb`: the file, relative to the manifest.
  - `offset`: the collection's `instance_offset`, Y-up.
  - `entities`: the template objects in the same shape as scene entities. `parent` is `null` for top-level objects. A nested prefab instance has `prefab`.
  - `animations`: the clips that animate template objects, with `entities` limited to the template.
- **Template space:** template nodes are in collection space. Top-level nodes have their collection transform; the offset is not applied.
- **Scene instances:** an instancer entity with `prefab` is built at load:
  - Its node transform already includes `-offset` (§7.4).
  - The runtime clones the template's top-level nodes as its children (`SkeletonUtils.clone`, so skins are rebound).
  - It creates one entity per template entity, with id `<instancer id>/<template id>`.
  - `parentId` is the mapped template parent, or the instancer for top-level objects.
  - Components are copied, and entity fields that hold template ids are mapped to the new ids.
- **Runtime spawns:** `ctx.spawn(name, { position, rotation, scale, parent })` does the same with `rt_<n>` ids.
  - With one top-level object, that object's clone is the root. `position`, `rotation` and `scale` replace its transform. Without them, the template transform minus the offset is kept.
  - With several top-level objects, a new group entity named after the prefab is the root. It gets the transform, and the clones under it are shifted by `-offset`.
- **Source objects in the scene:** objects of a prefab collection that is also linked into the scene's view layer are exported as ordinary scene entities as well. The game shows what Blender shows.

### 4.5 Asset files

`asset` fields hold project-relative paths, or absolute paths for files outside the project. On export, the add-on collects the non-empty asset values of every exported entity and template entity, then those of the game shaders in `materials` (§4.2), and copies the files:

- A file inside the project is copied to `export/files/<project-relative path>`.
- A file outside the project is copied to `export/files/external/<sha1(path)[:8]>-<name>`.
- Files whose size and modification time match the copy are not copied again.
- Missing files are logged and left out.

`assets` maps each value to its copy, as `files/...?v=<10 hex>`. The version changes with the source file's size and modification time. The runtime resolves an asset path through this map, relative to the manifest URL. For other paths, it falls back to `<base>/<path>` (the dev server serves the project root). URLs with a scheme and paths starting with `/` are used unchanged. `copyExports` puts the whole export folder, `files/` included, into builds.

### 4.6 Export fidelity

The game shows what the Blender viewport shows. On top of §4.1–4.5:

- **Objects** come from the view layer. The exporter also writes objects of excluded collections; their root nodes are taken out of the scene. Local and linked objects that share a name keep their own ids: the local one is renamed while the exporter runs, and its node gets the name back. A missing library file is an export error that names the file.
- **Evaluated geometry.** Modifiers are applied (§4.1). Geometry-nodes, particle and vertex/face instances are exported (`export_gn_mesh`, only when the scene has some). When an object with modifiers, or one that geometry nodes instance, has object-linked material slots, export gives it a temporary mesh copy so the exporter takes the materials Blender draws.
- **GPU instancing.** At least `instancing` (default 4) leaf nodes under one parent that share a mesh, are not entities, not animated, not skinned and not mirrored become one node with `EXT_mesh_gpu_instancing` (three.js: one `InstancedMesh`). A `Collider` on the parent counts each instance at its own transform (§6.3).
- **Only what the scene uses.** The exporter also writes nodes the scene does not reach: the objects of excluded collections (among them the sources of geometry-nodes instances, so their meshes would be written twice) and prefab instance contents once they are detached. After the steps above, both GLBs keep only the nodes reachable from the scene (plus the joints of kept skins), the animation channels that target them, and the skins, meshes, materials, textures, images, samplers, accessors and buffer views that something kept uses; the BIN chunk is repacked. A GLB that uses an extension the pruner does not follow (one that could point at nodes or accessors it cannot see) is left as it is.
- **Not drawn in Blender, not drawn in the game.** Boolean cutters that are hidden, not rendered or drawn as wire/bounds, hidden objects that geometry nodes instance, and objects both drawn as wire/bounds and not rendered keep their node and entity but lose their mesh. Objects with components keep the mesh, for what the components do with it, but are not drawn (`drawn: false`, §4.2). Other objects hidden in the viewport are exported and listed in a warning.
- **Collision-only objects.** A mesh object with an enabled `Collider` whose viewport display (Object Properties → Viewport Display → Display As) is **Wire** or **Bounds** is collision geometry: it keeps its node, entity and mesh, the manifest marks it `drawn: false`, and the runtime sizes its collider from the mesh without drawing it. This is the authoring rule for invisible walls, simple proxies around detailed models (put the proxy on the object with the `Collider` and the detailed mesh on a child without one) and trigger volumes. Their meshes, when nothing drawn shares them, are written as welded positions and triangles only, one primitive without a material, normals or UVs. Children keep their own display.
- **Procedural materials** (`core/bake.py`). A material is glTF-safe when its active output takes a Principled BSDF whose Base Color, Metallic, Roughness, Normal and Emission inputs are constants, image textures on a UV map (directly, through a UV Map/Mapping node, Separate Color or a tangent Normal Map) or a Color Attribute. Other Principled inputs (noise, Voronoi, ramps, mix/math, bump, images on generated coordinates, displacement) are baked with Cycles (EMIT through an Emission shader; tangent-space NORMAL); any other surface is baked approximately (diffuse colour, roughness, normal, emission). One image per channel per object, on the evaluated UV map when it lies in 0–1 without overlaps, else on a Smart UV Project of the evaluated mesh baked selected-to-active from the object, which then exports with that mesh. Size: `bakeDensity` px per metre of surface, a power of two up to `bakeSize`, metallic/roughness at half size, shrunk to fit `bakeBudgetMB`. Flat normal bakes are dropped. Bakes are cached in `.gamestudio/bake/<key>/` by a hash of the material node trees, the evaluated geometry, the UVs and the settings (plus the transform for position- or view-dependent nodes); entries unused for 30 days are deleted when new bakes are made. The user's materials, meshes, slots and modifiers are only swapped for the exporter run; export copies keep the original names in the GLB. Cycles is required: without it, export warns and writes flat colours, and Bake Materials fails with a clear error.
- **Mesh extraction is cached** across exports, in memory (512 MB, least recently used first), keyed by a hash of the mesh the exporter reads (attributes, corner normals, UV and colour layers, shape keys), the material info its faces use, the skin (joints, weights, matrices), the exporter settings it reads and the versions. Output is byte-identical to the uncached exporter. The cache wraps the exporter only for versions checked against it (Blender 5.2.2), and UDIM materials and extensions that hook attribute gathering are extracted as before; `GS_MESHCACHE=0` turns it off.
- **Cached bakes cost little.** The bake hash reads geometry as the exported view layer evaluates it (objects it does not draw are evaluated in a scene of their own), linked duplicates share their digests, and an export whose bakes are all cached starts no Cycles scene.
- **Textures.** Images larger than `maxTextureSize` are downscaled (aspect kept); with `webp`, images used only as colour maps (base colour, emission) are re-encoded as WebP and referenced through `EXT_texture_webp` (required); data maps stay lossless. Results are cached in `.gamestudio/bake/textures/` by a hash of the image bytes.

`gamestudio.json` → `export`:

| Key | Default | Meaning |
|---|---|---|
| `bake` | `"auto"` | `"auto"` bakes at export (cache first), `"cached"` only uses bakes made earlier (Bake Materials), `"off"` never bakes |
| `bakeSize` | 1024 | Largest baked texture, px |
| `bakeDensity` | 128 | Baked texels per metre of surface |
| `bakeSamples` | 4 | Cycles samples per bake |
| `bakeBudgetMB` | 64 | GPU memory (RGBA8 + mipmaps) for all baked textures of a scene |
| `maxTextureSize` | 2048 | Larger images are downscaled; 0 keeps them |
| `webp` | false | Colour maps as WebP |
| `webpQuality` | 90 | WebP quality |
| `instancing` | 4 | Repeats that become GPU instances; 0 turns it off |
| `warnSizeMB` | 32 | Warn when a scene's export (GLBs + asset files) is bigger |

`gamestudio.json` → `compression` (docs/WORKFLOWS.md, "Smaller builds"):

| Key | Default | Meaning |
|---|---|---|
| `geometry` | `"meshopt"` | `npm run build` (the dev server's Vite plugin, `compress.js`) rewrites the scene GLBs it copies into the build with `EXT_meshopt_compression` (required, with a fallback buffer that holds no data). The compression is lossless: it uses the ATTRIBUTES, TRIANGLES and INDICES modes with no filters, and every stream is decoded again to check it. Only buffer views change, and a view stays plain when encoding would not make it smaller both as it is and gzipped. Images, sparse data and views that another extension points at stay plain too. `"none"` turns it off. Exports for development are never compressed |
| `textures` | `"none"` | `"webp"`: the export writes colour maps as lossy WebP (`webpQuality`) and data maps as lossless WebP, in both cases only when the result is smaller (as `webp`, plus the data maps) |

### 4.7 Levels

A project has one level per `.blend` file in `<project>/scenes/`; `gamestudio.json` → `startScene` names the first one.

- **Export name:** a file in `<project>/scenes/` that holds a single scene (not counting GameStudio's temporary `gs_*` scenes) exports under the file's name: `scenes/level2.blend` → `level2.glb` / `level2.scene.json`, whatever its scene is called. Any other scene exports under its sanitized scene name, as before (a file with several scenes, or outside `scenes/`). The runtime loads levels by this name (`ctx.loadScene('level2')`).
- **Every level:** `export.export_all(bake=None)` exports each scene of the open file, then every other `scenes/*.blend` in a background Blender (`--background --factory-startup <file>`, importing this add-on from its own folder). The open file is not changed, and the other files are not saved, so their `gs_rev` stays behind; the revision rule above keeps their exports counting up. It returns `{"scenes": [{"file", "scene", "rev", "entities", "manifest", "warnings"}], "errors": [{"file", "error"}]}`; a file that fails to export is reported and the others still export.
- **Builds and the dev server:** the dev server logs levels that were never exported; `vite build` warns about each `scenes/*.blend` whose `<name>.scene.json` is missing or older than the file.

**Export result.** `export_scene()` also returns `size` (`bytes`, `glb`, `prefabs`, `files`, `textures`, `geometry`, `animation`, `triangles`, `draw_calls`, `texture_memory`, and `top`: the five largest textures and meshes; `triangles` and `draw_calls` count what the scene draws: nodes reachable from it, one draw call per primitive (two with a transparent double-sided material, which three.js draws back faces first), a GPU-instanced node once, collision-only objects left out; the game can draw fewer, as it skips what is off-screen, plus one for a sky), `bake` (`mode`, `baked`, `cached`, `units`, `fixed`, `seconds`), `textures` (re-encoded images), `instancing` (instanced groups), `instances` (instance count per instancer), `hidden_meshes`, `not_drawn` (objects exported with `drawn: false`), `pruned` (what was taken out of `<scene>.glb`: counts by kind and `bytes`) and `warnings`: every warning of the export (multi-strip NLA tracks, missing asset files, unbaked materials, downscaled textures, hidden objects, name collisions, size over budget). The Game tab and the Project panel show them. `clips` reports the armature clips: `fast` (rig-clip pairs GameStudio wrote), `sampled`, `cached`, `stock` (`[{object, reason}]` left to the exporter) and `seconds`.

### 4.8 Doing only what changed

Play, `playtest`, the MCP `export_scene` and the live link call `export_scene()`, which asks the export journal (`core/journal.py`) what changed since the scene's last export and does the least work that is still exact. The result says which in `tier`:

- **`reuse`:** nothing that reaches the export changed, and the files on disk are the ones that export wrote. Nothing is written; the result is the last one, with the same `rev`.
- **`layout`:** only object transforms, components (their enabled state included), the manifest-only scene settings (gravity, background, sky, fog, active camera, view transform and exposure), the game shader settings of materials (`Material.gs.shader` and the shader's values: the manifest's `materials`) or asset files changed. Only the manifest is written, with a new `rev`: the GLBs stay byte-identical (so `files` keeps its hashes and runtimes reuse their parses), and each moved entity gets `transform` (§4.2).
- **`full`:** everything else, with a short `reason`. The slow parts come from caches: clips (§4.1), bakes and textures (§4.6).

The journal decides conservatively; when it cannot be sure, the export is full. It records `depsgraph_update_post`, undo/redo and file loads, and after each export it keeps a snapshot of the exported objects (identity, parent, visibility, material slots, modifiers, components, the transform as exported, a digest of each evaluated mesh), the scene settings, a fingerprint of the export settings, component registry, format and versions, the files written, a digest of the keys of every action the export writes (actions on muted NLA tracks get no depsgraph update, so their keys are compared directly), and a digest of each material the exported objects draw with: its name, its own settings and its node tree, without its game shader settings. An update of any datablock other than objects, scenes and collections means full, except a material update: editing a game shader in the Properties editor sends one (with updates of the material's node tree and of the meshes that draw with it), so the material is checked against its digest instead, and when only its game shader settings changed the new manifest carries them. A geometry update of an object is checked against its digest, which also catches an object whose evaluated geometry follows another (a boolean cutter that moved). A move that a `transform` cannot carry (a prefab member, an instanced copy, a lightmapped or skinned object, a parent that is a bone or a vertex) means full. The first export of a session, or after undo, redo or a file load, is full. `export_scene(force=True)` (MCP: `force: true`) always exports in full; `export_scene_to()` always does.

### 4.9 Game UI files

Menus and HUDs authored in Blender live in UI files, `ui/*.blend` (one Blender scene per screen, a "canvas"), which are never levels. They export to `.gamestudio/export/ui/index.json` (the list of canvases) and `.gamestudio/export/ui/<Canvas>.ui.json` (one canvas: elements, layout, components), with their fonts and art copied into `export/files/` as in §4.5. The dev server serves them under `<base>__gs/export/ui/` like manifests (never cached), and builds copy them with the rest of the export folder. The formats, the export rules, the `ui.*` live-link messages and the runtime are specified in [docs/GAME-UI.md](GAME-UI.md).


---

## 5. Live link protocol (v1)

### 5.1 Topology

The **hub** runs inside the Vite dev server process, started by the `gamestudio()` Vite plugin. It accepts connections from two kinds of peer:

- **Editors and tools** connect over **TCP** to `127.0.0.1:<editorPort>` (default 7710). The framing is **NDJSON**: one UTF-8 JSON object per line.
- **Games** connect over **WebSocket** to `ws://127.0.0.1:<gamePort>` (default 7711), with one JSON object per text frame.

The hub binds to `127.0.0.1` only. It writes `.gamestudio/session.json` once it is listening:

```json
{ "pid": 1234, "url": "http://localhost:5173/", "editorPort": 7710, "gamePort": 7711, "token": "<32 hex>", "startedAt": "..." }
```

The token is random per server start. Games get it from the virtual module `virtual:gamestudio/session`. On a WebSocket upgrade, the hub rejects any `Origin` header that isn't a localhost or 127.0.0.1 origin.

### 5.2 Envelope and handshake

Every message is a JSON object with a string field `t` (the type):

- Requests carry `"id": "<unique string>"`.
- Responses carry `"re": "<request id>"`.
- `"to"` and `"from"` are peer IDs that the hub fills in when routing.

The client's first message must be a hello:

```json
{ "t": "hello", "role": "editor" | "game" | "tool", "token": "...", "protocol": 1, "client": "blender 5.2.2" }
```

If the token and protocol are valid, the hub replies:

```json
{ "t": "welcome", "protocol": 1, "peer": "p3", "peers": [{ "peer": "p1", "role": "game" }] }
```

Otherwise it replies `{ "t": "error", "code": "auth" | "protocol", "msg": "..." }` and closes the connection. Anything else as the first line, including a line that isn't JSON (a web page's `POST / HTTP/1.1`), also gets one error and closes the connection. Over WebSocket the role must be `game`: editors and tools connect over TCP. The hub sends `{ "t": "peers", "peers": [...] }` to everyone whenever a peer joins or leaves.

### 5.3 Routing rules (hub)

| Message from | Goes to |
|---|---|
| `editor` | every `game` peer, unless `to` is set |
| `game` | every `editor` and `tool` peer, unless `to` is set |
| `tool` | every `editor` peer, unless `to` is set |
| Any message with `to` | only that peer |

The hub stamps `from` on every routed message. Responses (`re`) are routed back to the requester, and only a peer the request went to can answer it. `tool.list` and `tool.call` are accepted from `tool` peers only (a game's page holds the hub token). The hub also sends some messages itself, such as `registry` and `peers`.

The hub routes by role only, never by type: new message types (the Scene view's `view.mode`, `view.camera` and `material.values`) pass through untouched. `view.mode` and `view.camera` concern one page, the one that draws the Scene view, so the editor sends them with `to` (every game peer would otherwise switch: a game in a browser window, a playtest's page).

### 5.4 Message catalogue

**Editor → game**

| `t` | Payload | Meaning |
|---|---|---|
| `scene.load` | `{ scene, url, rev }` | Load or reload a scene. `url` is the manifest URL, e.g. `/__gs/export/main.scene.json`. |
| `play.start` | `{}` | Run systems and physics. |
| `play.stop` | `{}` | Stop: reset to the loaded state and halt systems. |
| `play.pause` | `{ paused: bool }` | Freeze or unfreeze simulation time. |
| `play.step` | `{ frames?: int }` | Advance N fixed steps while paused. |
| `entity.transform` | `{ items: [{ id, p: [x,y,z], r: [x,y,z,w], s: [x,y,z] }], scene? }` | Live transform edit, Y-up, local to the parent. |
| `entity.components` | `{ id, components: { Name: {...} } }` | Replace the entity's full component set. |
| `entity.remove` | `{ ids: [..] }` | Remove entities. |
| `entity.visibility` | `{ id, visible }` | Toggle visibility. |
| `select` | `{ ids: [..], active?: id }` | Highlight the selection in the game (optional): bounding boxes in the game, outlines in Blender's selection colours in the Scene view (`active`, one of `ids`, lighter). Kept across scene loads and view changes; `ids: []` clears it. |
| `state.request` | `{ id, filter: { ids?: [..], component?: str, limit?: int } }` | Ask for live entity state. |
| `view.mode` | `{ mode: "scene" \| "game", animate?: bool }` or `{ mode: "ui", canvas, frame: [x, y, w, h], region?: [w, h], background?: "level" \| "checker", animate?: bool }` | Show the Scene view (§6.6): the level in its loaded, stopped state from the editor's camera, nothing simulating, menus and HUD hidden; or the game. A running game is stopped and reset first. `animate` (kept until changed; at first true) runs game shaders' clock with real time. `ui` is the Scene view of a UI file (docs/GAME-UI.md §11.2): stopped in the same way, the Game UI canvas `canvas` alone, at rest, laid out at `w × h` and drawn at `[x, y]`. `frame` is in px of `region`, y down; the editor sends its region's size, and the page maps the frame onto its own size. Without `region` it is in the page's CSS px. The canvas is drawn over the loaded level (`level`, the default) or a checkerboard. Sent again with a new frame, canvas or background as the editor's view changes. Ignored without `canvas` or with a frame that is not 4 finite numbers of positive size. Ignored while a test holds the clock. |
| `view.camera` | `{ view: [16], projection: [16], ortho: bool, near?, far? }` | The Scene view's camera. Column-major (three.js `Matrix4.fromArray` order), in the game's Y-up space. `view` is world → view (`matrixWorldInverse`); `projection` is Blender's `region_data.window_matrix` as is (OpenGL clip space, −Z forward). Blender converts its Z-up view matrix with `view @ R^T`, `R` mapping Blender to game world, `(x, y, z) → (x, z, −y)`. `near`/`far` default to the matrix's. Latest wins; kept across frames, resizes and scene loads; matrices that are not 16 finite numbers are ignored. |
| `ui.canvas` | `{ canvas, data }` | Game UI (docs/GAME-UI.md §4): adds or replaces a canvas (`data`: a whole `<Canvas>.ui.json`). An open canvas stays open, keeping its focused button. |
| `ui.remove` | `{ canvas }` | Closes the canvas and forgets it. |
| `ui.open` | `{ canvas }` | Opens a canvas as `ctx.ui.open(canvas)` does (Show in Game, MCP screenshots). |
| `ui.close` | `{ canvas? }` | `ctx.ui.close(canvas)`: without `canvas`, the open menu. The game is not started or unpaused unless the menu paused it. |
| `material.values` | `{ material, values: { field: value, ... }, scene? }` | Game-shader values of a material, at once: `ctx.shaders.set(material, values)` (§6.5). Numbers, vectors, colours and booleans change uniforms in place; strings and enums apply the shader again. Fields the bound shader lacks and materials the scene binds no shader to are ignored quietly. Kept across `play.stop` like other live edits; a `scene.load` drops them (the export has them). |

`entity.transform`, `entity.components`, `entity.remove`, `entity.visibility`, `select` and `material.values` may carry `scene`: the export name of the scene the edit is for. A game showing another scene ignores the message (after `ctx.loadScene`, two levels can share ids when one was saved as a copy of the other). Without `scene`, the edit applies to whatever the game shows.

**Game → editor and tools**

| `t` | Payload | Meaning |
|---|---|---|
| `game.ready` | `{ scene, rev }` | The scene finished loading (also after a scene change the game made with `ctx.loadScene`). |
| `game.status` | `{ state: "loading" \| "playing" \| "paused" \| "stopped", view: "game" \| "scene" \| "ui" }` | The game changed state or view (also sent on connect). In the Scene view (and the `ui` view) the state is `stopped`. `play.start` (or `play.step`) in the Scene view leaves it first: `{ state: "stopped", view: "game" }`, then `playing`. |
| `log` | `{ level: "debug" \| "info" \| "warn" \| "error", msg, source? }` | Captured `console.*` output. |
| `error` | `{ msg, stack?, file?, line?, col? }` | An uncaught error or rejected promise. |
| `stats` | `{ fps, frameMs, entities, bodies }` | Sent about once per second. |
| `state.response` | `{ re, entities: [{ id, name, position: [..], components: {...} }] }` | Reply to `state.request`. |

**Hub → peers**

| `t` | Payload | Meaning |
|---|---|---|
| `registry` | `{ registry: <components.json contents> }` | Sent to editors on connect and after each regeneration. |
| `peers` | `{ peers: [...] }` | The peer list changed. |
| `error` | `{ kind: "build", msg, file?, line?, col?, plugin?, frame? }` | Sent to editors and tools when a module fails to build (a TypeScript syntax error, an import that does not resolve, a component file that throws while the registry is generated). `file` is project-relative when inside the project; `line` and `col` are 1-based. It has no `code`, unlike the handshake errors. |
| `ping` / `pong` | `{}` | Keepalive. Either side may send `ping`. |

**Tool ↔ editor** (used by the MCP bridge; see §7.5)

| `t` | Payload | Meaning |
|---|---|---|
| `tool.list` | `{ id }` | Sent to the editor; the editor replies with `tool.list.result { re, tools: [...] }`. |
| `tool.call` | `{ id, name, input }` | The editor replies with `tool.result { re, ok, content }`. Write tools only stage proposals. |

Unknown message types must be ignored, not treated as errors.

### 5.5 Rates

- While the user drags in Blender, the editor sends at most **30 transform batches per second**, containing only the IDs that changed.
- Component edits are debounced to 100 ms.
- `view.camera` is sent only when the view changes, at most **60 per second**; `material.values` is debounced to about 30 ms.

---

## 6. Runtime API (`@gamestudio/engine`)

### 6.1 Entry

```ts
import { createGame } from '@gamestudio/engine';
import.meta.glob('./components/*.ts', { eager: true });
import.meta.glob('./systems/*.ts', { eager: true });
import.meta.glob('./shaders/*.ts', { eager: true });

createGame({ canvas: '#game', startScene: 'main' });
```

### 6.2 Components

`defineComponent` is exported from `@gamestudio/engine/schema`, a module with no DOM dependencies, so the dev server can load it in Node.

```ts
import { defineComponent, field } from '@gamestudio/engine/schema';

export const Spin = defineComponent('Spin', {
  description: 'Rotates the entity continuously.',
  category: 'Demo',
  fields: {
    speed: field.float(1, { min: 0, max: 100, description: 'Radians per second' }),
    axis: field.vec3([0, 1, 0]),
  },
});
```

- The `field` builders are `float`, `int`, `bool`, `string`, `enum(options, default)`, `vec2`, `vec3`, `color`, `entity()` and `asset(extensions?)`.
- Redefining a component with the same name replaces its schema. Hot module replacement relies on this.

### 6.3 Built-in components

These ship with the engine, and the registry marks them `builtin: true`.

| Component | Fields |
|---|---|
| `RigidBody` | `type` enum (dynamic, fixed, kinematic; default dynamic), `mass` float 1, `linearDamping` 0, `angularDamping` 0.05, `gravityScale` 1, `lockRotation` bool false, `ccd` bool false, `lockX` / `lockY` / `lockZ` bool false (keep that world coordinate; one lock makes a 2D body) |
| `Collider` | `shape` enum (auto, box, sphere, capsule, convex, mesh; default auto), `friction` 0.5, `restitution` 0, `sensor` bool false. `auto` picks box from the mesh bounds. `capsule` is upright along Y. Sizes come from the mesh's bounding box. GPU instances (an `InstancedMesh`) count one by one at their instance transforms: `auto`/`box` make one compound collider with a box per instance, `convex` a hull per instance, `mesh` one trimesh of all the copies (a dynamic body gets the hulls), `sphere` and `capsule` enclose them all. Drawn as Wire or Bounds in Blender, the object is collision-only (§4.6). |
| `CharacterController` | `speed` 5, `jumpSpeed` 6, `gravity` 20, `turnSpeed` 10, `plane` enum (none, side, top; default none) |
| `FollowCamera` | `target` entity, `offset` vec3 [0, 4, 8], `smoothing` 5, `lookAtOffset` vec3 [0, 1, 0] |
| `SideCamera` | `target` entity, `follow` enum (both, horizontal, vertical; default both), `offset` vec2 [0, 1], `deadZone` vec2 [1, 2], `lookAhead` 2, `smoothing` 5, `bounds` entity, `boundsMin` / `boundsMax` vec3 [0, 0, 0], `pixelSnap` false (API.md §27) |
| `Spin` | `speed` float 1, `axis` vec3 [0, 1, 0] |
| `Animator` | `clip` string '', `loop` bool true, `speed` float 1, `playOnStart` bool true, `fade` float 0.25 (s), `graph` string '' (an animation graph, API.md §15), `state` string '' (runtime: the graph's state) |
| `AudioSource` | `clip` asset (ogg, mp3, wav) '', `volume` 1, `loop` false, `autoplay` false, `spatial` true, `refDistance` 2, `maxDistance` 50, `rolloff` 1, `distanceModel` enum (inverse, linear, exponential; default inverse) |
| `ParticleEmitter` | `playOnStart` true, `loop` true, `duration` 1, `rate` 20, `burst` int 0, `maxParticles` int 200, `lifetime` vec2 [0.6, 1.2], `speed` vec2 [1, 2], `direction` vec3 [0, 1, 0], `spread` 0.4 (rad), `shape` enum (point, sphere, box, cone; default point), `radius` 0.5, `box` vec3 [1, 1, 1], `gravity` 0, `drag` 0, `size` vec2 [0.2, 0.05], `colorStart` [1, 1, 1, 1], `colorEnd` [1, 1, 1, 0], `brightness` 1, `texture` enum (dot, spark, star, smoke, square; default dot), `image` asset (png, jpg, jpeg, webp) '', `blending` enum (additive, alpha; default additive), `stretch` 0, `spin` 0, `space` enum (world, local; default world), `seed` int 0 |
| `PostEffects` | `bloom` false, `bloomStrength` 0.6, `bloomThreshold` 0.85, `bloomRadius` 0.4, `vignette` false, `vignetteAmount` 0.35, `exposure` 1, `contrast` 1, `saturation` 1, `tint` [1, 1, 1, 1] |

An object with a `Collider` but no `RigidBody` gets a fixed body. `Animator` plays its clip with a Three.js `AnimationMixer` rooted at the entity (only tracks that target the entity's subtree). `AudioSource` plays through Three.js `Audio`/`PositionalAudio` and an `AudioListener` on the active camera. Audio starts after the first user input on the page. Edits to both components apply to what is playing. `ParticleEmitter` emits camera-facing sprites from the entity (instanced, one draw call per emitter, simulated in the fixed steps with its own random stream); `PostEffects` on any object turns on that scene's bloom, vignette and grading. `runtime/packages/engine/API.md` §15–16 and §21–24 give the details.

### 6.4 Systems

```ts
import { defineSystem } from '@gamestudio/engine';
import { Spin } from '../components/spin';

export default defineSystem({
  name: 'SpinSystem',          // unique; redefining replaces (HMR)
  query: [Spin],               // entities that have all of these
  phase: 'update',             // 'fixed' | 'update' | 'late'  (default 'update')
  update(ctx, entities) {
    for (const e of entities) {
      const s = e.get(Spin);
      e.object3d.rotateOnAxis(new Vector3(...s.axis).normalize(), s.speed * ctx.dt);
    }
  },
});
```

The `ctx` argument has these members:

| Member | Contents |
|---|---|
| `dt`, `time`, `frame` | Timing |
| `world` | `query(...components)`, `get(id)`, `findByName(name)`, `descendants(entity, ...components)`, `spawn(...)` |
| `input` | `key(code)`, `keyDown(code)`, `keyUp(code)`, `mouse`, `axis(name)`, `gamepad` |
| `physics` | Rapier world access, `raycast`, `bodyOf(entity)`, `collisions` (this frame's start and end events) |
| `hud` | `set(key, text, options?)`, `bar(key, value, options?)`, `clear(key)` |
| `three` | `scene`, `camera`, `renderer` |
| `log` | Logging |
| `animation` | `play(entity, clip?, options?)`, `stop`, `isPlaying`, `current`, `clips`, `events` (clip end and loop events of this frame), `mixer` |
| `audio` | `play(entity \| clip, options?)`, `stop`, `isPlaying`, `preload`, `volume`, `muted`, `unlocked`, `listener` |
| `spawn(prefab, options?)`, `destroy(entity)`, `prefabs` | Prefab instancing (§4.4) |
| `after(seconds, fn)`, `every(seconds, fn)` | Game-time timers |
| `random` | Seeded gameplay random numbers (`ctx.random()`, `range`, `int`, `pick`, `chance`), independent of `Math.random` |
| `vfx` | Particle effects: `burst(entity \| point, preset \| options)`, `play`, `stop`, `emit`, `count` |
| `ui` | Menu screens that pause the game: `screen(name, options)`, `close`, `current`, `pauseMenu` |
| `loadScene(name, { fade?, color?, keep? })`, `restart()` | Level changes with a fade, carrying entities over; starting over |
| `store`, `save` | State that survives scene changes; values that survive page reloads |
| `post` | Screen effect settings and code overrides |
| `shaders` | Live game-shader values: `set(entity \| materialName, values)`, `get(entity \| materialName)` (§6.5) |

An entity handle `e` has these members:

| Member | Contents |
|---|---|
| `id`, `name` | Identity |
| `object3d` | The Three.js object |
| `get(C)`, `has(C)`, `add(C, values?)`, `remove(C)` | Component access |
| `destroy()` | Removes the entity |

### 6.5 Game shaders and tone mapping

A game shader (§3) draws the Blender materials that name it in the manifest's `materials` (§4.2).

```ts
import { defineShader, field, patchShader } from '@gamestudio/engine';

export const Swell = defineShader('Swell', {
  description: 'Waves that move with game time',
  category: 'Water',
  fields: {
    height: field.float(0.15, { min: 0, unit: 'distance' }),
    scan: field.asset(['.webp', '.png'], { colorSpace: 'srgb' }),
  },
  apply(material, values, ctx) {   // once per bound material instance; patch it, or return a replacement
    patchShader(material, 'swell', (shader) => {
      shader.uniforms.uTime = ctx.time;               // the shared clock
      shader.uniforms.uHeight = ctx.uniforms.height;  // this instance's live value
      /* edit shader.vertexShader, fragmentShader */
    });
  },
  mesh(mesh, values, ctx) {        // optional: once per mesh that draws the result
    mesh.castShadow = false;
  },
});
```

- `defineShader`, `getShader`, `listShaders`, `onShaderDefined`, `loadShader` and `patchShader` are exported from `@gamestudio/engine` and from `@gamestudio/engine/schema`. Names and fields follow §6.2 in a namespace of their own; `entity` fields throw. Redefinition replaces the shader, and a running game applies the new one to its bound materials without reloading the scene.
- `category?` groups shaders in Blender. `preview?: { mesh?, values?, t?, camera? }` is for the shader gallery (`tools/shader-gallery`) only. `load?: () => Promise<{ apply?, mesh? }>` replaces inline `apply`/`mesh` (giving both throws): `() => import('./impl')` puts the code in a chunk of its own that a game downloads only when a scene names the shader. A `load` runs once per definition.
- **Binding**, on every scene load: the engine first loads, in parallel with the GLBs, the `load` modules of the shaders the manifest names and the textures of their `asset` values; the load waits for them. Then, after the lightmaps, on the scene root and on every prefab template root before any prefab is instanced (instances share the templates' materials), each `materials` entry binds the materials whose `name` equals its key. `apply(material, values, ctx)` runs once per distinct material instance (lightmap variants are distinct), on a clone of it; the result (the patched clone, or the material `apply` returns) replaces it on every mesh that drew it. Then `mesh(mesh, values, ctx)` runs once per such mesh. `values` are coerced to the shader's fields with defaults filled, as component values are. The loaded materials are never changed, so a reload never patches twice. A `load` module that fails is reported like a throwing `apply`.
- `ctx` is `{ material, renderer, time, uniforms, white, assetUrl(path), warn(message) }`. `material` is the manifest name; `time` is one uniform object `{ value }` shared by every shader, equal to the systems' `ctx.time` and updated every frame. `uniforms` has one `{ value }` per field of this material instance: float/int → number, bool → boolean, vec2/vec3 → `Vector2`/`Vector3`, color → `Vector4` (linear RGBA), asset → the loaded `Texture` (`null` while loading or when empty); string and enum fields have none (they choose GLSL, so they belong in the patch key). `white` is a 1×1 white texture for unloaded samplers. Asset textures load through `assetUrl` with repeat wrapping, glTF orientation (`flipY` false) and mipmaps, in sRGB when the field says `colorSpace: 'srgb'` (else linear), once per file.
- **Live values** (`ctx.shaders` in systems): `set(target, values)` and `get(target)`. A material name targets its shared instances: their uniforms change in place. An entity targets its own meshes (not child entities'): the first `set` gives it its own instances of the materials they draw (a fresh `apply` on a clone of the loaded material, with its own uniforms; the GLSL and program key are the same, so the compiled program is shared), later ones change those uniforms in place. An entity's instance follows material-wide `set`s for the fields it did not set itself, is applied again on redefinition, and is disposed when the entity is destroyed. A string or enum change applies the shader again (a new program). Unknown fields, values that do not fit, unknown material names and entities without shaded materials are warned about once per load and ignored. `get` returns the values in effect (merged over an entity's materials), or `null`.
- **Library:** `runtime/packages/engine/src/shaders/library/<kebab-name>/meta.ts` defines the shader (fields, `category`, `preview`, `load: () => import('./impl')`, DOM-free) and `impl.ts` exports `apply` and optional `mesh`. `library/index.ts` registers every `*/meta.ts` with an eager `import.meta.glob`; the schema module imports it after the built-in components.
- `patchShader(material, key, edit)` chains `edit` onto `onBeforeCompile` (the previous one runs first) and appends `|key` to `customProgramCacheKey`; a key the material already has is a no-op. A patched material's `clone()` keeps its patches. The lightmap patch uses it (key `gs_lightmap`). Patches are WebGL only: the WebGPU renderer ignores `onBeforeCompile`.
- Warnings, each once per load, through the engine's warning log (`[gamestudio] ...`): `Material "<m>" uses game shader "<S>", which no src/shaders file defines` (the material is left as is); `Material "<m>" has game shader "<S>" but no mesh in the scene or its prefabs draws it`; `Game shader "<S>" failed on material "<m>": <error>` (`apply` or `mesh` threw; that material keeps its Blender look).
- **Tone mapping:** on every scene load the renderer (WebGL or WebGPU) takes `settings.toneMapping`: `neutral` → `NeutralToneMapping`, `agx` → `AgXToneMapping`, `aces` → `ACESFilmicToneMapping`, `none` → `NoToneMapping`. `toneMappingExposure` is `2 ** settings.exposure` times the `PostEffects` exposure. A manifest without these keys gets ACES and exposure 0, as before.

### 6.6 The Scene view

The page can show the level the way the editor sees it, drawn by the game's renderer: the **Scene view**. `?gs_view=scene` in the page URL starts the game in it (createGame's `view: 'scene'`; no autoplay, so no title screen); `view.mode` (§5.4) and `game.setView('scene' | 'game', { animate? })` switch. The tab remembers the view it is in (sessionStorage `gs_view`), and that wins over the URL on a reload: a page Vite reloads during Play plays again, one reloaded in the Scene view stays in it.

- **State:** the game is `stopped`, in its loaded state plus live edits. Systems, physics, animation mixers, particles, timers and sound do not run; `ctx.time` stays 0. A load ends stopped. `play()` (`play.start`) and `step()` leave it, as Play from a stopped game: systems start, title screens open, the game's camera draws. `stop()` ends in the game view; the editor sends `view.mode` again.
- **Camera:** the editor's (`view.camera`, `game.setViewCamera`): a `PerspectiveCamera`, or an `OrthographicCamera` for `ortho`, placed by the inverse of `view`, whose projection is the given matrix (also when something calls `updateProjectionMatrix()`; mapped to 0..1 depth for WebGPU). `near`, `far`, `fov`/`aspect` or the orthographic extents are read off the matrix for code that reads them. Resizes fit the game's camera only. Shadows fit the editor's camera. Until the first `view.camera`, the game's camera draws. In an orthographic view the gradient sky is one colour, the sky's in the view direction (as in Blender). `game.viewCamera` is the camera that draws.
- **Shader clock:** game shaders' `ctx.time` advances with real time (steps capped at 0.1 s; under a held test clock, by the test's steps) while `animate` is on, and keeps its value across scene loads; it is game time again in the game view.
- **Overlays:** `ctx.ui` screens, the HUD and the fade are hidden (`visibility: hidden`), and so are Game UI canvases (not drawn; their DOM mirror hidden); the dev error overlay stays.
- **The `ui` view** (`view.mode` `ui`; `game.setView('ui', { canvas, frame, region?, background?, animate? })`; docs/GAME-UI.md §11.2 has the details): the Scene view of a UI file. The state is the same: `stopped`, nothing simulating, a running game stopped and reset first, the shader clock as above. The page shows one Game UI canvas as the game draws it:
  - **Placement:** the game's `<canvas>` element moves to the frame (`position: fixed`), mapped from `region` onto the page. It is sized `w × h` CSS px and scaled by the page's size over the region's. The renderer, the canvas layout (its scale mode) and the game's camera therefore see a game of `w × h`.
  - **Background:** the loaded level from the game's camera, with the frame's aspect and post effects, under a menu's backdrop. A checkerboard instead with `background: "checker"`, or while no level is loaded.
  - **The canvas:** only `canvas`. A menu is drawn over its backdrop with its first focus shown; a HUD without one.
  - **Rest pose:** an `open` animation is skipped, and a `loop` runs on the UI clock.
  - **Placeholders:** bindings without a value render as written (`{score}`, `{time:m:ss}`).
  - **Input:** no input, no pause, and nothing opens (`ctx.ui` and `state().ui` see nothing open).
  - **Live edits:** `ui.canvas` updates the canvas. A new canvas name fetches `ui/index.json` again.
  - **Leaving:** the selection is hidden meanwhile. `play()`/`step()` leave the view as they leave the Scene view, and `setView('scene' | 'game')` clears it, putting the canvas element's own style back. `game.view` is `'ui'`, and `game.uiPreview` holds what it shows.
- **Selection:** `select` draws an inverted hull per mesh the entity draws itself (children that follow skinning, morphs and GPU instances), pushed out on screen by 2 px times the pixel ratio, in `#ED5700` (selected) and `#FFAA40` (active), without tone mapping or fog. The draws carry `userData.gsShaderPass` (physics shapes and navmeshes skip them), cast no shadow and take no ray. An open flat mesh seen from its front gets no outline. In the game view the selection is bounding boxes, as before.
- `window.__gs` gains `view` and `message(msg)`: it handles an editor message as the live link would and returns the replies (tools that drive a page without the link, such as a headless screenshot).
- `game.shadersReady()` resolves once queued scene loads are done, every bound game shader's `load` module has loaded and been applied, and every asset texture they asked for has loaded or failed (with its warning). Never rejects; a tool waits for it (with a timeout of its own) before a screenshot.

---

## 7. Blender add-on internal API (Python)

The core add-on modules expose these functions. The assistant uses only these and plain `bpy`.

```python
# gamestudio/core/project.py
def find_project_root(blend_path: str | None = None) -> pathlib.Path | None
def project_root() -> pathlib.Path | None            # for the current .blend; None if unsaved or no gamestudio.json
def gs_dir(root) -> pathlib.Path                     # root / ".gamestudio"

# gamestudio/core/ids.py
def get_id(obj) -> str | None
def ensure_id(obj) -> str
def find_by_id(gs_id: str) -> bpy.types.Object | None
def repair_duplicate_ids(scene=None) -> int          # returns the number of IDs reassigned

# gamestudio/core/registry.py
def reload_registry(root=None) -> bool               # reads components.json; (re)generates PropertyGroups
def set_registry(data: dict) -> None                 # from a link `registry` message
def schemas() -> list[dict]                          # component entries from §3
def get_schema(name: str) -> dict | None
def shader_schemas() -> list[dict]                   # game shader entries (§3 `shaders`; [] when missing)
def get_shader_schema(name: str) -> dict | None
def shader_names() -> list[str]

# gamestudio/core/shaders.py (§7.2): the game shader of a material
def get_shader(mat) -> str | None                    # mat.gs.shader; may name a shader the registry lacks
def set_shader(mat, name: str | None, values: dict | None = None) -> list[str]
                                                     # None or "" clears; validates, then applies; returns warnings.
                                                     # Unknown shader: ValueError listing the known ones, except when
                                                     # the registry has no shaders (the name is stored, one warning)
def get_values(mat, for_export=False) -> dict        # every field, defaults included; {} without a known shader;
                                                     # asset fields project-relative when for_export=True
def set_values(mat, values: dict) -> None            # for the material's shader; raises ValueError
def shader_materials(scene=None) -> list             # materials with a shader in the slots of the scene's objects,
                                                     # their collection instances (nested) and the prefab collections

# gamestudio/core/components.py
def list_components(obj) -> list[str]                # attached component names, in order
def get_values(obj, name, for_export=False) -> dict  # JSON-compatible; entity fields give the object name,
                                                     # or the gs_id when for_export=True
def add_component(obj, name, values: dict | None = None) -> None   # raises ValueError if unknown or invalid
def remove_component(obj, name) -> None
def set_field(obj, name, field, value) -> None       # validates and coerces; raises ValueError
def coerce_value(field_schema: dict, value) -> object  # raises ValueError with a helpful message

# gamestudio/core/export.py
def export_scene(scene=None, bake=None) -> dict      # writes the glbs and manifest; returns {"glb": path, "manifest": path, "rev": n,
                                                     #   "scene", "entities", "repaired", "prefabs": [names], "animations": n, "assets": n}
def export_name(scene) -> str                        # the export name (§4.7): the level file's name, else the scene's
def level_files(root=None) -> list                   # <project>/scenes/*.blend
def export_all(bake=None, blender=None) -> dict      # every level (§4.7): {"scenes": [...], "errors": [...]}
def export_file(path, bake=None, blender=None, timeout=600.0) -> dict   # one level file, in a background Blender
def is_prefab(collection) -> bool                    # collection["gs_prefab"] is true (§4.4)
def set_prefab(collection, value: bool = True) -> None
def prefab_collections() -> list                     # every marked collection, local ones first
def prefab_names(prefabs) -> dict                    # collection.as_pointer() -> template name (§4.4)
def object_clips(obj) -> list                        # clip names the export writes for obj and its children
def collision_only(objs) -> list                     # objects with a Collider drawn as Wire/Bounds: drawn: false (§4.6)

# gamestudio/core/fidelity.py (§4.6): GLB post-processing
def instance_meshes(glb, min_count) -> list          # repeated leaf meshes -> EXT_mesh_gpu_instancing
def collision_meshes(glb, hidden_ids) -> list        # meshes of drawn: false entities -> welded positions only
def prune(glb) -> dict                               # drop what the scene does not reach; repack the BIN chunk
def size_report(glb, label, hidden_ids=()) -> dict   # bytes by kind, triangles and draw calls of what is drawn

# gamestudio/core/bake.py (§4.6)
def analyze_material(mat) -> dict                    # {"status": "safe" | "bake", "surface", "channels", "notes", ...}
def bake_objects(objects, scene=None, cache_dir=None, settings=None, force=False) -> dict   # bake now into the cache
def cycles_available() -> bool

# gamestudio/core/compare.py: Blender's view vs the game's (the MCP tool compare_views)
def render_view(camera=None, scene=None, size=(800, 450), engine="eevee", samples=16) -> dict   # {"png", "camera_id", ...}
def game_request(camera=None, scene=None, size=(800, 450)) -> dict   # what the game screenshot must show
def compare_images(blender_png, game_png) -> dict    # mean_abs_diff, normalized_diff, detail_ratio, verdict, PNGs

# gamestudio/core/sceneview.py (§7.3): the Scene view, a 3D View drawn by the game's renderer
def enable(window, screen, area, scene=None) -> str  # on in that 3D View (off wherever else); "" or why not
def disable() -> None                                # off: the area's settings back; the page stays for Play
def toggle(window, screen, area, scene=None) -> str
def is_on() -> bool
def status() -> tuple[str, str]                      # (text, "info" | "playing" | "error"); ("", "") while the frame shows
def camera_message(view_matrix, projection) -> dict  # the view.camera message for Blender's view and window matrices
def scene_url(url) -> str                            # the game's address with ?gs_view=scene
def pump() -> None                                   # a timer calls it while on; tests call it

# gamestudio/core/link.py
def get_link() -> "LinkClient | None"
class LinkClient:
    connected: bool
    def send(self, msg: dict) -> None                # thread-safe; non-blocking
    def request(self, msg: dict, callback, timeout=5.0) -> None   # callback(response | None) runs on the main thread
    logs: collections.deque                          # last 500 `log`/`error` messages from games: {"t", "level", "msg", ...}
    def on(self, t: str, handler) -> None            # handler(msg) runs on the main thread
```

Threading rule: only the main thread touches `bpy`.

- **Live link:** the socket is **non-blocking** and is polled with `selectors` from a `bpy.app.timers` callback, so no thread is needed (`research/04` §9).
- **LLM calls:** they block, so they run on a worker thread that never touches `bpy`. Results come back through a `queue.Queue` that the main-thread timer drains.
- **Background mode:** `bpy.app.timers` **never fire in `--background` mode**. Every module that relies on a timer therefore exposes a `pump()` function, and headless tests call it in a loop.

### 7.1 Component storage in Blender

- **Attachment list:** `bpy.types.Object.gs` is a static PropertyGroup `GSObjectProps`:
  - `components`: a `CollectionProperty` of `GSComponentRef { name: StringProperty, enabled: BoolProperty }`, declared with `override={'LIBRARY_OVERRIDABLE', 'USE_INSERTION'}` so that prefab instances can add components.
  - `ai_lock`: a `BoolProperty`. The assistant never proposes changes to locked objects.
  - `ai_touched`: a `StringProperty` holding the ID of the last AI proposal applied to the object.
- **Field values:** for each registry component `Name`, a dynamically generated PropertyGroup class `GSC_<Name>` is registered and attached as `bpy.types.Object.gsc_<Name> = PointerProperty(type=GSC_<Name>, override={'LIBRARY_OVERRIDABLE'})`. Every field property is declared with `override={'LIBRARY_OVERRIDABLE'}`.
- **Registry changes:** when the registry changes, the add-on regenerates the classes. It deletes the old Object pointer, unregisters the old class, registers the new one and reassigns the pointer. The stored values survive, because they are IDProperties keyed by name, so fields that keep their name keep their values.
- **Removed components:** removing a component from the registry never deletes data. The object keeps the entry in `obj.gs.components`, and the UI shows it as "missing" until the definition returns.
- **Edits go through `bpy.data`**, never `bpy.ops`, which gets slow in large scenes (`research/04` §17).
- **Linked and override data:** writes are refused when `obj.library` is set, or when `obj.override_library` is a system override.

### 7.2 Game shader storage in Blender

- **The shader:** `bpy.types.Material.gs` is a static PropertyGroup `GSMaterialProps` (registered by `core/registry.py`) with `shader: StringProperty` (`""` = none). It is a string, not an enum, so a material whose shader was deleted from code keeps the name, and the UI and `get_object` report it as missing. `shader_menu` is the UI's selector over it (an enum with get/set that stores nothing).
- **Field values:** for each registry shader `Name`, a PropertyGroup class `GSS_<Name>` is generated and attached as `bpy.types.Material.gss_<Name>`, with the same property factory, overrides and migration as `GSC_<Name>` (§7.1). Entity fields are not allowed in shaders and are skipped with a registry warning.
- **Switching shader** keeps the values of the previous one, so switching back restores them. A registry change migrates the stored values of every shader a material has values for, not only its current one.
- **Linked and override data:** writes are refused when `mat.library` is set, or when `mat.override_library` is a system override.
- **UI:** Properties › Material › Game (`MATERIAL_PT_gamestudio_shader`): the Game Shader selector (None, the registry's shaders, and the stored name with an error icon when the registry lacks it), the shader's fields, its source file, and "In the game, <Name> draws this material. The viewport shows its Blender nodes."

### 7.3 The Scene view in Blender

The **Scene view** is a 3D View that shows the level drawn by the game's renderer (§6.6), from that area's own view, without playing. The **Game** toggle right of a 3D View's shading buttons (and View › Scene View, for a header too narrow to show it) turns it on there, in a GameStudio project; **GameStudio › Scene View** splits the Game workspace's 3D View vertically and turns it on in the right half. One area at a time: turning it on elsewhere moves it. `editors/scene_view.py` draws; `core/sceneview.py` is the lifecycle.

- **The page:** Play's streamed game (`editors/game_stream.py`): the same browser and page. A page Play left warm is reused; else a new one starts at the game's address with `?gs_view=scene`. An address that differs only by `gs_view` never reloads the page (the mode switches with `view.mode`). Turning it on also starts the dev server when the live link is down, turns the scene's Live Link on and exports. Once the page is on the live link it gets `scene.load`, then `view.mode` `{ mode: "scene", animate }` and `view.camera`, each with `to` set to that page (§5.3). A new page (a reload) gets all three again; a page the host loaded that never joins the live link within 30 s is loaded again (`gs_retry`, at most twice).
- **Camera:** `region_3d.view_matrix` and `window_matrix` of the area's main region, converted per §5.4 (`camera_message`; `near`/`far` read off the projection, negative near for Blender's orthographic views). Read when the region draws (its `PRE_VIEW` handler) and by the timer; sent when it changed, at most 60 a second, the latest winning.
- **Frames:** the stream follows the area's main region (at most `MAX_PIXELS`, its aspect kept) at the `scene_view_fps` preference (default 30, the host's `quality` `max_fps`); 60 again for the Game View. They pause while no window shows the area (another workspace); while the Scene view is on and shown, the page is never hidden (suspended).
- **Drawing:** under Blender's overlays. A `PRE_VIEW` handler remembers the viewport's render framebuffer; Blender draws the scene in Solid, which gives its overlays their depth; a `POST_VIEW` handler binds the remembered framebuffer and draws the frame over the whole render colour (an `SRGB8_A8` texture, so Solid's Standard view transform gives the browser's pixels back exactly). The viewport composites the overlay buffer on top: selection outlines, origins, lights, cameras, empties, the 3D cursor, Edit Mode's elements, then gizmos. So the game's own `select` outlines are not used. `PRE_VIEW` alone cannot work: the draw engines then write their whole result over the render buffer. A `POST_PIXEL` handler draws the status (starting, errors, where the game plays).
- **Area settings while on:** shading type Solid; while the frame shows, the viewport background Viewport/black (the overlay pass adds the background colour where Blender drew no object, which would wash the frame out), and the floor grid and X/Y axes off. Each goes back when no longer needed, unless the user changed it meanwhile. A save puts them back for the time it takes (`save_pre`/`save_post`); loading a file turns the Scene view off. Nothing is stored in the .blend.
- **Play and Stop:** Play sends the page `view.mode` `{ mode: "game" }` before its `scene.load` and `play.start` (the engine would leave the Scene view on `play.start` anyway), whether the Scene view is still on or not, then plays as always; the Game View opens by the usual rules, possibly in the Scene view's own area. The Scene view counts the game as played while Play waits, from `play.start` until the page reports `playing` (at most 15 s unless it is `loading`), and while the page reports `playing` or `paused` (also after Step). Then it shows "Playing in the Game View. Stop (F8) returns here" over Blender's own drawing. Once the page reports `stopped` (Stop sends `play.stop`; the page ends in the game view), it sends `view.mode` scene and the camera again.
- **Off:** the area's settings back, nothing drawn; the page stays in scene mode, warm for Play, until `game_idle_minutes` without Play or the Scene view.

### 7.4 Implementation notes (where the code refines this spec)

**Export**
- Export also passes `use_active_scene=True` and `use_selection=False`. Without them the GLB would contain every scene in the file.
- §4.3 is extended to mirror the glTF exporter exactly:
  - Children of cameras and lights get the inverse of the −90° X correction.
  - Collection-instance empties bake `-instance_offset` into their translation.

  `blender/tests/test_convert.py` verifies this against the real exporter within 1e-4.
- `entities` lists only the scene objects the exporter writes as nodes.
- **Disabled components** are left out of the manifest and out of `entity.components`.
- **Background color:** read from the world's *Background* node first, then `world.color`.
- **Gravity:** `[0,0,0]` when scene gravity is off.
- **Export file names** use the level file's name, or the sanitized scene name (§4.7). The Game panel warns when that name differs from `startScene`.

**Hub**
- In `welcome` and `peers`, the peer list leaves out the receiving peer.
- `pong` echoes `re` when the `ping` carries an `id`.
- Responses also set `to`.
- Extra error codes: `no_peer`, `parse`, `bad_message`.

**Game runtime**
- **`play.stop`:** re-applies the live edits received from the editor after resetting. A new `scene.load` discards them.
- **Play state during a load:** a `play.start` or `play.stop` that arrives while a scene loads (Blender sends `scene.load` and `play.start` back to back) is kept: the load ends playing or stopped as asked.
- **Reloads:** parsed GLBs are cached by content hash (`files`, §4.2). A reload or a `play.stop` reset clones the cached parse: geometries and textures are shared, each load gets its own materials (what game code does to a material does not outlive the run), and cached parses the new scene does not use are disposed. Manifest `transform` poses are applied right after the parse, before entities and bodies are built, so bodies start at the new pose.
- **Frame order:**
  0. menu input (`ctx.ui`: menus, canvas buttons, the pause key) and scene transitions, paused or not;
  1. fixed systems, physics steps and particle steps;
  2. animation mixers, AudioSource changes and due timers;
  3. `update` systems;
  4. `late` systems;
  5. HUD labels; Game UI canvases (open state, button states, bindings, bars, text, the DOM mirror), paused or not, also under a test's clock;
  6. render, then the open canvases over the frame and its post effects, through the raw three.js renderer (docs/GAME-UI.md §5.3).
- **Destroy:** `Entity.destroy()` also removes authoring children (entities whose `parentId` is the entity) that physics moved to the scene root.
- **Light units:** imported light intensities are scaled by 1/683 to convert the exporter's photometric units back to Three.js's.
- **API additions:** `state.response` entities also carry `rotation` and `visible`. The engine API adds entity helpers and a system `start` hook, all documented in `runtime/packages/engine/API.md`.
- **Scene changes from the game** (`ctx.loadScene`): fade out on the game's clock with the simulation frozen (state `loading`), load (the files are fetched while fading), fade in while the new scene plays. Entities in `keep` survive the world reset with their objects, components, bodies (rebuilt in the new physics world), mixers, sounds and particles; a new-scene entity with a kept entity's name (or id) is dropped with its subtree, the kept one takes its world pose, and entity fields that held the dropped ids are remapped. `game.ready` is sent for the new scene. Under the test clock the load lands before the next step, wherever `advance()` calls split the steps; `test.status()` counts it in `sceneChanges`, not `loads`.
- **Menus** (`ctx.ui`) pause through `game.pause()` and resume only what they paused; `test.begin()` does not unpause a game a menu holds.
- **Random:** `ctx.random` is reseeded on every scene load from the run seed (`test.begin({ seed })`, else random per page), the scene name and the load count. Particles use their own streams.
- **Shadows** fit the whole scene up to 45 m radius, else 45 m around the camera's view (snapped to shadow texels); `normalBias` is 1.2 shadow texels (at least 0.02 m) and `bias` half a texel in depth.

**Dev server**
- `@gamestudio/devserver` is plain ESM JavaScript with JSDoc types. Node 22 can't load `.ts` from a linked workspace package when Vite loads the config.
- `components.json` isn't rewritten when only `generatedAt` would change.
- **Export files:** manifests are served `no-store`. Other files get an ETag (`"<hash>"` for a verified `?h=`, else a weak one from inode, size and mtime) and `no-cache`, or `immutable` for a verified `?h=`. Each request opens its file once and closes it when the response ends or the client goes away, so Blender can always replace the file (on Windows an open file blocks the rename).
- `virtual:gamestudio/session` also exports `name` (the project name, which keys `ctx.save`) and `post` (gamestudio.json `post` or `null`), in development and in builds.
- **Warmup:** when the server listens, the plugin transforms `index.html`, `src/main`, the systems and components folders and the engine entry before any page asks (`server.warmup`), so Play's first page loads while Blender exports. The first `components.json` is generated after that (within 3 s).
- **The browser for playing** (stream host, the MCP server's headless games, runtime scripts): `GAMESTUDIO_BROWSER` / `GS_BROWSER` when the file exists; else Edge, Chrome, Chromium, Brave or Vivaldi on the system (the lists `WINDOWS_BROWSERS`, `MAC_BROWSERS`, `BROWSER_COMMANDS`, `LINUX_BROWSERS` in `devserver/src/browser.js`; skipped with `GAMESTUDIO_BROWSER_SYSTEM=0`); else a downloaded Chrome Headless Shell in `GAMESTUDIO_BROWSER_DIR`, then in `<extension user folder>/browser` of each Blender profile (`BLENDER_USER_RESOURCES`, then the per-version config folders, newest first). A browser folder counts when its marker `browser.json` (`{browser, buildId, platform, executable, version, installed}`, `executable` relative to the folder with `/`) names a file that exists. `gamestudio-browser install` writes the marker only after the browser ran; `gamestudio-browser path` prints `{"path"}` (null when none).
- The plugin pre-bundles the engine's third-party imports (`ENGINE_DEPS`: three, its addons, Rapier) through `optimizeDeps.include`. The engine is excluded from pre-bundling (it is source), so without the list Vite found them only while the first page loaded, re-optimized and reloaded the page.

**Blender live link**
- The editor doesn't send `entity.visibility` or `select` yet (the Scene view shows Blender's own selection outlines over the game's frame, §7.3).
- Deleting an object sends only `entity.remove`. New objects and geometry edits trigger a re-export plus `scene.load`.

### 7.5 Assistant tool bridge

> Coding agents such as Claude Code now use the **agent bridge** instead (`docs/MCP.md`): the `@gamestudio/mcp` server talks to Blender directly over a token-protected localhost socket and acts without proposals, with undo steps and checkpoints. The hub path below remains for the in-Blender assistant's proposal workflow.

`gamestudio/assistant/tools.py` keeps one tool registry, and the in-Blender agent loop and external MCP clients use the same one. External clients connect as `role: "tool"` to the hub and send `tool.list` and `tool.call`. The editor runs the call on its main thread and replies. Write tools only ever stage proposals, which the user applies with a click in Blender.
