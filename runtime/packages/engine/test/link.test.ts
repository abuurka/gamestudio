import { describe, expect, it } from 'vitest';
import { captureConsole, formatArgs, isShaderCompilerNoise, parseStackLocation, toErrorMessage, urlToFile } from '../src/link/capture';
import { World } from '../src/ecs/world';
import { describeEntities } from '../src/game/describe';
import { replaceComponents } from '../src/game/edits';
import '../src/schema'; // Registers the built-in components.

describe('stack parsing', () => {
  const chromeStack = [
    'TypeError: Cannot read properties of undefined',
    '    at Object.update (http://localhost:5173/@fs/F:/x/runtime/packages/engine/src/ecs/scheduler.ts:40:9)',
    '    at update (http://localhost:5173/src/systems/coins.ts?t=1700000000:31:17)',
    '    at http://localhost:5173/src/main.ts:5:1',
  ].join('\n');

  it('prefers the first frame in the game src/ folder', () => {
    expect(parseStackLocation(chromeStack)).toEqual({ file: 'src/systems/coins.ts', line: 31, col: 17 });
  });

  it('falls back to the first frame and maps /@fs/ paths', () => {
    expect(parseStackLocation('Error\n    at f (http://localhost:5173/@fs/F:/a/b.ts:2:3)')).toEqual({ file: 'F:/a/b.ts', line: 2, col: 3 });
    expect(parseStackLocation('no frames here')).toEqual({});
    expect(urlToFile('http://localhost:5173/src/a%20b.ts?v=1')).toBe('src/a b.ts');
  });

  it('builds error messages', () => {
    const error = new TypeError('bad');
    error.stack = chromeStack;
    expect(toErrorMessage(error)).toMatchObject({ t: 'error', msg: 'TypeError: bad', file: 'src/systems/coins.ts', line: 31, col: 17 });
    expect(toErrorMessage('plain')).toEqual({ t: 'error', msg: 'plain' });
    expect(formatArgs(['a', 1, { b: 2 }, undefined])).toBe('a 1 {"b":2} undefined');
  });
});

describe('console capture', () => {
  const header = 'THREE.WebGLProgram: Program Info Log:';
  const log = [
    '(210,81-129): warning X4122: sum of 1 and -1.49272e-017 cannot be represented accurately in double precision',
    '(209,37-61): warning X4122: sum of 0.996094 and -2.98545e-017 cannot be represented accurately in double precision',
  ].join('\n');

  it('drops Direct3D shader-compiler warnings from the editor log, but not real errors', () => {
    expect(isShaderCompilerNoise(`${header} ${log}`)).toBe(true);
    expect(isShaderCompilerNoise(`${header} (12,3): error X3000: syntax error`)).toBe(false);
    expect(isShaderCompilerNoise(`${header} ${log}\n(5,1): error X3004: undeclared identifier`)).toBe(false);
    expect(isShaderCompilerNoise('Bounce pad has no target height')).toBe(false);

    const seen: string[] = [];
    const restore = captureConsole((m) => seen.push(m.msg));
    try {
      console.warn(header, log);
      console.warn('a real warning');
    } finally {
      restore();
    }
    expect(seen).toEqual(['a real warning']);
  });
});

describe('editor edits and state', () => {
  it('replaces the component set, touching only changed components', () => {
    const world = new World();
    const e = world.spawn({ components: { Spin: { speed: 2 }, RigidBody: {} } });
    const changed = replaceComponents(e, { Spin: { speed: 2 }, Collider: { sensor: true } });
    expect(changed.sort()).toEqual(['Collider', 'RigidBody']);
    expect(e.components()).toEqual(['Spin', 'Collider']);
    expect(e.get('Collider')).toMatchObject({ sensor: true, shape: 'auto' });
  });

  it('describes entities with world positions and component values', () => {
    const world = new World();
    const parent = world.spawn({ id: 'gs_000000000001', name: 'P', position: [1, 0, 0], components: { Spin: {} } });
    world.spawn({ id: 'gs_000000000002', name: 'C', parent, position: [0, 2, 0] });
    const all = describeEntities(world);
    expect(all.map((s) => s.id)).toEqual(['gs_000000000001', 'gs_000000000002']);
    expect(all[1]!.position).toEqual([1, 2, 0]);
    expect(describeEntities(world, { component: 'Spin' })).toEqual([
      { id: 'gs_000000000001', name: 'P', position: [1, 0, 0], rotation: [0, 0, 0, 1], visible: true, components: { Spin: { speed: 1, axis: [0, 1, 0] } } },
    ]);
    expect(describeEntities(world, { ids: ['gs_000000000002', 'nope'] })).toHaveLength(1);
    expect(describeEntities(world, { limit: 1 })).toHaveLength(1);
  });
});
