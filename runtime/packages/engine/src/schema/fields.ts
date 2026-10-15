// Field builders for component and game-shader schemas (docs/SPEC.md §3 and §6.2), and the
// checks and value coercion both share. DOM-free and Three-free: the dev server evaluates this
// module in Node.

export type FieldType =
  | 'float'
  | 'int'
  | 'bool'
  | 'string'
  | 'enum'
  | 'vec2'
  | 'vec3'
  | 'color'
  | 'entity'
  | 'asset';

export type Vec2 = [number, number];
export type Vec3 = [number, number, number];
/** Linear RGBA, each channel 0..1. */
export type Color = [number, number, number, number];

export type FloatUnit = 'none' | 'distance' | 'angle' | 'time';

/** Options shared by every field builder. */
export interface FieldOptions {
  description?: string;
}

export interface NumberOptions extends FieldOptions {
  min?: number;
  max?: number;
}

export interface FloatOptions extends NumberOptions {
  step?: number;
  unit?: FloatUnit;
}

export interface StringOptions extends FieldOptions {
  /** UI hint only: edit as a multi-line text box. */
  multiline?: boolean;
}

/**
 * How a game shader's `asset` field image is loaded as a texture: `'srgb'` for colour images
 * (albedo scans, decals), `'linear'` (the default) for data (normal, roughness, height, noise, masks).
 */
export type TextureColorSpace = 'srgb' | 'linear';

export interface AssetOptions extends FieldOptions {
  default?: string;
  /** Game shaders only: how the image is loaded as a texture (default `'linear'`). */
  colorSpace?: TextureColorSpace;
}

/**
 * A field definition. `V` is the TypeScript type of the field's value; it is
 * only used for inference (`e.get(C)` returns correctly typed values).
 */
export interface FieldDef<T extends FieldType = FieldType, V = unknown> {
  readonly type: T;
  readonly default: V;
  readonly description?: string;
  readonly min?: number;
  readonly max?: number;
  readonly step?: number;
  readonly unit?: FloatUnit;
  readonly options?: readonly string[];
  readonly multiline?: boolean;
  readonly extensions?: readonly string[];
  readonly colorSpace?: TextureColorSpace;
}

export type AnyFieldDef = FieldDef<FieldType, unknown>;

function make<T extends FieldType, V>(
  type: T,
  def: V,
  opts: object | undefined,
  extra: Record<string, unknown> = {},
): FieldDef<T, V> {
  const out: Record<string, unknown> = { type, default: def, ...extra };
  if (opts) {
    for (const [k, v] of Object.entries(opts)) if (v !== undefined) out[k] = v;
  }
  return Object.freeze(out) as unknown as FieldDef<T, V>;
}

function tuple<N extends number>(value: readonly number[], size: N, what: string): number[] {
  if (!Array.isArray(value) || value.length !== size || value.some((n) => typeof n !== 'number')) {
    throw new Error(`${what} default must be an array of ${size} numbers`);
  }
  return [...value];
}

