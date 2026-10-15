// @ts-check
// SPDX-License-Identifier: MIT
// Scene tools: summary, queries, transforms, deletion and the run_python escape hatch.

import { DESTRUCTIVE, NAMES, READ, VEC3, WRITE, bridgeTool, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

const TRANSFORM_FIELDS = {
  location: { ...VEC3, description: 'Blender space: Z-up, metres.' },
  rotation_deg: { ...VEC3, description: 'XYZ Euler rotation in degrees.' },
  scale: VEC3,
};
const SNAP_FIELDS = {
  align: { type: 'string', enum: ['lowest', 'average', 'highest'], description: 'With snap_to_ground: which ground contact decides. Default "lowest" (nothing floats).' },
  tilt: { type: 'boolean', description: 'With snap_to_ground: turn the object to the slope first. Default false.' },
};

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function sceneTools(ctx) {
  return [
    bridgeTool(ctx, {
      name: 'scene_summary',
      title: 'Scene summary',
      description:
        'Compact overview of the open scene (big scenes are summarised; see max_objects): collections, every object (type, parent, location, rotation, scale, ' +
        'dimensions, components, face count; an armature root, empty or asset instance gets the size of what it carries, ' +
        'with dimensions_from), totals (vertices, triangles, materials), the camera, the selection, prefab ' +
        'collections, animated objects and the component types the game defines. Blender space: Z-up, metres, degrees.',
      inputSchema: object({
        max_objects: { type: 'integer', minimum: 1, maximum: 5000, description: 'Above this many objects (default 100) you get counts per collection and the 20 largest objects instead of every row.' },
        verbose: { type: 'boolean', description: 'Everything, not just the essentials. Default false.' },
      }),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'list_objects',
      title: 'List objects',
      description:
        'Objects matching a filter, as compact rows. filter.name is a substring or a glob (Crate*), filter.type a Blender ' +
        'type (MESH, EMPTY, LIGHT, CAMERA, CURVE), filter.component an attached component, filter.collection a collection ' +
        'name, filter.parent a parent name (null: top level), filter.selected true/false.',
      inputSchema: object({
        filter: object({
          name: { type: 'string' },
          type: { type: 'string' },
          component: { type: 'string' },
          collection: { type: 'string' },
          parent: { type: ['string', 'null'] },
          selected: { type: 'boolean' },
        }),
        limit: { type: 'integer', minimum: 1, maximum: 2000, description: 'Default 100; the result has next_offset when there are more.' },
        offset: { type: 'integer', minimum: 0 },
        verbose: { type: 'boolean', description: 'Everything, not just the essentials. Default false.' },
      }),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'get_object',
      title: 'Get object',
      description:
        'Everything about one object: transform (local and world), dimensions, world bounding box (for an armature root, ' +
        'an empty or an asset instance: of what it carries, with dimensions_from), parent and children, ' +
        'collections, components with all field values, mesh stats, UV maps, materials, modifiers, custom properties, ' +
        'the kit it was built with, its animation (action, NLA tracks, the clips the export makes) and its prefab ' +
        '(prefab_instance_of, in_prefab).',
      inputSchema: object({ name: { type: 'string', minLength: 1, description: 'Object name or gs_id.' }, verbose: { type: 'boolean', description: 'Everything, not just the essentials. Default false.' } }, ['name']),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'set_transform',
      title: 'Set transform',
      description:
        'Move, rotate or scale objects (one undo step). Either name plus location/rotation_deg/scale, or items: [{name, ' +
        'location?, rotation_deg?, scale?, snap_to_ground?}] for several at once. relative: true adds to location and ' +
        'rotation and multiplies scale. snap_to_ground: true then moves each object onto the ground: its underside ' +
        '(its lowest 10%: a crate\'s base, a tree\'s trunk, feet) is sampled in a grid and a ray finds the ground under ' +
        'each sample, ignoring the object itself. align: "lowest" (default: no part floats; on a slope the uphill side ' +
        'sinks in), "highest" (nothing sinks; the downhill side floats) or "average". tilt: true first turns the object ' +
        'to the slope under it (heading kept, up to 45 deg). Each snap reports float_m and sink_m, the gap left, with a ' +
        'hint when the ground is uneven. Pass only x/y in location and let it find z. Blender space: Z-up, metres, ' +
        'degrees. The running game follows live.',
      inputSchema: object({
        name: { type: 'string', minLength: 1 },
        ...TRANSFORM_FIELDS,
        relative: { type: 'boolean' },
        snap_to_ground: { type: 'boolean' },
        ...SNAP_FIELDS,
        items: {
          type: 'array',
          minItems: 1,
          maxItems: 500,
          items: object({ name: { type: 'string', minLength: 1 }, ...TRANSFORM_FIELDS, snap_to_ground: { type: 'boolean' }, ...SNAP_FIELDS }, ['name']),
        },
      }),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'list_clips',
      title: 'List animation clips',
      description:
        'Animation per object: the active action, NLA tracks and strips, and the clips the export makes from them (the ' +
        'active action plus each NLA track with exactly one unmuted strip; name, frame range, seconds), with warnings for ' +
        'tracks the exporter skips. Also the clips of the last export per entity. Games play clips by name: ' +
        'ctx.animation.play(entity, "Walk"). objects defaults to every animated object.',
      inputSchema: object({ objects: NAMES }),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'set_prefab',
      title: 'Mark prefab',
      description:
        'Mark a collection as a prefab (enabled: true, default) or unmark it (one undo step). Prefabs are exported as ' +
        'templates the game can spawn with ctx.spawn("<collection name>", {position}); collection instances of a prefab ' +
        'in the scene are built from the template. Running games reload.',
      inputSchema: object({ collection: { type: 'string', minLength: 1 }, enabled: { type: 'boolean' } }, ['collection']),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'ground_height',
      title: 'Ground height',
      description:
        'The height of the highest surface under each [x, y] point (rays cast straight down from above the scene): z and ' +
        'the object hit, or z: null over empty space. ignore: object names to see through (e.g. the prop you are about to ' +
        'place). Blender space, metres. Example: {"points": [[0, 0], [4.5, -2]]}.',
      inputSchema: object(
        {
          points: { type: 'array', minItems: 1, maxItems: 2000, items: { type: 'array', items: { type: 'number' }, minItems: 2, maxItems: 2 } },
          ignore: { type: 'array', items: { type: 'string' } },
        },
        ['points'],
      ),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'raycast',
      title: 'Raycast',
      description:
        'Cast a ray through the scene: the first surface hit from origin along direction (default straight down), with ' +
        'its location, normal, distance and object. ignore: object names to see through. Blender space (Z-up, metres). ' +
        'Example: {"origin": [0, -5, 1.5], "direction": [0, 1, 0]} (is the path ahead blocked?).',
      inputSchema: object(
        { origin: VEC3, direction: VEC3, max_distance: { type: 'number', minimum: 0 }, ignore: { type: 'array', items: { type: 'string' } } },
        ['origin'],
      ),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'delete_objects',
      title: 'Delete objects',
      description:
        'Delete objects (one undo step). Children are kept and unparented in place unless with_children is true. Objects ' +
        'the developer locked from AI are refused. Component fields elsewhere that pointed at a deleted object (e.g. ' +
        'Camera.FollowCamera.target) become empty: the result lists them as cleared_references.',
      inputSchema: object({ names: NAMES, with_children: { type: 'boolean' } }, ['names']),
      annotations: DESTRUCTIVE,
    }),
    {
      ...bridgeTool(ctx, {
        name: 'run_python',
        title: 'Run Python in Blender',
        description:
          'Escape hatch: run Python in Blender for anything the other tools do not cover (modelling with bmesh, modifiers, ' +
          'geometry nodes, bpy.ops, lights, world, animation). Predefined: bpy, C (context), D (data), mathutils, Vector, ' +
          'Matrix, Euler, Quaternion, math, gs (the gamestudio add-on: gs.core.components, gs.kits, ...). Returns the final ' +
          'expression (or the variable result), stdout, stderr and a traceback on error. The whole call is one undo step; on ' +
          'an error the scene is rolled back. read_only: true for queries: no undo step is recorded, and if the code changed ' +
          'data anyway the call fails and the change is undone. Runs on the main thread with the user\'s permissions: it can ' +
          'damage data, files or hang Blender, so prefer the structured tools, keep code short (split long work over several ' +
          'calls), never touch files outside the project, and never work around objects locked from AI.',
        inputSchema: object(
          {
            code: { type: 'string', minLength: 1, maxLength: 200_000 },
            timeout: { type: 'number', minimum: 1, maximum: 600, description: 'Seconds before Python code is interrupted. Default 60.' },
            rollback_on_error: { type: 'boolean', description: 'Undo the partial changes when the code raises. Default true.' },
            label: { type: 'string', maxLength: 60, description: 'Name of the undo step.' },
            read_only: { type: 'boolean', description: 'A query: no undo step; data changes fail and are undone. Default false.' },
          },
          ['code'],
        ),
        annotations: DESTRUCTIVE,
      }),
      handler: (args, call) => ctx.blender.call('run_python', args, { timeoutMs: ((args.timeout ?? 60) + 30) * 1000, signal: call.signal }),
    },
  ];
}
