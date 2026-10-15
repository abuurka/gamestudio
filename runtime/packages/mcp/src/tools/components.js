// @ts-check
// SPDX-License-Identifier: MIT
// Component and game shader tools, validated against the project's registry (.gamestudio/components.json).

import { ToolError } from '../util.js';
import { NAMES, READ, WRITE, object } from './common.js';
import { SHADER_COLORS, checkShaderColors } from './ui.js';

/** @typedef {import('./common.js').Context} Context */

const ASSET = {
  asset: { type: 'string', minLength: 1, description: 'A library asset: edit its objects in assets/<library>.blend instead of the open level (backed up first; asset_library_restore takes it back).' },
  library: { type: 'string', minLength: 1, description: 'With asset: the library (default: search all).' },
};
const ASSET_NOTE =
  'With asset: "<name>" (and library?) it edits the objects of that library asset instead: objects then names ' +
  'objects inside the asset (default: its one top object). A background Blender writes the library file in a second ' +
  'or two and the open level reloads it, so placed instances and ctx.spawn copies follow. Undo does not cover the ' +
  'library file, so it is backed up first: asset_library_restore takes the write back (the result gives the call).';

/**
 * A component tool that edits the open level, or a library asset's objects with `asset`.
 * @param {Context} ctx
 * @param {'add' | 'set' | 'remove'} op
 * @param {Omit<import('./common.js').ToolDefinition, 'handler'>} spec
 * @returns {import('./common.js').ToolDefinition}
 */
function componentTool(ctx, op, spec) {
  return {
    ...spec,
    handler: (args, call) => {
      if (args.asset !== undefined) return ctx.blender.call('asset_edit_components', { ...args, op }, { signal: call.signal });
      if (args.library !== undefined) throw new ToolError('library goes with asset.', { code: 'invalid_arguments', hint: 'Pass asset: "<name>" to edit a library asset, or leave library out.' });
      if (args.objects === undefined) throw new ToolError('objects is required (or asset, to edit a library asset).', { code: 'invalid_arguments', hint: 'Example: {"objects": "Player", "component": "Spin", "values": {"speed": 2}}' });
      return ctx.blender.call(spec.name, args, { signal: call.signal });
    },
  };
}

// The shader library's categories in listing order (the engine's src/shaders/library); a built-in shader with
// another category follows them, one without any is listed under "Other". The project's own shaders come first.
export const SHADER_CATEGORIES = ['Water', 'Nature', 'Magic & Sci-fi', 'Surfaces', 'Transformations', 'Gameplay', 'Style'];
export const PROJECT_GROUP = 'Project';
const LIBRARY_HINT = 'Library shaders show what they do in one line; list_component_types {"type": "<Shader>"} gives one\'s fields.';

/**
 * The first sentence of a description: a library shader's line in the compact listing.
 * @param {unknown} text
 */
export function firstSentence(text) {
  const s = typeof text === 'string' ? text.trim() : '';
  return /^[\s\S]+?[.!?](?=\s|$)/.exec(s)?.[0] ?? s;
}

/**
 * Compact shader entries grouped the way Blender's Game Shader menu lists them: "Project" (src/shaders, including
 * a project shader that replaces a library one of its name) first, with their fields, then the library by
 * category, one line each ({name, description}: the group is the category; `type` gives the fields). Names A-Z.
 * @param {Array<Record<string, any>>} shaders compact entries ({name, description?, category?, builtin?, fields?})
 * @param {Map<string, string>} categories shader name -> library category, for entries without one
 */
export function groupShaders(shaders, categories) {
  /** @type {Map<string, Array<Record<string, any>>>} */
  const groups = new Map();
  for (const shader of [...shaders].sort((a, b) => String(a.name).localeCompare(String(b.name)))) {
    const category = shader.category ?? categories.get(shader.name);
    const named = typeof category === 'string' && category.trim();
    const group = !shader.builtin ? PROJECT_GROUP : named ? category : 'Other';
    /** @type {Record<string, any>} */
    let entry;
    if (shader.builtin) {
      // An extension older than the one-line library entries also sends their fields: drop them here.
      entry = { name: shader.name };
      if (shader.description) entry.description = firstSentence(shader.description);
    } else {
      const { category: _category, ...rest } = shader;
      entry = rest;
      if (named) entry.category = category;
    }
    groups.set(group, [...(groups.get(group) ?? []), entry]);
  }
  const rank = (/** @type {string} */ g) => (g === PROJECT_GROUP ? -1 : SHADER_CATEGORIES.includes(g) ? SHADER_CATEGORIES.indexOf(g) : g === 'Other' ? 1000 : 100);
  const order = [...groups.keys()].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  return Object.fromEntries(order.map((g) => [g, groups.get(g)]));
}

