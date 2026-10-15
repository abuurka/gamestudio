// The Game: renderer, render loop, fixed-step simulation, scene loading/reset
// and play state. `createGame()` (create-game.ts) wires it to the page and the link.

import { Color, Fog, LinearSRGBColorSpace, Quaternion, Scene, Vector3, type Camera, type HemisphereLight, type Mesh, type Object3D, type OrthographicCamera, type Texture } from 'three';
import { AnimationService } from '../animation/animation';
import { AudioService, browserAudioEnvironment } from '../audio/audio';
import type { GameControls, GameState, GameView, Logger, SceneInfo, SceneLoadOptions, SystemContext, ThreeAccess } from '../context';
import { createLogger } from '../context';
import type { Entity } from '../ecs/entity';
import { Scheduler } from '../ecs/scheduler';
import type { SystemDef } from '../ecs/system';
import { World } from '../ecs/world';
import { Hud } from '../hud/hud';
import { Input } from '../input/input';
import { RapierPhysics } from '../physics/rapier-backend';
import { Prefabs, type SpawnPrefabOptions } from '../prefab/prefabs';
import { applyManifestTransforms, buildEntities, indexByGsId } from '../scene/build';
import { fetchSceneFiles, GltfCache, parseSceneGlbs, type SceneFiles } from '../scene/loader';
import { applyLightmaps, loadLightmapTextures } from '../scene/lightmaps';
import { processManifest, type SceneManifest, type SceneSettings, type SkySettings } from '../scene/manifest';
import { MaterialShaders } from '../scene/materials';
import { getComponent } from '../schema/registry';
import { getShader, onShaderDefined } from '../schema/shaders';
import { Fade } from '../ui/fade';
import { Ui } from '../ui/ui';
import { VfxService } from '../vfx/vfx';
import { NavService, sceneUsesNav } from '../nav/nav';
import { createDefaultCamera, fitCamera, markBlenderCamera, pickCamera } from './camera';
import { describeEntities, type EntityState, type StateFilter } from './describe';
import { EditorCamera, parseViewCamera, type ViewCamera } from './editor-camera';
import { applyTransform, EditLog, replaceComponents, type ComponentSet, type TransformItem } from './edits';
import { BLENDER_LIGHT_SCALE, createFillLight, prepareLighting, ShadowFitter } from './lighting';
import { PostService, type PostSettings } from './post';
import { freshSeed, GameRandom, hashSeed } from './random';
import { createRenderer, toneMappingOf, type GameRenderer, type RendererKind } from './renderer';
import { SaveStore } from './save';
import { Selection } from './selection';
import { createSkyUniforms, DEFAULT_BACKGROUND_RGB, skyColor, skyTexture, tintFillLight, updateSkyUniforms } from './sky';
import { FIXED_DT, FixedStepper } from './stepper';
import { TestControl } from './test-control';
import { Timers } from './timers';
import { CanvasPlacement, Checkerboard, parseUiPreview, uiPlacement, type UiPreview, type UiPreviewBackground } from './ui-preview';

export interface GameOptions {
  /** Canvas element or CSS selector. Default '#game'. */
  canvas?: string | HTMLCanvasElement;
  /** Scene to load first. `?scene=<name>` in the page URL overrides it. Default 'main'. */
  startScene?: string;
  /** 'webgl' (default) or 'webgpu' (opt-in, via three/webgpu). */
  renderer?: RendererKind;
  /** Start playing as soon as the first scene loads. Default true. */
  autoplay?: boolean;
  /** Multiplier for imported light intensities. Default 1/683 (undoes Blender's W→lm conversion). */
  lightScale?: number;
  /** Hemisphere fill light intensity (0 disables). Default 0.3. */
  fillLight?: number;
  /** Intensity of the neutral studio environment used for reflections and soft light (0 disables). Default 0.25. */
  environment?: number;
  /** URL prefix of exported scenes. Default `<base>__gs/export/`. */
  exportBase?: string;
  /** Screen effects (bloom, vignette, grading) for every scene; a scene's PostEffects component wins. Default: all off. */
  post?: Partial<PostSettings>;
  /** The game's name, which keys `ctx.save` in the browser. Default: the project name. */
  name?: string;
  /**
   * Start in the Scene view (`'scene'`: stopped, editor camera, no menus or HUD; see `setView`) or
   * the game (`'game'`, the default). `?gs_view=scene` in the page URL sets it.
   */
  view?: GameView;
}

/** Options of `game.setView`. */
export interface ViewOptions {
  /** Scene view: game shaders' clock (water, sway) runs with real time. Default: as before (at first true). */
  animate?: boolean;
  /** `'ui'`: the Game UI canvas to show (docs/GAME-UI.md §11.2). */
  canvas?: string;
  /** `'ui'`: where it goes, [x, y, width, height] with y down, in px of `region` (else of the page). It is laid out at width × height. */
  frame?: readonly number[];
  /** `'ui'`: the size of the space `frame` is in (the editor's region): the page maps the frame onto itself. */
  region?: readonly number[];
  /** `'ui'`: under the canvas, the loaded level (the default) or a checkerboard. */
  background?: UiPreviewBackground;
}

/** Called for runtime errors (system exceptions, load failures). */
export type ErrorReporter = (error: unknown, context: string) => void;

/** Who asked for a scene load: the first load, the editor (a re-export, `scene.load`) or the game's code. */
type LoadOrigin = 'start' | 'editor' | 'game';

interface LoadRequest {
  origin: LoadOrigin;
  keep?: Array<Entity | string>;
  /** Start over: clear `ctx.store` and close menus. */
  reset?: boolean;
  /** Scene files fetched ahead (during a fade). */
  files?: Promise<SceneFiles>;
  /** Play state to end in (a transition started while playing). */
  resume?: GameState;
}

interface Transition {
  phase: 'out' | 'load' | 'in';
  elapsed: number;
  /** Seconds of each half (out, in). */
  half: number;
  color: string;
  /** The load to run once faded out. */
  url: string;
  request: LoadRequest;
  done: Promise<void>;
  finish: () => void;
}

const DEFAULT_BACKGROUND = new Color().setRGB(...DEFAULT_BACKGROUND_RGB, LinearSRGBColorSpace);
/** `ctx.loadScene` fades over this many seconds unless told otherwise. */
export const SCENE_FADE = 0.5;
const engineWarn = (message: string): void => console.warn(`[gamestudio] ${message}`);

export class Game implements GameControls {
  readonly world: World;
  readonly physics = new RapierPhysics();
  readonly input: Input;
  readonly hud: Hud;
  readonly ui: Ui;
  readonly three: ThreeAccess;
  readonly log: Logger = createLogger();
  readonly canvas: HTMLCanvasElement;
  readonly animation = new AnimationService(engineWarn);
  readonly audio = new AudioService(browserAudioEnvironment(), engineWarn);
  readonly vfx = new VfxService(engineWarn);
  readonly nav = new NavService(engineWarn);
  readonly post: PostService;
  readonly random = new GameRandom();
  readonly save: SaveStore;
  /** `ctx.store`: game state that survives scene changes. */
  readonly store: Record<string, any> = {};

