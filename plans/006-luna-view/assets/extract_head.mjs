// Extract a point-cloud head from facecap.glb (three.js examples model).
//
// Reads the meshopt-compressed GLB, decodes every mesh, applies node
// transforms, area-weighted-samples N points across all surfaces, and bakes
// the `jawOpen` blendshape delta for each sampled point. Output `head.bin`:
//   Float32 LE: [N*3 base positions][N*3 jawOpen deltas][N brightness]
// normalized to height ~1.7, centered, front of the face toward -z.
// Brightness = local mesh detail (small triangles = eyes/nose/lips on a scan),
// so features glow brighter than the cranium in the dot cloud.
//
// Usage: node extract_head.mjs <facecap.glb> <head.bin> [N]
// Requires: npm install meshoptimizer  (run from a scratch dir; pass path via NODE_PATH)

import { readFileSync, writeFileSync } from "node:fs";
import { MeshoptDecoder } from "meshoptimizer";

const [glbPath, outPath, nArg] = process.argv.slice(2);
const N = parseInt(nArg || "24000", 10);
const glb = readFileSync(glbPath);

// ------------------------------------------------------------- GLB chunks →
const jsonLen = glb.readUInt32LE(12);
const gltf = JSON.parse(glb.subarray(20, 20 + jsonLen).toString("utf8"));
const binStart = 20 + jsonLen + 8;
const bin = glb.subarray(binStart);

await MeshoptDecoder.ready;

const COMP = { 5120: Int8Array, 5121: Uint8Array, 5122: Int16Array, 5123: Uint16Array, 5125: Uint32Array, 5126: Float32Array };
const SIZE = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };

function bufferViewData(bvIndex) {
  const bv = gltf.bufferViews[bvIndex];
  const ext = bv.extensions && bv.extensions.EXT_meshopt_compression;
  if (!ext) return bin.subarray(bv.byteOffset || 0, (bv.byteOffset || 0) + bv.byteLength);
  const src = bin.subarray(ext.byteOffset || 0, (ext.byteOffset || 0) + ext.byteLength);
  const out = new Uint8Array(ext.count * ext.byteStride);
  MeshoptDecoder.decodeGltfBuffer(out, ext.count, ext.byteStride, src, ext.mode, ext.filter);
  return Buffer.from(out.buffer);
}

function readAccessor(accIndex) {
  const acc = gltf.accessors[accIndex];
  const n = SIZE[acc.type];
  const T = COMP[acc.componentType];
  const data = bufferViewData(acc.bufferView);
  const stride = (gltf.bufferViews[acc.bufferView].extensions?.EXT_meshopt_compression?.byteStride)
    || gltf.bufferViews[acc.bufferView].byteStride || n * T.BYTES_PER_ELEMENT;
  const out = new Float32Array(acc.count * n);
  const off = acc.byteOffset || 0;
  for (let i = 0; i < acc.count; i++) {
    for (let c = 0; c < n; c++) {
      const byte = off + i * stride + c * T.BYTES_PER_ELEMENT;
      let v = new T(data.buffer, data.byteOffset + byte, 1)[0];
      if (acc.normalized) {
        const max = { 5120: 127, 5121: 255, 5122: 32767, 5123: 65535 }[acc.componentType];
        v = Math.max(v / max, -1);
      }
      out[i * n + c] = v;
    }
  }
  return out;
}

