# Asset libraries: `assets/*.blend` reused across levels

Use an asset library when the same prop appears in more than one level, or many times in one level. Editing the asset in its library then updates every level that places it, and the export stores it once.

## How it works

- `assets/<library>.blend` (`props.blend`, `nature.blend`, `buildings.blend`) holds **one collection per asset**, named like the asset (`Crate`, `Barrel`, `Lamp`). It has its materials and the components every copy shares, such as a `Collider` on a crate.
- Every asset is a Blender asset (preview, catalog, tags) and, by default, a GameStudio **prefab**, so game code can also `ctx.spawn('Crate', {position: [x, y, z]})` it.
- Levels never copy the meshes. They place **instances**: one empty per copy, linked to the library.
  - The empty is an entity. Put per-copy components on it (`RigidBody`, `Coin`, ...).
  - The asset's objects become entities under it, with their components. Their ids are `<instance id>/<template id>`.

## Make assets

Build and check the prop in the level first (kit or model, see [SKILL.md](SKILL.md)), then `asset_add {"objects": ["Crate", "Barrel"], "separate": true, "library": "props", "catalog": "props/wood", "tags": ["wood"]}`.

- The level must be saved inside the project. The library file is created if it is missing.
- Children come along. `separate: true` makes each top object its own asset, named after it; `name: "CrateStack"` names one asset made of several objects.
- `move: true` also replaces the objects in the level with an instance where they stood (named `Crate_01`, as `asset_place` names them): use it to turn hand-placed props into reusable ones.
- `spawn_only: true` instead removes them from the level and links the asset as a spawnable template, not placed: one call for a prefab that only code spawns (a bullet, an enemy, a drop). The prefab collection they came from goes too when it is left empty (`removed_collections`). The result's `spawn` shows the `ctx.spawn` call with the name the export really uses; a `warning` says when another prefab in the level takes the name.
- Adding an asset under an existing name replaces it. That is how you update an asset; open levels that link the library reload it.
- A background Blender writes the library file, which takes a second or two. Undo does not cover that file, so it is **backed up first**: the result's `restore` is the `asset_library_restore {"library": ...}` call that takes the write back. `asset_list {"backups": true}` lists the backups.

## Find, place, swap

- `asset_list` (optionally `library`, `catalog`, `tag`): `name, library, catalog, tags, prefab, components, size` and `placed` (copies in the open level). Each library also has a readable twin, `assets/<library>.scene.json`.
- `asset_place {"asset": "Crate", "location": [4, 2, 0], "rotation_deg": [0, 0, 30], "collection": "Props"}` returns the instance name: `Crate_01`, `_02`, ... unless you pass `name`. Vary rotation and `scale` so repeats don't look identical.
- Move instances with `set_transform` like any object; on uneven ground, `snap_to_ground: true`.
- `add_component` on the instance changes that copy only.
- `asset_replace {"instance": "Crate_01", "asset": "Barrel"}` swaps what an instance shows and keeps its name, transform and components.
- `asset_make_local {"instance": "Crate_02"}` turns one copy into editable local objects for a one-off edit. That copy no longer follows the library.

## Spawn at runtime

Game code can `ctx.spawn('<Asset>', {...})` any asset the level contains. An asset the level places is already there. For one it doesn't place, `asset_spawnable {"asset": "Crate"}` links it as a template only, then `save`; spawn it with the `spawn` call the result gives. Every level that spawns an asset needs it: in a second level, `asset_spawnable` the bullets, enemies and drops its code spawns. `ctx.prefabs` lists what the game can spawn.

## Edit an asset

- **Components:** `add_component`, `set_component` and `remove_component` take `asset` (and `library` when the name is in several): `set_component {"asset": "AmmoPack", "component": "Pickup", "values": {"amount": 60}}`. `objects` then names objects inside the asset (default: its one top object). A background Blender writes the library (a few seconds), it is backed up first, and the open level reloads it, so placed copies and spawns follow on the next export.
- **Geometry:** rebuild it in the level and `asset_add` it again under the same name, or `open_scene` the library file, change it, `save`, and `open_scene` the level again. Levels see the change when they are opened, or after `asset_reload`.
- **Undo a library write:** `asset_library_restore {"library": "props"}` puts back the latest backup (or `backup: "<id>"`); its result's `undo` takes the restore back.

## Rules

- One asset is one collection, built at the origin. Don't rename an asset that levels place: their instances would lose it.
- Components on the asset's objects apply to every copy. Components on the instance apply to that copy.
- Don't copy asset meshes into levels, and don't put levels in `assets/`.

## Common failures

| Symptom | Fix |
|---|---|
| `Save the level inside a GameStudio project` | The level has never been saved. `save` it under `scenes/` first. |
| `No asset named 'X'` | The message lists the assets that exist. Check `asset_list`. |
| `'X' is in several libraries` | Pass `library`. |
| Components can't be added to the asset's objects in the level | They are linked data. Add the component to the instance, change the asset in its library, or `asset_make_local` that copy. |
| An instance shows up, but its objects are not entities in the game | The asset was added with `prefab: false`, so its contents are only geometry. Add it again without that flag. |
| The level twin lists the library with `"missing": true` | The library file was moved or renamed. Put it back, or `asset_replace` the instances. |
| `ctx.spawn('X')` fails with `Unknown prefab "X"` and the list shows `X [props.blend]` | Another prefab in the level is named `X`, so the linked one is exported under its library name (the result of `asset_add` or `asset_spawnable` warned). `spawn_only` removes an empty source collection itself; for any other one, rename it or `set_prefab {"collection": "X", "enabled": false}`, and export again. |