  state: GameState = 'loading';
  /** True once the first scene has loaded. */
  ready = false;
  scene: SceneInfo = { name: '', rev: 0 };
  /** Scene loads and resets so far that the game's code did not ask for (first load, re-exports, resets). */
  loads = 0;
  /** Scene changes the game's code asked for (`ctx.loadScene`, `ctx.restart`). */
  sceneChanges = 0;

  /** What the page shows: the game, the level from the editor's camera (the Scene view) or a UI canvas. See `setView`. */
  private shown: GameView = 'game';
  /** Scene view: game shaders' clock runs with real time. */
  private animateShaders = true;
  /** The `'ui'` view: the canvas, its frame and its background. */
  private previewing: UiPreview | null = null;
  /** Moves the canvas element to the `'ui'` view's frame and back. */
  private readonly placement: CanvasPlacement;
  private checker: Checkerboard | null = null;
  /** The canvas's CSS size at the last resize. */
  private viewSize: [number, number] = [0, 0];

  /** Hooks set by createGame (link client, error overlay). */
  onStateChange: (state: GameState) => void = () => {};
  onViewChange: (view: GameView) => void = () => {};
  onSceneReady: (scene: SceneInfo) => void = () => {};
  reportError: ErrorReporter = (error) => console.error(error);

  private readonly options: Required<Omit<GameOptions, 'canvas' | 'startScene' | 'post' | 'name' | 'view'>> & { startScene: string };
  private readonly scheduler: Scheduler;
  private readonly ctx: SystemContext;
  private readonly stepper = new FixedStepper(FIXED_DT);
  private readonly edits = new EditLog();
  private readonly fade: Fade;
  private fill: HemisphereLight | null = null;
  private sky: Texture | null = null;
  /** The gradient `sky` draws, for orthographic views (one colour: the sky in their direction). */
  private skySettings: SkySettings | null = null;
  private readonly flatSky = new Color();
  private readonly lookDirection = new Vector3();
  private files: SceneFiles | null = null;
  /** play() or stop() called while a load ran (an editor's `play.start` right after `scene.load`). */
  private requested: 'playing' | 'stopped' | null = null;
  /** Parsed GLBs of the current scene by content hash: reloads and resets clone them instead of parsing. */
  private readonly gltfs = new GltfCache();
  private assets: Record<string, string> = {};
  private gltfRoot: Object3D | null = null;
  private prefabRoots: Object3D[] = [];
  private readonly library = new Prefabs(engineWarn);
  /** Game shaders' `ctx.time`: one uniform object, kept equal to `ctx.time` (in the Scene view, real time). */
  private readonly shaderTime = { value: 0 };
  /** Game shaders' `ctx.sky`: one set of uniforms, set from the scene's sky, background and fog on every load. */
  private readonly shaderSky = createSkyUniforms();
  private readonly shaders = new MaterialShaders({
    renderer: () => this.three.renderer,
    time: this.shaderTime,
    sky: this.shaderSky,
    assetUrl: (path) => this.assetUrl(path),
    warn: engineWarn,
    ownMeshes: (entity) => ownMeshes(this.world, entity),
  });
  /** The loaded scene's tone mapping (Blender's view transform), set on the renderer per load. */
  private toneMapping: SceneSettings['toneMapping'] = 'aces';
  private readonly timers = new Timers<SystemContext>();
  private shadows: ShadowFitter | null = null;
  /** The editor's selection: boxes in game mode, outlines in the Scene view. */
  private readonly selection: Selection;
  /** The Scene view's camera, from the editor (`view.camera`). */
  private readonly editorCamera = new EditorCamera();
  private pendingSteps = 0;
  private lastTime = 0;
  private fittedCamera: Camera | null = null;
  private fittedAspect = 0;
  /** Serializes loads and resets. */
  private queue: Promise<void> = Promise.resolve();
  private queued = 0;
  private transition: Transition | null = null;
  private runSeed = freshSeed();
  private instantiations = 0;
  private readyWaiters: Array<() => void> = [];
  private frameStats = { frames: 0, cpuMs: 0, since: 0 };
  private disposers: Array<() => void> = [];
  /** Canvas aspect, measured on resize (reading it every step would force a layout). */
  private viewAspect = 0;
  /** The clock HUD durations count on (ms): wall time, or game time while a test holds the clock. */
  private uiTime = 0;
  private held = false;
  onStats: (stats: { fps: number; frameMs: number; entities: number; bodies: number }) => void = () => {};

  /** Deterministic stepping for playtests (API.md §19). */
  readonly test: TestControl;

