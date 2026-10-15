// A real Game on the basic fixture in the fake DOM, with a held clock, plus a second scene
// ("level2": the same level with other ids) for scene changes.
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Game, type GameOptions } from '../src/game/game';
import { installDom, type FakeDom } from './dom';
import { FIXTURE_DIR } from './helpers';

export const BASIC = '/export/basic.scene.json';

/** Rewrites the ids in a GLB's JSON chunk (extras.gs_id), keeping the binary chunk. */
function rewriteGlb(buffer: Buffer, map: (id: string) => string): Buffer {
  const jsonLength = buffer.readUInt32LE(12);
  const json = JSON.parse(buffer.subarray(20, 20 + jsonLength).toString('utf8'));
  for (const node of json.nodes ?? []) if (node.extras?.gs_id) node.extras.gs_id = map(node.extras.gs_id);
  let body = Buffer.from(JSON.stringify(json), 'utf8');
  body = Buffer.concat([body, Buffer.alloc((4 - (body.length % 4)) % 4, 0x20)]);
  const rest = buffer.subarray(20 + jsonLength);
  const header = Buffer.alloc(20);
  header.write('glTF', 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(20 + body.length + rest.length, 8);
  header.writeUInt32LE(body.length, 12);
  header.writeUInt32LE(0x4e4f534a, 16);
  return Buffer.concat([header, body, rest]);
}

let files: Record<string, string> | null = null;

/** URL path → file: the fixture as "basic", and "level2" (ids gs_000000b…, Coin spins at 9). */
export function sceneFiles(): Record<string, string> {
  if (files) return files;
  const dir = mkdtempSync(path.join(tmpdir(), 'gs-level2-'));
  const swap = (id: string): string => id.replace('gs_000000a', 'gs_000000b');
  const manifest = JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
  manifest.scene = 'level2';
  manifest.glb = 'level2.glb';
  manifest.settings.activeCamera = swap(manifest.settings.activeCamera);
  for (const e of manifest.entities) {
    e.id = swap(e.id);
    if (e.parent) e.parent = swap(e.parent);
    if (e.components.FollowCamera) e.components.FollowCamera.target = swap(e.components.FollowCamera.target);
    if (e.components.Spin) e.components.Spin.speed = 9;
  }
  writeFileSync(path.join(dir, 'level2.scene.json'), JSON.stringify(manifest));
  writeFileSync(path.join(dir, 'level2.glb'), rewriteGlb(readFileSync(`${FIXTURE_DIR}basic.glb`), swap));
  files = {
    [BASIC]: `${FIXTURE_DIR}basic.scene.json`,
    '/export/basic.glb': `${FIXTURE_DIR}basic.glb`,
    '/export/level2.scene.json': path.join(dir, 'level2.scene.json'),
    '/export/level2.glb': path.join(dir, 'level2.glb'),
  };
  return files;
}

export function installSceneDom(): FakeDom {
  return installDom(sceneFiles());
}

/** A Game on "basic" with the clock held from the start (as a playtest page opens it). */
export async function heldGame(dom: FakeDom, options: GameOptions = {}): Promise<Game> {
  const game = new Game(dom.canvas as unknown as HTMLCanvasElement, {
    environment: 0,
    fillLight: 0,
    startScene: 'basic',
    exportBase: '/export/',
    ...options,
  });
  await game.physics.init();
  game.test.hold();
  await game.loadUrl(BASIC);
  return game;
}

/** Stands in for WebGLRenderer: drawing updates world matrices, like the real one. */
export function fakeRenderer(game: Game): { draws: number } {
  const counter = { draws: 0 };
  game.three.renderer = {
    render(scene: { updateMatrixWorld(): void }) {
      scene.updateMatrixWorld();
      counter.draws++;
    },
    setSize() {},
    dispose() {},
  } as never;
  return counter;
}