// ------------------------------------------- node transforms (scene graph) →
function nodeMatrix(node) {
  if (node.matrix) return node.matrix.slice();
  const t = node.translation || [0, 0, 0];
  const r = node.rotation || [0, 0, 0, 1];
  const s = node.scale || [1, 1, 1];
  const [x, y, z, w] = r;
  const m = [
    (1 - 2 * (y * y + z * z)) * s[0], 2 * (x * y + z * w) * s[0], 2 * (x * z - y * w) * s[0], 0,
    2 * (x * y - z * w) * s[1], (1 - 2 * (x * x + z * z)) * s[1], 2 * (y * z + x * w) * s[1], 0,
    2 * (x * z + y * w) * s[2], 2 * (y * z - x * w) * s[2], (1 - 2 * (x * x + y * y)) * s[2], 0,
    t[0], t[1], t[2], 1,
  ];
  return m;
}
function mul(a, b) {   // column-major a*b
  const o = new Array(16).fill(0);
  for (let i = 0; i < 4; i++)
    for (let j = 0; j < 4; j++)
      for (let k = 0; k < 4; k++) o[j * 4 + i] += a[k * 4 + i] * b[j * 4 + k];
  return o;
}
function apply(m, x, y, z, w = 1) {
  return [
    m[0] * x + m[4] * y + m[8] * z + m[12] * w,
    m[1] * x + m[5] * y + m[9] * z + m[13] * w,
    m[2] * x + m[6] * y + m[10] * z + m[14] * w,
  ];
}

const I = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const meshInstances = [];   // { meshIndex, worldMatrix }
function walk(nodeIndex, parent) {
  const node = gltf.nodes[nodeIndex];
  const world = mul(parent, nodeMatrix(node));
  if (node.mesh !== undefined) meshInstances.push({ meshIndex: node.mesh, world });
  for (const c of node.children || []) walk(c, world);
}
for (const n of gltf.scenes[gltf.scene || 0].nodes) walk(n, I);

// --------------------------------------------------- collect triangle soup →
// tris: [ax,ay,az, bx,..., cz] world space; jaw deltas in the same order
const triBase = [], triJaw = [], triBoost = [];
for (const { meshIndex, world } of meshInstances) {
  const mesh = gltf.meshes[meshIndex];
  const names = (mesh.extras && mesh.extras.targetNames) || [];
  const jawIdx = names.indexOf("jawOpen");
  // meshes 0/1 are the eyeballs (nodes eyeLeft/eyeRight): full spheres inside
  // the skull. Keep only their front caps or they glow through the head as
  // giant orbs (additive points have no occlusion). mesh 3 = teeth: dim them.
  const isEye = meshIndex === 0 || meshIndex === 1;
  const boost = isEye ? 1.7 : meshIndex === 3 ? 0.55 : 1.0;
  for (const prim of mesh.primitives) {
    const pos = readAccessor(prim.attributes.POSITION);
    const idx = prim.indices !== undefined
      ? readAccessor(prim.indices)
      : Float32Array.from({ length: pos.length / 3 }, (_, i) => i);
    const jaw = jawIdx >= 0 && prim.targets ? readAccessor(prim.targets[jawIdx].POSITION) : null;
    let eyeFrontZ = -Infinity;
    if (isEye) {   // world-space z threshold: keep the front ~40% of the eyeball
      let zmin = Infinity, zmax = -Infinity;
      for (let vi = 0; vi < pos.length / 3; vi++) {
        const p = apply(world, pos[vi * 3], pos[vi * 3 + 1], pos[vi * 3 + 2], 1);
        zmin = Math.min(zmin, p[2]); zmax = Math.max(zmax, p[2]);
      }
      eyeFrontZ = zmax - (zmax - zmin) * 0.40;   // front of face is +z pre-flip
    }
    for (let t = 0; t < idx.length; t += 3) {
      if (isEye) {
        let cz = 0;
        for (const vi of [idx[t], idx[t + 1], idx[t + 2]])
          cz += apply(world, pos[vi * 3], pos[vi * 3 + 1], pos[vi * 3 + 2], 1)[2] / 3;
        if (cz < eyeFrontZ) continue;
      }
      for (const vi of [idx[t], idx[t + 1], idx[t + 2]]) {
        const p = apply(world, pos[vi * 3], pos[vi * 3 + 1], pos[vi * 3 + 2], 1);
        triBase.push(p[0], p[1], p[2]);
        if (jaw) {
          const d = apply(world, jaw[vi * 3], jaw[vi * 3 + 1], jaw[vi * 3 + 2], 0);
          triJaw.push(d[0], d[1], d[2]);
        } else triJaw.push(0, 0, 0);
      }
      triBoost.push(boost);
    }
  }
}
const triCount = triBase.length / 9;
console.log("meshes:", meshInstances.length, "triangles:", triCount);

