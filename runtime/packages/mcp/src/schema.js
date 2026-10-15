// @ts-check
// SPDX-License-Identifier: MIT
// A small JSON Schema validator for tool arguments: the subset the tool schemas use (type,
// properties, required, additionalProperties, enum, items, minItems, maxItems, minimum,
// maximum, minLength, maxLength, oneOf of types via `type: [...]`). Errors name the path.

/**
 * @typedef {{ type?: string | string[], properties?: Record<string, Schema>, required?: string[],
 *   additionalProperties?: boolean, enum?: unknown[], items?: Schema, minItems?: number, maxItems?: number,
 *   minimum?: number, maximum?: number, minLength?: number, maxLength?: number, description?: string,
 *   default?: unknown, anyOf?: Schema[] }} Schema
 */

/** @param {unknown} value @param {string} type */
function typeMatches(value, type) {
  switch (type) {
    case 'object':
      return typeof value === 'object' && value !== null && !Array.isArray(value);
    case 'array':
      return Array.isArray(value);
    case 'string':
      return typeof value === 'string';
    case 'number':
      return typeof value === 'number' && Number.isFinite(value);
    case 'integer':
      return typeof value === 'number' && Number.isInteger(value);
    case 'boolean':
      return typeof value === 'boolean';
    case 'null':
      return value === null;
    default:
      return true;
  }
}

/** @param {unknown} value */
function describe(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}

/**
 * Returns human-readable problems (empty when valid).
 * @param {unknown} value
 * @param {Schema} schema
 * @param {string} [path]
 * @returns {string[]}
 */
export function validate(value, schema, path = 'arguments') {
  /** @type {string[]} */
  const errors = [];
  if (schema.anyOf) {
    const results = schema.anyOf.map((s) => validate(value, s, path));
    if (results.every((r) => r.length > 0)) errors.push(...(results.find((r) => r.length > 0) ?? []));
    return errors;
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((t) => typeMatches(value, t))) {
      errors.push(`${path}: expected ${types.join(' or ')}, got ${describe(value)}`);
      return errors;
    }
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push(`${path}: must be one of ${schema.enum.map((v) => JSON.stringify(v)).join(', ')}; got ${JSON.stringify(value)}`);
  }
  if (typeof value === 'number') {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${path}: must be >= ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${path}: must be <= ${schema.maximum}`);
  }
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) errors.push(`${path}: must not be empty`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${path}: longer than ${schema.maxLength} characters`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errors.push(`${path}: needs at least ${schema.minItems} item(s)`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${path}: allows at most ${schema.maxItems} items`);
    if (schema.items) value.forEach((item, i) => errors.push(...validate(item, /** @type {Schema} */ (schema.items), `${path}[${i}]`)));
  }
  if (typeMatches(value, 'object')) {
    const object = /** @type {Record<string, unknown>} */ (value);
    const properties = schema.properties ?? {};
    for (const name of schema.required ?? []) {
      if (!(name in object)) errors.push(`${path}: missing required '${name}'`);
    }
    if (schema.additionalProperties === false) {
      const known = Object.keys(properties);
      for (const name of Object.keys(object)) {
        if (!(name in properties)) errors.push(`${path}: unknown argument '${name}'${known.length ? ` (known: ${known.join(', ')})` : ''}`);
      }
    }
    for (const [name, sub] of Object.entries(properties)) {
      if (name in object && object[name] !== undefined) errors.push(...validate(object[name], sub, `${path}.${name}`));
    }
  }
  return errors;
}
