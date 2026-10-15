// Baked lighting (lightmaps, API.md §25): loads the atlases a scene manifest names, puts them on
// the static meshes and turns off the lamps whose light is baked. DOM-free except the default
// texture loader.
//
// Scheme ("baked indirect"): a lightmap holds all light except the direct light of realtime lamps.
// On a lightmapped material it REPLACES the ambient, hemisphere (fill) and light-probe irradiance
// and the environment's diffuse light (IBL irradiance); the environment's reflections stay. That
// takes a shader patch (onBeforeCompile), which only the WebGL renderer runs: the WebGPU renderer
// builds node materials and ignores onBeforeCompile, so there the lightmap ADDS to the fill light,
// ambient light and environment diffuse (baked meshes look brighter).

import {
  LinearFilter,
  ShaderChunk,
  SRGBColorSpace,
  TextureLoader,
  type Light,
  type Material,
  type Mesh,
  type MeshStandardMaterial,
  type Object3D,
  type Texture,
} from 'three';
import { patchShader } from '../schema/shaders';
import { indexByGsId } from './build';
import type { ManifestLightmaps, SceneManifest } from './manifest';

/** Loads one image file as a texture. */
export type LightmapTextureLoader = (url: string) => Promise<Texture>;

/** The default loader: three.js TextureLoader (an `<img>`; needs a browser). */
export const loadTextureFile: LightmapTextureLoader = (url) => new TextureLoader().loadAsync(url);

const defaultWarn = (message: string): void => console.warn(`[gamestudio] ${message}`);

/**
 * Loads the atlases of `lightmaps` (files are relative to `manifestUrl`) with the texture settings
 * lightmaps need. An atlas that fails to load is `null` (with a warning): its meshes are then lit
 * like moving objects. Never rejects. No lightmaps → `[]`.
 */
export async function loadLightmapTextures(
  lightmaps: ManifestLightmaps | null | undefined,
  manifestUrl: string,
  load: LightmapTextureLoader = loadTextureFile,
  warn: (message: string) => void = defaultWarn,
): Promise<Array<Texture | null>> {
  if (!lightmaps?.atlases.length) return [];
  return Promise.all(
    lightmaps.atlases.map(async (atlas, i) => {
      try {
        const base = new URL(manifestUrl, (globalThis as { location?: { href: string } }).location?.href);
        const texture = await load(new URL(atlas.file, base).href);
        texture.name = `gs_lightmap_${i}`;
        texture.colorSpace = SRGBColorSpace;
        texture.flipY = false; // glTF UV convention
        texture.minFilter = LinearFilter;
        texture.magFilter = LinearFilter;
        texture.generateMipmaps = false;
        return texture;
      } catch (error) {
        // TextureLoader rejects with the <img> error event, which says nothing more.
        const reason = error instanceof Error ? error.message : 'missing or not an image';
        warn(`Lightmap ${atlas.file} did not load (${reason}); its meshes are lit like moving objects`);
        return null;
      }
    }),
  );
}

/** What `applyLightmaps` did. */
export interface LightmapSummary {
  /** Meshes that got a lightmap. */
  meshes: number;
  /** Lightmapped materials made (clones, one per material, atlas and UV channel). */
  materials: number;
  /** Baked lamps turned off. */
  lights: number;
  /** Problems found (each also passed to `warn`). */
  warnings: string[];
}

/** Materials that take a lightmap and the shader patch. */
type LitMaterial = MeshStandardMaterial;

function isLit(material: Material): material is LitMaterial {
  const m = material as Material & { isMeshStandardMaterial?: boolean; isMeshLambertMaterial?: boolean; isMeshPhongMaterial?: boolean };
  return !!(m.isMeshStandardMaterial || m.isMeshLambertMaterial || m.isMeshPhongMaterial);
}

const MAPS_INCLUDE = '#include <lights_fragment_maps>';
const LIGHTMAP_ADD = 'irradiance += lightMapIrradiance;';
const IBL_DIFFUSE = /^[ \t]*iblIrradiance \+= getIBLIrradiance\( geometryNormal \);[ \t]*$/m;
let patchedChunk: string | null | undefined;

/**
 * `lights_fragment_maps` with the lightmap replacing the irradiance gathered so far (ambient,
 * hemisphere, light probes) and without the environment's diffuse term; the specular IBL part is
 * kept. `null` if the three.js chunk no longer has the expected lines.
 */
export function lightmapShaderChunk(): string | null {
  if (patchedChunk !== undefined) return patchedChunk;
  const chunk = ShaderChunk.lights_fragment_maps;
  patchedChunk =
    chunk.includes(LIGHTMAP_ADD) && IBL_DIFFUSE.test(chunk)
      ? chunk
          .replace(LIGHTMAP_ADD, 'irradiance = lightMapIrradiance; // gs_lightmap: replaces ambient, hemisphere and probe light')
          .replace(IBL_DIFFUSE, '\t\t\t// gs_lightmap: no environment diffuse (it is in the lightmap)')
      : null;
  return patchedChunk;
}

