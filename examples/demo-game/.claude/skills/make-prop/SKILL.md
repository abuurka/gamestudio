---
name: make-prop
description: Make a game-ready 3D prop or set piece in Blender, such as a rock, crate, barrel, tree, fence, pickup, character or custom object. Uses GameStudio kits first and models with run_python only when no kit fits, then validates and looks at the result. Use when the user asks for an object, prop, asset, model or decoration, or for variations of one.
argument-hint: "[what to make]"
---

# Make a prop

Order of preference: **library asset → kit → kit plus edits → custom model with the kit toolkit → import a file.** Whichever path you take, the result must pass the gates at the end.

## 1. Already made? Check the libraries

`asset_list` shows the project's library assets. If one fits, `asset_place` it (see [ASSET-LIBRARY.md](ASSET-LIBRARY.md)) and skip to the gates.

## 2. Kit first

1. `list_kits`: one line per kit with its params and defaults. `list_kits {"kind": "rock"}` gives that kit's schema with ranges. The kits are rock, crate, barrel, tree, bush, fence, stairs, platform, building, terrain (shapes and reliefs: hill, ramp, valley, ridge), collectible, pickup, key, character and character_rigged (see `animate-character`), plus assemblies with named parts: vehicle (`<name>_Wheel_*`), weapon (`<name>_Muzzle`), door (`<name>_Leaf`, open/close clips) and switch (`<name>_Handle`); also projectile, water and sky (sets the world and a sun).
2. `create_from_kit` with `kind`, `params` (only what differs from the defaults), `location`, `name` (a meaningful one) and `collection` (`Props`, `Pickups`...). Params outside their range are refused with the range in the error. With `items`, every item is checked before anything is built, so a bad one builds nothing ("Nothing was built."): fix it and send the same call again.
3. **Variation:** build the copies in one call, each with its own `seed` and slightly different sizes. Two identical rocks side by side look fake. The same seed and params always rebuild the same mesh.
   ```json
   create_from_kit {"items": [{"kind": "rock", "params": {"size": 1.4, "seed": 1}, "location": [3, 2, 0], "name": "Rock_1"},
                              {"kind": "rock", "params": {"size": 0.9, "seed": 2}, "location": [4, 1, 0], "name": "Rock_2"}],
                    "collection": "Props"}
   ```
   On uneven ground, follow with `set_transform {"items": [...], "snap_to_ground": true}` (`tilt: true` on slopes).
4. **Adjust later:** `regenerate_kit {"object": "Rock_1", "params": {...}}` rebuilds a kit object in place. Only the params you pass change; it keeps its name, transform and components. To rebuild many, loop in one `run_python`: `gs.kits.regenerate(D.objects[n], {...})`.

All kit objects follow the same conventions:
- metres, Z-up, front facing −Y;
- origin at the bottom centre (collectible: its centre; terrain: ground level);
- no rotation or scale, closed and outward-facing meshes, UVs;
- materials from the shared palette.

## 3. Materials

`apply_material` with a palette preset. These are the presets:

| Group | Presets |
|---|---|
| Stone and ground | stone, stone_dark, brick, concrete, grass, dirt, sand, snow |
| Wood and plants | wood, wood_dark, bark, leaves, pine |
| Building | plaster, roof |
| Metal | metal, iron, gold, copper |
| Other | plastic, rubber, skin |
| See-through | gem, ice, water, glass |
| Glowing | emissive |

Override `base_color` (`"#RRGGBB"`), `roughness`, `metallic` or `emission` only for a reason, such as team colours or a glowing variant. Stay in the palette so props match.

Blender owns the shape, the UVs, which surface is which (one material slot per surface) and the static colour. A surface with a look glTF can't carry, or one that moves or reacts in the game, keeps its Blender material and gets a **game shader** from the built-in library on it, with `set_material_shader`. Most build on the material, so its colour and textures stay.

