// @ts-check
// SPDX-License-Identifier: MIT
// Asset library tools (blender/gamestudio/assetlib.py) and the scene twin (core/twin.py).

import { NAMES, READ, VEC3, WRITE, bridgeTool, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

const LIBRARY = { type: 'string', minLength: 1, description: 'Library name: assets/<library>.blend (default: search all).' };

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function libraryTools(ctx) {
  return [
    bridgeTool(ctx, {
      name: 'asset_list',
      title: 'List library assets',
      description:
        'Assets in the project\'s libraries (assets/<library>.blend): name, library, catalog, tags, description, prefab, ' +
        'object count, components, size in metres and how many are placed in the open level. Filter by library, catalog ' +
        '(path prefix) or tag. Assets are reusable collections: place them with asset_place, spawn them in game code ' +
        'after asset_spawnable. backups: true also lists the library backups (newest first: library, id, tool, asset, ' +
        'created) that asset_library_restore can put back.',
      inputSchema: object({
        library: LIBRARY,
        catalog: { type: 'string' },
        tag: { type: 'string' },
        backups: { type: 'boolean', description: 'Also list the backups of the library files (all, or of library).' },
      }),
      annotations: READ,
    }),
    bridgeTool(ctx, {
      name: 'asset_add',
      title: 'Add to asset library',
      description:
        'Copy objects (their children come along) into a library file as an asset: one asset named name (default: the ' +
        'first top object), or one per top object with separate: true. Same-named assets are replaced. move: true also ' +
        'replaces the objects in the open level with an instance of the new asset, where they stood (that part is one ' +
        'undo step). spawn_only: true removes the objects from the open level instead and links the asset as a ' +
        'spawnable template (no instance is placed), so game code can ctx.spawn("<asset>") it: one call instead of ' +
        'asset_add, delete_objects and asset_spawnable. catalog (e.g. "props/nature"), tags, description; prefab ' +
        '(default true) makes it spawnable in game code. Takes a second or two (a background Blender writes the ' +
        'library). Undo does not cover the library file, so it is backed up first: the result gives library_backup and ' +
        'the asset_library_restore call that puts the previous file back. To change a library asset later, use ' +
        'set_component / add_component / remove_component with asset.',
      inputSchema: object(
        {
          objects: NAMES,
          library: { type: 'string', minLength: 1, description: 'Default "props" (assets/props.blend, created if missing).' },
          name: { type: 'string', minLength: 1 },
          separate: { type: 'boolean' },
          move: { type: 'boolean' },
          spawn_only: { type: 'boolean', description: 'Remove the objects from the level and make the asset spawnable (ctx.spawn), without placing it.' },
          catalog: { type: 'string' },
          tags: { type: ['array', 'string'], items: { type: 'string' } },
          description: { type: 'string', maxLength: 500 },
          prefab: { type: 'boolean' },
        },
        ['objects'],
      ),
      annotations: { ...WRITE, destructiveHint: true },
    }),
    bridgeTool(ctx, {
      name: 'asset_place',
      title: 'Place asset',
      description:
        'Place an instance of a library asset in the open level (one undo step): an empty that shows the asset, named ' +
        '<asset>_01, _02, ... unless name is given, in collection (created if missing). location in metres, rotation_deg ' +
        'XYZ degrees, scale a number or [x, y, z] (Blender space). Per-copy components go on the returned instance. ' +
        'Follow with set_transform {"name": <instance>, "snap_to_ground": true} on uneven ground.',
      inputSchema: object(
        {
          asset: { type: 'string', minLength: 1 },
          library: LIBRARY,
          location: VEC3,
          rotation_deg: VEC3,
          scale: { type: ['number', 'array'], items: { type: 'number' }, minItems: 3, maxItems: 3, minimum: 0 },
          name: { type: 'string', minLength: 1 },
          collection: { type: 'string', minLength: 1 },
        },
        ['asset'],
      ),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'asset_replace',
      title: 'Replace asset',
      description: 'Make an asset instance show another asset (one undo step); it keeps its name, transform and components.',
      inputSchema: object({ instance: { type: 'string', minLength: 1 }, asset: { type: 'string', minLength: 1 }, library: LIBRARY }, ['instance', 'asset']),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'asset_make_local',
      title: 'Make asset local',
      description:
        'Turn an asset instance into editable local copies of the asset\'s objects, for a one-off edit (one undo step). ' +
        'The instance empty stays as their parent with its name, transform, components and id; the copies get new ids ' +
        'and no longer follow the library.',
      inputSchema: object({ instance: { type: 'string', minLength: 1 } }, ['instance']),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'asset_spawnable',
      title: 'Make asset spawnable',
      description:
        'Link a prefab asset into the open level without placing it, so game code can create copies with ' +
        'ctx.spawn("<asset>", {position: [x, y, z]}). It is exported as a template only (one undo step).',
      inputSchema: object({ asset: { type: 'string', minLength: 1 }, library: LIBRARY }, ['asset']),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'asset_library_restore',
      title: 'Restore asset library',
      description:
        'Take back a library write: put assets/<library>.blend back as a backup has it (default: the latest; backup: ' +
        'an id from asset_list {"backups": true} or from a writing tool\'s result), then reload it so the instances and ' +
        'spawns of this level follow. Every library write (asset_add, and the component tools with asset) is backed up ' +
        'first under .gamestudio/library-backups/<library>/ (the last 20 per library); so is the file this call ' +
        'replaces, and the result gives the call that undoes the restore. A backup taken before the library existed ' +
        'removes it again. The open level\'s own part of a write (asset_add move or spawn_only) is an ordinary undo step.',
      inputSchema: object(
        {
          library: { type: 'string', minLength: 1, description: 'The library: "props" or "assets/props.blend".' },
          backup: { type: 'string', minLength: 1, description: 'A backup id (or a unique prefix). Default: the latest.' },
        },
        ['library'],
      ),
      annotations: WRITE,
    }),
    bridgeTool(ctx, {
      name: 'asset_reload',
      title: 'Reload libraries',
      description: 'Reload linked library files after they changed on disk (all, or the named libraries).',
      inputSchema: object({ libraries: { type: 'array', items: { type: 'string' } } }),
      annotations: WRITE,
    }),
    {
      name: 'scene_twin',
      title: 'Scene twin',
      description:
        'A compact, one-line-per-object text view of the open level as it is now (unsaved changes included): scenes, ' +
        'collections, objects with type, transform (local, Blender space, degrees), size, components that differ from ' +
        'their defaults, kits, asset instances, materials, animation. The same format that is written next to the ' +
        '.blend on every save (<name>.scene.json; save returns its path) and that git can diff. Big levels list the most ' +
        'important objects and summarise the rest per collection.',
      inputSchema: object({}),
      annotations: READ,
      handler: async (_args, call) => {
        const result = await ctx.blender.call('scene_twin', {}, { signal: call.signal });
        return result.text;
      },
    },
  ];
}
