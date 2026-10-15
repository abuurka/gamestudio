// After FX Lab "interior-mapping".
// Interior mapping (van Dongen 2008) on the Blender material: a grid in the material's UV says
// where the windows are (or a mask texture does), and behind each pane the view ray is intersected
// with a box room (walls, floor, ceiling) analytically, so a flat facade shows rooms with correct
// parallax. Rooms get a palette, a picture, a door, floor planks, a lamp, sometimes a TV, a piece of
// furniture or a person, curtains or blinds, and switch their lights on and off over time. The wall
// between the windows keeps the Blender material; panes reflect the scene like glass.
// Windows go only on vertical faces that face away from the object's origin (outside walls).

import type { MeshStandardMaterial } from 'three';
import { patchShader, type ShaderApply } from '../../../schema/shaders';
import type { fields } from './meta';

const VERTEX_HEAD = /* glsl */ `
varying vec2 vGsImUv;
varying vec3 vGsImWorld;
varying vec3 vGsImObjP;
varying vec3 vGsImObjN;
`;

const VERTEX_BODY = /* glsl */ `
{
  vec4 gsW = vec4(transformed, 1.0);
#ifdef USE_BATCHING
  gsW = batchingMatrix * gsW;
#endif
#ifdef USE_INSTANCING
  gsW = instanceMatrix * gsW;
#endif
  vGsImWorld = (modelMatrix * gsW).xyz;
  vGsImUv = uv;
  vGsImObjP = position;
  vGsImObjN = normal;
}
`;

// gsImHash11, gsImHash12 and gsImHash13 are David Hoskins' "Hash without Sine"
// (https://www.shadertoy.com/view/4djSRW, MIT; THIRD-PARTY-NOTICES.md).
const FRAGMENT_HEAD = /* glsl */ `
uniform float uGsTime;
uniform vec2 uImSpacing;
uniform vec2 uImWindow;
uniform float uImSill;
uniform vec2 uImOffset;
uniform float uImFloors;
uniform float uImDepth;
uniform float uImLit;
uniform float uImBrightness;
uniform vec4 uImAccent;
uniform sampler2D uImMask;
varying vec2 vGsImUv;
varying vec3 vGsImWorld;
varying vec3 vGsImObjP;
varying vec3 vGsImObjN;
float gsImGlass = 0.0;
float gsImFrame = 0.0;
vec3 gsImRoom = vec3(0.0);
vec3 gsImSpill = vec3(0.0);

float gsImHash11(float p) { p = fract(p * 0.1031); p *= p + 33.33; p *= p + p; return fract(p); }
float gsImHash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float gsImHash13(vec3 p3) { p3 = fract(p3 * 0.1031); p3 += dot(p3, p3.zyx + 31.32); return fract((p3.x + p3.y) * p3.z); }
float gsImNoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(gsImHash12(i), gsImHash12(i + vec2(1.0, 0.0)), f.x), mix(gsImHash12(i + vec2(0.0, 1.0)), gsImHash12(i + vec2(1.0, 1.0)), f.x), f.y);
}
vec3 gsImWallPalette(float h) {
  if (h < 0.25) return vec3(0.62, 0.52, 0.38);
  if (h < 0.5) return vec3(0.32, 0.47, 0.45);
  if (h < 0.75) return vec3(0.55, 0.56, 0.42);
  return vec3(0.42, 0.40, 0.52);
}
`;

