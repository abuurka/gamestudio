// Particles: the pool and emission cycle, presets and bursts, the ParticleEmitter component in a
// running game (deterministic under the test clock, frozen while paused), and the instance buffer.
import { Group, Matrix4, PerspectiveCamera, Quaternion, Vector3 } from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { defineSystem, removeSystem } from '../src/ecs/system';
import { World } from '../src/ecs/world';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { ParticleEmitter } from '../src/schema/builtins';
import { defaultValues } from '../src/schema/registry';
import '../src/systems';
import { EmitterCore, ParticlePool, type ParticleSettings } from '../src/vfx/particles';
import { mulberry32 } from '../src/game/random';
import { spriteAlpha } from '../src/vfx/textures';
import { VFX_PRESETS, VfxService } from '../src/vfx/vfx';
import type { FakeDom } from './dom';
import { fakeRenderer, heldGame, installSceneDom } from './game-fixture';

const settings = (values: Partial<ParticleSettings> = {}): ParticleSettings => ({ ...defaultValues<ParticleSettings>(ParticleEmitter), ...values });
const FRAME = { matrix: null, rotation: null };
const DOWN = new Vector3(0, -9.81, 0);
const DT = 1 / 60;

describe('ParticlePool and EmitterCore', () => {
  it('ages, moves and removes particles; gravity and drag act on velocity', () => {
    const core = new EmitterCore(new ParticlePool(8), mulberry32(1));
    const s = settings({ burst: 3, rate: 0, loop: false, duration: 0.1, speed: [2, 2], spread: 0, lifetime: [0.5, 0.5], gravity: 1, drag: 0 });
    core.play();
    core.step(0, s, FRAME, DOWN);
    expect(core.pool.count).toBe(3);
    expect([...core.pool.vel.subarray(0, 3)].map((v) => Math.round(v * 1000) / 1000)).toEqual([0, 2, 0]);
    for (let i = 0; i < 10; i++) core.step(DT, s, FRAME, DOWN);
    expect(core.pool.vel[1]).toBeCloseTo(2 - 9.81 * 10 * DT, 4);
    expect(core.pool.pos[1]).toBeGreaterThan(0.1);
    for (let i = 0; i < 25; i++) core.step(DT, s, FRAME, DOWN);
    expect(core.pool.count).toBe(0);
    expect(core.finished).toBe(true);
  });

  it('emits a burst per cycle and `rate` per second; one cycle without loop', () => {
    const looping = new EmitterCore(new ParticlePool(1000), mulberry32(2));
    const s = settings({ burst: 5, rate: 30, duration: 0.5, loop: true, lifetime: [10, 10] });
    looping.play();
    for (let i = 0; i < 60; i++) looping.step(DT, s, FRAME, DOWN);
    // Two cycles in 1 s: 2 bursts of 5 and 30 per second.
    expect(looping.pool.count).toBe(10 + 30);
    expect(looping.playing).toBe(true);
    const once = new EmitterCore(new ParticlePool(1000), mulberry32(2));
    once.play();
    for (let i = 0; i < 60; i++) once.step(DT, { ...s, loop: false }, FRAME, DOWN);
    expect(once.pool.count).toBe(5 + 15);
    expect(once.playing).toBe(false);
  });

  it('stops at the pool size, emits in the cone, from the shapes, and repeats from a seed', () => {
    const full = new EmitterCore(new ParticlePool(4), mulberry32(3));
    full.emit(10, settings(), FRAME);
    expect(full.pool.count).toBe(4);

    const cone = new EmitterCore(new ParticlePool(200), mulberry32(4));
    cone.emit(200, settings({ direction: [1, 0, 0], spread: 0.3, speed: [1, 1] }), FRAME);
    for (let i = 0; i < 200; i++) expect(cone.pool.vel[i * 3]!).toBeGreaterThan(Math.cos(0.3) - 1e-6);

    const sphere = new EmitterCore(new ParticlePool(200), mulberry32(5));
    sphere.emit(200, settings({ shape: 'sphere', radius: 2, speed: [1, 1] }), FRAME);
    for (let i = 0; i < 200; i++) {
      const p = new Vector3().fromArray(sphere.pool.pos, i * 3);
      const v = new Vector3().fromArray(sphere.pool.vel, i * 3);
      expect(p.length()).toBeLessThanOrEqual(2 + 1e-6);
      if (p.length() > 0.01) expect(p.normalize().dot(v)).toBeGreaterThan(0.999); // outward
    }
    const box = new EmitterCore(new ParticlePool(100), mulberry32(6));
    box.emit(100, settings({ shape: 'box', box: [2, 0, 4] }), FRAME);
    for (let i = 0; i < 100; i++) {
      expect(Math.abs(box.pool.pos[i * 3]!)).toBeLessThanOrEqual(1);
      expect(Math.abs(box.pool.pos[i * 3 + 1]!)).toBeLessThan(1e-9);
      expect(Math.abs(box.pool.pos[i * 3 + 2]!)).toBeLessThanOrEqual(2);
    }
    // World space: born at the emitter's matrix, moving along its rotation.
    const world = new EmitterCore(new ParticlePool(10), mulberry32(7));
    const rotation = new Quaternion().setFromAxisAngle(new Vector3(0, 0, 1), -Math.PI / 2); // +Y → +X
    world.emit(1, settings({ spread: 0, speed: [3, 3] }), { matrix: new Matrix4().compose(new Vector3(5, 1, 0), rotation, new Vector3(1, 1, 1)), rotation });
    expect([...world.pool.pos.subarray(0, 3)].map((n) => Math.round(n * 1e3) / 1e3)).toEqual([5, 1, 0]);
    expect(world.pool.vel[0]).toBeCloseTo(3, 5);

    const a = new EmitterCore(new ParticlePool(50), mulberry32(9));
    const b = new EmitterCore(new ParticlePool(50), mulberry32(9));
    a.emit(50, settings({ spread: Math.PI }), FRAME);
    b.emit(50, settings({ spread: Math.PI }), FRAME);
    expect([...b.pool.vel]).toEqual([...a.pool.vel]);
  });

  it('draws soft sprites: opaque middle, clear corners', () => {
    for (const kind of ['dot', 'spark', 'star', 'smoke', 'square'] as const) {
      expect(spriteAlpha(kind, 0, 0)).toBeGreaterThan(0.3);
      expect(spriteAlpha(kind, 0.98, 0.98)).toBeLessThan(0.05);
    }
  });
});

