// Shared test helpers (Node, no DOM).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { PerspectiveCamera, type Object3D } from 'three';
import { GLTFLoader, type GLTF } from 'three/addons/loaders/GLTFLoader.js';
import { AnimationService } from '../src/animation/animation';
import { AudioService, type AudioEnvironment } from '../src/audio/audio';
import type { SystemContext } from '../src/context';
import type { World } from '../src/ecs/world';
import { PostService } from '../src/game/post';
import { NavService } from '../src/nav/nav';
import { GameRandom } from '../src/game/random';
import { SaveStore } from '../src/game/save';
import { Timers } from '../src/game/timers';
import { VfxService } from '../src/vfx/vfx';
import type { InputAPI } from '../src/input/input';
import type { RapierPhysics } from '../src/physics/rapier-backend';

export const FIXTURE_DIR = fileURLToPath(new URL('../../../fixtures/basic/', import.meta.url));

export function readFixtureManifest(): unknown {
  return JSON.parse(readFileSync(`${FIXTURE_DIR}basic.scene.json`, 'utf8'));
}

export function loadFixtureGlb(): Promise<GLTF> {
  const buf = readFileSync(`${FIXTURE_DIR}basic.glb`);
  const arrayBuffer = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) as ArrayBuffer;
  return new GLTFLoader().parseAsync(arrayBuffer, '');
}

/** Input stub: `held` lists KeyboardEvent.code values currently down. */
export function fakeInput(held: Set<string> = new Set()): InputAPI {
  return {
    key: (code) => held.has(code),
    keyDown: () => false,
    keyUp: () => false,
    mouse: { x: 0, y: 0, nx: 0, ny: 0, dx: 0, dy: 0, wheel: 0, buttons: 0 },
    mouseButton: () => false,
    mouseButtonDown: () => false,
    mouseButtonUp: () => false,
    gamepad: null,
    gamepadButton: () => false,
    axis: () => 0,
  };
}

/** A system context good enough for physics-driven systems in Node. */
export function testContext(world: World, physics: RapierPhysics, input = fakeInput()): SystemContext {
  const camera = new PerspectiveCamera();
  camera.position.set(0, 5, 10);
  camera.lookAt(0, 0, 0);
  const log = Object.assign(() => {}, { debug() {}, info() {}, warn() {}, error() {} });
  const timers = new Timers<SystemContext>();
  const ctx: SystemContext = {
    dt: 1 / 60,
    time: 0,
    frame: 0,
    phase: 'fixed',
    world,
    input,
    physics,
    hud: { set() {}, bar() {}, clear() {} },
    three: { scene: world.root as never, camera, renderer: null as never },
    log,
    game: null as never,
    animation: new AnimationService(() => {}),
    audio: new AudioService(fakeAudio().env, () => {}),
    prefabs: [],
    spawn: (name) => {
      throw new Error(`Unknown prefab "${name}"`);
    },
    destroy: (entity) => (typeof entity === 'string' ? world.get(entity) : entity)?.destroy(),
    after: (seconds, fn) => timers.after(ctx.time, seconds, fn),
    every: (seconds, fn) => timers.every(ctx.time, seconds, fn),
    random: new GameRandom(1).api,
    vfx: new VfxService(() => {}),
    ui: null as never,
    post: new PostService(),
    nav: new NavService(() => {}),
    store: {},
    save: new SaveStore('test'),
    shaders: { set() {}, get: () => null },
    loadScene: async () => {},
    restart: async () => {},
  };
  return ctx;
}

// ---------------------------------------------------------------------------
// A minimal Web Audio stand-in: enough for Three.js Audio/PositionalAudio/AudioListener.

export class FakeParam {
  constructor(public value = 0) {}
  setTargetAtTime(value: number): this {
    this.value = value;
    return this;
  }
  setValueAtTime(value: number): this {
    this.value = value;
    return this;
  }
  linearRampToValueAtTime(value: number): this {
    this.value = value;
    return this;
  }
}

