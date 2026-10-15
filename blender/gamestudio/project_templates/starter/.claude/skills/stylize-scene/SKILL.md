---
name: stylize-scene
description: Give a GameStudio level a coherent stylized look. Assigns materials by role from the shared palette, sets up the sun, sky and lights, dresses the set with varied props, puts game shaders from the built-in library on surfaces that move or weather (water, foam, lava, wind in leaves and grass, snow, moss, rain, a toon look), and makes gameplay objects read clearly. Then compares before and after screenshots. Use when the user asks to make it look better, prettier, more polished, or themed (desert, winter, night, spooky), for water, lava, wind, snow or a shader effect on a material, or before showing the game to someone.
argument-hint: "[style or theme]"
---

# Stylize a scene

The style is flat-shaded low-poly with a small PBR palette. Readability comes first: the player, pickups and hazards must stand out from the world.

## 1. Baseline

1. `checkpoint` with the label "before stylize". Keep its id.
2. `play`, then `game_screenshot`. Keep it to compare against.

## 2. Pick a palette by role (4–6 presets)

| Role | Default | Desert | Winter | Night / spooky |
|---|---|---|---|---|
| Ground | grass | sand | snow | stone_dark |
| Structure | stone, plaster | sand, wood | stone, wood_dark | stone_dark, iron |
| Detail / trim | wood_dark, roof | wood_dark | iron | wood_dark |
| Foliage | leaves, pine | (few) leaves | pine | pine |
| Hero (player) | plastic `#3F7FD9` | plastic `#2BB3B3` | plastic `#E0533D` | plastic `#E8B53A` |
| Pickups | gold, gem | gold | gem | emissive |
| Hazards | emissive `#FF5A1F` | emissive `#FF5A1F` | ice | emissive `#7CFF6B` |

Apply the palette with `apply_material` on **every** object of a role in one call (a list of `objects`), or use `slot` for one part of a multi-material kit object (`slot: [0, 2]` for several). Keep the world muted and the hero saturated. Don't introduce new colours unless you have a reason.

## 3. Light and sky

- **Sky and sun in one call:** the `sky` kit, `create_from_kit {"kind": "sky", "name": "SkySun", "params": {"preset": "sunset"}}` (`day`, `sunset`, `night`, `overcast`; any colour can be set). It sets the world's gradient and makes the matching sun lamp; `regenerate_kit` changes it. The game draws the same gradient (ground below the horizon, horizon, zenith) and tints its fill light with it. Set `ground` to what continues the level past its edge (sea blue around islands, the lawn's green around a field), or the band below the horizon shows grey-green. Turn on the world's mist for light distance fog in the horizon colour: `run_python` with `m = bpy.context.scene.world.mist_settings; m.use_mist, m.start, m.depth = True, 60, 160` (start past the play area; `world["gs_fog_color"] = "#RRGGBB"` picks another colour). Delete the scene's old sun so there is only one. For a night scene that is too dark in the game, raise `sun_strength`.
- **Sun by hand** (`run_python`): one `SUN` light, `energy` 3–5, about 45° elevation and turned 30–40° off the camera axis, slightly warm such as `(1.0, 0.95, 0.85)`. For night, energy 0.5–1 with a blue tint.
- **Post effects:** a `PostEffects` component on the camera turns on `bloom` (emissive materials and bright particles glow), `vignette` and grading for that level. Use them sparingly: bloom costs several full-screen passes, and a strong emissive under bloom swallows what is next to it (keep `emission_strength` around 1–2 near the player). Bloom passes every pixel brighter than `bloomThreshold` whole, so at the default 0.85 a large emissive area (lava, a neon floor) floods its surroundings: set `bloomThreshold` around 2 and `bloomStrength` 0.4–0.5, and make what should glow brighter than that (`emission_strength` 3 or more on light colours, particles with `brightness` 3 or more).
- **Local lights:** only for lamps, fires and magic, with 1–6 `POINT` lights. Emissive materials glow but don't light their surroundings.
- The engine adds a hemisphere fill light and a dim environment. Tune them in `src/main.ts` with `createGame({ fillLight, environment })` if the scene is too flat or too dark.
- **Brightness and colour:** the game tone-maps as the scene's Color Management says: its view transform (Khronos PBR Neutral in new projects) and exposure. If the whole game is too dark or too bright, change the exposure in Blender (`run_python`: `C.scene.view_settings.exposure = 0.5`, in stops), not in code. Keep the look at None: the game has no looks.