// After color_fragment: where the windows are, and the room seen through each.
const FRAGMENT_ROOMS = /* glsl */ `
{
  // Derivatives first (uniform control flow): view ray, facade normal and UV per metre, world space.
  vec3 Vw = normalize(cameraPosition - vGsImWorld);
  vec3 q0 = dFdx(vGsImWorld), q1 = dFdy(vGsImWorld);
  vec3 Nw = normalize(cross(q0, q1));
  if (dot(Nw, Vw) < 0.0) Nw = -Nw;
  vec2 st0 = dFdx(vGsImUv), st1 = dFdy(vGsImUv);
  vec3 q1perp = cross(q1, Nw), q0perp = cross(Nw, q0);
  float det = dot(q0, q1perp);
  float inv = abs(det) > 1e-14 ? 1.0 / det : 0.0;
  vec3 gu = (q1perp * st0.x + q0perp * st1.x) * inv;
  vec3 gv = (q1perp * st0.y + q0perp * st1.y) * inv;
  vec2 uvAxis = max(vec2(length(gu), length(gv)), vec2(1e-6)); // UV units per metre along u and v
  float uvPerM = sqrt(uvAxis.x * uvAxis.y);
  // The grid's v runs up the wall, as in Blender's UV editor. glTF stores v flipped (1 - v), so
  // when v runs down the wall in world space, flip it back.
  bool flipV = gv.y < 0.0;
  vec2 uvG = vec2(vGsImUv.x, flipV ? 1.0 - vGsImUv.y : vGsImUv.y);
  if (flipV) gv = -gv;

  vec2 sp = max(uImSpacing, vec2(0.05));
  vec2 f = (uvG - uImOffset) / sp;
  vec2 cellId = floor(f);
  vec2 cu = f - cellId;
#ifdef GS_IM_SHOPFRONT
  bool lobby = cellId.y < 0.5;
#else
  bool lobby = false;
#endif
  vec2 wMin = lobby ? vec2(0.04) : vec2(0.5 - 0.5 * uImWindow.x / sp.x, uImSill / sp.y);
  vec2 wMax = lobby ? vec2(0.96, 0.9) : vec2(0.5 + 0.5 * uImWindow.x / sp.x, (uImSill + uImWindow.y) / sp.y);
  wMin = clamp(wMin, vec2(0.01), vec2(0.98));
  wMax = clamp(wMax, wMin + 0.01, vec2(0.99));
  vec2 wu = (cu - wMin) / (wMax - wMin);
  vec2 aa = fwidth(f) / (wMax - wMin) * 0.75; // from the continuous grid coordinate: no spikes at cell edges
#ifdef GS_IM_MASK
  float inWin = smoothstep(0.4, 0.6, texture2D(uImMask, vGsImUv).r);
  wu = cu;
  float frame = 0.0;
#else
  float inWin = smoothstep(-aa.x, aa.x, wu.x) * smoothstep(-aa.x, aa.x, 1.0 - wu.x) * smoothstep(-aa.y, aa.y, wu.y) * smoothstep(-aa.y, aa.y, 1.0 - wu.y);
  // Window frames and a mullion, sized in metres.
  vec2 winM = (wMax - wMin) * sp / uvAxis;
  vec2 edgeM = min(wu, 1.0 - wu) * winM;
  float frame = 1.0 - smoothstep(0.03, 0.05, min(edgeM.x, edgeM.y));
  float mullion = 1.0 - smoothstep(0.015, 0.03, abs(wu.x - 0.5) * winM.x);
  if (lobby) mullion = 1.0 - smoothstep(0.015, 0.03, abs(fract(wu.x * 3.0 + 0.5) - 0.5) * winM.x / 3.0);
  frame = max(frame, mullion);
#endif

  vec3 nObj = normalize(vGsImObjN);
  bool facade = abs(nObj.y) < 0.5 && dot(nObj.xz, vGsImObjP.xz) >= -0.01;
  bool row = cellId.y >= 0.0 && (uImFloors < 0.5 || cellId.y < uImFloors);
#ifdef GS_IM_GROUND_WALL
  row = row && cellId.y >= 1.0;
#endif
  if (facade && row) {
    // Which side of the building (box faces often share UVs): constant over a flat wall.
    vec3 an = abs(nObj);
    float faceId = an.x > an.z ? (nObj.x > 0.0 ? 1.0 : 2.0) : (nObj.z > 0.0 ? 3.0 : 4.0);
    // Room seeds.
    vec3 seed = vec3(cellId, faceId);
    float r0 = gsImHash13(seed);
    float r1 = gsImHash13(seed + 17.3);
    float r2 = gsImHash13(seed + 41.9);
    float r3 = gsImHash13(seed + 73.1);
    // Lights switch on and off now and then, each room on its own schedule.
    float epoch = floor(uGsTime / 9.0 + r1 * 9.0);
    float flip = step(gsImHash12(vec2(r0 * 91.0, epoch)), 0.12);
    float lit = abs(step(r0, uImLit) - flip);
    bool tvRoom = r2 > 0.78 && !lobby;
    if (lobby) lit = 1.0;

    // Ray in the room box, normalised to x, y in [-1, 1] and z in [0, 1] (depth into the room).
    vec3 rdW = -Vw;
    vec3 rd = vec3(dot(gu, rdW) / (sp.x * 0.5), dot(gv, rdW) / (sp.y * 0.5), dot(rdW, -Nw) * uvPerM / max(uImDepth, 0.1));
    vec3 o = vec3(cu * 2.0 - 1.0, 0.0);
    vec3 s = vec3(rd.x >= 0.0 ? 1.0 : -1.0, rd.y >= 0.0 ? 1.0 : -1.0, 1.0);
    vec3 tv = (s - o) * s / max(abs(rd), vec3(1e-4));
    float t = min(tv.x, min(tv.y, tv.z));
    vec3 hit = o + rd * t;

    vec3 albedo;
    if (t == tv.z) {
      albedo = gsImWallPalette(r1);
      // A framed picture on the back wall.
      vec2 pic = abs(hit.xy - vec2(r3 * 0.8 - 0.4, 0.15)) - vec2(0.22, 0.16);
      if (max(pic.x, pic.y) < 0.0) albedo = mix(vec3(0.5, 0.25, 0.15), vec3(0.15, 0.3, 0.5), r3) * (0.6 + 0.5 * gsImNoise(hit.xy * 6.0 + r3 * 40.0));
      albedo *= 0.9 + 0.1 * step(0.5, fract(hit.x * 6.0 + r2));
    } else if (t == tv.x) {
      albedo = gsImWallPalette(r1) * 0.92;
      // Door on one side wall.
      if (abs(hit.z - 0.6) < 0.18 && hit.y < 0.35 && s.x * (r3 - 0.5) > 0.0) albedo = vec3(0.3, 0.2, 0.12);
    } else if (s.y < 0.0) {
      float plank = fract(hit.x * 3.0 + floor(hit.z * 8.0) * 0.37);
      albedo = mix(vec3(0.24, 0.14, 0.07), vec3(0.16, 0.1, 0.06), r2) * (0.75 + 0.25 * smoothstep(0.0, 0.04, plank) * smoothstep(1.0, 0.96, plank));
      albedo = mix(albedo, vec3(0.4, 0.12, 0.1) * (r3 + 0.3), step(length(hit.xz - vec2(0.0, 0.55)), 0.45) * step(0.5, r3) * 0.8);
    } else {
      albedo = vec3(0.7, 0.68, 0.64);
    }

    // Room lighting: a ceiling lamp, falloff measured in metres.
    vec3 ambient = ambientLightColor * 0.25 + 0.015;
    vec3 lampP = vec3(r3 * 0.6 - 0.3, 0.92, 0.5);
    vec3 roomM = vec3(sp * 0.5 / uvAxis, uImDepth / uvPerM); // half width, half height and depth in metres
    vec3 dm = (hit - lampP) * roomM;
    vec3 lampCol = r1 < 0.6 ? vec3(1.0, 0.72, 0.42) : (r1 < 0.85 ? vec3(0.8, 0.92, 1.0) : mix(vec3(1.0, 0.8, 0.6), uImAccent.rgb, 0.6));
    float lampGlow = 2.6 / (1.0 + dot(dm, dm) * 0.55);
    if (t == tv.y && s.y > 0.0) lampGlow += 4.0 * exp(-dot(dm.xz, dm.xz) * 12.0);
    vec3 light = lampCol * lampGlow * lit + ambient;

    // Flickering TV glow: the room light is off and the screen lights everything blue and cold.
    float tvOn = 0.0;
    vec3 tvCol = mix(vec3(0.35, 0.55, 1.0), uImAccent.rgb, step(0.9, r2) * 0.8);
    if (tvRoom) {
      float frameNo = floor(uGsTime * 9.0 + r0 * 50.0);
      tvOn = 0.45 + 0.55 * gsImHash12(vec2(frameNo, r1 * 13.0));
      tvOn *= 0.8 + 0.2 * step(0.08, gsImHash12(vec2(floor(uGsTime * 1.3 + r2 * 9.0), r0)));
      light *= 1.0 - 0.85 * (1.0 - step(0.5, lit));
      vec3 tvP = vec3(r3 * 0.8 - 0.4, -0.45, 0.98);
      vec3 dt = (hit - tvP) * roomM;
      light += tvCol * tvOn * 2.0 / (1.0 + dot(dt, dt) * 2.5);
      if (t == tv.z) {
        vec2 sc = abs(hit.xy - tvP.xy) - vec2(0.24, 0.15);
        if (max(sc.x, sc.y) < 0.0) { albedo = vec3(1.0); light = tvCol * tvOn * 3.5 * (0.85 + 0.15 * sin(hit.y * 140.0)); }
      }
    }
    vec3 room = albedo * light;

    // A card halfway in: a sofa, a standing person or a desk, seen in parallax.
    float zc = 0.45 + 0.25 * r2;
    float tc = zc / max(rd.z, 1e-4);
    if (tc < t) {
      vec2 pc = o.xy + rd.xy * tc;
      float shape = 0.0;
      if (r3 < 0.45) {
        float sx = r1 * 0.6 - 0.3;
        shape = max(step(abs(pc.x - sx), 0.55) * step(pc.y, -0.55), step(abs(pc.x - sx), 0.5) * step(pc.y, -0.3) * step(0.0, pc.y + 0.75));
        shape = max(shape, step(abs(abs(pc.x - sx) - 0.5), 0.07) * step(pc.y, -0.4));
      } else if (r3 < 0.7 && lit > 0.5) {
        float px = sin(uGsTime * 0.25 + r0 * 40.0) * 0.55;
        vec2 q = pc - vec2(px, -0.35);
        float body = length(vec2(q.x * 2.4, max(abs(q.y) - 0.35, 0.0))) - 0.3;
        float head = length(q - vec2(0.0, 0.58)) - 0.12;
        shape = step(min(body, head), 0.0);
      } else {
        float tx = r1 * 0.8 - 0.4;
        shape = step(abs(pc.x - tx), 0.35) * step(abs(pc.y + 0.5), 0.04) + step(abs(abs(pc.x - tx) - 0.3), 0.03) * step(pc.y, -0.5);
      }
      if (shape > 0.0) room = (lampCol * lit * 0.12 + tvCol * tvOn * 0.25 + ambient * 0.2) * 0.35;
    }

    // Curtains and blinds in the window plane, glowing when the room behind is lit.
    vec3 roomLight = lampCol * lit * 1.4 + tvCol * tvOn * 0.6;
    if (r1 < 0.55) {
      float open = 0.12 + 0.3 * r3;
      float side = min(wu.x, 1.0 - wu.x);
      float folds = 0.6 + 0.4 * sin(wu.x * 55.0 + sin(wu.y * 6.0 + r0 * 9.0) * 1.5);
      float cover = 1.0 - smoothstep(open - 0.02, open, side);
      vec3 cloth = mix(vec3(0.75, 0.62, 0.45), vec3(0.6, 0.2, 0.15), step(0.5, gsImHash11(r0 * 31.0))) * folds;
      room = mix(room, cloth * (roomLight * 0.55 + ambient * 1.2), cover * 0.92);
    } else if (r1 > 0.8 && !lobby) {
      float down = 0.3 + 0.6 * r3;
      float slat = smoothstep(0.35, 0.5, fract(wu.y * 22.0));
      float cover = step(1.0 - down, wu.y) * slat;
      room = mix(room, vec3(0.75, 0.73, 0.68) * (roomLight * 0.45 + ambient * 1.2), cover);
    }
    gsImRoom = room * uImBrightness;
    gsImGlass = inWin * (1.0 - frame);
    gsImFrame = inWin * frame;
    // Light from lit rooms spills onto the sill and the wall below.
    float below = step(wMin.x, cu.x) * step(cu.x, wMax.x) * step(cu.y, wMin.y) * (1.0 - smoothstep(0.0, wMin.y, wMin.y - cu.y));
    gsImSpill = roomLight * uImBrightness * 0.06 * below * (1.0 - inWin);
  }
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.0), gsImGlass);
  diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.06, 0.063, 0.068), gsImFrame);
}
`;

