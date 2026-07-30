"""Sample a binary STL into the mock's head.bin point-cloud format.

Replaces extract_head.mjs for scans without blendshapes: the talking-jaw
delta is generated procedurally (points in the lower-front face rotate down
around an ear-height pivot). Also renders orthographic preview PNGs so the
orientation can be verified by eye.

Usage: python sample_stl.py <in.stl> <out_dir> [N] [--rot "axis:deg,axis:deg"]
                            [--crop <frac>]   # drop points below frac of height

"""

from __future__ import annotations

import struct
import sys
import zlib

import numpy as np

RNG = np.random.default_rng(7)


def load_stl(path: str) -> np.ndarray:
    """(T, 3, 3) float32 triangle vertices."""
    with open(path, "rb") as f:
        f.seek(80)
        ntri = struct.unpack("<I", f.read(4))[0]
        rec = np.fromfile(f, dtype=np.uint8, count=ntri * 50).reshape(ntri, 50)
    floats = rec[:, :48].copy().view("<f4").reshape(ntri, 4, 3)
    tris = floats[:, 1:4, :].astype(np.float32)
    # Museum scans carry mounting artifacts: a base plate and hole-fill
    # support planes, both perfectly axis-aligned — no organic surface is.
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    flat = (np.abs(n) > 0.999).any(axis=1)
    return tris[~flat]


def sample(tris: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Area-weighted surface sample → (points, brightness, unit normals)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    cross = np.cross(b - a, c - a)
    areas = 0.5 * np.linalg.norm(cross, axis=1)
    p = areas / areas.sum()
    idx = RNG.choice(len(tris), size=n, p=p)
    u, v = RNG.random(n), RNG.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    w = 1 - u - v
    pts = (w[:, None] * a[idx] + u[:, None] * b[idx] + v[:, None] * c[idx])
    # brightness from local detail: small triangles = fine features
    detail = np.sqrt(np.median(areas) / np.maximum(areas[idx], 1e-12))
    bright = np.clip(detail, 0.55, 2.6)
    nrm = cross[idx] / np.maximum(np.linalg.norm(cross[idx], axis=1, keepdims=True), 1e-12)
    return pts.astype(np.float32), bright.astype(np.float32), nrm.astype(np.float32)


def curvature_bright(pts: np.ndarray, nrm: np.ndarray,
                     voxel: float = 0.045) -> np.ndarray:
    """Brightness from curvature: where neighboring normals disagree (lips,
    eyes, nostrils, ears) glows; flat cheeks and scalp stay dim. Works on
    uniformly-decimated meshes where triangle size carries no signal."""
    key = np.floor(pts / voxel).astype(np.int64)
    k = key[:, 0] * 73856093 ^ key[:, 1] * 19349663 ^ key[:, 2] * 83492791
    uniq, inv = np.unique(k, return_inverse=True)
    sums = np.zeros((len(uniq), 3))
    np.add.at(sums, inv, nrm)
    cnt = np.bincount(inv).astype(np.float64)
    curv = 1.0 - np.linalg.norm(sums, axis=1)[inv] / cnt[inv]
    # normalize so the brightest decile of the head hits full glow
    hi = max(np.percentile(curv, 92), 1e-6)
    return np.clip(0.6 + 2.0 * (curv / hi), 0.55, 2.6).astype(np.float32)


ROT = {
    "x": lambda t: np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]]),
    "y": lambda t: np.array([[np.cos(t), 0, np.sin(t)], [0, 1, 0], [-np.sin(t), 0, np.cos(t)]]),
    "z": lambda t: np.array([[np.cos(t), -np.sin(t), 0], [np.sin(t), np.cos(t), 0], [0, 0, 1]]),
}


def normalize(pts: np.ndarray, height: float = 1.7) -> np.ndarray:
    lo, hi = pts.min(0), pts.max(0)
    pts = pts - (lo + hi) / 2
    return pts * (height / (hi[1] - lo[1]))


def jaw_deltas(pts: np.ndarray, mouth: float = 0.28,
               pivot: float = 0.42, chin: float = 0.0) -> np.ndarray:
    """Procedural jawOpen: rotate lower-front points down around an
    ear-height x-axis pivot, weight ramping from the hinge to the lips.
    mouth/pivot/chin are height fractions — retune per model (busts with
    shoulders put the lips higher up the bounding box than bare heads;
    chin cuts the effect off so necks and shoulders stay still)."""
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    ylo, yhi = y.min(), y.max()
    h = yhi - ylo
    mouth_y = ylo + mouth * h
    pivot_y = ylo + pivot * h
    chin_y = ylo + chin * h
    front = z < np.percentile(z, 35)  # face side (-z after orientation)
    lower = y < pivot_y
    w = np.zeros(len(pts), dtype=np.float32)
    m = front & lower
    # 0 at the hinge, 1 from the lips down; only the front wedge moves
    w[m] = np.clip((pivot_y - y[m]) / (pivot_y - mouth_y), 0, 1) ** 1.5
    w[y < chin_y] = 0                 # neck and below never move
    # also require closeness to the mouth in x (cheeks stay put);
    # window scales with head size (lips → crown), not full model height
    xw = np.clip(1.0 - np.abs(x) / (0.30 * (yhi - mouth_y)), 0, 1)
    w *= xw
    theta = -0.22 * w                 # radians, scaled per point
    dy = (np.cos(theta) - 1) * (y - pivot_y) - np.sin(theta) * (z - z.mean())
    dz = np.sin(theta) * (y - pivot_y) + (np.cos(theta) - 1) * (z - z.mean())
    out = np.zeros_like(pts)
    out[:, 1] = dy
    out[:, 2] = dz
    return out.astype(np.float32)


