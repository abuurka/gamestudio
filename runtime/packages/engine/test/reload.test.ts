// Re-exports that leave GLBs as they were: manifest `files` hashes (no refetch, no re-parse: the
// cached parse is cloned) and manifest `transform` poses of objects that moved after their GLB was written.
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { DirectionalLight, Mesh, Vector3, type Color, type Material, type Object3D } from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { Game } from '../src/game/game';
import { handleEditorMessage } from '../src/link/handlers';
import { RapierPhysics } from '../src/physics/rapier-backend';
import '../src/systems';
import { installDom, type FakeDom } from './dom';
import { sceneFiles } from './game-fixture';
import { FIXTURE_DIR } from './helpers';

const COIN = 'gs_000000a00006';
const CRATE = 'gs_000000a00002';
const LAMP = 'gs_000000a00003';
const BASIC_HASH = createHash('sha1').update(readFileSync(`${FIXTURE_DIR}basic.glb`)).digest('hex').slice(0, 16);

/** A GLB with only a JSON chunk (nodes, no meshes). */
function jsonGlb(json: object): Buffer {
  let body = Buffer.from(JSON.stringify(json), 'utf8');
  body = Buffer.concat([body, Buffer.alloc((4 - (body.length % 4)) % 4, 0x20)]);
  const header = Buffer.alloc(20);
  header.write('glTF', 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(20 + body.length, 8);
  header.writeUInt32LE(body.length, 12);
  header.writeUInt32LE(0x4e4f534a, 16);
  return Buffer.concat([header, body]);
}

const dir = mkdtempSync(path.join(tmpdir(), 'gs-reload-'));
const basicManifest = (): Record<string, any> => JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));

/** Writes `/export/<name>.scene.json` (served from the temp dir); GLB names resolve next to it. */
function writeManifest(name: string, manifest: object): string {
  writeFileSync(path.join(dir, `${name}.scene.json`), JSON.stringify(manifest));
  return `/export/${name}.scene.json`;
}

// A level whose only entity instances the prefab "Coin" (one node, 1 m above the instancer).
const PREFABBED_GLB = jsonGlb({
  asset: { version: '2.0' },
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ name: 'CoinInstance', translation: [-3, 0, 0], extras: { gs_id: 'gs_inst' } }],
});
const PREFABS_GLB = jsonGlb({
  asset: { version: '2.0' },
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ name: 'CoinBody', translation: [0, 1, 0], extras: { gs_id: 'gs_c1' } }],
});
writeFileSync(path.join(dir, 'prefabbed.glb'), PREFABBED_GLB);
writeFileSync(path.join(dir, 'prefabbed.prefabs.glb'), PREFABS_GLB);
const prefabbed = (transform: object | undefined) => ({
  format: 'gamestudio.scene',
  formatVersion: 2,
  scene: 'prefabbed',
  rev: 1,
  glb: 'prefabbed.glb',
  files: { 'prefabbed.glb': 'aaaa1111', 'prefabbed.prefabs.glb': 'bbbb2222' },
  entities: [{ id: 'gs_inst', name: 'CoinInstance', type: 'EMPTY', parent: null, components: {}, prefab: 'Coin', ...(transform && { transform }) }],
  prefabs: [
    { name: 'Coin', glb: 'prefabbed.prefabs.glb', offset: [0, 0, 0], entities: [{ id: 'gs_c1', name: 'CoinBody', type: 'EMPTY', parent: null, components: {} }] },
  ],
});

let dom: FakeDom;
let requests: Array<{ path: string; search: string; cache: RequestCache | undefined }>;
let parses: ReturnType<typeof vi.spyOn>;