export class FakeNode {
  readonly outputs = new Set<FakeNode>();
  connect<T extends FakeNode>(node: T): T {
    this.outputs.add(node);
    return node;
  }
  disconnect(node?: FakeNode): void {
    if (node) this.outputs.delete(node);
    else this.outputs.clear();
  }
}

export class FakeGain extends FakeNode {
  readonly gain = new FakeParam(1);
}

export class FakePanner extends FakeNode {
  panningModel = 'equalpower';
  distanceModel = 'inverse';
  refDistance = 1;
  maxDistance = 10000;
  rolloffFactor = 1;
  readonly positionX = new FakeParam();
  readonly positionY = new FakeParam();
  readonly positionZ = new FakeParam();
  readonly orientationX = new FakeParam(1);
  readonly orientationY = new FakeParam();
  readonly orientationZ = new FakeParam();
}

export class FakeBufferSource extends FakeNode {
  buffer: unknown = null;
  loop = false;
  loopStart = 0;
  loopEnd = 0;
  onended: (() => void) | null = null;
  started = false;
  stopped = false;
  /** Where in the buffer it started (seconds): a resumed sound starts where it was paused. */
  offset = 0;
  readonly detune = new FakeParam();
  readonly playbackRate = new FakeParam(1);
  start(_when = 0, offset = 0): void {
    this.started = true;
    this.offset = offset;
  }
  stop(): void {
    this.stopped = true;
  }
  /** Simulates the sound reaching its end. */
  finish(): void {
    this.onended?.();
  }
}

export class FakeAudioContext {
  state: 'running' | 'suspended' | 'closed' = 'running';
  currentTime = 0;
  readonly destination = new FakeNode();
  readonly listener = Object.fromEntries(
    ['positionX', 'positionY', 'positionZ', 'forwardX', 'forwardY', 'forwardZ', 'upX', 'upY', 'upZ'].map((k) => [k, new FakeParam()]),
  ) as Record<string, FakeParam>;
  readonly sources: FakeBufferSource[] = [];
  createGain(): FakeGain {
    return new FakeGain();
  }
  createPanner(): FakePanner {
    return new FakePanner();
  }
  createBufferSource(): FakeBufferSource {
    const source = new FakeBufferSource();
    this.sources.push(source);
    return source;
  }
  resume(): Promise<void> {
    this.state = 'running';
    return Promise.resolve();
  }
  suspend(): Promise<void> {
    this.state = 'suspended';
    return Promise.resolve();
  }
  close(): Promise<void> {
    this.state = 'closed';
    return Promise.resolve();
  }
}

/** An AudioEnvironment over FakeAudioContext. `gesture()` simulates the first click. URLs containing "missing" fail to load. */
export function fakeAudio(): {
  env: AudioEnvironment;
  contexts: FakeAudioContext[];
  loads: string[];
  gesture(): void;
} {
  let onGesture: (() => void) | null = null;
  const contexts: FakeAudioContext[] = [];
  const loads: string[] = [];
  const env: AudioEnvironment = {
    createContext: () => {
      const context = new FakeAudioContext();
      contexts.push(context);
      return context as unknown as AudioContext;
    },
    decode: async () => ({ duration: 1, length: 48000, sampleRate: 48000, numberOfChannels: 1 }) as AudioBuffer,
    load: async (url) => {
      loads.push(url);
      if (url.includes('missing')) throw new Error('HTTP 404');
      return new ArrayBuffer(8);
    },
    onFirstGesture: (callback) => {
      onGesture = callback;
      return () => {
        onGesture = null;
      };
    },
  };
  return { env, contexts, loads, gesture: () => onGesture?.() };
}

export function worldY(object: Object3D): number {
  object.updateWorldMatrix(true, false);
  return object.matrixWorld.elements[13]!;
}