  constructor(canvas: HTMLCanvasElement, options: GameOptions = {}) {
    this.canvas = canvas;
    this.options = {
      startScene: options.startScene ?? 'main',
      renderer: options.renderer ?? 'webgl',
      autoplay: options.autoplay ?? true,
      lightScale: options.lightScale ?? BLENDER_LIGHT_SCALE,
      fillLight: options.fillLight ?? 0.3,
      environment: options.environment ?? 0.25,
      exportBase: options.exportBase ?? `${import.meta.env.BASE_URL}__gs/export/`,
    };
    this.shown = options.view === 'scene' ? 'scene' : 'game';
    this.placement = new CanvasPlacement(canvas);
    this.post = new PostService(options.post ?? {});
    this.save = new SaveStore(options.name ?? 'game');
    this.audio.useSettings(this.save); // channel volumes and mutes (`gs.audio`), before any sound
    const scene = new Scene();
    scene.background = DEFAULT_BACKGROUND.clone();
    if (this.options.fillLight > 0) scene.add((this.fill = createFillLight(this.options.fillLight)));
    this.world = new World(scene);
    this.post.attach(this.world);
    this.input = new Input(canvas);
    this.hud = new Hud(canvas, () => this.uiTime);
    this.fade = new Fade(canvas);
    this.three = { scene, camera: createDefaultCamera(), renderer: null as unknown as GameRenderer };
    this.selection = new Selection({ scene, world: this.world, ownMeshes: (entity) => ownMeshes(this.world, entity) });
    this.scheduler = new Scheduler((system, error, hook) => this.onSystemError(system, error, hook));
    const library = this.library;
    const game = this;
    this.ctx = {
      dt: 0,
      time: 0,
      frame: 0,
      phase: 'update',
      world: this.world,
      input: this.input,
      physics: this.physics,
      hud: this.hud,
      three: this.three,
      log: this.log,
      game: this,
      animation: this.animation,
      audio: this.audio,
      get prefabs() {
        return library.names();
      },
      spawn: (prefab, options) => this.spawn(prefab, options),
      destroy: (entity) => (typeof entity === 'string' ? this.world.get(entity) : entity)?.destroy(),
      after: (seconds, fn) => this.timers.after(this.ctx.time, seconds, fn),
      every: (seconds, fn) => this.timers.every(this.ctx.time, seconds, fn),
      random: this.random.api,
      vfx: this.vfx,
      ui: null as unknown as Ui,
      post: this.post,
      nav: this.nav,
      store: this.store,
      save: this.save,
      shaders: {
        set: (target, values) => this.shaders.set(target, values),
        get: (target) => this.shaders.get(target),
      },
      loadScene: (name, options) => this.loadScene(name, { fade: SCENE_FADE, ...options }),
      restart: (options) => this.restartGame(options),
    };
    this.ui = new Ui(canvas, {
      input: this.input,
      state: () => this.state,
      pause: (paused) => this.pause(paused),
      ctx: () => this.ctx,
      restart: () => void this.restartGame(),
      loadScene: (name) => void this.ctx.loadScene(name),
      // Blender-authored canvases (docs/GAME-UI.md): next to the level manifests.
      indexUrl: `${this.options.exportBase}ui/index.json`,
      // Value paths of bindings, bars, UiShow, sliders and toggles (docs/GAME-UI.md §10.1); `game.time`
      // and `game.level` read the clock and the level, never write them.
      values: {
        store: this.store,
        save: this.save,
        audio: this.audio,
        game: {
          get time() {
            return game.ctx.time;
          },
          get level() {
            return game.scene.name;
          },
        },
      },
      // Button, slider and toggle sounds play on the `ui` channel, which a pause never holds.
      sounds: () => (url) => void this.audio.play(url, { channel: 'ui' }),
      error: (error, context) => this.reportError(error, context),
      warn: engineWarn,
    });
    this.ctx.ui = this.ui;
    this.showOverlays();
    this.test = new TestControl({
      canvas,
      world: this.world,
      physics: this.physics,
      info: () => ({
        state: this.state,
        scene: this.scene,
        time: this.ctx.time,
        loads: this.loads,
        sceneChanges: this.sceneChanges,
        screen: this.ui.current,
        uiPaused: this.ui.holdsPause,
      }),
      setHeld: (held) => {
        this.held = held;
        this.pendingSteps = 0;
        this.lastTime = 0;
      },
      step: () => this.testFrame(),
      render: () => this.render(),
      busy: () => this.queued > 0,
      idle: () => this.idle(),
      restart: () => this.restart(),
      play: () => this.play(),
      describe: (filter) => this.describe(filter),
      seed: (seed) => this.seed(seed),
      isolate: (on) => {
        this.save.isolate(on);
        this.audio.useSettings(this.save); // a playtest's own store: the default channel settings
        this.ui.reloadLocale(); // and the default UI language (the browser's best match)
      },
      seedSave: (values) => {
        for (const [key, value] of Object.entries(values)) this.save.set(key, value);
        this.audio.useSettings(this.save); // a seeded `gs.audio` sets the channels
        this.ui.reloadLocale(); // a seeded `gs.locale` the UI language
      },
      seedStore: (values) => {
        for (const [key, value] of Object.entries(values)) if (key !== '__proto__') this.store[key] = value;
      },
      saveEntries: () => Object.fromEntries(this.save.keys().map((key) => [key, this.save.get(key)])),
      ui: () => this.ui.state(),
      vfx: () => this.vfx.stats(),
      nav: () => this.nav.stats(),
    });
    // A redefined game shader (hot module replacement) is applied again to its materials.
    this.disposers.push(onShaderDefined((shader) => this.shaders.redefine(shader)));
    // An entity's own game-shader materials (ctx.shaders.set) go with it.
    this.disposers.push(this.world.onChange((event) => event.type === 'destroy' && this.shaders.forget(event.entity)));
  }

  /** Names of the prefabs in the loaded scene. */
  get prefabs(): readonly string[] {
    return this.library.names();
  }

  /**
   * Resolves once the scene's game shaders are ready to draw: queued scene loads have finished, every
   * bound shader's `load` module has loaded and been applied, and every asset texture they asked for
   * has loaded (or failed, with its warning). For screenshots of a loaded scene. Never rejects.
   */
  async shadersReady(): Promise<void> {
    await this.idle();
    await this.shaders.ready();
  }

  /** The scene the game started with. */
  get startScene(): string {
    return this.options.startScene;
  }

  /** Instantiates a prefab (see `ctx.spawn`). */
  spawn(prefab: string, options?: SpawnPrefabOptions): Entity {
    return this.library.spawn(this.world, prefab, options);
  }

  /** Creates the renderer and physics, loads the first scene and starts the loop. */
  async start(): Promise<void> {
    const renderer = await createRenderer(this.options.renderer, this.canvas);
    this.three.renderer = renderer;
    this.applyToneMapping();
    if (this.options.environment > 0) {
      // Without an environment, metallic materials from Blender render almost black.
      this.three.scene.environment = renderer.createEnvironment();
      this.three.scene.environmentIntensity = this.options.environment;
    }
    await this.physics.init();
    this.resize();
    const observer = new ResizeObserver(() => this.resize());
    observer.observe(this.canvas);
    const onResize = (): void => {
      this.placeView(); // the 'ui' view's frame maps onto the page's new size
      this.resize();
    };
    window.addEventListener('resize', onResize);
    this.disposers.push(() => observer.disconnect(), () => window.removeEventListener('resize', onResize));
    requestAnimationFrame(this.frame);
    await this.request(this.sceneUrl(this.options.startScene), { origin: 'start' });
  }

  /** Resolves once the first scene has loaded. */
  whenReady(): Promise<void> {
    return this.ready ? Promise.resolve() : new Promise((resolve) => this.readyWaiters.push(resolve));
  }

  /** Resolves once queued scene loads and resets have finished. */
  async idle(): Promise<void> {
    let queue: Promise<void>;
    do {
      queue = this.queue;
      await queue;
    } while (queue !== this.queue);
  }

  /** Rebuilds the scene from the loaded export (keeping editor edits) and plays it: a fresh run. */
  async restart(): Promise<void> {
    if (!this.files) return this.idle();
    this.cancelTransition();
    await this.enqueue(async () => {
      this.setState('loading');
      await this.instantiate({ origin: 'editor', reset: true }).catch((error) => this.reportError(error, 'scene reset'));
      this.settle('playing');
    });
  }

  /** URL of an exported scene's manifest. */
  sceneUrl(name: string): string {
    return `${this.options.exportBase}${encodeURIComponent(name)}.scene.json`;
  }

  /**
   * Loads another scene (game code). Without options it cuts straight to it; `fade` adds a
   * transition and `keep` carries entities over (see `ctx.loadScene`).
   */
  loadScene(name: string, options: SceneLoadOptions = {}): Promise<void> {
    return this.request(this.sceneUrl(name), { origin: 'game', keep: options.keep }, options);
  }

  /** Loads (or reloads) a scene from its manifest URL (the editor). Play state is kept; the first load autoplays. */
  loadUrl(manifestUrl: string): Promise<void> {
    return this.request(manifestUrl, { origin: this.ready ? 'editor' : 'start' });
  }

  /** `ctx.restart()`: clears the store, closes menus and loads the start scene. */
  restartGame(options: { fade?: number } = {}): Promise<void> {
    return this.request(this.sceneUrl(this.options.startScene), { origin: 'game', reset: true }, { fade: options.fade ?? SCENE_FADE });
  }

