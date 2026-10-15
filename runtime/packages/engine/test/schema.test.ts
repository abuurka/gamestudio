import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  defineComponent,
  defaultValues,
  field,
  fillDefaults,
  getComponent,
  libraryShaders,
  listComponents,
  toRegistryJSON,
} from '../src/schema';

describe('component schema → registry JSON (SPEC §3)', () => {
  it('produces the documented entry shape', () => {
    defineComponent('DemoSpin', {
      description: 'Rotates the entity continuously.',
      category: 'Demo',
      fields: {
        speed: field.float(1.0, { min: 0, max: 100, description: 'Radians per second' }),
        axis: field.vec3([0, 1, 0]),
      },
    });
    const json = toRegistryJSON({ generatedAt: '2026-10-09T12:00:00.000Z', sources: { DemoSpin: 'src/components/spin.ts' } });
    expect(json.format).toBe('gamestudio.registry');
    expect(json.formatVersion).toBe(1);
    expect(json.generatedAt).toBe('2026-10-09T12:00:00.000Z');
    const entry = json.components.find((c) => c.name === 'DemoSpin');
    expect(entry).toEqual({
      name: 'DemoSpin',
      description: 'Rotates the entity continuously.',
      category: 'Demo',
      builtin: false,
      source: 'src/components/spin.ts',
      fields: [
        { name: 'speed', type: 'float', default: 1.0, min: 0, max: 100, description: 'Radians per second' },
        { name: 'axis', type: 'vec3', default: [0, 1, 0] },
      ],
    });
    // Documented key order keeps the file diff-friendly.
    expect(Object.keys(entry!)).toEqual(['name', 'description', 'category', 'builtin', 'source', 'fields']);
    expect(Object.keys(entry!.fields[0]!)).toEqual(['name', 'type', 'default', 'min', 'max', 'description']);
  });

  it('serializes every field type with its extra keys', () => {
    defineComponent('AllFields', {
      fields: {
        f: field.float(0.5, { step: 0.1, unit: 'angle' }),
        i: field.int(3, { min: 1, max: 9 }),
        b: field.bool(true),
        s: field.string('hi', { multiline: true }),
        e: field.enum(['low', 'high'], 'high'),
        v2: field.vec2([1, 2]),
        v3: field.vec3(),
        c: field.color([1, 0, 0, 1]),
        target: field.entity(),
        sound: field.asset(['.ogg', '.mp3']),
      },
    });
    const entry = toRegistryJSON().components.find((c) => c.name === 'AllFields')!;
    expect(entry.fields).toEqual([
      { name: 'f', type: 'float', default: 0.5, step: 0.1, unit: 'angle' },
      { name: 'i', type: 'int', default: 3, min: 1, max: 9 },
      { name: 'b', type: 'bool', default: true },
      { name: 's', type: 'string', default: 'hi', multiline: true },
      { name: 'e', type: 'enum', default: 'high', options: ['low', 'high'] },
      { name: 'v2', type: 'vec2', default: [1, 2] },
      { name: 'v3', type: 'vec3', default: [0, 0, 0] },
      { name: 'c', type: 'color', default: [1, 0, 0, 1] },
      { name: 'target', type: 'entity', default: null },
      { name: 'sound', type: 'asset', default: '', extensions: ['.ogg', '.mp3'] },
    ]);
    expect(entry.builtin).toBe(false);
    expect(entry).not.toHaveProperty('source');
  });

  it('includes the built-in components with builtin: true and SPEC §6.3 defaults', () => {
    const json = toRegistryJSON();
    const builtins = json.components.filter((c) => c.builtin).map((c) => c.name);
    expect(builtins).toEqual([
      'Animator', 'AudioSource', 'CharacterController', 'Collider', 'FollowCamera', 'Locomotion', 'NavAgent', 'NavMesh',
      'ParticleEmitter', 'PostEffects', 'RigidBody', 'SideCamera', 'Spin',
      'UiAnchor', 'UiBar', 'UiButton', 'UiCanvas', 'UiModel', 'UiShow', 'UiSlice', 'UiSlider', 'UiState', 'UiText', 'UiToggle',
    ]);
    const defaults = (name: string) =>
      Object.fromEntries(json.components.find((c) => c.name === name)!.fields.map((f) => [f.name, f.default]));
    expect(defaults('RigidBody')).toEqual({
      type: 'dynamic', mass: 1, linearDamping: 0, angularDamping: 0.05, gravityScale: 1, lockRotation: false, ccd: false,
      lockX: false, lockY: false, lockZ: false,
    });
    expect(defaults('Collider')).toEqual({ shape: 'auto', friction: 0.5, restitution: 0, sensor: false });
    expect(defaults('CharacterController')).toEqual({ speed: 5, jumpSpeed: 6, gravity: 20, turnSpeed: 10, plane: 'none' });
    expect(defaults('FollowCamera')).toEqual({ target: null, offset: [0, 4, 8], smoothing: 5, lookAtOffset: [0, 1, 0] });
    expect(defaults('SideCamera')).toEqual({
      target: null, follow: 'both', offset: [0, 1], deadZone: [1, 2], lookAhead: 2, smoothing: 5,
      bounds: null, boundsMin: [0, 0, 0], boundsMax: [0, 0, 0], pixelSnap: false,
    });
    expect(defaults('Spin')).toEqual({ speed: 1, axis: [0, 1, 0] });
    expect(defaults('Animator')).toEqual({ clip: '', loop: true, speed: 1, playOnStart: true, fade: 0.25, graph: '', state: '' });
    expect(defaults('Locomotion')).toEqual({
      idle: 'Idle', walk: 'Walk', run: 'Run', jump: 'Jump', fall: 'Fall', walkSpeed: 0.3, runSpeed: 4, fade: 0.15, clip: '',
    });
    expect(defaults('AudioSource')).toEqual({
      clip: '', volume: 1, loop: false, autoplay: false, spatial: true,
      refDistance: 2, maxDistance: 50, rolloff: 1, distanceModel: 'inverse', channel: 'sfx',
    });
    const clip = json.components.find((c) => c.name === 'AudioSource')!.fields.find((f) => f.name === 'clip')!;
    expect(clip).toMatchObject({ type: 'asset', extensions: ['ogg', 'mp3', 'wav'] });
    const shape = json.components.find((c) => c.name === 'Collider')!.fields.find((f) => f.name === 'shape')!;
    expect(shape.options).toEqual(['auto', 'box', 'sphere', 'capsule', 'convex', 'mesh']);
    expect(defaults('PostEffects')).toMatchObject({ bloom: false, vignette: false, exposure: 1, contrast: 1, saturation: 1 });
    expect(defaults('ParticleEmitter')).toMatchObject({ playOnStart: true, loop: true, rate: 20, burst: 0, shape: 'point', blending: 'additive' });
  });

  it('matches the effect components the Blender export tests use', () => {
    // GS_UPDATE_FIXTURES=1 rewrites the fixture after a schema change.
    const file = fileURLToPath(new URL('../../../../blender/tests/addon/fixtures/effects_components.json', import.meta.url));
    const entries = toRegistryJSON().components.filter((c) => c.name === 'ParticleEmitter' || c.name === 'PostEffects');
    if (process.env.GS_UPDATE_FIXTURES) writeFileSync(file, `${JSON.stringify(entries, null, 2)}
`);
    expect(JSON.parse(readFileSync(file, 'utf8'))).toEqual(entries);
  });

  it('always writes shaders: the built-in library when the project defines none', () => {
    const json = toRegistryJSON();
    expect(json.shaders.map((s) => s.name)).toEqual(libraryShaders().map((s) => s.name));
    expect(json.shaders.every((s) => s.builtin && !('source' in s))).toBe(true);
    expect(Object.keys(json)).toEqual(['format', 'formatVersion', 'generatedAt', 'components', 'shaders']);
  });

  it('sorts components by name', () => {
    const names = toRegistryJSON().components.map((c) => c.name);
    expect(names).toEqual([...names].sort());
    expect(listComponents().map((c) => c.name)).toEqual(names);
  });

  it('replaces a component when it is redefined', () => {
    const first = defineComponent('Redefined', { fields: { a: field.int(1) } });
    const second = defineComponent('Redefined', { fields: { b: field.bool() } });
    expect(first).not.toBe(second);
    expect(getComponent('Redefined')).toBe(second);
    expect(getComponent(first)).toBe(second); // Stale handles resolve to the current schema.
    expect(toRegistryJSON().components.filter((c) => c.name === 'Redefined')).toHaveLength(1);
    expect(defaultValues(first)).toEqual({ b: false });
  });

  it('rejects invalid names and defaults', () => {
    expect(() => defineComponent('lowercase')).toThrow(/Invalid component name/);
    expect(() => defineComponent('Bad', { fields: { Upper: field.int() } })).toThrow(/Invalid field name/);
    expect(() => field.enum(['a', 'b'], 'c' as 'a')).toThrow(/not one of/);
    expect(() => field.int(1.5)).toThrow(/integer/);
    expect(() => field.vec3([1, 2] as unknown as [number, number, number])).toThrow(/3 numbers/);
  });

  it('fills defaults and coerces values', () => {
    const Thing = defineComponent('Thing', {
      fields: { n: field.int(2), v: field.vec3([1, 2, 3]), e: field.enum(['x', 'y']), t: field.entity() },
    });
    const filled = fillDefaults(Thing, { n: 4.6, v: [1, 2], e: 'nope', extra: 1, t: '' });
    expect(filled).toEqual({ n: 5, v: [1, 2, 3], e: 'x', t: null });
    const a = defaultValues(Thing);
    const b = defaultValues(Thing);
    expect(a.v).not.toBe(b.v); // Arrays are never shared between entities.
    expect(fillDefaults('NotRegistered', { keep: 1 })).toEqual({ keep: 1 });
  });
});
