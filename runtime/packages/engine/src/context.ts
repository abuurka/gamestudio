// The `ctx` object passed to systems (docs/SPEC.md §6.4).

import type { Camera, Scene } from 'three';
import type { AnimationAPI } from './animation/animation';
import type { AudioAPI } from './audio/audio';
import type { Entity } from './ecs/entity';
import type { Phase } from './ecs/system';
import type { World } from './ecs/world';
import type { GameRenderer } from './game/renderer';
import type { Timer } from './game/timers';
import type { HudAPI } from './hud/hud';
import type { InputAPI } from './input/input';
import type { NavAPI } from './nav/nav';
import type { PhysicsAPI } from './physics/backend';
import type { PostAPI } from './game/post';
import type { RandomAPI } from './game/random';
import type { ShadersAPI } from './scene/materials';
import type { SaveAPI } from './game/save';
import type { SpawnPrefabOptions } from './prefab/prefabs';
import type { UiAPI } from './ui/ui';
import type { VfxAPI } from './vfx/vfx';

export type GameState = 'loading' | 'playing' | 'paused' | 'stopped';

/**
 * What the page shows: the game (`'game'`), the level as the editor sees it (`'scene'`, the Scene
 * view: stopped, nothing simulates, an editor camera, no menus or HUD), or one Game UI canvas as the
 * editor's camera frame shows it (`'ui'`, the Scene view of a UI file: stopped, the canvas at rest
 * over the level or a checkerboard; docs/GAME-UI.md §11.2).
 */
export type GameView = 'game' | 'scene' | 'ui';

export interface SceneInfo {
  /** Scene name, e.g. "main". Empty before the first load. */
  name: string;
  /** Export revision from the manifest. */
  rev: number;
}

/** `ctx.log(...)` logs at info level; `.debug/.info/.warn/.error` pick a level. */
export interface Logger {
  (...args: unknown[]): void;
  debug(...args: unknown[]): void;
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

/** How `ctx.loadScene` changes scenes. */
export interface SceneLoadOptions {
  /** Seconds of the transition: fade to `color`, load, fade back. Default 0.5 for `ctx.loadScene`; 0 cuts. */
  fade?: number;
  /** Fade colour (CSS). Default '#000'. */
  color?: string;
  /**
   * Entities (handles, names or ids) carried into the new scene with their children, components,
   * physics, clips, sounds and particles. When the new scene has an entity with the same name (its
   * own "Player"), the kept one takes its place and references to it.
   */
  keep?: Array<Entity | string>;
}

/** Game-level controls available to systems as `ctx.game`. */
export interface GameControls {
  readonly state: GameState;
  readonly scene: SceneInfo;
  /** The scene the game started with (`startScene`, or the page's `?scene=`). */
  readonly startScene: string;
  /** Load another exported scene by name (e.g. "level2"). No fade unless `options.fade` is set (see `ctx.loadScene`). */
  loadScene(name: string, options?: SceneLoadOptions): Promise<void>;
  /** Run systems and physics. */
  play(): void;
  /** Reset to the loaded state and halt. */
  stop(): void;
  /** Freeze or unfreeze simulation time. */
  pause(paused?: boolean): void;
}

export interface ThreeAccess {
  scene: Scene;
  /** Active camera. Assign a different camera to switch views. */
  camera: Camera;
  renderer: GameRenderer;
}

export interface SystemContext {
  /** Seconds since the previous tick of this phase (1/60 in 'fixed'). */
  dt: number;
  /** Simulated seconds since the scene started playing. */
  time: number;
  /** Simulated frame count since the scene started playing. */
  frame: number;
  /** The phase currently running. */
  phase: Phase;
  world: World;
  input: InputAPI;
  physics: PhysicsAPI;
  hud: HudAPI;
  three: ThreeAccess;
  log: Logger;
  game: GameControls;
  /** Clip playback on entities (Blender actions). */
  animation: AnimationAPI;
  /** Sounds; starts after the first user input. */
  audio: AudioAPI;
  /** Names of the prefabs `spawn` accepts (collections marked as prefabs in Blender). */
  readonly prefabs: readonly string[];
  /** Instantiates a prefab with its components and returns its root entity. Throws for unknown names. */
  spawn(prefab: string, options?: SpawnPrefabOptions): Entity;
  /** Destroys an entity (by handle or id) and its children. Ignores null and dead entities. */
  destroy(entity: Entity | string | null | undefined): void;
  /** Calls `fn` once after `seconds` of game time. */
  after(seconds: number, fn: (ctx: SystemContext) => void): Timer;
  /** Calls `fn` every `seconds` of game time (at most once per frame). */
  every(seconds: number, fn: (ctx: SystemContext) => void): Timer;
  /** Seeded random numbers for gameplay: `ctx.random()`, `.range`, `.int`, `.pick`, `.chance`. */
  random: RandomAPI;
  /** Particle effects: `ctx.vfx.burst(entityOrPoint, 'pickup')`, and ParticleEmitter control. */
  vfx: VfxAPI;
  /**
   * Menus and HUDs: canvases authored in Blender (`ctx.ui.open`, `on`, `text`, `bar`) and code-only
   * screens (`ctx.ui.screen`, `ctx.ui.pauseMenu`).
   */
  ui: UiAPI;
  /** Bloom, vignette and colour grading settings. */
  post: PostAPI;
  /** Navigation meshes and pathfinding: `ctx.nav.path(from, to)`, NavAgent control, the debug view. */
  nav: NavAPI;
  /** Game state that survives scene changes (score, lives, inventory). Cleared by `restart` and Stop. */
  store: Record<string, any>;
  /** Values that survive page reloads (high scores, settings), per game. */
  save: SaveAPI;
  /**
   * Live game-shader values: `ctx.shaders.set(enemy, { hitAt: ctx.time })` changes one entity's
   * materials, `ctx.shaders.set('GS_Water', { speed: 2 })` a material everywhere. See ShadersAPI.
   */
  shaders: ShadersAPI;
  /** Changes scene with a fade (default 0.5 s). See SceneLoadOptions. */
  loadScene(name: string, options?: SceneLoadOptions): Promise<void>;
  /** Starts the game over: clears `store`, closes menus and loads the start scene (with a fade). */
  restart(options?: { fade?: number }): Promise<void>;
}

export function createLogger(prefix = '[game]'): Logger {
  const log = ((...args: unknown[]) => console.info(prefix, ...args)) as Logger;
  log.debug = (...args) => console.debug(prefix, ...args);
  log.info = (...args) => console.info(prefix, ...args);
  log.warn = (...args) => console.warn(prefix, ...args);
  log.error = (...args) => console.error(prefix, ...args);
  return log;
}
