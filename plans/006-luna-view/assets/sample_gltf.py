"""Sample a .gltf + .bin pair into the mock's head.bin point-cloud format.

Sketchfab exports split big sculpts into 65k-vert chunks and add scene props
(backdrop planes, lights). This loader takes an explicit mesh-index range so
the props stay out. Transforms are ignored — valid only when every kept chunk
sits under the same node chain with a net identity rotation (true for the
Muse "Female Head Sculpt" export; verify per model).

Usage: python sample_gltf.py <scene.gltf> <out_dir> [N]
                             [--rot "axis:deg,..."] [--crop <frac>]
                             [--meshes 0-41]
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sample_stl import parse_args, run  # noqa: E402

CTYPE = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}


def read_accessor(g: dict, buf: bytes, idx: int) -> np.ndarray:
    acc = g["accessors"][idx]
    view = g["bufferViews"][acc["bufferView"]]
    ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[acc["type"]]
    dt = np.dtype(CTYPE[acc["componentType"]])
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or dt.itemsize * ncomp
    raw = np.frombuffer(buf, dtype=np.uint8,
                        count=stride * acc["count"], offset=off)
    raw = raw.reshape(acc["count"], stride)[:, : dt.itemsize * ncomp]
    return raw.copy().view(dt).reshape(acc["count"], ncomp)


def load_gltf(path: str, meshes: range) -> np.ndarray:
    """(T, 3, 3) float32 triangle vertices from the selected meshes."""
    g = json.load(open(path))
    with open(os.path.join(os.path.dirname(path), g["buffers"][0]["uri"]), "rb") as f:
        buf = f.read()
    tris = []
    for mi in meshes:
        for prim in g["meshes"][mi]["primitives"]:
            if prim.get("mode", 4) != 4:
                continue
            pos = read_accessor(g, buf, prim["attributes"]["POSITION"]).astype(np.float32)
            if "indices" in prim:
                idx = read_accessor(g, buf, prim["indices"]).ravel().astype(np.int64)
                tris.append(pos[idx].reshape(-1, 3, 3))
            else:
                tris.append(pos.reshape(-1, 3, 3))
    return np.concatenate(tris)


def main() -> None:
    src, outdir, n, rots, crop, jaw_fracs, curv = parse_args()
    lo, hi = 0, None
    if "--meshes" in sys.argv:
        lo, hi = (int(v) for v in sys.argv[sys.argv.index("--meshes") + 1].split("-"))
    g = json.load(open(src))
    meshes = range(lo, (hi if hi is not None else len(g["meshes"]) - 1) + 1)
    run(load_gltf(src, meshes), outdir, n, rots, crop, jaw_fracs, curv)


if __name__ == "__main__":
    main()