  /** Plays (from the Scene view or the 'ui' view too, which it leaves). During a load (an editor reload, a reset, the first load) the load ends playing. */
  play(): void {
    if (this.shown !== 'game') this.setView('game');
    if (this.state === 'loading') this.requested = 'playing';
    else if (this.ready) this.setState('playing');
  }

  /** Resets to the loaded state (keeping editor edits) and halts systems and physics. */
  stop(): void {
    if (!this.files) return;
    this.cancelTransition();
    if (this.state === 'loading') {
      this.requested = 'stopped'; // the load gives the loaded state; it ends stopped
      return;
    }
    this.setState('stopped');
    void this.enqueue(() => this.instantiate({ origin: 'editor', reset: true }).catch((error) => this.reportError(error, 'scene reset')));
  }

  pause(paused = true): void {
    if (paused && this.state === 'playing') this.setState('paused');
    else if (!paused && this.state === 'paused') this.setState('playing');
  }

  /** Advances `frames` fixed steps while paused (pauses first if playing; leaves the Scene view). Ignored while a test holds the clock. */
  step(frames = 1): void {
    if (!this.ready || this.state === 'loading' || this.held) return;
    if (this.shown !== 'game') this.setView('game');
    if (this.state !== 'paused') this.setState('paused');
    this.pendingSteps += Math.max(1, Math.floor(frames));
  }

  /** Restarts `ctx.random` from a seed (`null`: a fresh one), as a playtest does at its start. */
  seed(seed: number | null): void {
    this.runSeed = seed === null ? freshSeed() : seed >>> 0;
    this.instantiations = 1;
    this.random.reseed(hashSeed(this.runSeed, this.scene.name, 0));
  }

  // ---------------------------------------------------------------------------
  // Live edits (also recorded so they survive play.stop)

  applyTransforms(items: TransformItem[]): void {
    for (const item of items) {
      if (item && typeof item.id === 'string' && applyTransform(this.world, this.physics, item)) {
        this.edits.recordTransform(item);
        this.nav.touch(); // static geometry may have moved
      }
    }
  }

  replaceComponents(id: string, components: ComponentSet): void {
    const entity = this.world.get(id);
    if (!entity) return;
    replaceComponents(entity, components);
    this.edits.recordComponents(id, components);
  }

  removeEntities(ids: string[]): void {
    for (const id of ids) {
      this.world.get(id)?.destroy();
      this.edits.recordRemove(id);
    }
  }

  setVisibility(id: string, visible: boolean): void {
    const entity = this.world.get(id);
    if (!entity) return;
    entity.object3d.visible = visible;
    this.edits.recordVisibility(id, visible);
  }

  /**
   * Highlights entities (the editor's selection; `active` is the active object): bounding boxes in
   * game mode, outlines in Blender's selection colours in the Scene view. Kept across scene reloads.
   */
  select(ids: string[], active: string | null = null): void {
    this.selection.set(ids, active);
    if (this.shown === 'ui') this.selection.hide(); // kept for the other views
  }

  /** The selected entity ids and the active one. */
  get selected(): { ids: readonly string[]; active: string | null } {
    return { ids: this.selection.selected, active: this.selection.active };
  }

  // ---------------------------------------------------------------------------
  // The Scene view (SPEC §5.4 `view.mode`, `view.camera`)

  /** What the page shows: `'game'`, `'scene'` (the Scene view) or `'ui'` (a UI canvas for the editor). */
  get view(): GameView {
    return this.shown;
  }

  /** Scene view: whether game shaders' clock runs. */
  get animate(): boolean {
    return this.animateShaders;
  }

  /** The `'ui'` view's canvas, frame and background; null in the other views. */
  get uiPreview(): Readonly<UiPreview> | null {
    return this.previewing;
  }

  /**
   * Shows the game, the Scene view or the `'ui'` view.
   *
   * The Scene view: the level in its loaded state (a running game is stopped and reset), drawn from the
   * editor's camera (`setViewCamera`; the game's camera until the first), with no systems, physics,
   * animation, particles, timers or sound running, menus and the HUD hidden (the error overlay stays)
   * and the selection outlined. Game shaders' clock runs with real time while `animate` is on;
   * `ctx.time` stays 0. `play()` (and `step()`) leave it as Play does: from the loaded state, with the
   * game's camera and its title screen.
   *
   * The `'ui'` view (docs/GAME-UI.md §11.2): stopped in the same way, the Game UI canvas `canvas` alone,
   * laid out at the frame's size and drawn where it is (the canvas element moves there), at rest, over the
   * level from the game's camera (dimmed by a menu's backdrop, as in the game) or a checkerboard (also
   * while no level is loaded). Bindings without a value show as written (`{score}`); no input, no pause,
   * the selection hidden. Called again in it, it takes the new canvas, frame and background. Ignored
   * without a canvas name or a usable frame.
   */
  setView(view: GameView, options: ViewOptions = {}): void {
    if (typeof options.animate === 'boolean') this.animateShaders = options.animate;
    if (view !== 'scene' && view !== 'game' && view !== 'ui') return;
    const preview = view === 'ui' ? parseUiPreview(options) : null;
    if (view === 'ui' && !preview) {
      engineWarn('setView("ui") needs a canvas name and a frame [x, y, width, height] with a positive size');
      return;
    }
    const before = this.previewing;
    this.previewing = preview;
    this.ui.preview(preview?.canvas ?? null);
    if (this.placeView()) this.resize();
    // Canvases exported since the page loaded (a screen made since): fetched again for a new canvas.
    if (preview && this.ready && preview.canvas !== before?.canvas) void this.ui.refresh();
    if (view === this.shown) return;
    this.shown = view;
    if (view !== 'game') {
      if (!this.files) {
        if (this.state === 'loading') this.requested = 'stopped'; // the first load ends stopped
      } else if (this.state !== 'stopped' || this.transition) this.stop();
    } else {
      this.shaderTime.value = this.ctx.time; // game time again
    }
    this.showOverlays();
    this.showSelection();
    this.onViewChange(view);
  }

  /**
   * The Scene view's camera (`view.camera`): `view` (world → view) and `projection` (used as given:
   * perspective, orthographic or a Blender camera view) as column-major arrays of 16 numbers in the
   * game's Y-up space. Kept across frames, resizes and scene loads; latest wins. False (and nothing
   * changes) when the matrices are not 16 finite numbers each.
   */
  setViewCamera(camera: ViewCamera): boolean {
    const parsed = parseViewCamera(camera);
    if (!parsed) return false;
    this.editorCamera.set(parsed);
    return true;
  }

  /** The camera the page draws with: the editor's in the Scene view (once it sent one), else the game's. */
  get viewCamera(): Camera {
    return (this.shown === 'scene' && this.editorCamera.current) || this.three.camera;
  }