def write_png(path: str, img: np.ndarray) -> None:
    """img: (H, W, 3) uint8 → minimal PNG."""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[i].tobytes() for i in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def render(pts: np.ndarray, bright: np.ndarray, path: str,
           axes: tuple[int, int] = (0, 1), size: int = 560,
           mouth: np.ndarray | None = None) -> None:
    """Orthographic additive splat — violet dots on Luna's dark ground."""
    img = np.zeros((size, size, 3), dtype=np.float32)
    img[:] = (11 / 255, 14 / 255, 20 / 255)
    p = pts + (mouth if mouth is not None else 0)
    u = p[:, axes[0]]
    v = p[:, axes[1]]
    span = max(u.max() - u.min(), v.max() - v.min()) * 1.15
    ui = ((u - (u.max() + u.min()) / 2) / span + 0.5) * (size - 1)
    vi = (0.5 - (v - (v.max() + v.min()) / 2) / span) * (size - 1)
    ui = np.clip(ui.astype(int), 0, size - 1)
    vi = np.clip(vi.astype(int), 0, size - 1)
    accent = np.array([0.655, 0.545, 0.980])  # #a78bfa
    np.add.at(img, (vi, ui), accent[None, :] * (bright[:, None] * 0.16))
    img = 1 - np.exp(-img * 2.2)  # soft tonemap
    write_png(path, (np.clip(img, 0, 1) * 255).astype(np.uint8))


def run(tris: np.ndarray, outdir: str, n: int,
        rots: list[tuple[str, float]], crop: float,
        jaw_fracs: tuple[float, float, float] = (0.28, 0.42, 0.0),
        curv: bool = False) -> None:
    """Triangles → previews + head.bin (the shared tail of every sampler)."""
    # oversample so the point count survives cropping, then thin back to n
    pts, bright, nrm = sample(tris, n * 4 if crop > 0 else n)
    for axis, deg in rots:
        rm = ROT[axis](np.radians(deg)).T.astype(np.float32)
        pts, nrm = pts @ rm, nrm @ rm
    if crop > 0:  # busts: keep the head, cut chest/base
        y = pts[:, 1]
        keep = y > y.min() + crop * (y.max() - y.min())
        pts, bright, nrm = pts[keep], bright[keep], nrm[keep]
        pick = RNG.choice(len(pts), size=min(n, len(pts)), replace=False)
        pts, bright, nrm = pts[pick], bright[pick], nrm[pick]
    pts = normalize(pts)
    if curv:
        bright = curvature_bright(pts, nrm)
    jaw = jaw_deltas(pts, *jaw_fracs)

    render(pts, bright, f"{outdir}/preview_front.png", axes=(0, 1))
    render(pts, bright, f"{outdir}/preview_side.png", axes=(2, 1))
    render(pts, bright, f"{outdir}/preview_top.png", axes=(0, 2))
    render(pts, bright, f"{outdir}/preview_mouth.png", axes=(0, 1), mouth=jaw)

    out = np.concatenate([pts.ravel(), jaw.ravel(), bright])
    out.astype("<f4").tofile(f"{outdir}/head.bin")
    print(f"wrote {outdir}/head.bin  {len(pts)} points; "
          f"max jaw delta {np.abs(jaw).max():.4f}")


def parse_args():
    src, outdir = sys.argv[1], sys.argv[2]
    n = int(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 24000
    rots = []
    if "--rot" in sys.argv:
        spec = sys.argv[sys.argv.index("--rot") + 1]
        for part in spec.split(","):
            axis, deg = part.split(":")
            rots.append((axis, float(deg)))
    crop = 0.0
    if "--crop" in sys.argv:
        crop = float(sys.argv[sys.argv.index("--crop") + 1])
    jaw_fracs = (0.28, 0.42, 0.0)
    if "--jaw" in sys.argv:  # mouth,pivot,chin height fractions
        jaw_fracs = tuple(float(v) for v in
                          sys.argv[sys.argv.index("--jaw") + 1].split(","))
    return src, outdir, n, rots, crop, jaw_fracs, "--curv" in sys.argv


def main() -> None:
    src, outdir, n, rots, crop, jaw_fracs, curv = parse_args()
    run(load_stl(src), outdir, n, rots, crop, jaw_fracs, curv)


if __name__ == "__main__":
    main()
