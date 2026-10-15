// The agent-facing docs (AGENTS.md, CLAUDE.md, .claude/skills, docs/WORKFLOWS.md) teach the coding agent
// (Claude Code or Codex) the tools by name, and .claude/settings.json pre-approves them by name. A renamed
// or misspelled tool fails silently there, so this test checks every tool name they use against the
// server's tool list.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, describe, expect, it } from 'vitest';
import { Context } from '../src/context.js';
import { createTools } from '../src/index.js';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const TEMPLATES = path.join(REPO, 'blender/gamestudio/project_templates');
const STARTER = path.join(TEMPLATES, 'starter');
const DEMO = path.join(REPO, 'examples/demo-game');

const ctx = new Context({ root: null, env: {} });
const TOOLS = new Set(createTools(ctx).map((t) => t.name));
afterAll(() => ctx.shutdown());

// snake_case names that start like a tool (list_, set_, asset_, ...) but are something else.
const NOT_TOOLS = new Set([
  'add_clip', 'add_clips', 'remove_clip', // gs.kits.animation (Python, in run_python)
  'add_to_library', 'list_assets', 'list_libraries', 'place_asset', 'replace_asset', // gs.assetlib
]);
// Words before " {" in code that are not tool calls.
const KEYWORDS = new Set(['do', 'else', 'try', 'finally', 'return', 'class', 'interface', 'enum', 'type', 'import', 'export']);

/** @param {string} dir @returns {string[]} */
function markdownFiles(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return markdownFiles(full);
    return name.endsWith('.md') ? [full] : [];
  });
}

/** Every project template folder (starter and the genres) and the demo. */
function projects() {
  const templates = existsSync(TEMPLATES)
    ? readdirSync(TEMPLATES).map((n) => path.join(TEMPLATES, n)).filter((p) => statSync(p).isDirectory())
    : [];
  return [...templates, DEMO];
}

function agentDocs() {
  const files = [path.join(REPO, 'docs/WORKFLOWS.md')];
  for (const project of projects()) {
    for (const name of ['AGENTS.md', 'CLAUDE.md']) {
      const file = path.join(project, name);
      if (existsSync(file)) files.push(file);
    }
    files.push(...markdownFiles(path.join(project, '.claude/skills')));
  }
  return files.filter((f) => existsSync(f));
}

/**
 * Tool names a markdown file uses: calls written as `name {...}` (inline or at the start of a code
 * line), and inline `snake_case` names that begin like a tool (list_..., asset_...).
 * @param {string} text
 * @returns {{ name: string, line: number, how: string }[]}
 */
function toolMentions(text) {
  const prefixes = new Set([...TOOLS].filter((t) => t.includes('_')).map((t) => t.split('_')[0]));
  const found = [];
  const lines = text.split(/\r?\n/);
  let fence = false;
  lines.forEach((raw, i) => {
    const line = i + 1;
    if (/^\s*```/.test(raw)) {
      fence = !fence;
      return;
    }
    if (fence) {
      const call = /^\s*([a-z][a-z0-9_]*) \{/.exec(raw);
      if (call && !KEYWORDS.has(call[1])) found.push({ name: call[1], line, how: 'call in a code block' });
      return;
    }
    for (const [, span, after] of raw.matchAll(/`([^`]+)`(.?)/g)) {
      const call = /^([a-z][a-z0-9_]*) \{/.exec(span);
      if (call) {
        found.push({ name: call[1], line, how: 'call' });
        continue;
      }
      if (!/^[a-z][a-z0-9]*(_[a-z0-9]+)+$/.test(span) || after === '(') continue;
      if (prefixes.has(span.split('_')[0]) && !NOT_TOOLS.has(span)) found.push({ name: span, line, how: 'name' });
    }
  });
  return found;
}

describe('agent docs name only existing tools', () => {
  const files = agentDocs();

  it('finds the docs', () => {
    expect(files).toContain(path.join(STARTER, 'AGENTS.md'));
    expect(files).toContain(path.join(STARTER, 'CLAUDE.md'));
    expect(files).toContain(path.join(DEMO, 'AGENTS.md'));
    expect(files.some((f) => f.endsWith(path.join('playtest-and-fix', 'SKILL.md')))).toBe(true);
  });

  it.each(files.map((f) => [path.relative(REPO, f).split(path.sep).join('/'), f]))('%s', (_rel, file) => {
    const unknown = toolMentions(readFileSync(file, 'utf8'))
      .filter((m) => !TOOLS.has(m.name))
      .map((m) => `line ${m.line}: ${m.name} (${m.how})`);
    expect(unknown, `Not a gamestudio tool. Tools: ${[...TOOLS].sort().join(', ')}`).toEqual([]);
  });

  it('recognises tool mentions', () => {
    const text = [
      'Use `create_from_kit` then `set_transform {"name": "A", "snap_to_ground": true}`.',
      '`gs.kits.animation.add_clip(obj, "spin")` and `add_clip` are Python; `snap_to_ground` is a parameter.',
      '```json', 'validate_assets {"names": "all"}', '```',
      '`list_asset` is a typo.',
    ].join('\n');
    expect(toolMentions(text).map((m) => m.name)).toEqual(['create_from_kit', 'set_transform', 'validate_assets', 'list_asset']);
  });
});

describe('.claude/settings.json pre-approves only existing tools', () => {
  const settings = projects().map((p) => path.join(p, '.claude/settings.json')).filter((f) => existsSync(f));

  it('finds the settings', () => {
    expect(settings).toContain(path.join(STARTER, '.claude/settings.json'));
  });

  it.each(settings.map((f) => [path.relative(REPO, f).split(path.sep).join('/'), f]))('%s', (_rel, file) => {
    const allow = JSON.parse(readFileSync(file, 'utf8')).permissions?.allow ?? [];
    const names = allow.filter((/** @type {string} */ a) => a.startsWith('mcp__gamestudio__')).map((/** @type {string} */ a) => a.slice('mcp__gamestudio__'.length));
    expect(names.length).toBeGreaterThan(0);
    expect(names.filter((/** @type {string} */ n) => !TOOLS.has(n))).toEqual([]);
  });
});

describe('the demo ships the starter skills', () => {
  it('has identical skill files', () => {
    const rel = (/** @type {string} */ root) => markdownFiles(path.join(root, '.claude/skills')).map((f) => path.relative(root, f)).sort();
    const starter = rel(STARTER);
    expect(rel(DEMO)).toEqual(starter);
    const differ = starter.filter((f) => readFileSync(path.join(STARTER, f), 'utf8') !== readFileSync(path.join(DEMO, f), 'utf8'));
    expect(differ, 'Copy the starter skills to examples/demo-game/.claude/skills').toEqual([]);
  });
});