  /**
   * Game-shader values of a material, from the editor (`material.values`): `ctx.shaders.set(material,
   * values)`, kept across Stop like other live edits. Quiet about what does not apply: a material the
   * scene binds no game shader to is ignored, and so are fields the bound shader does not have (the
   * editor switched shaders; its re-export follows).
   */
  setMaterialValues(material: string, values: Record<string, unknown>): void {
    if (typeof material !== 'string' || !values || typeof values !== 'object' || Array.isArray(values)) return;
    const bound = this.shaders.shaderOf(material);
    if (bound === null) return;
    const fields = getShader(bound)?.fields;
    const known = fields ? Object.fromEntries(Object.entries(values).filter(([key]) => key in fields)) : values;
    if (Object.keys(known).length === 0) return;
    this.shaders.set(material, known);
    this.edits.recordMaterial(material, known);
  }

  private selectionStyle(): 'box' | 'outline' {
    return this.shown === 'scene' ? 'outline' : 'box';
  }

  /** The selection as the view shows it: boxes in the game, outlines in the Scene view, none in the 'ui' view. */
  private showSelection(): void {
    if (this.shown === 'ui') this.selection.hide();
    else this.selection.show(this.selectionStyle());
  }

  /**
   * Menus (canvases too), the HUD and the fade show in the game and hide in the Scene view (the error
   * overlay stays). The 'ui' view hides the DOM ones and draws its one canvas.
   */
  private showOverlays(): void {
    const visibility = this.shown === 'game' ? '' : 'hidden';
    for (const element of [this.hud.element, this.ui.overlay, this.fade.element]) {
      if (element.style.visibility !== visibility) element.style.visibility = visibility;
    }
    this.ui.setHidden(this.shown === 'scene');
  }

  /**
   * The canvas element at the 'ui' view's frame (mapped from the editor's region onto the page), or
   * back in its own place. True when it moved or changed size: the caller lays out again.
   */
  private placeView(): boolean {
    const preview = this.previewing;
    if (!preview) return this.placement.restore();
    const page: [number, number] | null = window.innerWidth > 0 && window.innerHeight > 0 ? [window.innerWidth, window.innerHeight] : null;
    return this.placement.place(uiPlacement(preview, page));
  }

  describe(filter?: StateFilter): EntityState[] {
    return describeEntities(this.world, filter);
  }

  dispose(): void {
    for (const fn of this.disposers.splice(0)) fn();
    this.timers.clear();
    this.animation.dispose();
    this.audio.dispose();
    this.vfx.dispose();
    this.nav.dispose();
    this.post.dispose();
    this.selection.dispose();
    this.input.dispose();
    this.hud.dispose();
    this.ui.dispose();
    this.fade.dispose();
    this.physics.dispose();
    this.world.dispose();
    this.sky?.dispose();
    this.checker?.dispose();
    this.three.renderer?.dispose();
  }

  // ---------------------------------------------------------------------------

  private enqueue(task: () => Promise<void>): Promise<void> {
    this.queued++;
    const run = this.queue.then(task).finally(() => {
      this.queued--;
    });
    this.queue = run.catch(() => {});
    return run;
  }

  /** The state a load ends in: what play() or stop() asked for while it ran, else `state`. The Scene and 'ui' views stay stopped. */
  private settle(state: GameState): void {
    const requested = this.requested;
    this.requested = null;
    const next = requested ?? state;
    this.setState(this.shown !== 'game' && next !== 'loading' ? 'stopped' : next);
  }

  private setState(state: GameState): void {
    if (state === this.state) return;
    this.state = state;
    this.audio.setPaused(state === 'paused');
    this.onStateChange(state);
  }

  /** Starts a load: at once, or after fading out when `fade` is set (game code only). */
  private request(url: string, request: LoadRequest, transition: { fade?: number; color?: string } = {}): Promise<void> {
    if (request.origin !== 'game') this.cancelTransition();
    const fade = request.origin === 'game' ? Math.max(0, transition.fade ?? 0) : 0;
    if (fade <= 0 || !this.ready) return this.enqueueLoad(url, request);
    if (this.transition) return this.transition.done; // One change at a time: the first wins.
    const files = fetchSceneFiles(url, this.files);
    files.catch(() => {}); // Reported when the load uses it.
    let finish = (): void => {};
    const done = new Promise<void>((resolve) => (finish = resolve));
    const resume = this.state === 'loading' ? 'playing' : this.state;
    this.transition = {
      phase: 'out',
      elapsed: 0,
      half: fade / 2,
      color: transition.color ?? '#000',
      url,
      request: { ...request, files, resume },
      done,
      finish,
    };
    this.setState('loading');
    return done;
  }

  /** Advances a transition by `ms` of the game's clock: fade out, load, fade in. */
  private tickTransition(ms: number): void {
    const t = this.transition;
    if (!t) return;
    if (t.phase === 'out') {
      t.elapsed += ms / 1000;
      if (t.elapsed < t.half) return;
      t.phase = 'load';
      void this.enqueueLoad(t.url, t.request).finally(() => {
        if (this.transition !== t) return;
        t.phase = 'in';
        t.elapsed = 0;
      });
    } else if (t.phase === 'in') {
      t.elapsed += ms / 1000;
      if (t.elapsed < t.half) return;
      this.transition = null;
      t.finish();
    }
  }

  private cancelTransition(): void {
    const t = this.transition;
    if (!t) return;
    this.transition = null;
    if (this.state === 'loading' && t.phase === 'out' && this.ready) this.setState('playing');
    t.finish();
  }

  private fadeOpacity(): number {
    const t = this.transition;
    if (!t) return 0;
    if (t.phase === 'load') return 1;
    const f = t.half > 0 ? Math.min(1, t.elapsed / t.half) : 1;
    return t.phase === 'out' ? f : 1 - f;
  }

  private enqueueLoad(url: string, request: LoadRequest): Promise<void> {
    return this.enqueue(async () => {
      const previous = request.resume ?? this.state;
      this.setState('loading');
      try {
        // GLBs whose content hash did not change come from the current files, without a request.
        const files = request.files ?? fetchSceneFiles(url, this.files);
        // The first load also loads the game's menus and HUDs (never rejects): the title is there when it plays.
        const canvases = this.ready ? null : this.ui.load();
        this.files = await files;
        this.edits.clear();
        await this.instantiate(request);
        await canvases;
      } catch (error) {
        this.reportError(error, 'scene load');
        this.settle(previous === 'loading' ? (this.ready ? 'playing' : 'stopped') : previous);
        return;
      }
      const first = !this.ready;
      this.ready = true;
      for (const resolve of this.readyWaiters.splice(0)) resolve();
      this.onSceneReady(this.scene);
      if (first) this.settle(this.options.autoplay ? 'playing' : 'stopped');
      else if (request.origin === 'game') {
        if (this.ui.holdsPause && this.requested === 'playing') this.requested = null; // a menu holds the pause
        this.settle(this.ui.holdsPause ? 'paused' : 'playing');
      }
      else this.settle(previous === 'loading' ? 'playing' : previous);
    });
  }

  /** URL of an asset path from a component (`field.asset`): the exported copy, else the file in the project. */
  assetUrl(path: string): string {
    return this.resolveAsset(path, this.assets);
  }