| Prop | Shader | Notes |
|---|---|---|
| Car or kart body | `CarPaint` | Flakes and a clear coat over the paint colour (the base colour) |
| Gem, crystal, collectible | `Gem` | Coloured fire in the base colour's hue. Shade it flat, or `cutSurface: true` on a round mesh. Small props only |
| Forge, blade, glowing pipe | `HotMetal` | `heat` 0 cold to 1 white-hot |
| Building facade | `InteriorMapping` | Lit rooms behind the windows. On a `building` kit: `windows` 0, the `spacing` height at its floor height, `floors` at its storeys |
| Paving, carved stone | `ParallaxOcclusion` | Deep relief; floors and walls only, it is expensive |
| Bubble, oil, beetle shell | `Iridescence` | Shows best on a glossy material |
| Leaves, flag | `FoliageSway`, `FlagWave` | `stylize-scene`, section 5, has water, wind and weather |
| Pickup, usable object | `InteractGlint` | A sweep of light every few seconds (`add-mechanic` drives it) |
| Cartoon style | `Toon` | Flat light bands, rim light, ink outline |

`list_component_types {"type": "CarPaint"}` gives a shader's fields; the defaults look right. Never look the material up by name in code.

## 4. No kit fits: model it with the kit toolkit

Use `run_python`. The code sees `gs`, the GameStudio package. `gs.kits.geometry.MeshBuilder` builds closed, UV-mapped, palette-material meshes from boxes, prisms, lathes and spheres. The API and worked examples are in [MODELING.md](MODELING.md).

Rules for custom models:
- Put the origin at the bottom centre, with rotation and scale applied.
- Build closed shells (overlapping separate shells are fine).
- Stay within the triangle budget: about 50–500 tris for small props and 2k–5k for big set pieces.
- Use real-world sizes in metres. A door is 2.2 m and a table 0.75 m.
- Use one object per gameplay entity. Merge decorative parts into it; don't parent dozens of pieces.

Prefer `bpy.data` and `bmesh` over `bpy.ops`, which depends on context and is fragile in `run_python`.

## 5. Importing a file

`import_asset` with `path` (`.glb`, `.gltf`, `.fbx` or `.obj`), `collection`, `location` and `scale`. Imports often have the wrong scale or rotation, or no UVs. Run `validate_assets` on them and fix what it reports; MODELING.md shows how to apply transforms.

## 6. Juice and reuse

- **Motion:** a pickup that spins and bobs, a tree that sways, a door that opens. These are generated clips (`gs.kits.animation.add_clip`) played by an `Animator`; see `animate-character`, section 3.
- **Reuse:** a prop that appears in several levels, or many times in one, belongs in an asset library. `asset_add` it, then `asset_place` copies (`spawn_only: true` when only game code spawns it). See [ASSET-LIBRARY.md](ASSET-LIBRARY.md).

## Verification gates

1. `validate_assets {"names": [...]}` with the new objects. There must be no errors. Fix warnings unless you know why they don't matter.
2. `render_views {"target": "<name>"}` gives front, side, top and iso with rulers and a 1.8 m person. Add `focus: true` to see it in the scene. Look at the image and check:
   - The silhouette reads at game distance.
   - The proportions match the person.
   - Nothing is inside out (dark or missing faces).
   - The materials match the rest of the scene. A game shader shows only in the game: check it with `game_screenshot`.
3. If it is a gameplay object, add its components: `Collider` (`auto` for boxes, `convex` for round dynamic things, `mesh` for static complex shapes) and `RigidBody {type: "dynamic"}` if it should be pushable. `add_component` with `components` adds both at once.
4. `save`.

## Common failures

| Symptom | Fix |
|---|---|
| `create_from_kit` rejects the params | Read the error: it names the param and its range. `list_kits {"kind": ...}` has the schema. |
| The prop floats or sinks | `set_transform {"name": ..., "snap_to_ground": true}` rests the bottom of its bounds on the surface, wherever its origin is. To hover a pickup, then add `{"location": [0, 0, 1], "relative": true}`. |
| Faces are black or missing in the render | Normals point inward. Call `bmesh.ops.recalc_face_normals` on custom meshes. MeshBuilder does this for you. |
| The collider is far too big | `auto` uses the bounding box. Remove stray far-away vertices, or use `convex`/`mesh`. |
| An imported model is 100× too big or lies on its side | FBX units or a Y-up source. Fix the scale or rotation, then apply transforms. |