beforeAll(async () => {
  await new RapierPhysics().init();
});
beforeEach(() => {
  const served: Record<string, string> = { ...sceneFiles() };
  for (const name of ['hashed', 'moved', 'old', 'prefabbed']) served[`/export/${name}.scene.json`] = path.join(dir, `${name}.scene.json`);
  served['/export/prefabbed.glb'] = path.join(dir, 'prefabbed.glb');
  served['/export/prefabbed.prefabs.glb'] = path.join(dir, 'prefabbed.prefabs.glb');
  dom = installDom(served);
  const serve = globalThis.fetch;
  requests = [];
  globalThis.fetch = ((input: string | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://game.test/');
    requests.push({ path: url.pathname, search: url.search, cache: init?.cache });
    return serve(input, init);
  }) as typeof fetch;
  parses = vi.spyOn(GLTFLoader.prototype, 'parseAsync');
});
afterEach(() => {
  parses.mockRestore();
  dom.restore();
});

async function heldGameAt(url: string): Promise<Game> {
  const game = new Game(dom.canvas as unknown as HTMLCanvasElement, { environment: 0, fillLight: 0, startScene: 'basic', exportBase: '/export/' });
  await game.physics.init();
  game.test.hold();
  await game.loadUrl(url);
  return game;
}

const reload = async (game: Game, url: string): Promise<void> => {
  handleEditorMessage(game, { t: 'scene.load', url }, () => {});
  await game.idle();
};
const stop = async (game: Game): Promise<void> => {
  game.stop();
  await game.idle();
};
const glbRequests = () => requests.filter((r) => r.path.endsWith('.glb'));
const sceneRoot = (game: Game): Object3D => game.three.scene.getObjectByName('gs_scene_root')!;
const groundMesh = (game: Game): Mesh => {
  let mesh: Mesh | null = null;
  game.world.findByName('Ground')!.object3d.traverse((o) => {
    if (!mesh && o instanceof Mesh) mesh = o;
  });
  return mesh!;
};
const groundMaterial = (game: Game) => groundMesh(game).material as Material & { color: Color };

describe('manifest transforms (objects moved after the GLB was written)', () => {
  it('put objects where the manifest says, under live edits, and again after play.stop', async () => {
    const manifest = basicManifest();
    manifest.files = { 'basic.glb': BASIC_HASH };
    manifest.entities.find((e: { id: string }) => e.id === COIN).transform = { p: [5, 1, -3] };
    manifest.entities.find((e: { id: string }) => e.id === CRATE).transform = { p: [2, 6, 2], r: [0, 0, 0, 2] };
    // CrateLamp, the crate's child: its pose is local to the crate.
    manifest.entities.find((e: { id: string }) => e.id === LAMP).transform = { p: [0, 1, 0], r: [0, 0, 0, 1] };
    const url = writeManifest('moved', manifest);
    const game = await heldGameAt(url);
    expect(game.test.positions(['Coin', 'Crate']).Coin).toEqual([5, 1, -3]);
    // The crate's dynamic body starts at the new pose (physics took the object out of its parent).
    const crate = game.world.get(CRATE)!;
    expect(game.test.positions(['Crate']).Crate).toEqual([2, 6, 2]);
    const body = game.physics.bodyOf(crate)!.translation();
    expect([body.x, body.y, body.z].map((n) => Math.round(n * 1000) / 1000)).toEqual([2, 6, 2]);
    expect(game.test.positions(['CrateLamp']).CrateLamp).toEqual([2, 7, 2]);

    await stop(game);
    expect(game.test.positions(['Coin', 'Crate', 'CrateLamp'])).toEqual({ Coin: [5, 1, -3], Crate: [2, 6, 2], CrateLamp: [2, 7, 2] });

    // A live edit wins over the manifest pose and survives Stop, as edits do.
    handleEditorMessage(game, { t: 'entity.transform', items: [{ id: COIN, p: [7, 1, 7] }] }, () => {});
    expect(game.test.positions(['Coin']).Coin).toEqual([7, 1, 7]);
    await stop(game);
    expect(game.test.positions(['Coin']).Coin).toEqual([7, 1, 7]);
    // A new scene.load drops the edits: the manifest's pose again.
    await reload(game, url);
    expect(game.test.positions(['Coin']).Coin).toEqual([5, 1, -3]);
  });

  it('move a prefab instancer with the instance placed under it', async () => {
    const url = writeManifest('prefabbed', prefabbed({ p: [4, 0, 2] }));
    const game = await heldGameAt(url);
    expect(game.test.positions(['gs_inst', 'gs_inst/gs_c1'])).toEqual({ gs_inst: [4, 0, 2], 'gs_inst/gs_c1': [4, 1, 2] });
    await stop(game);
    expect(game.test.positions(['gs_inst/gs_c1'])['gs_inst/gs_c1']).toEqual([4, 1, 2]);
    // Without a transform the GLB's pose stands.
    writeManifest('prefabbed', { ...prefabbed(undefined), rev: 2 });
    await reload(game, url);
    expect(game.test.positions(['gs_inst/gs_c1'])['gs_inst/gs_c1']).toEqual([-3, 1, 0]);
    expect(game.scene.rev).toBe(2);
  });
});

