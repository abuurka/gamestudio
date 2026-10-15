// `@gamestudio/engine/schema`: component and game-shader schemas and the registry.
// No DOM and no Three.js (types only), so Node (the dev server) can evaluate it.
// Importing this module always registers the built-in components and the shader library
// (each library shader's GLSL stays in a module of its own, loaded only when a scene uses it).

export {
  field,
  cloneValue,
  type FieldType,
  type FieldDef,
  type AnyFieldDef,
  type FieldOptions,
  type FloatOptions,
  type NumberOptions,
  type StringOptions,
  type FloatUnit,
  type AssetOptions,
  type TextureColorSpace,
  type Vec2,
  type Vec3,
  type Color,
} from './schema/fields';

export {
  defineComponent,
  getComponent,
  listComponents,
  onComponentDefined,
  coerceField,
  defaultValues,
  fillDefaults,
  toRegistryJSON,
  type ComponentType,
  type ComponentValues,
  type ComponentOptions,
  type ValuesOf,
  type RegistryJSON,
  type RegistryJSONOptions,
  type RegistryComponent,
  type RegistryShader,
  type RegistryField,
} from './schema/registry';

export {
  defineShader,
  getShader,
  listShaders,
  onShaderDefined,
  shaderValues,
  patchShader,
  skyGlsl,
  skyUniforms,
  loadShader,
  SHADER_CATEGORIES,
  type ShaderType,
  type ShaderValues,
  type ShaderOptions,
  type ShaderContext,
  type ShaderEdit,
  type ShaderApply,
  type ShaderMesh,
  type ShaderImpl,
  type ShaderPreview,
  type ShaderUniforms,
  type ShaderSky,
  type UniformValueOf,
} from './schema/shaders';

export {
  RigidBody,
  Collider,
  CharacterController,
  FollowCamera,
  SideCamera,
  Spin,
  Animator,
  Locomotion,
  AudioSource,
  ParticleEmitter,
  PostEffects,
  NavMesh,
  NavAgent,
  UiCanvas,
  UiButton,
  UiAnchor,
  UiText,
  UiBar,
  UiState,
  UiShow,
  UiSlider,
  UiToggle,
  UiSlice,
  UiModel,
  builtinComponents,
} from './schema/builtins';

// The built-in shader library registers itself (after the components, so a project's own
// src/shaders files, which import this module, define their shaders later and win).
export { libraryShaders } from './shaders/library';
