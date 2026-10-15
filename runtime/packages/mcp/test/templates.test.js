// save_template: the project as a user template (blender/gamestudio/bridge/templates.py does the copying;
// blender/tests/addon/test_user_templates.py tests it in Blender).
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Context } from '../src/context.js';
import { createGameStudioServer, createTools } from '../src/index.js';
import { callTool, makeProject, startFakeBridge } from './helpers.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');

let project;
let bridge;
let server;
let ctx;

beforeEach(async () => {
  project = makeProject();
  bridge = await startFakeBridge({
    save_template: (p) => {
      if (p.id === 'taken' && !p.overwrite) {
        throw { code: -32000, message: "The template 'taken' already exists in C:/Templates.", data: { hint: 'Pick another id, or replace it (overwrite).' } };
      }
      return { id: p.id, label: p.label ?? 'Arena', path: `C:/Templates/${p.id}`, files: 12, placeholders: ['package.json'], left_out: [], warnings: [] };
    },
  });
  bridge.writeDiscovery(project.root);
  ctx = new Context({ root: project.root, env: { HOME: project.root } });
  ({ server } = createGameStudioServer({ root: project.root, context: ctx }));
});
afterEach(async () => {
  await ctx.shutdown();
  await bridge.close();
  project.cleanup();
});

describe('save_template', () => {
  it('is a write tool that only writes outside the project', () => {
    const tool = createTools(ctx).find((t) => t.name === 'save_template');
    expect(tool).toBeDefined();
    expect(tool.annotations).toMatchObject({ readOnlyHint: false, destructiveHint: false });
    expect(tool.inputSchema.required).toEqual(['id']);
    expect(tool.description).toContain('overwrite');
    expect(tool.description).toContain('GAMESTUDIO_TEMPLATES');
  });

  it('forwards the arguments to Blender', async () => {
    const r = await callTool(server, 'save_template', { id: 'arena-shooter', label: 'Arena', description: 'Waves of enemies in a pit' });
    expect(r.isError).toBe(false);
    expect(r.data).toMatchObject({ id: 'arena-shooter', path: 'C:/Templates/arena-shooter', files: 12 });
    expect(bridge.calls.at(-1)).toEqual({ method: 'save_template', params: { id: 'arena-shooter', label: 'Arena', description: 'Waves of enemies in a pit' } });
    await callTool(server, 'save_template', { id: 'taken', overwrite: true });
    expect(bridge.calls.at(-1).params).toEqual({ id: 'taken', overwrite: true });
  });

  it('refuses a bad id or label before calling Blender', async () => {
    const bad = await callTool(server, 'save_template', { id: 'My Template' });
    expect(bad.isError).toBe(true);
    expect(bad.data).toMatchObject({ code: 'invalid_arguments', error: expect.stringMatching(/not a valid template id/) });
    for (const id of ['../escape', '-dash', 'UPPER', '']) {
      expect((await callTool(server, 'save_template', { id })).isError).toBe(true);
    }
    const long = await callTool(server, 'save_template', { id: 'ok', label: 'x'.repeat(41) });
    expect(long.data.problems).toEqual(['arguments.label: longer than 40 characters']);
    expect((await callTool(server, 'save_template', {})).data.problems).toEqual(["arguments: missing required 'id'"]);
    expect(bridge.calls).toHaveLength(0);
  });

  it("passes Blender's refusal on with its hint", async () => {
    const r = await callTool(server, 'save_template', { id: 'taken' });
    expect(r.isError).toBe(true);
    expect(r.data.error).toContain('already exists');
    expect(r.data.hint).toContain('overwrite');
  });

  it('is pre-approved in the starter and the demo, like checkpoint', () => {
    for (const rel of ['blender/gamestudio/project_templates/starter/.claude/settings.json', 'examples/demo-game/.claude/settings.json']) {
      const allow = JSON.parse(readFileSync(path.join(REPO, rel), 'utf8')).permissions.allow;
      expect(allow, rel).toContain('mcp__gamestudio__save_template');
      expect(allow, rel).toContain('mcp__gamestudio__checkpoint');
    }
  });
});