describe('VfxService', () => {
  const camera = new PerspectiveCamera();
  camera.position.set(0, 2, 10);
  camera.lookAt(0, 0, 0);

  it('plays every preset once at a point, then frees and reuses its buffers', () => {
    const world = new World(new Group());
    const vfx = new VfxService(() => {});
    vfx.reset(world, (p) => p);
    for (const preset of VFX_PRESETS) {
      const effect = vfx.burst([0, 1, 0], preset);
      expect(effect.count).toBeGreaterThan(0);
      for (let i = 0; i < 600 && effect.alive; i++) vfx.step(DT, DOWN);
      expect(effect.alive).toBe(false);
    }
    expect(vfx.stats()).toEqual({ emitters: 0, particles: 0 });
    const first = vfx.burst([0, 0, 0], 'sparkle');
    vfx.render(camera);
    const mesh = world.root.children.find((o) => o.name === 'gs_particles')!;
    expect(mesh).toBeDefined();
    for (let i = 0; i < 120; i++) vfx.step(DT, DOWN);
    expect(first.alive).toBe(false);
    expect(mesh.parent).toBeNull();
    vfx.burst([0, 0, 0], 'sparkle');
    vfx.render(camera);
    expect(world.root.children.find((o) => o.name === 'gs_particles')).toBe(mesh); // pooled
  });

  it('takes options over a preset: scale, amount, colour, palette', () => {
    const world = new World(new Group());
    const vfx = new VfxService(() => {});
    vfx.reset(world, (p) => p);
    const plain = vfx.burst([0, 0, 0], { burst: 10, colorStart: '#ff0000' });
    expect(plain.count).toBe(10);
    const doubled = vfx.burst([0, 0, 0], 'sparkle', { amount: 2 });
    expect(doubled.count).toBe(32);
    const warned: string[] = [];
    const strict = new VfxService((m) => warned.push(m));
    strict.reset(world, (p) => p);
    strict.burst([0, 0, 0], 'fireworks' as never);
    expect(warned[0]).toMatch(/Unknown preset "fireworks"/);
  });

  it('writes positions relative to the emitter, sizes and colours over life, alpha particles back to front', () => {
    const world = new World(new Group());
    const vfx = new VfxService(() => {});
    vfx.reset(world, (p) => p);
    vfx.burst([2, 0, 0], { burst: 40, blending: 'alpha', spread: Math.PI, speed: [1, 3], size: [0.4, 0.2], lifetime: [1, 1] });
    for (let i = 0; i < 30; i++) vfx.step(DT, DOWN);
    vfx.render(camera);
    const mesh = world.root.children.find((o) => o.name === 'gs_particles') as unknown as {
      position: Vector3;
      geometry: { instanceCount: number; attributes: Record<string, { data: { array: Float32Array } }> };
    };
    expect(mesh.position.toArray()).toEqual([2, 0, 0]);
    expect(mesh.geometry.instanceCount).toBe(40);
    const data = mesh.geometry.attributes.iOffset!.data.array;
    const forward = camera.getWorldDirection(new Vector3());
    let last = Infinity;
    for (let k = 0; k < 40; k++) {
      const depth = new Vector3(data[k * 12]! + 2, data[k * 12 + 1]!, data[k * 12 + 2]!).dot(forward);
      expect(depth).toBeLessThanOrEqual(last + 1e-5); // far first
      last = depth;
      expect(data[k * 12 + 7]).toBeGreaterThan(0.2 * 0.8 - 1e-6); // halfway between 0.4 and 0.2 (± 20 %)
      expect(data[k * 12 + 7]).toBeLessThan(0.4 * 1.2);
    }
  });
});

