// The exported Game UI files (docs/GAME-UI.md §3): ui/index.json and ui/<Canvas>.ui.json, written by
// the add-on from ui/*.blend and sent live as `ui.canvas`. Types only, plus the format names.

export const UI_INDEX_FORMAT = 'gamestudio.ui-index';
export const UI_CANVAS_FORMAT = 'gamestudio.ui';
export const UI_FORMAT_VERSION = 1;

export type UiCanvasKind = 'menu' | 'hud';
export type UiCanvasOpen = 'manual' | 'start' | 'firstStart';
export type UiBackAction = 'none' | 'close' | 'resume' | 'previous' | 'restart';
export type UiScaleMode = 'fit' | 'width' | 'height' | 'fixed';
export type UiAnchorPoint =
  | 'center'
  | 'top-left'
  | 'top'
  | 'top-right'
  | 'left'
  | 'right'
  | 'bottom-left'
  | 'bottom'
  | 'bottom-right';
export type UiStretch = 'none' | 'width' | 'height' | 'both' | 'cover';
export type UiElementType = 'group' | 'text' | 'image' | 'mesh' | 'model';
/** Linear RGBA 0..1. */
export type UiColor = [number, number, number, number];

export interface UiIndexEntry {
  /** Relative to the index: `<sanitized canvas>.ui.json`. */
  file: string;
  rev: number;
  /** Project-relative UI file, e.g. `ui/ui.blend`. */
  source: string;
  kind: UiCanvasKind;
  open: UiCanvasOpen;
  /** Phase 3 (§11.1): the canvas's GLB (`<Canvas>.ui.glb`) -> content hash, as on the canvas (`UiCanvasData.files`). */
  files?: Record<string, string>;
}

export interface UiIndex {
  format: typeof UI_INDEX_FORMAT;
  formatVersion: number;
  canvases: Record<string, UiIndexEntry>;
  /** Phase 3 (§11.3): the language the texts are authored in (gamestudio.json `locale`, default 'en'). */
  sourceLocale?: string;
  /** Phase 3: translation tables, language code -> { file (relative to the index), name (the language's own name) }. */
  locales?: Record<string, { file: string; name: string }>;
}

/** A translation table, ui/locales/<code>.json (§11.3): authored text (bindings included) -> translation. */
export interface UiLocaleData {
  name: string;
  strings: Record<string, string>;
}

/** A 3D model element (§11.1): the UiModel empty's children, exported as one node of the canvas's GLB. */
export interface UiModelData {
  /** The canvas GLB, relative to the canvas file (`<Canvas>.ui.glb`). */
  glb: string;
  /** The node in the GLB holding this model's objects (its name: the element id). */
  node: string;
  /** Animation clips in the GLB that animate this model. */
  clips: string[];
}

/** The canvas camera's UiCanvas values (defaults filled in). `focus` is an element id. */
export interface UiCanvasSettings {
  kind: UiCanvasKind;
  open: UiCanvasOpen;
  pause: boolean;
  pauseMenu: boolean;
  back: UiBackAction;
  scaleMode: UiScaleMode;
  backdrop: UiColor;
  layer: number;
  focus: string | null;
}

/** Local to the parent, design px, y down; rotation in radians, clockwise positive. */
export interface UiTransform {
  x: number;
  y: number;
  rotation: number;
  scaleX: number;
  scaleY: number;
}

export interface UiTextData {
  /** Blender's body, bindings unresolved (`Score {score}`). */
  body: string;
  /** Project-relative font asset path ('' = the engine's fallback font). */
  font: string;
  /** Em size in design px. */
  size: number;
  color: UiColor;
  align: 'left' | 'center' | 'right' | 'justify' | 'flush';
  valign: 'top-baseline' | 'top' | 'center' | 'bottom' | 'bottom-baseline';
  /** Blender space_line, space_character, space_word (factors). */
  lineSpacing: number;
  letterSpacing: number;
  wordSpacing: number;
  /** First text box [x, y, w, h] in local px (y down), or null; text wraps at its width. */
  box: [number, number, number, number] | null;
  /** offset_x / offset_y in px (y down). */
  offset: [number, number];
  shear: number;
  /**
   * CSS font size in px that draws the glyphs as big as Blender does: Blender scales a font by
   * 1 / (head.yMax - head.yMin), not 1 / unitsPerEm, so this is size * upem / (yMax - yMin).
   * null (or absent) when the export could not read the font: use ascent / fontBoundingBoxAscent at 1 px.
   */
  fontSize?: number | null;
  /** Blender's ascent and descent in px (FreeType ascender / descender over (yMax - yMin), times size). */
  ascent?: number;
  descent?: number;
  /** Blender's text box overflow. */
  overflow?: 'none' | 'scale' | 'truncate';
  /** Phase 3 (§11.5): a variable font's axis values as Blender draws it (its default instance), tag -> value. */
  fontAxes?: Record<string, number>;
}