## 4. Dress the set

- Scatter `rock`, `bush` and `tree` kits along the edges and borders, in clusters of 3–5 with different `seed`s and sizes: one `create_from_kit {"items": [...]}` call, then `set_transform {"items": [...], "snap_to_ground": true}`. Keep them off the critical path.
- A prop repeated across the level or across levels belongs in an asset library: `asset_add` it once, then `asset_place` the copies (`make-prop`, ASSET-LIBRARY.md).
- Decoration doesn't need gameplay components. Add a simple `Collider` only where the player could otherwise walk through something large.
- Make pickups pop: an emissive or gold material, hovering about 1 m above the ground.
- **Motion** brings a still scene to life: generated clips (`sway` on trees, `["spin", "bob"]` on pickups, `hover` on floating things) played by an `Animator` (`animate-character`, section 3). Don't animate objects that code moves.

## 5. Water, wind and weather: game shaders

The palette covers static surfaces. Water, wind in leaves and grass, snow, moss, rain or a toon look need a **game shader**: code that draws a Blender material in the game. GameStudio ships a library of 31; reach for it first. Blender keeps the shape, the UVs, which surface is which and the static colour and roughness, and most library shaders build on that material (its colour, textures and lighting stay).

| Surface | Library shader | Notes |
|---|---|---|
| Lake, sea, pond | `Water` | A flat plane is enough. `waveLength` about 1 m for a pond, 4 m for a lake, 15 m for the sea. The base colour tints it; its alpha is how see-through it is. |
| Surf around rocks, along a beach | `ShorelineFoam` | A ring or strip just above the water, unwrapped V 0 at the shore to 1 out at sea |
| River, stream, waterfall | `Flow` | A strip unwrapped U across, V along; `speed` negative reverses it |
| Lava lake or channel | `Lava` | `mapping: "uv"` for a channel; it glows by itself |
| Pool floor, sunken wreck | `Caustics` | On the floor and walls under the water |
| Rain-soaked street | `WetSurface` | Puddles on upward ground, drips on walls |
| Trees and bushes in the wind | `FoliageSway` | On the leaves; the bark with the same values and `rustle`, `translucency` 0 |
| Grass | `GrassWind` | On grass blades and clumps, scattered ones included |
| Flags and banners | `FlagWave` | A subdivided plane (about 20 cuts) with UVs; `pinnedEdge` is the pole side |
| Winter | `SnowCover` | On rocks, roofs and ground (`amount` 0–1) |
| Ruins, old stone | `MossOvergrowth` | `progress` 0–1 |
| Frozen things | `Ice` | `freeze` 0–1 |
| Rock and sand without good UVs | `Triplanar` | Photo scans as `diffuse`/`normal`/`rough` asset values |
| Cartoon look | `Toon` | On characters and props; `outline` 0 skips the ink line |

1. `list_component_types {"type": "Water"}` gives the shader's fields and what they do; the whole library is under `shaders` in `list_component_types`. The defaults look right, so set only what the scene needs.
2. Attach it to the material: `set_material_shader {"objects": ["Pond"], "shader": "Water", "values": {"waveLength": 1.5}}` (`get_object` lists the slots, `slot` picks one; `material` names a material instead; `"shader": null` removes it). It belongs to the material, so every object that draws it gets it, spawned copies included. Palette materials are shared: give objects that need their own look their own material first (`apply_material` with a `name`).
3. One wind for the level: give `FoliageSway`, `GrassWind` and `FlagWave` the same `wind` and `windAngle`, and the gusts roll through trees, grass and flags together.
4. Keep the material's Blender colour close to the game's look, so `render_views` still reads.
5. Judge it in the game: Blender's viewport and `render_views` show the Blender nodes. Check materials in the level with `game_screenshot {"mode": "scene", "view": {"from": [x, y, z], "at": [x, y, z]}}` from a viewpoint close to them (Blender coordinates; `{"camera": "Camera"}` looks through a camera). That is the game's renderer, shaders and lighting without playing, so no title screen or HUD is in the way. For motion, take a `playtest` with two screenshots apart in time. `game_logs` names a material whose shader is undefined or failed.