/** @param {string} a @param {string} b */
function editDistance(a, b) {
  let row = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const next = [i];
    for (let j = 1; j <= b.length; j++) next[j] = Math.min(row[j] + 1, next[j - 1] + 1, row[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    row = next;
  }
  return row[b.length];
}

/**
 * Known names close to `name`, best first: the same letters ignoring case, spaces and a trailing "shader"
 * ("hit_flash" -> HitFlash), one containing the other ("outline" -> SelectionOutline), or a small typo.
 * @param {string} name @param {string[]} known @param {number} [max]
 */
export function closeMatches(name, known, max = 5) {
  const norm = (/** @type {string} */ s) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const query = norm(name).replace(/(.{3,})shader$/, '$1');
  /** @type {Array<[number, string]>} */
  const scored = [];
  for (const candidate of known) {
    const n = norm(candidate);
    const ratio = 1 - editDistance(query, n) / Math.max(query.length, n.length, 1);
    if (n === query) scored.push([3, candidate]);
    else if (query.length >= 3 && (n.includes(query) || query.includes(n))) scored.push([2 + Math.min(n.length, query.length) / Math.max(n.length, query.length), candidate]);
    else if (ratio >= 0.6) scored.push([1 + ratio, candidate]);
  }
  return scored.sort((a, b) => b[0] - a[0] || a[1].localeCompare(b[1])).slice(0, max).map(([, candidate]) => candidate);
}

/**
 * The names of the game shaders in a list_component_types result (a list, or grouped by category).
 * @param {any} shaders
 * @returns {string[]}
 */
function shaderNames(shaders) {
  const entries = Array.isArray(shaders) ? shaders : shaders && typeof shaders === 'object' ? Object.values(shaders).flat() : [];
  return entries.map((s) => s?.name).filter((n) => typeof n === 'string');
}

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function componentTools(ctx) {
  return [
    {
      name: 'list_component_types',
      title: 'List component types',
      description:
        'Every component the game defines (src/components/*.ts plus the engine built-ins): fields with type, default and ' +
        'range. Only these can be attached. vec3 values are runtime space (Y-up). To create a new component, write a ' +
        'defineComponent file in src/components/; the dev server regenerates the registry. Fields come as compact ' +
        '"type = default [min..max] - description" strings; verbose: true gives the raw registry entries. shaders lists ' +
        'the game shaders, grouped: "Project" (the project\'s defineShader files in src/shaders/*.ts) first, with their ' +
        `fields, then the built-in shader library by category (${SHADER_CATEGORIES.join(', ')}), one line each: ` +
        'name and what it does. type: "<Shader>" gives one shader\'s fields and full description (read it before ' +
        'setting values); set_material_shader puts one on a material.',
      inputSchema: object({
        type: { type: 'string', minLength: 1, description: 'One component (or game shader) with its raw registry entry.' },
        verbose: { type: 'boolean', description: 'Raw registry entries for all (shaders as a flat list). Default false.' },
      }),
      annotations: READ,
      handler: async (args, call) => {
        const result = await ctx.blender.call('list_component_types', args, { signal: call.signal });
        const listing = result && typeof result === 'object' && Array.isArray(result.components);
        if (!listing) return result; // one raw entry (type)
        // An extension older than game shaders sends none: a missing list means no shaders.
        /** @type {Array<Record<string, any>>} */
        const shaders = Array.isArray(result.shaders) ? result.shaders : [];
        if (args.verbose) return { ...result, shaders };
        // An older extension's compact entries carry builtin but not the library category: read it from the raw entries.
        /** @type {Map<string, string>} */
        const categories = new Map();
        if (shaders.some((s) => s.builtin && !('category' in s))) {
          const raw = await ctx.blender.call('list_component_types', { verbose: true }, { signal: call.signal });
          for (const s of Array.isArray(raw?.shaders) ? raw.shaders : []) if (typeof s?.category === 'string') categories.set(s.name, s.category);
        }
        const out = { ...result, shaders: groupShaders(shaders, categories) };
        if (shaders.some((s) => s.builtin)) out.hint = [result.hint, LIBRARY_HINT].filter(Boolean).join(' ');
        return out;
      },
    },
    componentTool(ctx, 'add', {
      name: 'add_component',
      title: 'Add component',
      description:
        'Attach a component to objects (one undo step), with defaults overridden by values. Values are validated and ' +
        'coerced against the registry; entity fields take object names. Re-adding an attached component applies values. ' +
        'Several at once: components: {"RigidBody": {"type": "kinematic"}, "Collider": {}, "CharacterController": {}} ' +
        `(instead of component/values). The running game gets the change live. ${ASSET_NOTE}`,
      inputSchema: object({
        objects: NAMES,
        component: { type: 'string', minLength: 1 },
        values: { type: 'object', description: 'Field -> value.' },
        components: { type: 'object', description: 'Component name -> values ({} for defaults).' },
        ...ASSET,
      }),
      annotations: WRITE,
    }),
    componentTool(ctx, 'set', {
      name: 'set_component',
      title: 'Set component values',
      description:
        'Change field values of an attached component (one undo step); only the given fields change. add_if_missing: ' +
        `true attaches it first when needed. ${ASSET_NOTE} Example: {"asset": "Boulder", "component": "RigidBody", ` +
        '"values": {"linearDamping": 0.2}}.',
      inputSchema: object(
        {
          objects: NAMES,
          component: { type: 'string', minLength: 1 },
          values: { type: 'object', description: 'Field -> value.' },
          add_if_missing: { type: 'boolean' },
          ...ASSET,
        },
        ['component', 'values'],
      ),
      annotations: WRITE,
    }),
    componentTool(ctx, 'remove', {
      name: 'remove_component',
      title: 'Remove component',
      description: `Detach a component from objects and drop its values (one undo step). ${ASSET_NOTE}`,
      inputSchema: object({ objects: NAMES, component: { type: 'string', minLength: 1 }, ...ASSET }, ['component']),
      annotations: WRITE,
    }),
    {
      name: 'set_material_shader',
      title: 'Set material shader',
      description:
        'Choose the game shader that draws a material in the game, and its values (one undo step). GameStudio ships a ' +
        'shader library (water, foliage and grass wind, snow, toon, dissolve, hologram, hit flash, outlines and more), ' +
        'and the project adds its own as defineShader files in src/shaders/*.ts; list_component_types lists them all ' +
        'under shaders: the project\'s with their fields, the library\'s by category with one line each ' +
        '(list_component_types {"type": "<Shader>"} gives a shader\'s fields). Prefer a library shader; ' +
        'to customise one, copy it into src/shaders/ under the same name (the project\'s definition replaces the ' +
        'library\'s); to make a new one, write a file there. The dev server regenerates the registry. Name the ' +
        'material with material (exact name), or pass objects to use the materials in their slots (slot: an index or ' +
        'a list, default every slot). values are validated and coerced against the shader\'s fields; fields left out ' +
        `keep their values. ${SHADER_COLORS} ` +
        'shader: null takes the shader off (the game draws the material as exported). The game applies the shader to ' +
        'every mesh that draws the material, prefab copies included, from the next export (export_scene, play). ' +
        'Blender\'s viewport keeps showing the material\'s nodes, so check the look in the game (game_screenshot with ' +
        'mode "scene" from a viewpoint, ' +
        'compare_views). Returns materials [{name, shader, values, users}] (users: the objects using it) and warnings. ' +
        'Examples: {"material": "GS_Rock", "shader": "Triplanar", "values": {"tile": 3.2, "wetness": 0.6}}, ' +
        '{"objects": "Hero", "shader": "Toon"}, {"objects": "Beach", "slot": 0, "shader": null}.',
      inputSchema: object(
        {
          material: { type: 'string', minLength: 1, description: 'A material name, exactly as in Blender.' },
          objects: { ...NAMES, description: 'Instead of material: the materials in the slots of these objects (names or gs_ids).' },
          slot: { type: ['integer', 'array'], items: { type: 'integer', minimum: 0 }, minItems: 1, minimum: 0, description: 'With objects: a slot index or a list of them (default: every slot).' },
          shader: { type: ['string', 'null'], minLength: 1, description: 'A shader name from list_component_types (shaders), or null to take the shader off.' },
          values: { type: 'object', description: `Field -> value, e.g. {"tile": 3.2} or {"edgeColor": "#FF8A3D"}. ${SHADER_COLORS}` },
        },
        ['shader'],
      ),
      annotations: WRITE,
      handler: async (args, call) => {
        const example = 'Example: {"material": "GS_Sand", "shader": "Triplanar"} or {"objects": "Beach", "slot": 0, "shader": "Triplanar"}';
        if ((args.material === undefined) === (args.objects === undefined)) {
          const message = args.material === undefined ? 'Pass material or objects.' : 'Pass material or objects, not both.';
          throw new ToolError(message, { code: 'invalid_arguments', hint: example });
        }
        if (args.slot !== undefined && args.objects === undefined) {
          throw new ToolError('slot goes with objects.', { code: 'invalid_arguments', hint: example });
        }
        if (args.shader === null && args.values !== undefined) {
          throw new ToolError('values goes with a shader: shader null takes the shader off.', { code: 'invalid_arguments', hint: 'Leave values out, or name the shader.' });
        }
        checkShaderColors(args.values, 'values', 'Example: {"objects": "Crate", "shader": "Dissolve", "values": {"edgeColor": "#FF8A3D"}}');
        // The bridge takes objects as a list.
        const params = typeof args.objects === 'string' ? { ...args, objects: [args.objects] } : args;
        try {
          return await ctx.blender.call('set_material_shader', params, { signal: call.signal });
        } catch (error) {
          // An unknown shader: with the library there are dozens, so name the close ones rather than all of them.
          if (error instanceof ToolError && typeof args.shader === 'string' && /^Unknown game shader\b/.test(error.message)) {
            const known = await ctx.blender
              .call('list_component_types', {}, { signal: call.signal })
              .then((r) => shaderNames(r?.shaders))
              .catch(() => []);
            if (known.length) {
              const close = closeMatches(args.shader, known);
              const all = `list_component_types lists all ${known.length} game shaders (src/shaders and the shader library); {"type": "<Shader>"} gives one's fields.`;
              error.hint = close.length ? `Close matches: ${close.join(', ')}. ${all}` : `No close match. ${all}`;
            }
          }
          throw error;
        }
      },
    },
  ];
}
