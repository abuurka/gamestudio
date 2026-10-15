// `@gamestudio/engine`: the full runtime. See API.md for the reference.

// Schemas, the registry, game shaders and built-in components (DOM-free, also at ./schema).
export * from './schema';

// ECS
export { Entity, type ComponentRef } from './ecs/entity';
export { World, type SpawnOptions, type WorldEvent } from './ecs/world';
export { defineSystem, removeSystem, listSystems, type SystemDef, type Phase } from './ecs/system';

// Game
export { createGame, type DebugHandle } from './game/create-game';
export { Game, SCENE_FADE, type GameOptions, type ViewOptions } from './game/game';
export type { ViewCamera } from './game/editor-camera';
export { GameRandom, hashSeed, type RandomAPI } from './game/random';
export { PostService, type PostAPI, type PostSettings } from './game/post';
export type { SaveAPI } from './game/save';
export { BLENDER_LIGHT_SCALE } from './game/lighting';
export { FIXED_DT } from './game/stepper';
export {
  TestControl,
  expandTestInputs,
  type TestInput,
  type TestEvent,
  type TestStatus,
  type TrackedEntity,
  type FieldChange,
  type PlaceOptions,
  type TestBeginOptions,
  type SavedOptions,
} from './game/test-control';
export type { Timer } from './game/timers';
export type { GameRenderer, RendererKind } from './game/renderer';
export type { StateFilter, EntityState } from './game/describe';
export type { TransformItem } from './game/edits';
export type { ShadersAPI } from './scene/materials';
export type {
  SystemContext,
  SceneLoadOptions,
  GameControls,
  GameState,
  GameView,
  SceneInfo,
  Logger,
  ThreeAccess,
} from './context';

// Input, HUD, physics
export type { InputAPI, MouseState, GamepadState, AxisName } from './input/input';
export type { HudAPI, HudOptions, HudBarOptions, HudPosition } from './hud/hud';
export type {
  PhysicsAPI,
  PhysicsBackend,
  CollisionEvent,
  RaycastHit,
  RaycastOptions,
  Vec3Like,
} from './physics/backend';
export { RapierPhysics, initRapier } from './physics/rapier-backend';
export { COLLISION_LAYER } from './scene/build';

// Animation, audio, prefabs
export { AnimationService, type AnimationAPI, type AnimationEvent, type AnimationSnapshot, type PlayOptions } from './animation/animation';
export {
  defineAnimationGraph,
  getAnimationGraph,
  listAnimationGraphs,
  removeAnimationGraph,
  type AnimationGraph,
  type AnimationGraphDef,
  type AnimationGraphHandle,
  type GraphBlendClip,
  type GraphCondition,
  type GraphMotionSource,
  type GraphParamDef,
  type GraphParamOptions,
  type GraphParamType,
  type GraphParams,
  type GraphStateDef,
  type GraphTransitionDef,
} from './animation/graph';
export {
  AudioService,
  browserAudioEnvironment,
  type AudioAPI,
  type AudioChannel,
  type AudioChannelName,
  type AudioEnvironment,
  type DistanceModel,
  type Sound,
  type SoundChannel,
  type SoundOptions,
  type SoundState,
} from './audio/audio';
export { Prefabs, type SpawnPrefabOptions } from './prefab/prefabs';
export {
  VfxService,
  VFX_PRESETS,
  type VfxAPI,
  type VfxEffect,
  type VfxPreset,
  type ParticleOptions,
  type ParticleSettings,
} from './vfx/vfx';
export {
  Ui,
  type UiAPI,
  type UiAction,
  type UiScreenButton,
  type UiScreenOptions,
  type PauseMenuOptions,
  type UiButtonState,
  type UiTestState,
  type UiEventHandler,
  type UiEventInfo,
  type UiControlKind,
  type UiLocale,
} from './ui/ui';
// Types only: the canvas layer itself is a separate chunk, loaded when the game has Blender UI.
export type { UiElementInfo } from './ui/canvas/types';
export type { Vec3Input, RotationInput } from './math';

// Scene files
export {
  processManifest,
  MANIFEST_FORMAT_VERSION,
  type SceneManifest,
  type ManifestEntity,
  type ManifestAnimation,
  type ManifestPrefab,
  type SceneSettings,
  type ToneMappingName,
  type ManifestMaterial,
  type SkySettings,
  type FogSettings,
  type ManifestLightmaps,
  type ManifestLightmapAtlas,
  type ManifestLightmapObject,
} from './scene/manifest';
export { SceneNotFoundError, createGltfLoader } from './scene/loader';
export { applyLightmaps, loadLightmapTextures, type LightmapSummary, type LightmapTextureLoader } from './scene/lightmaps';

// Built-in systems register themselves on import.
export {
  SpinSystem,
  FollowCameraSystem,
  SideCameraSystem,
  CharacterControllerSystem,
  LocomotionSystem,
  characterMotion,
  moveForward,
  snapCamera,
  type CharacterMotion,
} from './systems';

// Navigation: navmeshes, pathfinding (ctx.nav) and the NavAgent system.
export { NavService, loadRecast, type NavAPI, type NavPathOptions, type NavStats, type NavMeshSettings } from './nav/nav';
export { NavAgentSystem } from './nav/agent';