// After metalnessmap_fragment: glass is a smooth dielectric, frames are painted metal.
const FRAGMENT_SURFACE = /* glsl */ `
roughnessFactor = mix(roughnessFactor, 0.03, gsImGlass);
roughnessFactor = mix(roughnessFactor, 0.45, gsImFrame);
metalnessFactor = mix(metalnessFactor, 0.0, gsImGlass);
metalnessFactor = mix(metalnessFactor, 0.5, gsImFrame);
`;

// After emissivemap_fragment: the room shows through the glass (less at grazing angles, where the
// pane mirrors the scene), and spill light warms the wall below.
const FRAGMENT_EMIT = /* glsl */ `
{
  vec3 gsV = isOrthographic ? vec3(0.0, 0.0, 1.0) : normalize(vViewPosition);
  float ndv = saturate(dot(normal, gsV));
  float F = 0.04 + 0.96 * pow(1.0 - ndv, 5.0);
  totalEmissiveRadiance += gsImRoom * gsImGlass * (1.0 - F);
  totalEmissiveRadiance += gsImSpill * diffuseColor.rgb;
}
`;

export const apply: ShaderApply<typeof fields> = (material, values, ctx) => {
  if (!(material as MeshStandardMaterial).isMeshStandardMaterial) {
    throw new Error(`InteriorMapping needs a lit (Principled BSDF) material, got ${material.type}`);
  }
  // The mask and the ground floor style change the GLSL: they are in the key.
  const masked = Boolean(values.mask);
  const ground = values.groundFloor;
  const defines = [
    masked ? '#define GS_IM_MASK' : '',
    ground === 'shopfront' ? '#define GS_IM_SHOPFRONT' : '',
    ground === 'wall' ? '#define GS_IM_GROUND_WALL' : '',
  ].join('\n');
  const u = ctx.uniforms;
  const mask = u.mask;
  patchShader(material, `gs_interior_mapping${masked ? '_mask' : ''}_${ground}`, (shader) => {
    Object.assign(shader.uniforms, {
      uGsTime: ctx.time,
      uImSpacing: u.spacing,
      uImWindow: u.windowSize,
      uImSill: u.sill,
      uImOffset: u.offset,
      uImFloors: u.floors,
      uImDepth: u.roomDepth,
      uImLit: u.lit,
      uImBrightness: u.brightness,
      uImAccent: u.accent,
      uImMask: { get value() { return mask.value ?? ctx.white; } },
    });
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `#include <common>\n${VERTEX_HEAD}`)
      .replace('#include <project_vertex>', `#include <project_vertex>\n${VERTEX_BODY}`);
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${defines}\n${FRAGMENT_HEAD}`)
      .replace('#include <color_fragment>', `#include <color_fragment>\n${FRAGMENT_ROOMS}`)
      .replace('#include <metalnessmap_fragment>', `#include <metalnessmap_fragment>\n${FRAGMENT_SURFACE}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${FRAGMENT_EMIT}`);
  });
};
