// Public types for @gamestudio/devserver (the implementation is plain ESM JS).
import type { Plugin } from 'vite';

// ---------------------------------------------------------------------------
// Vite plugin

export interface GamestudioPluginOptions {
  /** Component definitions folder, relative to the Vite root. Default 'src/components'. */
  componentsDir?: string;
  /** Game shader definitions folder (`defineShader`), relative to the Vite root. Default 'src/shaders'. */
  shadersDir?: string;
  /** Systems folder, relative to the Vite root. Default 'src/systems'. */
  systemsDir?: string;
}

/** `gamestudio.json` (docs/SPEC.md §1). */
export interface ProjectConfig {
  name: string;
  formatVersion: number;
  startScene: string;
  link: { editorPort: number; gamePort: number };
  /** Screen effects for every scene (bloom, vignette, grading), or null. */
  post: Record<string, unknown> | null;
  /** How builds are compressed (`"compression"`, docs/WORKFLOWS.md "Smaller builds"). */
  compression: CompressionConfig;
}

/** gamestudio.json `"compression"`: geometry is compressed by `npm run build`, textures by the export. */
export interface CompressionConfig {
  /** "meshopt" (default): EXT_meshopt_compression on the build's scene GLBs, lossless. */
  geometry: 'meshopt' | 'none';
  /** "webp": the Blender export writes WebP textures (EXT_texture_webp). Default "none". */
  textures: 'webp' | 'none';
}

/** What `compressGlb` / `compressExports` did to one GLB. */
export interface CompressResult {
  /** File name in the export folder. */
  file: string;
  /** Bytes before and after. */
  before: number;
  after: number;
  /** Bytes of the buffer views that were candidates (vertex data, indices, animation), before and after. */
  geometryBefore: number;
  geometryAfter: number;
  /** Buffer views compressed. */
  views: number;
  /** Bytes of embedded images, and their count per format ({ png: 2, webp: 1 }). */
  textures: number;
  images: Record<string, number>;
  /** Why the file was left as it was. */
  skipped?: string;
}

/** A level file without an up-to-date export (`checkSceneExports`). */
export interface SceneExportProblem {
  /** Project-relative .blend path, e.g. "scenes/level2.blend". */
  file: string;
  /** The export name it should have. */
  scene: string;
  problem: 'missing' | 'stale';
}

export interface RegistryError {
  /** Project-relative file that failed to load. */
  file: string;
  message: string;
}

export interface RegistryResult {
  /** Contents written to `.gamestudio/components.json`. */
  registry: RegistryJSON;
  errors: RegistryError[];
}

/** One field of a component or shader in `components.json`. */
export interface RegistryFieldJSON {
  name: string;
  type: string;
  default: unknown;
  [key: string]: unknown;
}

/** Minimal shape of `.gamestudio/components.json` (docs/SPEC.md §3). */
export interface RegistryJSON {
  format: 'gamestudio.registry';
  formatVersion: 1;
  generatedAt: string;
  components: Array<{
    name: string;
    description?: string;
    category?: string;
    builtin: boolean;
    source?: string;
    fields: RegistryFieldJSON[];
  }>;
  /**
   * Game shaders: the engine's shader library (`builtin: true`) and `defineShader` in src/shaders/
   * (with `source`; a project file that defines a library name overrides it). Always written by this
   * dev server; readers treat a missing key as [].
   */
  shaders: Array<{
    name: string;
    description?: string;
    category?: string;
    builtin: boolean;
    source?: string;
    fields: RegistryFieldJSON[];
  }>;
}

export interface GamestudioPluginAPI {
  /** Regenerates and writes the component and shader registry (needs a dev server, e.g. middleware mode). */
  generateRegistry(): Promise<RegistryResult>;
  /** The running hub (dev server only). */
  readonly hub: Hub | null;
}

export function gamestudio(options?: GamestudioPluginOptions): Plugin & { api: GamestudioPluginAPI };
export default gamestudio;

// ---------------------------------------------------------------------------
// Live-link hub (docs/SPEC.md §5)

export type PeerRole = 'editor' | 'game' | 'tool';

export interface PeerInfo {
  peer: string;
  role: PeerRole;
  client: string;
}

export interface HubMessage {
  t: string;
  [key: string]: unknown;
}

export interface HubLogger {
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
}

export interface HubOptions {
  /** TCP NDJSON port for editors and tools. Default 7710; 0 = ephemeral. */
  editorPort?: number;
  /** WebSocket port for games. Default 7711; 0 = ephemeral. */
  gamePort?: number;
  /** Shared secret every client must send in `hello`. */
  token: string;
  /** Bind address. Default '127.0.0.1'. */
  host?: string;
  logger?: HubLogger;
  /** Rewrites routed messages (after `from` is stamped). Return null to drop. */
  transform?: (message: HubMessage, from: PeerInfo) => HubMessage | null | undefined;
}

export interface Hub {
  readonly editorPort: number;
  readonly gamePort: number;
  readonly token: string;
  /** Authenticated peers. */
  peers(): PeerInfo[];
  /** Stores the registry and sends `{ t: 'registry' }` to every editor (and to editors that connect later). */
  setRegistry(registry: unknown): void;
  /** Sends a hub message to one peer. Returns false if the peer is unknown. */
  send(peer: string, message: HubMessage): boolean;
  /** Sends a hub message to every peer (optionally only some roles). */
  broadcast(message: HubMessage, roles?: PeerRole[]): void;
  on(event: 'message', listener: (message: HubMessage, from: PeerInfo) => void): () => void;
  on(event: 'connect' | 'disconnect', listener: (peer: PeerInfo) => void): () => void;
  close(): Promise<void>;
}

export const PROTOCOL: 1;
/** The engine's third-party imports, pre-bundled by the plugin (`optimizeDeps.include`). */
export const ENGINE_DEPS: readonly string[];
/** The third-party notices a build writes at the root of its output (`build.license`, unless the project sets it). */
export const NOTICES_FILE: 'THIRD-PARTY-NOTICES.md';
/** Level files (`scenes/*.blend`) whose export is missing or older than the file. */
export function checkSceneExports(root: string): SceneExportProblem[];
export function createHub(options: HubOptions): Promise<Hub>;