export interface UiImageData {
  /** Project-relative asset path (or `generated/<name>-<hash>.png`), mapped by `assets`. */
  src: string;
  /** [x, y, w, h] in local px, y down. */
  rect: [number, number, number, number];
  opacity: number;
  pixelated: boolean;
}

export interface UiMeshMaterial {
  /** Phase 3 (§11.4): the material's game shader and its values (asset paths mapped by `assets`). */
  shader?: { name: string; values: Record<string, unknown> };
  color: UiColor;
  /** Asset path of an image texture ('' = none). */
  texture: string;
  blend: 'alpha' | 'opaque' | 'additive';
}

/**
 * A 9-slice panel (GAME-UI §10.6): the runtime rebuilds the mesh as 9 quads whenever its laid-out size
 * changes, so its corners keep `insets × canvas scale` on screen.
 */
export interface UiSliceData {
  /** [left, right, top, bottom] in image px. */
  insets: [number, number, number, number];
  /** The texture's size [w, h] in px. */
  image: [number, number];
  /** The panel's design size [w, h] in px at scale 1. */
  size: [number, number];
}

export interface UiMeshData {
  /** Flat [x, y, ...] in local px, y down. */
  positions: number[];
  indices: number[];
  /** Flat [u, v, ...], Blender convention (v up), or null. */
  uvs: number[] | null;
  /** Linear RGBA per vertex, or null. */
  colors: number[] | null;
  /** [minX, minY, maxX, maxY] in local px. */
  bounds: [number, number, number, number];
  material: UiMeshMaterial;
  /** 9-slice panel (Phase 2); absent: the mesh is drawn as it is. */
  slice?: UiSliceData;
}

export interface UiElementData {
  /** The object's gs_id. */
  id: string;
  /** The Blender object name (unique within a UI file). */
  name: string;
  parent: string | null;
  type: UiElementType;
  /** False: starts hidden (Disable in Renders). */
  visible: boolean;
  /** Global draw order; higher draws on top. */
  order: number;
  transform: UiTransform;
  /**
   * Rest opacity 0..1: the object's colour alpha (Object.color[3]); absent: 1. It multiplies down the
   * hierarchy. On an image it stands in for `image.opacity` (the same alpha in Blender).
   */
  opacity?: number;
  /** Top-level elements only; already resolved (never 'auto'). */
  anchor?: UiAnchorPoint;
  stretch?: UiStretch;
  /** Component name -> values (all fields), as in scene manifests; entity fields hold element ids. */
  components: Record<string, Record<string, unknown>>;
  text?: UiTextData;
  image?: UiImageData;
  mesh?: UiMeshData;
  /** Phase 3 (§11.1): type 'model'. */
  model?: UiModelData;
}

export interface UiCanvasData {
  format: typeof UI_CANVAS_FORMAT;
  formatVersion: number;
  canvas: string;
  source: string;
  rev: number;
  exportedAt: string;
  /** Design size [W, H] in px. */
  size: [number, number];
  settings: UiCanvasSettings;
  /** Parents before children. */
  elements: UiElementData[];
  /** Asset path -> URL relative to the canvas file. */
  assets: Record<string, string>;
  /** Open, close and loop animations (Phase 2); absent: the canvas shows and hides at once. */
  animations?: UiCanvasAnimations;
  /**
   * Phase 3 (§11.1): the canvas's GLB file (as `model.glb` names it) -> content hash (a SHA-1 hex prefix,
   * as in scene manifests). The GLB is then requested as `?h=<hash>` and parsed again only when the hash
   * changes; without one, every new `rev` loads it again.
   */
  files?: Record<string, string>;
  /**
   * Phase 3 (§11.1, §11.4): game shaders of the 3D models' materials, as in scene manifests: the material's
   * name in the GLB -> its shader and values (asset paths mapped by `assets`).
   */
  materials?: Record<string, { shader: string; values: Record<string, unknown> }>;
}

/**
 * One element's animated channels (GAME-UI §10.5): one value per frame, `duration × fps + 1` values, in
 * the units of `transform` (local px, y down, radians clockwise); opacity 0..1. Only channels that change.
 */
export interface UiAnimationTrack {
  x?: number[];
  y?: number[];
  rotation?: number[];
  scaleX?: number[];
  scaleY?: number[];
  opacity?: number[];
}

/** A canvas animation, sampled per frame from the canvas scene's timeline. */
export interface UiAnimation {
  /** Seconds. */
  duration: number;
  /** Samples per second. */
  fps: number;
  /** Element id -> its channels that change. */
  tracks: Record<string, UiAnimationTrack>;
}

/** `open` plays when the canvas opens, then `loop` over and over; `close` plays before it hides. */
export interface UiCanvasAnimations {
  open?: UiAnimation;
  close?: UiAnimation;
  loop?: UiAnimation;
}
