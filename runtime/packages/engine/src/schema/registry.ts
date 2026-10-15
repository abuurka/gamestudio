// Global component registry (docs/SPEC.md §3 and §6.2), and the registry JSON with the game
// shaders (shaders.ts). DOM-free: the dev server loads it through Vite's SSR runner to write
// `.gamestudio/components.json`.

import { checkDefinition, cloneValue, fillFields, type AnyFieldDef, type FieldDef } from './fields';
import { listShaders } from './shaders';

export { coerceField } from './fields';

/** Maps a field-definition record to the value object it describes. */
export type ValuesOf<F> = {
  [K in keyof F]: F[K] extends FieldDef<infer _T, infer V> ? V : never;
};

export interface ComponentOptions<F extends Record<string, AnyFieldDef>> {
  description?: string;
  /** Groups components in the Blender UI, e.g. "Physics" or "Gameplay". */
  category?: string;
  fields?: F;
  /** @internal Set only by the engine for its built-in components. */
  builtin?: boolean;
}

/**
 * A component type handle returned by `defineComponent`. Entities store data by
 * component *name*, so a hot-reloaded definition keeps working with old handles.
 */
export interface ComponentType<V = Record<string, unknown>> {
  readonly name: string;
  readonly description?: string;
  readonly category?: string;
  readonly builtin: boolean;
  readonly fields: Readonly<Record<string, AnyFieldDef>>;
  /** Type-level only; never set at runtime. */
  readonly __values?: V;
}

/** Value type of a component handle: `ComponentValues<typeof Spin>`. */
export type ComponentValues<C> = C extends ComponentType<infer V> ? V : never;

const registry = new Map<string, ComponentType>();
const listeners = new Set<(type: ComponentType) => void>();

/**
 * Defines (or redefines) a component. Redefinition with the same name replaces
 * the schema; hot module replacement relies on this.
 */
export function defineComponent<F extends Record<string, AnyFieldDef> = Record<string, never>>(
  name: string,
  options: ComponentOptions<F> = {},
): ComponentType<ValuesOf<F>> {
  const fields = options.fields ?? ({} as F);
  checkDefinition('component', name, fields);
  const type: ComponentType<ValuesOf<F>> = Object.freeze({
    name,
    description: options.description,
    category: options.category,
    builtin: options.builtin === true,
    fields: Object.freeze({ ...fields }),
  });
  registry.set(name, type as ComponentType);
  for (const listener of listeners) listener(type as ComponentType);
  return type;
}

/** The current definition for a name (or for a possibly stale handle). */
export function getComponent(type: string | ComponentType<any>): ComponentType | undefined {
  return registry.get(typeof type === 'string' ? type : type.name);
}

/** All registered components, sorted by name. */
export function listComponents(): ComponentType[] {
  return [...registry.values()].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** Calls `listener` whenever a component is defined or redefined. Returns an unsubscribe function. */
export function onComponentDefined(listener: (type: ComponentType) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Fresh default values for a component. */
export function defaultValues<V>(type: ComponentType<V> | string): V {
  return fillDefaults(type, {});
}

/**
 * Returns a complete value object: known fields from `values` (coerced), the
 * rest from schema defaults. Unknown keys are dropped. For an unregistered
 * component name the values are copied as-is.
 */
export function fillDefaults<V>(type: ComponentType<V> | string, values: Record<string, unknown> = {}): V {
  const def = getComponent(type as ComponentType | string);
  if (!def) return { ...values } as V;
  return fillFields(def.fields, values) as V;
}

// ---------------------------------------------------------------------------
// Registry JSON (SPEC §3)

export interface RegistryField {
  name: string;
  type: string;
  default: unknown;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  options?: string[];
  multiline?: boolean;
  extensions?: string[];
  description?: string;
}

export interface RegistryComponent {
  name: string;
  description?: string;
  category?: string;
  builtin: boolean;
  source?: string;
  fields: RegistryField[];
}

/** A game shader (`defineShader`): the component shape. `builtin` is true for the engine's shader library. */
export interface RegistryShader {
  name: string;
  description?: string;
  category?: string;
  builtin: boolean;
  source?: string;
  /** Present (true) when the shader works on Game UI meshes (`ui: true`, GAME-UI §11.4). */
  ui?: true;
  fields: RegistryField[];
}

export interface RegistryJSON {
  format: 'gamestudio.registry';
  formatVersion: 1;
  generatedAt: string;
  components: RegistryComponent[];
  /** Always written by this version (possibly empty); readers treat a missing key as `[]`. */
  shaders: RegistryShader[];
}

export interface RegistryJSONOptions {
  generatedAt?: string;
  /** Component name → project-relative source file. */
  sources?: Record<string, string>;
  /** Game shader name → project-relative source file. */
  shaderSources?: Record<string, string>;
}

function fieldJSON(name: string, def: AnyFieldDef): RegistryField {
  // Fixed key order keeps the file stable and diff-friendly.
  const out: RegistryField = { name, type: def.type, default: cloneValue(def.default) };
  if (def.min !== undefined) out.min = def.min;
  if (def.max !== undefined) out.max = def.max;
  if (def.step !== undefined) out.step = def.step;
  if (def.unit !== undefined) out.unit = def.unit;
  if (def.options !== undefined) out.options = [...def.options];
  if (def.multiline !== undefined) out.multiline = def.multiline;
  if (def.extensions !== undefined) out.extensions = [...def.extensions];
  if (def.description !== undefined) out.description = def.description;
  return out;
}

/** The registry in the `components.json` shape, components and game shaders sorted by name. */
export function toRegistryJSON(options: RegistryJSONOptions = {}): RegistryJSON {
  return {
    format: 'gamestudio.registry',
    formatVersion: 1,
    generatedAt: options.generatedAt ?? new Date().toISOString(),
    components: listComponents().map((c) => {
      // Keys are added in the documented order so the file is stable.
      const entry: RegistryComponent = { name: c.name } as RegistryComponent;
      if (c.description !== undefined) entry.description = c.description;
      if (c.category !== undefined) entry.category = c.category;
      entry.builtin = c.builtin;
      const source = options.sources?.[c.name];
      if (source !== undefined) entry.source = source;
      entry.fields = Object.entries(c.fields).map(([n, d]) => fieldJSON(n, d));
      return entry;
    }),
    shaders: listShaders().map((s) => {
      const entry: RegistryShader = { name: s.name } as RegistryShader;
      if (s.description !== undefined) entry.description = s.description;
      if (s.category !== undefined) entry.category = s.category;
      const source = options.shaderSources?.[s.name];
      // A project file that defines a library shader's name overrides it: that one is not built in.
      entry.builtin = s.builtin && source === undefined;
      if (source !== undefined) entry.source = source;
      if (s.ui) entry.ui = true;
      entry.fields = Object.entries(s.fields).map(([n, d]) => fieldJSON(n, d));
      return entry;
    }),
  };
}