  private resolveAsset(path: string, assets: Record<string, string>): string {
    if (/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(path)) return path;
    const exported = assets[path];
    if (exported && this.files) return new URL(exported, this.files.manifestUrl).href;
    return `${import.meta.env.BASE_URL}${path}`;
  }

  /** The entities to carry into the next scene: the requested ones with their descendants. */
  private keepSet(keep: Array<Entity | string> | undefined): Set<Entity> {
    const out = new Set<Entity>();
    for (const item of keep ?? []) {
      const entity = typeof item === 'string' ? (this.world.get(item) ?? this.world.findByName(item)) : item;
      if (!entity?.alive) {
        if (typeof item === 'string') engineWarn(`loadScene keep: no entity named "${item}"`);
        continue;
      }
      out.add(entity);
      for (const child of this.world.descendants(entity)) out.add(child);
    }
    return out;
  }

  /** Builds the world from the cached scene files (scene load and reset). */
  private async instantiate(request: Omit<LoadRequest, 'files'> = { origin: 'editor' }): Promise<void> {
    const files = this.files;
    if (!files) return;
    const { manifest, warnings } = processManifest(files.raw);
    for (const warning of warnings) console.warn(`[gamestudio] ${warning}`);
    const [{ gltf, prefabGltfs, keys }, lightmaps] = await Promise.all([
      parseSceneGlbs(files, this.gltfs), // clones of cached parses for GLBs whose hash did not change
      loadLightmapTextures(manifest.lightmaps, files.manifestUrl, undefined, engineWarn), // never rejects
      // Game shaders' code-split modules and asset textures; never rejects.
      this.shaders.prepare(manifest.materials ?? {}, (path) => this.resolveAsset(path, manifest.assets)),
    ]);

    const keep = this.keepSet(request.keep);
    const scene3 = this.three.scene;
    // Kept objects leave the old scene graph (keeping their world pose) before it is disposed.
    for (const entity of keep) {
      const parent = entity.object3d.parent;
      const owner = parent ? this.world.entityOf(parent) : undefined;
      if (!owner || !keep.has(owner)) scene3.attach(entity.object3d);
    }
    const keptResources = resourcesOf([...keep].map((e) => e.object3d));

    // Tear down the previous scene (the selection is shown again once the new one is built).
    this.selection.release();
    this.timers.clear();
    const assetUrl = (path: string): string => this.assetUrl(path);
    this.audio.reset(this.world, assetUrl, keep);
    this.vfx.reset(this.world, assetUrl, keep);
    // Clearing the world takes entity objects out of the scene graph: collect the scene's resources first.
    const stale = this.gltfRoot ? resourcesOf([this.gltfRoot]) : new Set<unknown>();
    this.world.clear(keep);
    this.shaders.clear();
    this.gltfRoot?.removeFromParent();
    // Cached parses this load does not use are dropped; what the cache keeps is shared with the new
    // scene (clones), so it stays alive, as do the resources of kept entities.
    const dropped = this.gltfs.retain(keys);
    const spare = this.gltfs.size ? new Set([...keptResources, ...resourcesOf(this.gltfs.roots())]) : keptResources;
    for (const resource of stale) if (!spare.has(resource)) (resource as { dispose(): void }).dispose();
    for (const prefabRoot of this.prefabRoots.splice(0)) disposeObject(prefabRoot, spare);
    for (const cached of dropped) disposeObject(cached, spare);
    if (request.reset) {
      for (const key of Object.keys(this.store)) delete this.store[key];
      // ctx.restart() reopens the `start` canvases; Stop and a test reset start a fresh run (title screens too).
      this.ui.reset(request.origin === 'game' ? 'restart' : 'run');
    }

    this.assets = manifest.assets; // asset fields resolve against this export from here on (game shaders too)
    const root = gltf.scene;
    root.name = 'gs_scene_root';
    // Objects that moved after the GLB was written (the GLB was kept, the manifest has their poses).
    applyManifestTransforms(root, manifest.entities);
    const placements = keep.size ? replaceKept(manifest, root, keep) : null;
    // A baked scene's light is in its lightmaps: no default sun on top (it isn't in the bake).
    const suns = prepareLighting(root, this.options.lightScale, { defaultSun: !manifest.lightmaps });
    scene3.add(root);
    this.gltfRoot = root;
    this.applyEnvironment(manifest);

    const [gx, gy, gz] = manifest.settings.gravity;
    this.physics.reset(this.world, { x: gx, y: gy, z: gz });
    buildEntities(this.world, root, manifest);
    applyLightmaps(root, manifest, lightmaps, engineWarn); // before prefab instances: they are never baked

    // Prefab templates stay out of the scene; instances placed in Blender are built now.
    for (const prefabGltf of prefabGltfs.values()) {
      prepareLighting(prefabGltf.scene, this.options.lightScale, { defaultSun: false });
      this.prefabRoots.push(prefabGltf.scene);
    }
    // Game shaders, after the lightmaps; on the templates before any instance: instances share their materials.
    this.shaders.bind([root, ...this.prefabRoots], manifest.materials ?? {}, () => [this.three.scene, ...this.prefabRoots]);
    this.library.load(manifest.prefabs, prefabGltfs);
    for (const entry of manifest.entities) {
      const instancer = entry.prefab ? this.world.get(entry.id) : undefined;
      if (instancer) this.library.instantiateUnder(this.world, entry.prefab!, instancer);
    }
    for (const { entity, position, quaternion } of placements ?? []) {
      if (entity.alive) this.physics.teleport(entity, position, quaternion);
    }
    const clips = [gltf, ...prefabGltfs.values()].flatMap((g) => g.animations);
    this.animation.reset(this.world, clips, keep);

    root.traverse((o) => {
      if ((o as Camera).isCamera) markBlenderCamera(o as Camera);
    });
    this.three.camera = pickCamera(this.world, root, manifest.settings.activeCamera) ?? createDefaultCamera();
    this.shadows = new ShadowFitter(suns, root);
    this.edits.applyAll(this.world, this.physics);
    for (const [material, values] of this.edits.materialValues()) if (this.shaders.has(material)) this.shaders.set(material, values);
    // The navmesh is ready before the scene runs its first step (a playtest's step 0 has it).
    await this.nav.reset(this.world, this.physics, this.three.scene, sceneUsesNav(manifest));

    this.scheduler.reset();
    this.stepper.reset();
    this.pendingSteps = 0;
    this.ctx.time = 0;
    if (this.shown === 'game') this.shaderTime.value = 0; // the Scene view's water does not jump on every edit
    this.ctx.frame = 0;
    this.scene = { name: manifest.scene, rev: manifest.rev };
    if (request.origin === 'game') this.sceneChanges++;
    else this.loads++;
    this.random.reseed(hashSeed(this.runSeed, manifest.scene, this.instantiations++));
    this.showSelection();
    this.syncView();
  }

