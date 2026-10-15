// The Scene view's camera (`view.camera`, SPEC §5.4): Blender's viewport camera as matrices. The
// view matrix places it; the projection matrix is used exactly as given (Blender's window matrix,
// perspective or orthographic), never rebuilt from a field of view. `near`, `far`, `fov`/`aspect`
// (or the orthographic extents) are filled in from it for code that reads them (shadow fitting).

import {
  Matrix4,
  OrthographicCamera,
  PerspectiveCamera,
  WebGPUCoordinateSystem,
  type Camera,
} from 'three';

/** The payload of `view.camera`: column-major matrices (three.js `Matrix4.fromArray` order), in the game's Y-up space. */
export interface ViewCamera {
  /** World → view (three.js `matrixWorldInverse`). */
  view: number[];
  /** View → clip, OpenGL conventions (Blender's `region_data.window_matrix`). */
  projection: number[];
  ortho: boolean;
  near?: number;
  far?: number;
}

const matrix16 = (value: unknown): value is number[] =>
  Array.isArray(value) && value.length === 16 && value.every((n) => typeof n === 'number' && Number.isFinite(n));

/** A `view.camera` payload that can be used, or null. */
export function parseViewCamera(value: unknown): ViewCamera | null {
  const v = value as Partial<ViewCamera> | null;
  if (!v || typeof v !== 'object' || !matrix16(v.view) || !matrix16(v.projection)) return null;
  const finite = (n: unknown): number | undefined => (typeof n === 'number' && Number.isFinite(n) ? n : undefined);
  return { view: v.view, projection: v.projection, ortho: v.ortho === true, near: finite(v.near), far: finite(v.far) };
}

/** OpenGL clip depth (-1..1) → WebGPU (0..1): z' = (z + w) / 2. */
const DEPTH_01 = new Matrix4().set(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0.5, 0.5, 0, 0, 0, 1);

/** The given projection, for the renderer's clip-space depth convention. */
function applyProjection(camera: Camera & { source: Matrix4 }): void {
  camera.projectionMatrix.copy(camera.source);
  if (camera.coordinateSystem === WebGPUCoordinateSystem) camera.projectionMatrix.premultiply(DEPTH_01);
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}

/** A perspective camera whose projection is the editor's matrix; `updateProjectionMatrix()` applies it again. */
export class EditorPerspectiveCamera extends PerspectiveCamera {
  readonly source = new Matrix4();
  override updateProjectionMatrix(): void {
    // The base constructor calls this before `source` exists.
    if (!this.source) super.updateProjectionMatrix();
    else applyProjection(this);
  }
}

/** An orthographic camera whose projection is the editor's matrix; `updateProjectionMatrix()` applies it again. */
export class EditorOrthographicCamera extends OrthographicCamera {
  readonly source = new Matrix4();
  override updateProjectionMatrix(): void {
    if (!this.source) super.updateProjectionMatrix();
    else applyProjection(this);
  }
}

/** The Scene view's two cameras, one of which is in use. */
export class EditorCamera {
  readonly perspective = new EditorPerspectiveCamera();
  readonly orthographic = new EditorOrthographicCamera();
  /** The camera of the last `view.camera`; null before the first. */
  current: EditorPerspectiveCamera | EditorOrthographicCamera | null = null;
  private readonly world = new Matrix4();

  constructor() {
    this.perspective.name = 'gs_editor_camera';
    this.orthographic.name = 'gs_editor_camera_ortho';
  }

  /** Applies a `view.camera` payload. */
  set(view: ViewCamera): Camera {
    const camera = view.ortho ? this.orthographic : this.perspective;
    const p = camera.source.fromArray(view.projection).elements;
    // Where it stands: the inverse of the view matrix (a view matrix has no scale).
    this.world.fromArray(view.view).invert().decompose(camera.position, camera.quaternion, camera.scale);
    camera.scale.set(1, 1, 1);
    camera.updateMatrixWorld(true);
    // What reads the camera's fields (shadow fitting, culling distances) gets the matrix's values.
    if (camera instanceof EditorOrthographicCamera) {
      // [0] = 2/(r-l), [12] = -(r+l)/(r-l); [5] = 2/(t-b), [13] = -(t+b)/(t-b); [10] = -2/(f-n), [14] = -(f+n)/(f-n)
      camera.left = (-1 - p[12]!) / p[0]!;
      camera.right = (1 - p[12]!) / p[0]!;
      camera.bottom = (-1 - p[13]!) / p[5]!;
      camera.top = (1 - p[13]!) / p[5]!;
      camera.near = view.near ?? (p[14]! + 1) / p[10]!;
      camera.far = view.far ?? (p[14]! - 1) / p[10]!;
      camera.zoom = 1;
    } else {
      // [5] = 1/tan(fov/2), [0] = [5]/aspect; [10] = -(f+n)/(f-n), [14] = -2fn/(f-n)
      camera.fov = (2 * Math.atan(1 / p[5]!) * 180) / Math.PI;
      camera.aspect = p[5]! / p[0]!;
      camera.near = view.near ?? p[14]! / (p[10]! - 1);
      camera.far = view.far ?? p[14]! / (p[10]! + 1);
      camera.zoom = 1;
    }
    camera.updateProjectionMatrix(); // the given matrix, as is
    this.current = camera;
    return camera;
  }
}