/** Makes a material's shader take its indirect diffuse light from the lightmap only (WebGL). */
function patchLightmap(material: LitMaterial, chunk: string): void {
  patchShader(material, 'gs_lightmap', (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(MAPS_INCLUDE, () => chunk);
  });
}

/** The meshes an entity draws itself: its object and descendants that are not other entities. */
function ownMeshes(root: Object3D): Mesh[] {
  const meshes: Mesh[] = [];
  const visit = (object: Object3D): void => {
    if ((object as Mesh).isMesh) meshes.push(object as Mesh);
    for (const child of object.children) {
      if (typeof child.userData?.gs_id !== 'string') visit(child);
    }
  };
  visit(root);
  return meshes;
}

/**
 * Puts the lightmaps of `manifest.lightmaps` on the static meshes under `root` and turns off the
 * baked lamps (`visible = false`). `textures` are the atlases from `loadLightmapTextures` (by
 * index; `null` = not loaded, skipped). Materials are cloned per (material, atlas, UV channel), as
 * glTF materials are shared with moving meshes, which stay as they are. Lightmapped materials get
 * `userData.gs_lightmap = true`. Only lit materials (standard, physical, Lambert, Phong) take a
 * lightmap. Entities without a node under `root` are skipped.
 */
export function applyLightmaps(
  root: Object3D,
  manifest: Pick<SceneManifest, 'lightmaps'>,
  textures: ReadonlyArray<Texture | null | undefined>,
  warn: (message: string) => void = defaultWarn,
): LightmapSummary {
  const summary: LightmapSummary = { meshes: 0, materials: 0, lights: 0, warnings: [] };
  const lightmaps = manifest.lightmaps;
  if (!lightmaps) return summary;
  const report = (message: string): void => {
    summary.warnings.push(message);
    warn(message);
  };
  const nodes = indexByGsId(root);
  const chunk = lightmapShaderChunk();
  if (!chunk && Object.keys(lightmaps.objects).length) {
    report('This three.js version has another lights_fragment_maps shader chunk: lightmaps add to the fill light instead of replacing it');
  }

  // One texture per (atlas, channel): clones share the image (and the GPU texture).
  const maps = new Map<string, Texture>();
  const mapFor = (atlas: number, channel: number): Texture => {
    const key = `${atlas}:${channel}`;
    let map = maps.get(key);
    if (!map) {
      map = textures[atlas]!.clone();
      map.channel = channel;
      maps.set(key, map);
    }
    return map;
  };
  const clones = new Map<Material, Map<string, Material>>();
  const unsupported = new Set<string>();

  for (const [id, entry] of Object.entries(lightmaps.objects)) {
    const node = nodes.get(id);
    const atlas = lightmaps.atlases[entry.atlas];
    if (!node || !atlas || !textures[entry.atlas]) continue; // Not in this scene graph, or the atlas did not load.
    const attribute = entry.uv === 0 ? 'uv' : `uv${entry.uv}`;
    const key = `${entry.atlas}:${entry.uv}`;
    let missingUv = false;
    for (const mesh of ownMeshes(node)) {
      if (!mesh.geometry?.getAttribute(attribute)) {
        missingUv = true;
        continue;
      }
      let applied = false;
      const swap = (material: Material): Material => {
        if (!isLit(material)) {
          if (!unsupported.has(material.type)) {
            unsupported.add(material.type);
            report(`${material.type} takes no lightmap (it is not lit); meshes with it keep their look (first: ${node.name || id})`);
          }
          return material;
        }
        let byKey = clones.get(material);
        if (!byKey) clones.set(material, (byKey = new Map()));
        let clone = byKey.get(key);
        if (!clone) {
          const lit = material.clone() as LitMaterial;
          lit.lightMap = mapFor(entry.atlas, entry.uv);
          lit.lightMapIntensity = Math.PI * atlas.scale; // three.js divides irradiance by PI (Lambert BRDF)
          lit.userData.gs_lightmap = true;
          if (chunk) patchLightmap(lit, chunk);
          byKey.set(key, (clone = lit));
          summary.materials++;
        }
        applied = true;
        return clone;
      };
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map(swap) : swap(mesh.material);
      if (applied) summary.meshes++;
    }
    if (missingUv) report(`${node.name || id} has no lightmap UVs ("${attribute}"), so it gets no lightmap; bake and export again`);
  }

  for (const id of lightmaps.bakedLights) {
    const node = nodes.get(id);
    if (!node) continue; // AREA lamps are not exported.
    const visit = (object: Object3D): void => {
      if ((object as Light).isLight && object.visible) {
        object.visible = false;
        summary.lights++;
      }
      for (const child of object.children) {
        const childId = child.userData?.gs_id;
        if (typeof childId !== 'string' || childId === id) visit(child);
      }
    };
    visit(node);
  }
  return summary;
}
