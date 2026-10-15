// Renderer abstraction: WebGL by default (widest support, works headless),
// WebGPU opt-in through `three/webgpu` (loaded only when requested).

import {
  ACESFilmicToneMapping,
  AgXToneMapping,
  NeutralToneMapping,
  NoToneMapping,
  PCFShadowMap,
  PMREMGenerator,
  SRGBColorSpace,
  WebGLRenderer,
  type Camera,
  type Scene,
  type Texture,
  type ToneMapping,
} from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { WebGPURenderer } from 'three/webgpu';
import type { ToneMappingName } from '../scene/manifest';

export type RendererKind = 'webgl' | 'webgpu';

export interface GameRenderer {
  readonly kind: RendererKind;
  readonly domElement: HTMLCanvasElement;
  /** The underlying Three.js renderer. */
  readonly raw: WebGLRenderer | WebGPURenderer;
  /** Resize the drawing buffer (CSS pixels; device pixel ratio is applied, capped at 2). */
  setSize(width: number, height: number): void;
  render(scene: Scene, camera: Camera): void;
  /** A prefiltered neutral studio environment (for `scene.environment`). */
  createEnvironment(): Texture;
  dispose(): void;
}

type ThreeRenderer = WebGLRenderer | WebGPURenderer;

const TONE_MAPPING: Record<ToneMappingName, ToneMapping> = {
  neutral: NeutralToneMapping,
  agx: AgXToneMapping,
  aces: ACESFilmicToneMapping,
  none: NoToneMapping,
};

/**
 * The three.js tone mapping for a manifest `settings.toneMapping` (Blender's view transform):
 * Khronos PBR Neutral → Neutral, AgX → AgX, Filmic → ACES, Standard → none. The game sets it on
 * every scene load; ACES (the default) when the scene has none.
 */
export function toneMappingOf(name: ToneMappingName): ToneMapping {
  return TONE_MAPPING[name] ?? ACESFilmicToneMapping;
}
/** Builds a PMREM texture from a scene with the backend's own generator. */
type EnvironmentBaker = (scene: Scene) => Texture;

function configure(raw: ThreeRenderer): void {
  raw.outputColorSpace = SRGBColorSpace;
  raw.toneMapping = ACESFilmicToneMapping;
  raw.toneMappingExposure = 1;
  raw.shadowMap.enabled = true;
  raw.shadowMap.type = PCFShadowMap;
}

function wrap(kind: RendererKind, raw: ThreeRenderer, canvas: HTMLCanvasElement, bake: EnvironmentBaker): GameRenderer {
  return {
    kind,
    domElement: canvas,
    raw,
    setSize(width, height) {
      raw.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      raw.setSize(width, height, false);
    },
    render(scene, camera) {
      raw.render(scene, camera);
    },
    createEnvironment() {
      const room = new RoomEnvironment();
      const texture = bake(room);
      room.dispose();
      return texture;
    },
    dispose() {
      raw.dispose();
    },
  };
}

export async function createRenderer(kind: RendererKind, canvas: HTMLCanvasElement): Promise<GameRenderer> {
  if (kind === 'webgpu') {
    const webgpu = await import('three/webgpu');
    const raw = new webgpu.WebGPURenderer({ canvas, antialias: true });
    await raw.init();
    configure(raw);
    return wrap('webgpu', raw, canvas, (scene) => {
      const pmrem = new webgpu.PMREMGenerator(raw);
      const texture = pmrem.fromScene(scene, 0.04).texture;
      pmrem.dispose();
      return texture;
    });
  }
  const raw = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  configure(raw);
  return wrap('webgl', raw, canvas, (scene) => {
    const pmrem = new PMREMGenerator(raw);
    const texture = pmrem.fromScene(scene, 0.04).texture;
    pmrem.dispose();
    return texture;
  });
}
