// @ts-check
// SPDX-License-Identifier: MIT
// Asset tools: kits, import, materials, the game-readiness report and rendered views.

import { withImages } from '../protocol.js';
import { NAMES, READ, VEC3, WRITE, bridgeTool, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

// Preset names of kits.materials (blender/gamestudio/kits/materials.py); Blender validates them.
const MATERIAL_PRESETS = [
  'stone', 'stone_dark', 'wood', 'wood_dark', 'bark', 'leaves', 'pine', 'grass', 'dirt', 'sand', 'plaster', 'roof',
  'metal', 'iron', 'gold', 'plastic', 'rubber', 'skin', 'gem', 'emissive', 'brick', 'concrete', 'copper', 'snow', 'ice',
  'water', 'glass',
];
const COLOR = {
  type: ['string', 'array'],
  items: { type: 'number', minimum: 0 },
  minItems: 3,
  maxItems: 4,
  description: '"#RRGGBB" (sRGB hex) or [r, g, b(, a)] linear 0-1.',
};
const VIEWS = ['front', 'back', 'left', 'right', 'side', 'top', 'bottom', 'iso', 'camera'];

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function assetTools(ctx) {
  return [
    bridgeTool(ctx, {
      name: 'list_kits',
      title: 'List asset kits',
      description:
        'Parametric asset generators (crates, rocks, trees, buildings, ...) with a JSON Schema for each kit\'s params, and ' +
        'the material presets apply_material takes. Use create_from_kit to build one: kits make game-ready meshes (metres, ' +
        'origin at the bottom centre, front facing -Y, UVs, materials) that regenerate_kit rebuilds with new params. ' +
        'The procedural section lists the geometry-nodes builders for whole areas (scatter, array, fence, road, river, ' +
        'cliff, rock_wall, building) that create_procedural makes; kind gives the full schema of one kit or builder.',
      inputSchema: object({
        kind: { type: 'string', minLength: 1, description: "One kit with its full JSON Schema." },
        verbose: { type: 'boolean', description: 'Everything, not just the essentials. Default false.' },
      }),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'create_from_kit',
      title: 'Create from kit',
      description:
        'Build an asset from a kit (one undo step): kind from list_kits, params per its schema (omitted ones use ' +
        'defaults), location in Blender space (Z-up, metres), name for every object it makes (a rigged character named ' +
        '"Hero" is the armature "Hero" with the mesh "Hero_Mesh"), collection to put it in. ' +
        'items: [{kind, params?, location?, name?, collection?}, ...] builds many in one call (one undo step; top-level ' +
        'fields are defaults for every item; every item, params included, is checked before any is built, so a bad ' +
        'item builds nothing). Example: {"items": [{"kind": "rock", "location": [2, 0, 0]}, ' +
        '{"kind": "tree", "location": [5, 3, 0], "name": "Oak"}], "collection": "Props"}. Follow with set_transform ' +
        '{"items": [...], "snap_to_ground": true} on uneven ground.',
      inputSchema: object({
        kind: { type: 'string', minLength: 1 },
        params: { type: 'object' },
        location: VEC3,
        name: { type: 'string', minLength: 1 },
        collection: { type: 'string', minLength: 1 },
        items: {
          type: 'array',
          minItems: 1,
          maxItems: 200,
          items: object(
            { kind: { type: 'string', minLength: 1 }, params: { type: 'object' }, location: VEC3, name: { type: 'string', minLength: 1 }, collection: { type: 'string', minLength: 1 } },
            ['kind'],
          ),
        },
      }),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'create_procedural',
      title: 'Create procedural',
      description:
        'Build a procedural (geometry-nodes) part of a level in one undo step: kind scatter (forests, rocks, grass over ' +
        'a terrain), array (copies along a path), fence, road, river, cliff, rock_wall or building (a modular house). ' +
        'params per list_kits {"kind": "<kind>"}; objects are given by name: surface, sources (objects or a collection; ' +
        'they are moved into the hidden "Procedural Sources" collection), curve or path_or_bounds (a curve name or ' +
        '[[x, y], ...] points), terrain, keep_out. It stays parametric: change it with set_procedural. Repeated sources ' +
        'export as GPU instances. Returns the object, all its inputs (params) and stats (instances, triangles, draw ' +
        'calls). Example: {"kind": "scatter", "params": {"surface": "Hill", "sources": ["Tree_1", "Tree_2"], ' +
        '"density": 0.03, "keep_out": ["Road"]}, "name": "Forest"}.',
      inputSchema: object(
        {
          kind: { type: 'string', enum: ['scatter', 'array', 'fence', 'road', 'river', 'cliff', 'rock_wall', 'building'] },
          params: { type: 'object', description: 'Builder parameters (list_kits {"kind": "<kind>"}).' },
          name: { type: 'string', minLength: 1 },
          collection: { type: 'string', minLength: 1 },
        },
        ['kind'],
      ),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'set_procedural',
      title: 'Set procedural inputs',
      description:
        'Change inputs of an object made by create_procedural (one undo step), e.g. {"object": "Forest", "params": ' +
        '{"density": 0.05, "seed": 3}}. Any key its params list, plus scale_range and lists of object names for ' +
        'sources and keep_out; values are validated and nothing changes on an error. No rebuild: the geometry nodes ' +
        're-evaluate. Returns all inputs and fresh stats.',
      inputSchema: object(
        { object: { type: 'string', minLength: 1 }, params: { type: 'object', description: 'Inputs to change.' } },
        ['object', 'params'],
      ),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'regenerate_kit',
      title: 'Regenerate kit object',
      description:
        'Rebuild an object made by create_from_kit with some params changed (one undo step), e.g. {"object": "Crate", ' +
        '"params": {"size": 1.5}}. It keeps its name, transform, parent, components and materials (when the slot count ' +
        'is unchanged). get_object shows the kind and params an object was built with.',
      inputSchema: object({ object: { type: 'string', minLength: 1 }, params: { type: 'object' } }, ['object', 'params']),
      annotations: WRITE,
    }),
    {
      ...bridgeTool(ctx, {
        name: 'import_asset',
        title: 'Import asset',
        description:
          'Import a .glb/.gltf, .fbx or .obj file (one undo step) into a collection named after the file (or collection). ' +
          'location offsets the imported roots, scale multiplies them, name renames a single root. Run validate_assets on ' +
          'the result.',
        inputSchema: object(
          {
            path: { type: 'string', minLength: 1, description: 'Absolute, or relative to the project.' },
            location: VEC3,
            scale: { type: 'number', minimum: 0 },
            name: { type: 'string', minLength: 1 },
            collection: { type: 'string', description: 'Target collection; "" keeps the importer\'s choice.' },
          },
          ['path'],
        ),
        annotations: WRITE,
      }),
      handler: (args, call) => ctx.blender.call('import_asset', { ...args, path: ctx.resolvePath(args.path) }, { signal: call.signal }),
    },
    bridgeTool(ctx, {
      name: 'apply_material',
      title: 'Apply material',
      description:
        'Give objects a material from the shared palette (the one the kits use; exports to glTF), one undo step. ' +
        `preset: ${MATERIAL_PRESETS.join(', ')} (aliases: dark_wood, glow, steel, rock, foliage); list_kits lists them ` +
        'with descriptions. params override the preset: {base_color, roughness, metallic, emission, emission_strength, ' +
        'alpha}, colours as "#RRGGBB" or linear [r, g, b]; with params only, a neutral base (plastic) is used. Presets ' +
        'are shared materials (GS_Stone, ...); overrides make a separate one, or one called name. Replaces every slot, or ' +
        'only slot (an index or a list, e.g. [0, 2]). Examples: {"objects": ["Door"], "preset": "wood_dark"}, {"objects": "Lamp", "preset": "emissive", ' +
        '"params": {"base_color": "#66CCFF"}}.',
      inputSchema: object(
        {
          objects: NAMES,
          preset: { type: 'string', minLength: 1 },
          params: object({
            base_color: COLOR,
            color: COLOR,
            metallic: { type: 'number', minimum: 0, maximum: 1 },
            roughness: { type: 'number', minimum: 0, maximum: 1 },
            emission: COLOR,
            emission_strength: { type: 'number', minimum: 0, maximum: 1000 },
            alpha: { type: 'number', minimum: 0, maximum: 1 },
          }),
          name: { type: 'string', minLength: 1 },
          slot: { type: ['integer', 'array'], items: { type: 'integer', minimum: 0 }, minItems: 1, minimum: 0, description: 'A slot index or a list of them (default: every slot).' },
        },
        ['objects'],
      ),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'validate_assets',
      title: 'Validate assets',
      description:
        'Game-readiness report per object, each issue with severity (error/warning/info) and a fix: unapplied or negative ' +
        'scale, real-world size, origin placement, flipped or inconsistent normals, non-manifold and loose geometry, ' +
        'degenerate faces, triangle budget (max_tris, default 20000, counted after modifiers), UVs, and materials (empty ' +
        'slots, nodes glTF cannot export, missing textures), and dangling component fields: emptied by deleting ' +
        'their target (e.g. a FollowCamera whose target was deleted) or pointing at an object outside the scene; a ' +
        'field left null on purpose is fine. names defaults to "all": every mesh and every object with components; ' +
        'in the UI file (ui/ui.blend) every element of every canvas, checked only for missing image and font files ' +
        'and dangling fields (UI art is flat Emission by design). The ' +
        'result states its scope. Only objects with errors or warnings are listed; above 25 of them you get a summary ' +
        '(counts per check, the 10 worst objects); verbose: true lists every object with its stats.',
      inputSchema: object({
        names: { ...NAMES, description: 'Object name(s), "all" (default) or "selection".' },
        max_tris: { type: 'integer', minimum: 1 },
        summary: { type: 'boolean', description: 'Counts per check plus the 10 worst objects (default: when more than 25 objects have problems).' },
        verbose: { type: 'boolean', description: 'Every object with its stats and info notes. Default false.' },
      }),
      annotations: READ,
    }),
    {
      name: 'render_views',
      title: 'Render views',
      description:
        'Look at objects or the scene: ONE labelled contact sheet (default 768 px wide) from named angles, framed ' +
        'tightly on the targets, with scale references. views: front, back, left, right, side (= right), top, bottom, ' +
        'iso, camera (the scene camera); default front/side/top/iso, or top/iso for wide flat targets such as levels. ' +
        'Orthographic tiles have rulers in world metres, graph paper behind the objects and yellow dimension lines with ' +
        'the overall size; iso and top tiles have a ground grid; a blue 1.8 m person stands front-left when that helps ' +
        '(person: true/false to force). target: object name(s) (children included), "selection", ' +
        '"collection:<name>", or "scene" (default); only the targets are drawn, unless focus: true, which ghosts the ' +
        'rest of the scene and outlines the targets in orange. names: label objects (auto: 2-12 targets), or a list ' +
        'of object names to label just those (e.g. ["Player", "Key_1"]; they need not be targets; labels: [...] works ' +
        'too); the result lists them as labelled / not_in_view. shading: ' +
        '"studio" (default: studio light, material colours, cavity, outlines), "clay" (one neutral colour, for shape), ' +
        'or "material" (EEVEE look-dev). compare: a checkpoint id renders the same views of that checkpoint (left) and ' +
        'now (right), side by side (default views iso and top). save: true also writes the PNG under ' +
        '.gamestudio/mcp/renders/.',
      inputSchema: object({
        target: { type: ['string', 'array'], items: { type: 'string', minLength: 1 }, minItems: 1 },
        views: { type: 'array', items: { type: 'string', enum: VIEWS }, minItems: 1, maxItems: 9 },
        size: { type: 'integer', minimum: 128, maximum: 2048, description: 'Sheet width in pixels. Default 768.' },
        shading: { type: 'string', enum: ['studio', 'clay', 'material', 'solid', 'eevee'] },
        focus: { type: 'boolean' },
        person: { type: ['boolean', 'string'], description: 'true, false or "auto" (default: shown for targets 0.4-80 m).' },
        names: { type: ['boolean', 'string', 'array'], items: { type: 'string', minLength: 1 }, maxItems: 100, description: 'true, false, "auto", or a list of object names to label.' },
        labels: { type: ['boolean', 'array'], items: { type: 'string', minLength: 1 }, maxItems: 100, description: 'Titles, rulers, dimensions and names (default true); a list of object names labels those.' },
        compare: { type: 'string', minLength: 1, description: 'A checkpoint id: render it beside the current scene.' },
        save: { type: 'boolean' },
      }),
      annotations: READ,
      handler: async (args, call) => {
        const { save, ...params } = args;
        const result = await ctx.blender.call('render_views', params, { signal: call.signal });
        const { png, ...data } = result;
        if (save && ctx.root) data.saved = ctx.saveImage('renders', args.compare ? 'compare' : (args.views ?? ['views']).join('-'), png);
        return withImages(data, [{ data: png, mimeType: 'image/png' }]);
      },
    },
  ];
}
