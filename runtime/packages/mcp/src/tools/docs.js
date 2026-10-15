// @ts-check
// SPDX-License-Identifier: MIT
// engine_docs: the engine API reference, by section. The same lookup as the in-Blender
// assistant's get_engine_docs (blender/gamestudio/assistant/tools.py), done here so it works
// without Blender.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { engineDocsPath } from '../discovery.js';
import { ToolError } from '../util.js';
import { READ, object } from './common.js';

const MAX_CHARS = 30_000;
const HERE = fileURLToPath(import.meta.url);

/**
 * Splits markdown into (heading, body) sections at every heading line.
 * @param {string} markdown
 * @returns {[string, string][]}
 */
export function sections(markdown) {
  /** @type {[string, string][]} */
  const parts = [];
  let heading = 'Introduction';
  /** @type {string[]} */
  let buf = [];
  for (const line of markdown.split(/\r?\n/)) {
    if (line.startsWith('#')) {
      if (buf.length) parts.push([heading, buf.join('\n')]);
      heading = line.replace(/^#+/, '').trim();
      buf = [line];
    } else buf.push(line);
  }
  if (buf.length) parts.push([heading, buf.join('\n')]);
  return parts;
}

/**
 * @param {string} text
 * @param {string | undefined} topic
 */
export function lookup(text, topic) {
  const all = sections(text);
  const query = (topic ?? '').toLowerCase().trim();
  if (!query) {
    if (text.length <= MAX_CHARS) return text;
    return { sections: all.map(([h]) => h), note: 'The reference is long; call engine_docs again with a topic (a section heading).' };
  }
  let hits = all.filter(([h]) => h.toLowerCase().includes(query));
  if (hits.length === 0) hits = all.filter(([, body]) => body.toLowerCase().includes(query)).slice(0, 4);
  if (hits.length === 0) return { error: `No section matching '${topic}'.`, sections: all.map(([h]) => h) };
  const joined = hits.map(([, body]) => body).join('\n\n');
  return joined.length > MAX_CHARS ? `${joined.slice(0, MAX_CHARS)}\n... [truncated]` : joined;
}

/** @param {import('./common.js').Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function docsTools(ctx) {
  return [
    {
      name: 'engine_docs',
      title: 'Engine docs',
      description:
        'The GameStudio engine API reference (API.md): defineComponent, defineSystem, the system context (input, physics, ' +
        'hud, world), entities, built-in components. Pass a topic (a section heading or a word) to get just the matching ' +
        'sections; without one you get the section list. Only use APIs documented here when writing src/ code.',
      inputSchema: object({ topic: { type: 'string' } }),
      annotations: READ,
      handler: async (args) => {
        const file = engineDocsPath(ctx.root, HERE);
        if (!file) throw new ToolError('Engine docs (API.md) not found.', { code: 'not_found', hint: 'The project\'s game packages are not set up yet: open the project in Blender once (launch_blender); GameStudio\'s setup links node_modules/@gamestudio/engine.' });
        return lookup(readFileSync(file, 'utf8'), args.topic);
      },
    },
  ];
}