  /**
   * Background (flat colour or gradient sky), fill light tint, fog, tone mapping and exposure from the
   * scene settings; game shaders' sky uniforms (`ctx.sky`) follow the same settings.
   */
  private applyEnvironment(manifest: SceneManifest): void {
    const scene = this.three.scene;
    const { background, sky, fog, toneMapping, exposure } = manifest.settings;
    updateSkyUniforms(this.shaderSky, manifest.settings);
    this.toneMapping = toneMapping;
    this.applyToneMapping();
    this.post.exposureScale = 2 ** exposure; // times the PostEffects exposure, every frame
    this.sky?.dispose();
    this.sky = sky ? skyTexture(sky) : null;
    this.skySettings = sky;
    scene.background = this.sky ?? (background ? new Color().setRGB(background[0], background[1], background[2], LinearSRGBColorSpace) : DEFAULT_BACKGROUND.clone());
    if (this.fill) tintFillLight(this.fill, sky);
    scene.fog = fog ? new Fog(new Color().setRGB(fog.color[0], fog.color[1], fog.color[2], LinearSRGBColorSpace), fog.near, fog.far) : null;
  }

  /** The scene's tone mapping on the renderer (WebGL and WebGPU). */
  private applyToneMapping(): void {
    const raw = this.three.renderer?.raw;
    if (raw) raw.toneMapping = toneMappingOf(this.toneMapping);
  }

  private readonly frame = (now: number): void => {
    requestAnimationFrame(this.frame);
    if (this.held) return; // A test drives time (testFrame) and renders on request.
    const started = performance.now();
    const dt = this.lastTime ? Math.min((now - this.lastTime) / 1000, 0.1) : FIXED_DT;
    const elapsed = this.lastTime ? now - this.lastTime : 0;
    this.uiTime += elapsed;
    this.lastTime = now;

    this.input.beginFrame();
    this.physics.beginFrame();
    this.ui.update();
    const screen = this.ui.current;
    this.tickTransition(Math.min(elapsed, 100));
    let advancing = false;
    if (this.shown !== 'game') {
      if (this.animateShaders) this.shaderTime.value += dt; // real time (capped), not ctx.time
    } else if (this.ready && this.state === 'playing') {
      this.simulate(dt, this.stepper.advance(dt));
      this.uiAfterSimulate(screen);
      advancing = true;
    } else if (this.ready && this.state === 'paused' && this.pendingSteps > 0) {
      const steps = this.pendingSteps;
      this.pendingSteps = 0;
      this.simulate(steps * FIXED_DT, steps);
    }
    if (!advancing) this.input.discardFixedEdges();
    if (this.shown !== 'scene') this.ui.frame(dt); // the 'ui' view's canvas: bindings, its loop
    this.render();
    this.input.endFrame();
    this.ui.endFrame();
    this.recordStats(now, performance.now() - started);
  };

  /**
   * One test step: a frame of exactly FIXED_DT with one fixed step. It does what a real frame
   * does except draw, so results do not depend on when (or whether) the test renders.
   */
  private testFrame(): boolean {
    this.uiTime += FIXED_DT * 1000;
    this.input.beginFrame(false);
    this.physics.beginFrame();
    this.ui.update();
    const screen = this.ui.current;
    this.tickTransition(FIXED_DT * 1000);
    const advancing = this.shown === 'game' && this.ready && this.state === 'playing';
    if (advancing) {
      this.simulate(FIXED_DT, 1);
      this.uiAfterSimulate(screen);
    } else this.input.discardFixedEdges();
    if (this.shown !== 'game' && this.animateShaders) this.shaderTime.value += FIXED_DT;
    if (this.shown !== 'scene') this.ui.frame(FIXED_DT);
    this.syncView();
    this.input.endFrame();
    this.ui.endFrame();
    return advancing;
  }

  /**
   * A screen that opened during this frame's simulation (a title shown from start(), or a start
   * canvas) gets this frame's input too, so a key or click on that frame, such as a playtest's Enter
   * or click at 0 s, is not lost: the frame's earlier clicks are hit-tested again against it, then
   * its keys are read again.
   */
  private uiAfterSimulate(screenBefore: string | null): void {
    this.ui.afterSimulate(); // the canvases a start opens: after the systems' start()
    if (screenBefore === null && this.ui.current !== null) {
      this.ui.replayPointer();
      this.ui.update();
    }
  }

  private simulate(dt: number, steps: number): void {
    const ctx = this.ctx;
    ctx.time += dt;
    this.shaderTime.value = ctx.time;
    this.scheduler.prepare(ctx);
    const gravity = steps > 0 ? this.physics.getGravity() : ZERO;
    for (let i = 0; i < steps; i++) {
      ctx.dt = FIXED_DT;
      this.input.beginFixedStep();
      this.scheduler.run('fixed', ctx);
      this.input.endFixedStep();
      this.physics.step(FIXED_DT);
      this.vfx.step(FIXED_DT, gravity);
    }
    ctx.dt = dt;
    ctx.phase = 'update';
    this.animation.update(dt);
    this.audio.update(this.three.camera);
    this.timers.run(ctx.time, ctx, (error) => this.reportError(error, 'timer'));
    this.scheduler.run('update', ctx);
    this.scheduler.run('late', ctx);
    ctx.frame++;
  }

  /** What rendering does to the scene besides drawing: world matrices, camera aspect, HUD expiry. */
  private syncView(): void {
    const { scene, camera } = this.three;
    scene.updateMatrixWorld();
    if (camera.parent === null) camera.updateMatrixWorld();
    this.fitView();
    this.hud.expire();
  }

  private fitView(): void {
    const camera = this.three.camera;
    if (camera !== this.fittedCamera || this.viewAspect !== this.fittedAspect) {
      fitCamera(camera, this.viewAspect);
      this.fittedCamera = camera;
      this.fittedAspect = this.viewAspect;
    }
  }

  private render(): void {
    const renderer = this.three.renderer;
    if (!renderer) return;
    const scene = this.three.scene;
    this.fitView(); // the game's camera; the editor's is never fitted
    const camera = this.viewCamera;
    this.shadows?.update(camera);
    this.hud.update(camera);
    this.fade.set(this.fadeOpacity(), this.transition?.color);
    this.selection.update();
    // An orthographic view looks one way: the gradient sky is the one colour in that direction, as
    // Blender draws it (three.js would draw the sky as a box about the size of a pixel).
    const background = scene.background;
    if (background && background === this.sky && this.skySettings && (camera as OrthographicCamera).isOrthographicCamera) {
      const [r, g, b] = skyColor(this.skySettings, camera.getWorldDirection(this.lookDirection).y);
      scene.background = this.flatSky.setRGB(r, g, b, LinearSRGBColorSpace);
    }
    try {
      const preview = this.shown === 'ui' ? this.previewing : null;
      if (preview && (preview.background === 'checker' || !this.files)) {
        (this.checker ??= new Checkerboard()).render(renderer, ...this.viewSize); // no level to show
      } else {
        this.vfx.render(camera, renderer.kind);
        if (!this.post.render(renderer, scene, camera)) renderer.render(scene, camera);
      }
      // Blender-authored menus and HUDs, over the frame and its post effects (not through renderer.render).
      if (this.shown !== 'scene') this.ui.render(renderer.raw);
      // ctx.hud is DOM above the canvas: its labels (+10, enemy bars) would sit on a Blender menu.
      const hudOpacity = this.ui.canvasMenuDrawn ? '0' : '';
      if (this.hud.element.style.opacity !== hudOpacity) this.hud.element.style.opacity = hudOpacity;
    } catch (error) {
      this.reportError(error, 'render');
    } finally {
      scene.background = background;
    }
  }