/** Field builders. Each returns an immutable field definition. */
export const field = {
  float(def = 0, opts?: FloatOptions): FieldDef<'float', number> {
    return make('float', def, opts);
  },
  int(def = 0, opts?: NumberOptions): FieldDef<'int', number> {
    if (!Number.isInteger(def)) throw new Error(`field.int default must be an integer, got ${def}`);
    return make('int', def, opts);
  },
  bool(def = false, opts?: FieldOptions): FieldDef<'bool', boolean> {
    return make('bool', def, opts);
  },
  string(def = '', opts?: StringOptions): FieldDef<'string', string> {
    return make('string', def, opts);
  },
  /** `options` lists the allowed values; `def` defaults to the first option. */
  enum<const O extends readonly string[]>(
    options: O,
    def?: O[number],
    opts?: FieldOptions,
  ): FieldDef<'enum', O[number]> {
    if (!Array.isArray(options) || options.length === 0) throw new Error('field.enum needs at least one option');
    const value = def ?? options[0];
    if (!options.includes(value as string)) {
      throw new Error(`field.enum default "${String(value)}" is not one of ${options.join(', ')}`);
    }
    return make('enum', value as O[number], opts, { options: [...options] });
  },
  vec2(def: readonly [number, number] = [0, 0], opts?: NumberOptions): FieldDef<'vec2', Vec2> {
    return make('vec2', tuple(def, 2, 'field.vec2') as Vec2, opts);
  },
  vec3(def: readonly [number, number, number] = [0, 0, 0], opts?: NumberOptions): FieldDef<'vec3', Vec3> {
    return make('vec3', tuple(def, 3, 'field.vec3') as Vec3, opts);
  },
  /** Linear RGBA 0..1. */
  color(def: readonly [number, number, number, number] = [1, 1, 1, 1], opts?: FieldOptions): FieldDef<'color', Color> {
    return make('color', tuple(def, 4, 'field.color') as Color, opts);
  },
  /** Reference to another entity by `gs_id`; the value is the id string or `null`. */
  entity(opts?: FieldOptions): FieldDef<'entity', string | null> {
    return make('entity', null, opts);
  },
  /** Project-relative asset path, e.g. `"assets/music.ogg"`. */
  asset(extensions?: readonly string[], opts?: AssetOptions): FieldDef<'asset', string> {
    const { default: def = '', ...rest } = opts ?? {};
    return make('asset', def, rest, extensions ? { extensions: [...extensions] } : {});
  },
};

/** Deep-copies a default value so entities never share arrays. */
export function cloneValue<V>(value: V): V {
  return (Array.isArray(value) ? [...value] : value) as V;
}

const TYPE_NAME = /^[A-Z][A-Za-z0-9]{0,40}$/;
const FIELD_NAME = /^[a-z][a-zA-Z0-9_]{0,40}$/;

/**
 * @internal The name and field checks of `defineComponent` and `defineShader` (separate namespaces,
 * same rules). Game shaders take no `entity` fields: a material is not an entity. Throws on the first problem.
 */
export function checkDefinition(kind: 'component' | 'shader', name: string, fields: Record<string, AnyFieldDef>): void {
  if (!TYPE_NAME.test(name)) {
    throw new Error(`Invalid ${kind} name "${name}": must match ${TYPE_NAME}`);
  }
  for (const [fieldName, def] of Object.entries(fields)) {
    if (!FIELD_NAME.test(fieldName)) {
      throw new Error(`Invalid field name "${name}.${fieldName}": must match ${FIELD_NAME}`);
    }
    if (!def || typeof def !== 'object' || typeof def.type !== 'string') {
      throw new Error(`Field "${name}.${fieldName}" must be created with a field.* builder`);
    }
    if (kind === 'shader' && def.type === 'entity') {
      throw new Error(`Field "${name}.${fieldName}": game shaders cannot have entity fields (a material is not an entity)`);
    }
  }
}

/** Coerces one value to a field's type, falling back to the default when it doesn't fit. */
export function coerceField(def: AnyFieldDef, value: unknown): unknown {
  if (value === undefined) return cloneValue(def.default);
  const vecSize = { vec2: 2, vec3: 3, color: 4 } as Record<string, number | undefined>;
  switch (def.type) {
    case 'float':
    case 'int':
      return typeof value === 'number' && Number.isFinite(value)
        ? def.type === 'int' ? Math.round(value) : value
        : cloneValue(def.default);
    case 'bool':
      return typeof value === 'boolean' ? value : Boolean(value);
    case 'enum':
      return typeof value === 'string' && def.options?.includes(value) ? value : cloneValue(def.default);
    case 'entity':
      return typeof value === 'string' && value !== '' ? value : null;
    case 'string':
    case 'asset':
      return typeof value === 'string' ? value : String(value);
    case 'vec2':
    case 'vec3':
    case 'color': {
      const size = vecSize[def.type]!;
      return Array.isArray(value) && value.length === size && value.every((n) => typeof n === 'number')
        ? [...value]
        : cloneValue(def.default);
    }
    default:
      return value;
  }
}

/** @internal Every field of `fields`: the value from `values` (coerced) or the default. Unknown keys are dropped. */
export function fillFields(fields: Readonly<Record<string, AnyFieldDef>>, values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, def] of Object.entries(fields)) out[name] = coerceField(def, values[name]);
  return out;
}