// ------------------------------------------- area-weighted surface sampling →
const areas = new Float64Array(triCount);
let totalArea = 0;
for (let t = 0; t < triCount; t++) {
  const o = t * 9;
  const ux = triBase[o + 3] - triBase[o], uy = triBase[o + 4] - triBase[o + 1], uz = triBase[o + 5] - triBase[o + 2];
  const vx = triBase[o + 6] - triBase[o], vy = triBase[o + 7] - triBase[o + 1], vz = triBase[o + 8] - triBase[o + 2];
  const cx = uy * vz - uz * vy, cy = uz * vx - ux * vz, cz = ux * vy - uy * vx;
  areas[t] = Math.sqrt(cx * cx + cy * cy + cz * cz) / 2;
  totalArea += areas[t];
}
const cdf = new Float64Array(triCount);
let acc = 0;
for (let t = 0; t < triCount; t++) { acc += areas[t] / totalArea; cdf[t] = acc; }

function pickTri() {
  const r = Math.random();
  let lo = 0, hi = triCount - 1;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (cdf[mid] < r) lo = mid + 1; else hi = mid; }
  return lo;
}

// detail metric: smaller triangles = denser topology = facial features
const sortedAreas = Float64Array.from(areas).sort();
const medianArea = sortedAreas[triCount >> 1];

const base = new Float32Array(N * 3), jawOut = new Float32Array(N * 3), bright = new Float32Array(N);
for (let i = 0; i < N; i++) {
  const t = pickTri(), o = t * 9;
  let a = Math.random(), b = Math.random();
  if (a + b > 1) { a = 1 - a; b = 1 - b; }
  const c = 1 - a - b;
  for (let k = 0; k < 3; k++) {
    base[i * 3 + k] = c * triBase[o + k] + a * triBase[o + 3 + k] + b * triBase[o + 6 + k];
    jawOut[i * 3 + k] = c * triJaw[o + k] + a * triJaw[o + 3 + k] + b * triJaw[o + 6 + k];
  }
  const detail = Math.sqrt(medianArea / Math.max(areas[t], 1e-12));
  bright[i] = Math.min(2.6, Math.max(0.55, detail)) * triBoost[t];
}

// ------------------------------------- normalize: center, height 1.7, face -z →
const mn = [1e9, 1e9, 1e9], mx = [-1e9, -1e9, -1e9];
for (let i = 0; i < N; i++)
  for (let k = 0; k < 3; k++) {
    mn[k] = Math.min(mn[k], base[i * 3 + k]);
    mx[k] = Math.max(mx[k], base[i * 3 + k]);
  }
const ctr = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
const scale = 1.7 / (mx[1] - mn[1]);
console.log("bounds:", mn.map(v => v.toFixed(3)), mx.map(v => v.toFixed(3)), "scale:", scale.toFixed(4));
for (let i = 0; i < N; i++) {
  // rotate 180° around Y (glTF faces +z; our camera looks at -z)
  const x = (base[i * 3] - ctr[0]) * scale, y = (base[i * 3 + 1] - ctr[1]) * scale, z = (base[i * 3 + 2] - ctr[2]) * scale;
  base[i * 3] = -x; base[i * 3 + 1] = y; base[i * 3 + 2] = -z;
  jawOut[i * 3] = -jawOut[i * 3] * scale;
  jawOut[i * 3 + 1] = jawOut[i * 3 + 1] * scale;
  jawOut[i * 3 + 2] = -jawOut[i * 3 + 2] * scale;
}

// jaw sanity: how big is the max delta?
let mj = 0;
for (let i = 0; i < N * 3; i++) mj = Math.max(mj, Math.abs(jawOut[i]));
console.log("max jaw delta:", mj.toFixed(4));

const out = Buffer.concat([Buffer.from(base.buffer), Buffer.from(jawOut.buffer), Buffer.from(bright.buffer)]);
writeFileSync(outPath, out);
console.log("wrote", outPath, out.length, "bytes,", N, "points (base+jaw+brightness)");