  private resize(): void {
    const width = this.canvas.clientWidth || window.innerWidth;
    const height = this.canvas.clientHeight || window.innerHeight;
    this.three.renderer?.setSize(width, height);
    this.hud.layout();
    this.ui.layout(width, height);
    this.fade.layout();
    this.viewAspect = width / Math.max(1, height);
    this.viewSize = [width, height];
  }

  private onSystemError(system: SystemDef, error: unknown, hook: string): void {
    this.reportError(error, `system ${system.name}.${hook}`);
  }

  private recordStats(now: number, cpuMs: number): void {
    const s = this.frameStats;
    if (s.since === 0) s.since = now;
    s.frames++;
    s.cpuMs += cpuMs;
    if (now - s.since >= 1000) {
      const seconds = (now - s.since) / 1000;
      this.onStats({
        fps: Math.round((s.frames / seconds) * 10) / 10,
        frameMs: Math.round((s.cpuMs / s.frames) * 100) / 100,
        entities: this.world.size,
        bodies: this.physics.bodyCount,
      });
      s.frames = 0;
      s.cpuMs = 0;
      s.since = now;
    }
  }
}

const ZERO = new Vector3();

/** Geometries, materials and textures used under `roots` (kept across a scene load, so not disposed). */
function resourcesOf(roots: Object3D[]): Set<unknown> {
  const found = new Set<unknown>();
  for (const root of roots) {
    root.traverse((object) => {
      const mesh = object as Object3D & { geometry?: unknown; material?: unknown };
      if (mesh.geometry) found.add(mesh.geometry);
      const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
      for (const material of materials as Array<Record<string, unknown>>) {
        found.add(material);
        for (const value of Object.values(material)) if ((value as { isTexture?: boolean })?.isTexture) found.add(value);
      }
    });
  }
  return found;
}

/** The meshes an entity draws itself: those under its object, without child entities' subtrees. */
function ownMeshes(world: World, entity: Entity): Mesh[] {
  const meshes: Mesh[] = [];
  const visit = (object: Object3D): void => {
    if (object !== entity.object3d && world.entityOf(object)?.object3d === object) return; // another entity
    if ((object as Mesh).isMesh) meshes.push(object as Mesh);
    for (const child of object.children) visit(child);
  };
  visit(entity.object3d);
  return meshes;
}

/** Frees GPU resources of a detached subtree (except those in `spare`). */
function disposeObject(root: Object3D, spare: Set<unknown> = new Set()): void {
  root.traverse((object) => {
    const mesh = object as Object3D & { geometry?: { dispose(): void }; material?: unknown };
    if (mesh.geometry && !spare.has(mesh.geometry)) mesh.geometry.dispose();
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    for (const material of materials as Array<Record<string, unknown> & { dispose(): void }>) {
      if (spare.has(material)) continue;
      for (const value of Object.values(material)) {
        if (value && typeof value === 'object' && (value as { isTexture?: boolean }).isTexture && !spare.has(value)) {
          (value as { dispose(): void }).dispose();
        }
      }
      material.dispose();
    }
  });
}

interface Placement {
  entity: Entity;
  /** World pose of the scene's own entity that the kept one replaces. */
  position: Vector3;
  quaternion: Quaternion;
}

/**
 * Makes room for kept entities in a freshly parsed scene: a scene entity with the name of a kept
 * one (its own "Player") is dropped with its subtree, and the kept entity will stand where it stood;
 * entity fields that pointed at the dropped entities point at the kept ones. Scene entities whose
 * ids a kept entity already uses are dropped too. Edits `manifest` and `root` in place.
 */
function replaceKept(manifest: SceneManifest, root: Object3D, keep: Set<Entity>): Placement[] {
  const keptIds = new Set([...keep].map((e) => e.id));
  const keptRoots = [...keep].filter((e) => e.parentId === null || ![...keep].some((k) => k.id === e.parentId));
  const byName = new Map<string, Entity>();
  for (const e of keptRoots) if (!byName.has(e.name)) byName.set(e.name, e);
  const children = new Map<string, string[]>();
  for (const entry of manifest.entities) {
    if (entry.parent) children.set(entry.parent, [...(children.get(entry.parent) ?? []), entry.id]);
  }
  const names = new Map(manifest.entities.map((e) => [e.id, e.name]));
  const nodes = indexByGsId(root);
  root.updateMatrixWorld(true);
  const dropped = new Set<string>();
  const remap = new Map<string, string>();
  const places: Placement[] = [];
  const dropTree = (id: string, kept: Entity | null): void => {
    const subtree: string[] = [];
    const visit = (i: string): void => {
      if (dropped.has(i)) return;
      dropped.add(i);
      subtree.push(i);
      for (const c of children.get(i) ?? []) visit(c);
    };
    visit(id);
    if (kept) {
      remap.set(id, kept.id);
      const keptByName = new Map<string, Entity>();
      for (const e of keep) if (!keptByName.has(e.name)) keptByName.set(e.name, e);
      for (const i of subtree.slice(1)) {
        const match = keptByName.get(names.get(i)!);
        if (match) remap.set(i, match.id);
      }
    }
    const node = nodes.get(id);
    node?.removeFromParent();
  };
  const used = new Set<string>();
  for (const entry of manifest.entities) {
    if (dropped.has(entry.id)) continue;
    const kept = byName.get(entry.name);
    if (kept && !used.has(entry.name)) {
      used.add(entry.name);
      const node = nodes.get(entry.id);
      const position = new Vector3();
      const quaternion = new Quaternion();
      if (node) node.matrixWorld.decompose(position, quaternion, new Vector3());
      places.push({ entity: kept, position, quaternion });
      dropTree(entry.id, kept);
    } else if (keptIds.has(entry.id)) {
      engineWarn(`loadScene keep: "${entry.name}" in the new scene has the id of a kept entity; the kept one stays`);
      dropTree(entry.id, null);
    }
  }
  manifest.entities = manifest.entities.filter((e) => !dropped.has(e.id));
  for (const entry of manifest.entities) remapFields(entry.components, remap);
  return places;
}

/** Points entity fields that hold old ids at their new ids. */
function remapFields(components: Record<string, Record<string, unknown>>, remap: Map<string, string>): void {
  if (remap.size === 0) return;
  for (const [name, values] of Object.entries(components)) {
    const type = getComponent(name);
    if (!type) continue;
    for (const [field, def] of Object.entries(type.fields)) {
      const value = values[field];
      if (def.type === 'entity' && typeof value === 'string' && remap.has(value)) values[field] = remap.get(value)!;
    }
  }
}