describe('GLB content hashes', () => {
  it('fetch a GLB once while its hash stays the same, and clone its parse instead of parsing again', async () => {
    const manifest = { ...basicManifest(), scene: 'hashed', files: { 'basic.glb': BASIC_HASH } };
    const url = writeManifest('hashed', manifest);
    const game = await heldGameAt(url);
    const ready: number[] = [];
    game.onSceneReady = (scene) => ready.push(scene.rev);
    expect(glbRequests()).toEqual([{ path: '/export/basic.glb', search: `?h=${BASIC_HASH}`, cache: undefined }]);
    expect(requests[0]).toMatchObject({ path: url, cache: 'no-store' }); // the manifest is never cached
    expect(parses).toHaveBeenCalledTimes(1);
    const firstRoot = sceneRoot(game);
    const material = groundMaterial(game);
    const tint = material.color.getHex();
    material.color.set(0xff0000); // what game code does for a hit flash
    const geometry = groundMesh(game).geometry;
    const disposed = vi.fn();
    geometry.addEventListener('dispose', disposed);

    // A re-export that kept the GLB (same hash, new rev): only the manifest is fetched, nothing parsed.
    writeManifest('hashed', { ...manifest, rev: 2 });
    await reload(game, url);
    await stop(game);
    expect(glbRequests()).toHaveLength(1);
    expect(requests.filter((r) => r.path === url)).toHaveLength(2);
    expect(parses).toHaveBeenCalledTimes(1);
    expect(ready).toEqual([2]); // game.ready reports the manifest's rev
    expect(game.scene).toEqual({ name: 'hashed', rev: 2 });
    // A fresh scene graph that shares geometries and textures, which stay alive, with its own materials:
    // what the game did to them is gone.
    expect(sceneRoot(game)).not.toBe(firstRoot);
    expect(groundMesh(game).geometry).toBe(geometry);
    expect(groundMaterial(game)).not.toBe(material);
    expect(groundMaterial(game).color.getHex()).toBe(tint);
    expect(disposed).not.toHaveBeenCalled();
    expect(game.world.size).toBe(8);

    // The GLB changed: fetched with its new hash, parsed, and the old resources are freed.
    writeManifest('hashed', { ...manifest, rev: 3, files: { 'basic.glb': 'ffff0000ffff0000' } });
    await reload(game, url);
    expect(glbRequests().map((r) => r.search)).toEqual([`?h=${BASIC_HASH}`, '?h=ffff0000ffff0000']);
    expect(parses).toHaveBeenCalledTimes(2);
    expect(groundMesh(game).geometry).not.toBe(geometry);
    expect(disposed).toHaveBeenCalled();
  });

  it('keep directional lights aimed in cloned scenes', async () => {
    const url = writeManifest('hashed', { ...basicManifest(), scene: 'hashed', files: { 'basic.glb': BASIC_HASH } });
    const game = await heldGameAt(url);
    await stop(game); // a clone of the cached parse
    const suns: DirectionalLight[] = [];
    sceneRoot(game).traverse((o) => {
      if (o instanceof DirectionalLight) suns.push(o);
    });
    expect(suns.length).toBeGreaterThan(0);
    game.three.scene.updateMatrixWorld(true);
    for (const sun of suns) {
      expect(sun.target.parent).toBe(sun); // not a detached copy that never moves
      const aim = sun.target.getWorldPosition(new Vector3()).sub(sun.getWorldPosition(new Vector3()));
      expect(aim.length()).toBeCloseTo(1, 3);
    }
  });

  it('leave older manifests (no files) as they were: fetched with ?rev, never cached, parsed on every load', async () => {
    const url = writeManifest('old', { ...basicManifest(), scene: 'old' });
    const game = await heldGameAt(url);
    await reload(game, url);
    await stop(game);
    expect(glbRequests()).toEqual([
      { path: '/export/basic.glb', search: '?rev=1', cache: 'no-store' },
      { path: '/export/basic.glb', search: '?rev=1', cache: 'no-store' },
    ]);
    expect(parses).toHaveBeenCalledTimes(3); // load, reload, stop
    expect(game.test.positions(['Coin']).Coin).toEqual([2, 1, -3]);
  });

  it('reuse prefab GLBs by hash too, and fetch only the one that changed', async () => {
    const url = writeManifest('prefabbed', prefabbed(undefined));
    const game = await heldGameAt(url);
    expect(glbRequests().map((r) => `${r.path}${r.search}`)).toEqual(['/export/prefabbed.glb?h=aaaa1111', '/export/prefabbed.prefabs.glb?h=bbbb2222']);
    const next = prefabbed(undefined);
    writeManifest('prefabbed', { ...next, rev: 2, files: { ...next.files, 'prefabbed.prefabs.glb': 'cccc3333' } });
    await reload(game, url);
    expect(glbRequests().map((r) => `${r.path}${r.search}`).slice(2)).toEqual(['/export/prefabbed.prefabs.glb?h=cccc3333']);
    expect(parses).toHaveBeenCalledTimes(3);
    expect(game.world.get('gs_inst/gs_c1')).toBeDefined();
    expect(game.spawn('Coin').name).toBe('CoinBody');
  });

  it('do not keep bytes under a hash the dev server says they do not have (replaced after the manifest)', async () => {
    const serve = globalThis.fetch;
    globalThis.fetch = (async (input: string | URL, init?: RequestInit) => {
      const res = await serve(input, init);
      if (!String(input).includes('.glb?h=')) return res;
      const headers = new Headers(res.headers);
      headers.set('X-Content-SHA1', 'f'.repeat(40)); // other content than the manifest's hash
      return new Response(await res.arrayBuffer(), { status: res.status, headers });
    }) as typeof fetch;
    const url = writeManifest('hashed', { ...basicManifest(), scene: 'hashed', files: { 'basic.glb': BASIC_HASH } });
    const game = await heldGameAt(url);
    await reload(game, url);
    expect(glbRequests()).toHaveLength(2); // fetched again: nothing was kept under the hash
    expect(parses).toHaveBeenCalledTimes(2); // and nothing cached
  });
});

describe('play state across an editor reload', () => {
  /** Sends `scene.load`, then `command` the moment the reload starts (Blender sends both back to back). */
  const reloadWith = async (game: Game, url: string, command: 'play.start' | 'play.stop'): Promise<void> => {
    let sent = false;
    game.onStateChange = (state) => {
      if (state !== 'loading' || sent) return;
      sent = true;
      handleEditorMessage(game, { t: command }, () => {});
    };
    handleEditorMessage(game, { t: 'scene.load', url }, () => {});
    await game.idle();
    expect(sent).toBe(true);
  };

  it('ends playing when play.start comes during the load, and stopped when play.stop does', async () => {
    const url = writeManifest('hashed', { ...basicManifest(), scene: 'hashed', files: { 'basic.glb': BASIC_HASH } });
    const game = await heldGameAt(url);
    game.test.end(); // play controls are the editor's again (a held clock ignores them)
    await stop(game);
    expect(game.state).toBe('stopped');

    await reloadWith(game, url, 'play.start');
    expect(game.state).toBe('playing');
    await reloadWith(game, url, 'play.stop');
    expect(game.state).toBe('stopped');
  });
});