Nothing in the library fits? Copy the nearest one into `src/shaders/` and change it (API.md §29, "Customise one"), or write a `defineShader` of your own (API.md §28). Patch with `patchShader`, never `onBeforeCompile` directly, so patches compose with each other and with baked lighting, and never look materials up by name in code.

## 6. Bake the lighting

Once materials, lights and props are final, bake lightmaps for the static objects: soft sky light, darker corners and light bouncing off walls, at no cost per frame. The sun stays realtime. Skip it for a quick pass on an open, sunny level.

1. `bake_lighting {"quality": "preview"}` while you iterate (quick, coarse). Blender is busy while it bakes. It exports the level afterwards, so the game shows the result.
2. `compare_views {"view": "iso"}`. Baked surfaces should be close to Blender, with no black patches and nothing brighter than in Blender.
3. `bake_lighting {"quality": "final"}` before shipping (slower). Bake again after moving or adding static objects or changing the lamps or the world; `export_scene` warns about stale lightmaps.

Opt-outs: objects with a `RigidBody` that isn't `fixed`, a `CharacterController`, `Spin`, `Animator` or clips are left out by themselves. Objects that code moves, hides or swaps need the custom property `gs_lightmap` set to `False` (`run_python`: `obj["gs_lightmap"] = False`). On a lamp, `"baked"` bakes it fully: soft shadows, but the game turns it off, so it doesn't light the player. AREA lamps are always baked. More in the engine docs (`engine_docs {"topic": "Baked lighting"}`).

## 7. Verify

1. `render_views {"compare": "<checkpoint id>", "views": ["camera", "iso"], "shading": "material"}` shows before and after side by side; `names: ["Player", "Door", ...]` labels the objects that matter. Then `game_screenshot` from the same spot as the baseline. Check:
   - The player, pickups and hazards are distinguishable at a glance.
   - Nothing is too dark or blown out.
   - Shadows are visible.
   - No z-fighting.
2. Judge the final look from `game_screenshot`. The game tone-maps with the scene's view transform and exposure, so it is close to Blender's preview; game shaders and post effects show only in the game. If it differs more, `compare_views` shows both side by side (about 10 s) with no camera to place: `{"view": "iso"}` frames the scene, or `{"camera": {"from": [x, y, z], "at": [x, y, z], "lens": 30}}` poses one (for a follow camera: the player plus its offset). `match` or `close` is fine. `verdict_reason` says which number decided; a `detail_ratio` well below 1 means material detail got lost, and `detail_cells` / `worst_cells` show where. Blender renders no post effects, so `compare_views` turns the game's bloom, vignette and grading off for its shot and names them in `post_effects`; judge the glow from `game_screenshot`.
3. `validate_assets {}`. Decoration must not blow the triangle budget (see `prepare-for-web`).
4. `save`.

## Common failures

| Symptom | Fix |
|---|---|
| The game is much darker than Blender | There is no sun (the engine only casts shadows from directional lights), or the sun's energy is below 2. |
| The whole game's colours or contrast differ from Blender | The scene uses a view transform the game lacks (Filmic) or a look; `export_scene` warns about both. Use Khronos PBR Neutral (or AgX), look None, and set brightness with the exposure. |
| A material with a game shader looks like its Blender nodes in the game | `game_logs` says why: no `src/shaders` file defines that shader, or its code threw. Fix the file; saving it re-applies the shader. |
| Foam, flowing water or a flag looks scrambled, or a flag doesn't move | `ShorelineFoam`, `Flow`, `FlagWave` and `PortalVortex` follow the UVs: unwrap as the table in section 5 says. A flag needs a subdivided plane; a single quad cannot fold. |
| Everything is washed out | The sun's energy is above 6, or several suns. Keep one sun. |
| Flickering stripes on overlapping surfaces | Coplanar faces (z-fighting). Offset one surface by 1–2 cm. |
| An emissive lamp doesn't light the area | Add a `POINT` light at the lamp. |
| A colour override created many materials | `apply_material` with the same preset and colour reuses one material. Don't vary the colour per object. |
| Kit objects lost their custom material after a param change | `regenerate_kit` keeps materials only when the slot count is unchanged. Re-apply. |
| A pickup with a clip snaps back to an old spot | Object clips key the place it had. Add the clip after the final placement, or again after moving it. |
| Light or shadow stuck in the wrong place after a change | The lightmap is stale. `bake_lighting` again, or set `gs_lightmap` to `False` on an object that code moves. |
