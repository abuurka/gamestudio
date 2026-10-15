// @ts-check
// SPDX-License-Identifier: MIT
// Schema snippets and helpers shared by the tool definitions.

/** @typedef {import('../protocol.js').ToolDefinition} ToolDefinition */
/** @typedef {import('../context.js').Context} Context */

export const VEC3 = { type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3 };
export const NAMES = {
  type: ['string', 'array'],
  items: { type: 'string', minLength: 1 },
  minItems: 1,
  description: 'An object name (or gs_id), or a list of them.',
};

export const READ = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
export const WRITE = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false };
export const DESTRUCTIVE = { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false };

/**
 * @param {Record<string, any>} properties
 * @param {string[]} [required]
 */
export function object(properties, required = []) {
  return { type: 'object', properties, required, additionalProperties: false };
}

/**
 * A tool that forwards its arguments to the Blender bridge method of the same name.
 * @param {Context} ctx
 * @param {Omit<ToolDefinition, 'handler'> & { method?: string, map?: (args: any) => Record<string, unknown> }} spec
 * @returns {ToolDefinition}
 */
export function bridgeTool(ctx, { method, map, ...spec }) {
  return {
    ...spec,
    handler: (args, call) => ctx.blender.call(method ?? spec.name, map ? map(args) : args, { signal: call.signal }),
  };
}