describe('ParticleEmitter in a game', () => {
  let dom: FakeDom;
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    dom = installSceneDom();
  });
  afterEach(() => {
    removeSystem('BurstProbe');
    dom.restore();
  });

  async function emitterRun(render: boolean) {
    defineSystem({
      name: 'BurstProbe',
      update(ctx) {
        if (ctx.input.keyDown('KeyB')) ctx.vfx.burst(ctx.world.findByName('Crate')!, 'explosion');
      },
    });
    const game = await heldGame(dom);
    fakeRenderer(game);
    game.world.findByName('Coin')!.add(ParticleEmitter, { rate: 40, burst: 10, lifetime: [0.5, 1], spread: 0.6 });
    await game.test.begin();
    game.test.schedule([{ at: 0.5, key: 'KeyB' }]);
    const counts: number[] = [];
    for (let i = 0; i < 60; i++) {
      await game.test.advance(1);
      if (render) game.test.render();
      counts.push(game.vfx.count());
    }
    const mesh = game.three.scene.getObjectsByProperty('name', 'gs_particles');
    const out = { counts, state: game.test.state().vfx, meshes: mesh.length, game };
    removeSystem('BurstProbe');
    return out;
  }

  it('emits from a Blender-style component, bursts from code, and repeats exactly with or without drawing', async () => {
    const drawn = await emitterRun(true);
    const blind = await emitterRun(false);
    expect(blind.counts).toEqual(drawn.counts);
    expect(drawn.counts[0]).toBe(10); // the first step: the burst
    const jumps = drawn.counts.slice(1).map((n, i) => n - drawn.counts[i]!);
    expect(jumps.findIndex((d) => d > 50)).toBe(29); // the explosion, on the step that reads B at 0.5 s
    expect(drawn.state.emitters).toBe(4); // the emitter and the explosion layers still alive (the flash is gone)
    expect(drawn.meshes).toBeGreaterThanOrEqual(2);
    expect(blind.meshes).toBe(0); // nothing is drawn until the game draws
  });

  it('freezes while paused, follows live edits and lets particles finish after the entity goes', async () => {
    const game = await heldGame(dom);
    const coin = game.world.findByName('Coin')!;
    coin.add(ParticleEmitter, { rate: 20, lifetime: [1, 1] });
    await game.test.begin();
    await game.test.advance(30);
    const count = game.vfx.count(coin);
    expect(count).toBeGreaterThan(5);
    expect(game.vfx.isPlaying(coin)).toBe(true);
    game.pause(true);
    await game.test.advance(30);
    expect(game.vfx.count(coin)).toBe(count);
    game.pause(false);
    game.replaceComponents(coin.id, { Spin: coin.get('Spin'), Collider: coin.get('Collider'), ParticleEmitter: { rate: 0, lifetime: [1, 1] } });
    await game.test.advance(70);
    expect(game.vfx.count(coin)).toBe(0); // no new ones; the old ones died
    game.vfx.emit(coin, 12);
    expect(game.vfx.count(coin)).toBe(12);
    game.vfx.stop(coin, { clear: true });
    expect(game.vfx.count(coin)).toBe(0);
    game.vfx.play(coin);
    expect(game.vfx.isPlaying(coin)).toBe(true);
    const effect = game.vfx.burst(coin, 'pickup');
    coin.destroy();
    await game.test.advance(1);
    expect(effect.alive).toBe(true);
    expect(game.vfx.count()).toBeGreaterThan(0);
    await game.test.advance(120);
    expect(game.vfx.stats()).toEqual({ emitters: 0, particles: 0 });
  });
});
