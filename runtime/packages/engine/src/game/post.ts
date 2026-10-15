// Optional screen effects: bloom, vignette and simple colour grading. Off by default. Settings come
// from gamestudio.json "post", then createGame({ post }), then the scene's PostEffects component,
// then ctx.post.set(). The composer exists only while an effect is on (WebGL only).

import { HalfFloatType, Vector2, WebGLRenderer, WebGLRenderTarget, type Camera, type Scene } from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import type { World } from '../ecs/world';
import { PostEffects } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';
import { defaultValues } from '../schema/registry';
import type { GameRenderer } from './renderer';

export type PostSettings = ComponentValues<typeof PostEffects>;

export interface PostAPI {
  /** The settings in effect now. */
  readonly settings: Readonly<PostSettings>;
  /** Overrides some settings from code (they stay until changed); `null` drops every override. */
  set(overrides: Partial<PostSettings> | null): void;
  /** True while an effect pass runs (bloom, vignette or grading). */
  readonly active: boolean;
}

const FINISH_SHADER = {
  name: 'GsFinishShader',
  uniforms: {
    tDiffuse: { value: null },
    vignette: { value: 0 },
    contrast: { value: 1 },
    saturation: { value: 1 },
    tint: { value: [1, 1, 1] },
  },
  vertexShader: /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`,
  fragmentShader: /* glsl */ `
uniform sampler2D tDiffuse;
uniform float vignette;
uniform float contrast;
uniform float saturation;
uniform vec3 tint;
varying vec2 vUv;
void main() {
  vec4 c = texture2D(tDiffuse, vUv);
  vec3 col = c.rgb * tint;
  float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
  col = mix(vec3(l), col, saturation);
  col = (col - 0.5) * contrast + 0.5;
  vec2 d = vUv - 0.5;
  col *= mix(1.0, smoothstep(0.95, 0.25, length(d) * 1.35), vignette);
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), c.a);
}`,
};

/** A stable key of what the passes must look like. */
function needs(s: PostSettings): { bloom: boolean; finish: boolean } {
  const grading = s.contrast !== 1 || s.saturation !== 1 || s.tint.slice(0, 3).some((v) => v !== 1);
  return { bloom: s.bloom && s.bloomStrength > 0, finish: (s.vignette && s.vignetteAmount > 0) || grading };
}

export class PostService implements PostAPI {
  private overrides: Partial<PostSettings> = {};
  private world: World | null = null;
  private composer: EffectComposer | null = null;
  private renderPass: RenderPass | null = null;
  private bloomPass: UnrealBloomPass | null = null;
  private finishPass: ShaderPass | null = null;
  private layout = '';
  private warned = false;
  private readonly size = new Vector2();
  /**
   * Multiplies the `exposure` setting: the scene's own exposure (`2 ** stops` from Blender's Color
   * Management, set by the game on every scene load). 1 keeps the setting as it is.
   */
  exposureScale = 1;

  /** `base`: project and createGame settings. */
  constructor(private readonly base: Partial<PostSettings> = {}) {}

  /** The settings in effect: the scene's PostEffects component (if any) over the base, then overrides. */
  get settings(): PostSettings {
    const entity = this.world?.query(PostEffects)[0];
    return this.compute(entity ? entity.get<PostSettings>(PostEffects) : null);
  }

  get active(): boolean {
    const n = needs(this.settings);
    return n.bloom || n.finish;
  }

  set(overrides: Partial<PostSettings> | null): void {
    this.overrides = overrides ? { ...this.overrides, ...overrides } : {};
  }

  /** Reads the PostEffects component of this world's scene. */
  attach(world: World | null): void {
    this.world = world;
  }

  /** Draws through the effect passes. Returns false when no effect is on (draw normally). */
  render(renderer: GameRenderer, scene: Scene, camera: Camera): boolean {
    const raw = renderer.raw;
    const s = this.settings;
    const exposure = this.exposureScale * s.exposure;
    if (raw instanceof WebGLRenderer) raw.toneMappingExposure = exposure;
    else if (raw && 'toneMappingExposure' in raw) (raw as { toneMappingExposure: number }).toneMappingExposure = exposure;
    const n = needs(s);
    if (!n.bloom && !n.finish) {
      this.disposeComposer();
      return false;
    }
    if (!(raw instanceof WebGLRenderer)) {
      if (!this.warned && renderer.kind === 'webgpu') console.warn('[gamestudio] Post effects need the WebGL renderer (the default); they are off with renderer "webgpu"');
      this.warned = true;
      return false;
    }
    const layout = `${n.bloom}|${n.finish}`;
    if (!this.composer || layout !== this.layout) this.build(raw, n);
    this.renderPass!.camera = camera;
    this.renderPass!.scene = scene;
    if (this.bloomPass) {
      this.bloomPass.strength = s.bloomStrength;
      this.bloomPass.radius = s.bloomRadius;
      this.bloomPass.threshold = s.bloomThreshold;
    }
    if (this.finishPass) {
      const u = this.finishPass.uniforms;
      u.vignette!.value = s.vignette ? s.vignetteAmount : 0;
      u.contrast!.value = s.contrast;
      u.saturation!.value = s.saturation;
      u.tint!.value = [s.tint[0], s.tint[1], s.tint[2]];
    }
    raw.getSize(this.size);
    if (this.composer!.renderTarget1.width !== Math.floor(this.size.x * raw.getPixelRatio())) this.resize(raw);
    this.composer!.render();
    return true;
  }

  dispose(): void {
    this.disposeComposer();
  }

  private compute(scene: PostSettings | null): PostSettings {
    return { ...defaultValues<PostSettings>(PostEffects), ...this.base, ...(scene ?? {}), ...this.overrides };
  }

  private build(raw: WebGLRenderer, n: { bloom: boolean; finish: boolean }): void {
    this.disposeComposer();
    raw.getSize(this.size);
    const ratio = raw.getPixelRatio();
    const target = new WebGLRenderTarget(Math.max(1, this.size.x * ratio), Math.max(1, this.size.y * ratio), {
      type: HalfFloatType,
      samples: 4,
    });
    const composer = new EffectComposer(raw, target);
    this.renderPass = new RenderPass(null as unknown as Scene, null as unknown as Camera);
    composer.addPass(this.renderPass);
    if (n.bloom) {
      this.bloomPass = new UnrealBloomPass(new Vector2(this.size.x, this.size.y), 0.6, 0.4, 0.85);
      composer.addPass(this.bloomPass);
    }
    composer.addPass(new OutputPass());
    if (n.finish) {
      this.finishPass = new ShaderPass(FINISH_SHADER);
      composer.addPass(this.finishPass);
    }
    this.composer = composer;
    this.layout = `${n.bloom}|${n.finish}`;
    this.resize(raw);
  }

  private resize(raw: WebGLRenderer): void {
    raw.getSize(this.size);
    this.composer!.setPixelRatio(raw.getPixelRatio());
    this.composer!.setSize(Math.max(1, this.size.x), Math.max(1, this.size.y));
  }

  private disposeComposer(): void {
    if (!this.composer) return;
    for (const pass of this.composer.passes) pass.dispose();
    this.composer.dispose();
    this.composer = null;
    this.renderPass = null;
    this.bloomPass = null;
    this.finishPass = null;
    this.layout = '';
  }
}
