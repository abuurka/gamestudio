// Scene look: the gradient sky and fog from the manifest, and the post-effect settings.
import { Color, EquirectangularReflectionMapping, Fog, Group, HemisphereLight, type DataTexture } from 'three';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { World } from '../src/ecs/world';
import { PostService } from '../src/game/post';
import { skyColor, skyTexture, tintFillLight } from '../src/game/sky';
import { RapierPhysics } from '../src/physics/rapier-backend';
import { processManifest } from '../src/scene/manifest';
import { PostEffects } from '../src/schema/builtins';
import type { FakeDom } from './dom';
import { heldGame, installSceneDom, sceneFiles } from './game-fixture';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const SKY = { horizon: [0.5, 0.7, 0.8] as [number, number, number], zenith: [0.05, 0.25, 0.7] as [number, number, number], ground: [0.25, 0.3, 0.25] as [number, number, number] };

describe('sky and fog settings', () => {
  it('reads settings.sky and settings.fog, defaulting the fog colour to the horizon', () => {
    const base = { format: 'gamestudio.scene', formatVersion: 2, scene: 'x', glb: 'x.glb', entities: [] };
    const { manifest } = processManifest({ ...base, settings: { background: [0.1, 0.1, 0.1], sky: SKY, fog: { near: 10, far: 60 } } });
    expect(manifest.settings.sky).toEqual(SKY);
    expect(manifest.settings.fog).toEqual({ color: SKY.horizon, near: 10, far: 60 });
    const plain = processManifest({ ...base, settings: { background: [0.1, 0.2, 0.3], fog: { near: 5, far: 4 } } }).manifest.settings;
    expect(plain).toMatchObject({ sky: null, fog: null }); // far must be beyond near
    const flat = processManifest({ ...base, settings: { background: [0.1, 0.2, 0.3], fog: { near: 0, far: 30 } } }).manifest.settings;
    expect(flat.fog).toEqual({ color: [0.1, 0.2, 0.3], near: 0, far: 30 });
    expect(processManifest({ ...base, settings: { sky: { horizon: [1, 1] } } }).manifest.settings.sky).toBeNull();
  });

  it('draws the gradient like the sky kit: ground below, horizon, then the zenith overhead', () => {
    expect(skyColor(SKY, -0.5)).toEqual(SKY.ground);
    const close = (a: number[], b: number[]) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, 6));
    close(skyColor(SKY, 0), SKY.horizon);
    close(skyColor(SKY, 1), SKY.zenith);
    const high = skyColor(SKY, 0.6);
    expect(high[2]).toBeLessThan(SKY.horizon[2]);
    expect(high[2]).toBeGreaterThan(SKY.zenith[2]);
    const texture = skyTexture(SKY);
    expect(texture.mapping).toBe(EquirectangularReflectionMapping);
    const data = texture.image.data as Uint8Array;
    const row = (r: number) => [...data.subarray(r * 16, r * 16 + 3)];
    expect(row(0)).toEqual(row(100)); // ground
    expect(row(511)[2]).toBeLessThan(row(260)[2]!); // the zenith is deeper than the horizon
    const fill = new HemisphereLight(0xffffff, 0x000000, 0.3);
    tintFillLight(fill, SKY);
    expect(fill.groundColor.r).toBeCloseTo(0.25, 5);
    tintFillLight(fill, null);
    expect(fill.color.getHex()).toBe(0xdfe8ff);
  });
});

describe('a game with a sky and fog', () => {
  let dom: FakeDom;
  beforeAll(async () => {
    await new RapierPhysics().init();
  });
  beforeEach(() => {
    const files = sceneFiles();
    const manifest = JSON.parse(readFileSync(files['/export/basic.scene.json']!, 'utf8'));
    manifest.settings.sky = SKY;
    manifest.settings.fog = { near: 8, far: 40 };
    const file = path.join(path.dirname(files['/export/level2.scene.json']!), 'sky.scene.json');
    writeFileSync(file, JSON.stringify({ ...manifest, scene: 'sky' }));
    dom = installSceneDom();
    files['/export/sky.scene.json'] = file;
  });
  afterEach(() => {
    dom.restore();
  });

  it('uses the gradient as background, tints the fill light and adds fog; a plain scene drops them', async () => {
    const game = await heldGame(dom, { fillLight: 0.3 });
    await game.loadUrl('/export/sky.scene.json');
    const scene = game.three.scene;
    expect((scene.background as DataTexture).isDataTexture).toBe(true);
    expect(scene.fog).toBeInstanceOf(Fog);
    expect((scene.fog as Fog).near).toBe(8);
    expect((scene.fog as Fog).color.r).toBeCloseTo(SKY.horizon[0], 5);
    const fill = scene.getObjectByName('gs_fill_light') as HemisphereLight;
    expect(fill.groundColor.g).toBeCloseTo(SKY.ground[1], 5);
    await game.loadUrl('/export/basic.scene.json');
    expect(scene.background).toBeInstanceOf(Color);
    expect(scene.fog).toBeNull();
    expect(fill.color.getHex()).toBe(0xdfe8ff);
  });
});

describe('PostService', () => {
  it('is off by default; project < createGame < scene component < code overrides', () => {
    const post = new PostService({ vignette: true, vignetteAmount: 0.5 });
    expect(post.settings.bloom).toBe(false);
    expect(post.active).toBe(true); // the project turned the vignette on
    const world = new World(new Group());
    post.attach(world);
    expect(post.settings.vignetteAmount).toBe(0.5);
    const holder = world.spawn({ name: 'Camera' });
    holder.add(PostEffects, { bloom: true, bloomStrength: 1.2 });
    expect(post.settings).toMatchObject({ bloom: true, bloomStrength: 1.2, vignette: false }); // the scene wins
    post.set({ saturation: 0 });
    expect(post.settings).toMatchObject({ bloom: true, saturation: 0 });
    post.set(null);
    holder.remove(PostEffects);
    expect(post.settings).toMatchObject({ bloom: false, vignette: true, saturation: 1 });
    expect(new PostService().active).toBe(false);
  });

  it('draws normally (no effect passes) without a WebGL renderer, and sets the exposure', () => {
    const post = new PostService({ bloom: true, exposure: 1.3 });
    const raw = { toneMappingExposure: 1 };
    const drawn = post.render({ kind: 'webgpu', raw } as never, null as never, null as never);
    expect(drawn).toBe(false);
    expect(raw.toneMappingExposure).toBe(1.3);
  });
});
