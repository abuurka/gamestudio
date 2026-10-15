---
name: prepare-for-web
description: Get a GameStudio game ready to ship as a static web build. Checks triangle, draw-call, texture and light budgets per level, reduces what is over, exports every level, runs the typecheck and npm run build, and smoke-tests the output. Use when the user wants to publish, deploy, share a link, build, or make the game run well on laptops, phones or the web.
argument-hint: "[target: desktop | mobile]"
---

# Prepare for web

The build is `npm run build`, which writes `dist/`. It bundles the code and copies `.gamestudio/export/`, so **every level must be exported first**. The build also compresses the scene geometry with meshopt. The compression is lossless and on by default, and exports stay plain while you develop.

## Budgets per level

| | Desktop web | Mobile / low-end |
|---|---|---|
| Triangles in view | 300k or fewer | 100k or fewer |
| Mesh objects × material slots (draw calls, roughly) | 300 or fewer | 120 or fewer |
| Largest texture | 2048 px | 1024 px |
| Shadow-casting suns | 1 | 1 |
| Point lights | 8 or fewer | 4 or fewer |
| Exported `.glb` per level | 20 MB or less | 8 MB or less |

## 1. Inventory every level

For each `scenes/*.blend`:

1. `open_scene`, then `export_scene {"compress": true}`. Its `size` has the bytes and the largest textures and meshes (`top`). `size.compressed` has each GLB as the build will ship it. `warnings` lists unbaked materials, downscaled textures and a size over budget.
2. `game_stats` plays the export headless: draw calls and triangles per frame, GPU textures, memory, the 10 heaviest meshes and `over_budget`. `scene` measures another level. Its fps is software-rendered and far below a real GPU; compare the other numbers. Check its `state`: a title screen holds the game `"paused"` (no physics bodies yet), and the result then has a `warning`; `keys: ["Enter"]` presses past the title before measuring. Draw calls and triangles count every render pass of a frame, `PostEffects` (bloom) included.
3. `validate_assets {"max_tris": 5000}` flags meshes over 5,000 triangles; terrain may keep up to 20,000. `summary: true` gives counts per check and the worst objects. Fix every error.
4. `checkpoint` with the label "before web budget". Keep its id.

## 2. Reduce what is over budget (biggest wins first)

- **Kit objects:** `regenerate_kit` at a lower detail (or loop `gs.kits.regenerate(obj, {...})` in one `run_python` for many). Useful params:
  - `rock.detail` 1;
  - `terrain.resolution` 24–32;
  - `barrel.segments` 8–10;
  - `collectible.segments` 12;
  - fewer `building.windows`.
- **Repeated props:** keep them as asset library instances (`asset_add` with `move: true`). The export stores each asset once.
- **Imported or dense meshes:** add a Decimate modifier. It is applied on export.
  ```python
  m = obj.modifiers.new("Decimate", "DECIMATE"); m.ratio = 0.5
  ```
- **Textures:** the export downscales images above `maxTextureSize`. `"compression": {"textures": "webp"}` in `gamestudio.json` writes colour maps as WebP and data maps as lossless WebP, which is usually much smaller than PNG. For mobile, also set `"export": {"maxTextureSize": 1024}`, instead of editing images. Procedural materials are baked to textures at export. Re-export, then check the look with `compare_views`.
- **Game shader textures** (`field.asset` values set with `set_material_shader`) are copied as they are, like other asset files: no downscaling and no WebP. Save them at the budget size, as WebP or JPEG. Check a material with a game shader with `game_screenshot`, not `render_views`.
- **Library shaders** add no download unless a scene uses them, and most are cheap. The costly ones say so in their description (`list_component_types {"type": "<Shader>"}`): `ParallaxOcclusion` (up to `steps` + 21 texture reads per pixel: lower `steps`, keep it to floors and walls), `Gem` (small props only), `EnergyShield`, and the extra draws of `SelectionOutline` (two per mesh), `XRaySilhouette` and the `Toon` outline (one each), which `game_stats` counts. For mobile, take them off the large surfaces first (`set_material_shader` with `"shader": null`).
- **Dense scatter** (grass, pebbles): a geometry-nodes scatter exports each repeated mesh as GPU instances, one draw call per mesh (`instancing` in the export result).
- **Draw calls:** remove hidden and unused objects (`delete_objects`). Join static decoration that shares materials, in `run_python`:
  ```python
  with C.temp_override(active_object=a, selected_editable_objects=[a, b, c]):
      bpy.ops.object.join()
  ```
- **Lights:** one sun. Replace far-away point lights with emissive materials.

After the reductions, `export_scene` and `game_stats` again at the same `size`, and `render_views {"compare": "<checkpoint id>"}`: quality must stay acceptable at game distance.

## 3. Export and build

1. `export_scene {"all": true}` exports **every** level (the open one, then each other `scenes/*.blend` in a background Blender) and returns `scenes` and `errors` per file. The start scene comes from `gamestudio.json`. Read each export's `warnings`. The build also warns about levels with a missing or stale export.
2. `npm run typecheck`. It must pass.
3. `npm run build`. Read the output:
   - The warning "no .gamestudio/export folder" means a level was never exported.
   - Note the size of the JS chunk. Rapier's WASM adds about 2 MB, which is expected.
   - "scene files" lists every GLB before and after compression, with its geometry and textures. A line that says it has PNG textures means `"compression": {"textures": "webp"}` would shrink them.
4. Check the sizes: `dist/` in total, and every `.glb` under `dist/`. `"compression": {"geometry": "none"}` turns the geometry compression off, for example to rule it out when you debug.

## 4. Smoke test the build

- Serve it with `npx vite preview` (in the background). It prints a local URL. Open it in a browser, or ask the user to. Check that the start scene loads, the controls work and the console has no errors.
- `playtest` again on the dev server for the critical path, so you know the exported scenes are current.
- Audio needs a first click or key press in the browser. If an `AudioSource` file is missing from `dist/`, the path in its `clip` field is wrong.

## 5. Report

Give the per-level triangles, draw calls, textures and export bytes before and after (from `game_stats`), the size of `dist/`, and anything over budget that you left as it is, with the reason.

## Common failures

| Symptom | Fix |
|---|---|
| The built game shows an empty scene or a 404 for `.glb` | That level was never exported. `export_scene`, then build again. |
| It works in dev but not when served from a sub-path | Set `base` in `vite.config.ts` (for example `base: './'`) and build again. |
| Huge `.glb` files | `size.top` names the culprits: big textures (lower `maxTextureSize`, set `"compression": {"textures": "webp"}`) or dense meshes (Subdivision modifiers are applied on export). The build already compresses geometry losslessly. |
| Frame rate drops on laptops | Too many dynamic bodies or point lights, or very dense terrain. Profile one level at a time. For low-end targets, keep `PostEffects` off (bloom adds several full-screen passes). |
| Type errors reach the build unnoticed | Vite strips types without checking them. Always run `npm run typecheck` before `npm run build`. |
