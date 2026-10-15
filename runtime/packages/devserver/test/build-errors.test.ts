import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { forwardBuildErrors, summarize, toBuildError } from '../src/build-errors.js';

const ROOT = path.resolve('/games/my game');
const abs = (rel: string) => path.join(ROOT, rel).split(path.sep).join('/');

// What Vite 8 logs for a TypeScript syntax error: Oxc's coloured diagnostic, no `loc`.
const OXC_MESSAGE =
  'Transform failed with 1 error:\n\n\u001b[31m[PARSE_ERROR] \u001b[0mUnexpected token\n' +
  '   \u001b[38;5;246m╭\u001b[0m\u001b[38;5;246m─\u001b[0m\u001b[38;5;246m[\u001b[0m src/systems/broken.ts?t=17:6:15 \u001b[38;5;246m]\u001b[0m\n' +
  '   \u001b[38;5;246m│\u001b[0m\n \u001b[38;5;246m6 │\u001b[0m     const x = ;\n' +
  ' \u001b[38;5;240m  │\u001b[0m               ┬  \n \u001b[38;5;240m  │\u001b[0m               ╰── \n\u001b[38;5;246m───╯\u001b[0m\n';

describe('build errors', () => {
  it('reads the location from an Oxc diagnostic (1-based) and drops the code frame', () => {
    const message = toBuildError({ message: OXC_MESSAGE, id: `${abs('src/systems/broken.ts')}?t=17`, plugin: 'vite:oxc' }, ROOT);
    expect(message).toEqual({
      t: 'error',
      kind: 'build',
      msg: 'Transform failed with 1 error: [PARSE_ERROR] Unexpected token',
      file: 'src/systems/broken.ts',
      line: 6,
      col: 15,
      plugin: 'vite:oxc',
    });
    expect(summarize(OXC_MESSAGE)).not.toContain('│');
  });

  it("uses Rollup's loc (0-based column) and keeps files outside the project absolute", () => {
    const inside = toBuildError(
      { message: 'Failed to resolve import "../components/nope"', loc: { file: abs('src/systems/missing.ts'), line: 2, column: 21 }, frame: '2 | import' },
      ROOT,
    );
    expect(inside).toMatchObject({ file: 'src/systems/missing.ts', line: 2, col: 22, frame: '2 | import' });
    const outside = toBuildError({ message: 'boom', loc: { file: path.resolve('/engine/src/a.ts'), line: 1, column: 0 } }, ROOT);
    expect(outside).toMatchObject({ file: path.resolve('/engine/src/a.ts').split(path.sep).join('/'), line: 1, col: 1 });
  });

  it('falls back to the module named in the logged line, and ignores non-errors', () => {
    const ssr = toBuildError({ message: 'field.int default must be an integer, got 2.5' }, ROOT,
      '(ssr) Error when evaluating SSR module /src/components/bad.ts: field.int default must be an integer');
    expect(ssr).toMatchObject({ file: 'src/components/bad.ts', msg: 'field.int default must be an integer, got 2.5' });
    expect(ssr).not.toHaveProperty('line');
    expect(toBuildError(undefined, ROOT)).toBeNull();
    expect(toBuildError({ message: '' }, ROOT)).toBeNull();
    expect(toBuildError('text', ROOT)).toBeNull();
  });

  it('wraps logger.error, sends each error once per burst, and restores the logger', () => {
    const logged: string[] = [];
    const logger = { error: (msg: string, _options?: { error?: unknown }) => void logged.push(msg) };
    const original = logger.error;
    const sent: Array<Record<string, unknown>> = [];
    const forwarder = forwardBuildErrors(logger, ROOT, (m) => void sent.push(m));
    const error = { message: 'Failed to resolve import "x"', loc: { file: abs('src/a.ts'), line: 1, column: 0 } };
    logger.error('Internal server error: Failed to resolve import', { error });
    logger.error('Internal server error: Failed to resolve import', { error }); // a repeat
    logger.error('plain message without an error object');
    forwarder.report({ message: 'field.int default must be an integer' }, 'Error when evaluating SSR module /src/components/b.ts: x');
    forwarder.report({ message: 'field.int default must be an integer', id: abs('src/components/b.ts') }); // same, from the registry
    expect(logged).toHaveLength(3);
    expect(sent.map((m) => [m.file, m.line])).toEqual([
      ['src/a.ts', 1],
      ['src/components/b.ts', undefined],
    ]);
    forwarder.restore();
    expect(logger.error).toBe(original);
  });
});
