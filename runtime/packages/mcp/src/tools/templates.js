// @ts-check
// SPDX-License-Identifier: MIT
// save_template: the project as a reusable template that New Project in Blender offers. It writes only
// to the user templates folder, outside the project, and replaces a template only with overwrite.

import { ToolError } from '../util.js';
import { WRITE, object } from './common.js';

/** @typedef {import('./common.js').Context} Context */

export const TEMPLATE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;

/** @param {Context} ctx @returns {import('./common.js').ToolDefinition[]} */
export function templateTools(ctx) {
  return [
    {
      name: 'save_template',
      title: 'Save as template',
      description:
        'Save this project as a template that New Project in Blender lists under "Your templates". It copies src/, ' +
        'scenes/, assets/, public/, tools/, AGENTS.md, CLAUDE.md, .claude/ (custom skills included), .mcp.json and the ' +
        'config files to Documents/GameStudio/Templates/<id>/ (GAMESTUDIO_TEMPLATES overrides the folder), and turns the ' +
        "game's name and package paths back into placeholders; instructions only in CLAUDE.md move to the template's " +
        'AGENTS.md, with a CLAUDE.md that imports it. node_modules, .gamestudio, dist, *.blend1 and ' +
        '.claude/settings.local.json stay behind, and the project is not changed. A project made from it opens its ' +
        'copy of the start scene. Refuses when the open scene has unsaved changes (save first), when id is a ' +
        'built-in template, and when the template exists, unless overwrite is true (ask the user first; the replaced ' +
        'template is kept in the .backups folder, and `replaced` says where). The result lists the files changed to ' +
        'placeholders, top-level files left out, and warnings.',
      inputSchema: object(
        {
          id: { type: 'string', minLength: 1, maxLength: 64, description: 'Folder name: lowercase letters, digits, - and _, e.g. "arena-shooter".' },
          label: { type: 'string', minLength: 1, maxLength: 40, description: 'Name in the New Project picker. Default: from id.' },
          description: { type: 'string', minLength: 1, maxLength: 100, description: 'One line for the picker.' },
          overwrite: { type: 'boolean', description: 'Replace an existing template with this id. Default false.' },
        },
        ['id'],
      ),
      annotations: WRITE,
      handler: (args, call) => {
        if (!TEMPLATE_ID.test(args.id)) {
          throw new ToolError(`'${args.id}' is not a valid template id.`, {
            code: 'invalid_arguments',
            hint: 'Use lowercase letters, digits, - and _, starting with a letter or digit, e.g. "arena-shooter".',
          });
        }
        return ctx.blender.call('save_template', args, { signal: call.signal, timeoutMs: 120_000 });
      },
    },
  ];
}
