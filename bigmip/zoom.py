"""How far a zoomed region is from the optic nerve head (ONH).

Two distances, both from the ONH box's center to a region's center:
  straight      a straight line (in a flat whole mount this is the usual eccentricity)
  along tissue  the shortest path that stays inside the tissue, so in a curved section it follows the retina instead
                of crossing the empty space inside the eye. The tissue outline comes from the overview (a small MIP):
                channels scaled and summed, then an Otsu threshold. Accurate to a few overview pixels (~5-10 um).
"""
import heapq

import numpy as np

ONH_LABELS = ("onh", "optic_nerve_head", "optic nerve head", "disc", "optic_disc")


def is_onh(label):
    return str(label).strip().lower() in ONH_LABELS


def _otsu(v):
    hist, edges = np.histogram(v, bins=256)
    mids = (edges[:-1] + edges[1:]) / 2
    w1 = np.cumsum(hist)
    w2 = w1[-1] - w1
    m1 = np.cumsum(hist * mids)
    mu1 = m1 / np.maximum(w1, 1)
    mu2 = (m1[-1] - m1) / np.maximum(w2, 1)
    return mids[np.argmax(w1 * w2 * (mu1 - mu2) ** 2)]


def tissue_mask(small, max_side=600):
    """(mask, k): tissue pixels of an overview (C, y, x), at 1/k of its size. Zero = outside the imaged tiles."""
    from .output import block_max
    k = max(1, int(np.ceil(max(small.shape[-2:]) / max_side)))
    a = block_max(small, k).astype(np.float32) if k > 1 else small.astype(np.float32)
    s = np.zeros(a.shape[-2:], np.float32)
    for c in a:
        nz = c[c > 0]
        if nz.size:
            s += c / max(float(np.percentile(nz, 99.5)), 1e-6)
    inside = a.max(axis=0) > 0
    if not inside.any():
        return inside, k
    v = np.log1p(100 * s)
    return (v > _otsu(v[inside])) & inside, k


def geodesic(mask, start):
    """Shortest-path distance (in mask pixels, 8-neighbour steps) from start (y, x) to every tissue pixel; inf where
    the tissue doesn't connect. start is moved to the nearest tissue pixel first."""
    ys, xs = np.nonzero(mask)
    dist = np.full(mask.shape, np.inf)
    if ys.size == 0:
        return dist
    i = int(np.argmin((ys - start[0]) ** 2 + (xs - start[1]) ** 2))
    sy, sx = int(ys[i]), int(xs[i])
    dist[sy, sx] = 0.0
    H, W = mask.shape
    r2 = 2 ** 0.5
    steps = ((-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0), (-1, -1, r2), (-1, 1, r2), (1, -1, r2), (1, 1, r2))
    heap = [(0.0, sy, sx)]
    while heap:
        d, y, x = heapq.heappop(heap)
        if d > dist[y, x]:
            continue
        for dy, dx, w in steps:
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and d + w < dist[ny, nx]:
                dist[ny, nx] = d + w
                heapq.heappush(heap, (d + w, ny, nx))
    return dist


class OnhDistance:
    """Distances from one ONH position (um) to other points (um), for one image's overview."""

    def __init__(self, small, f, um, onh_um):
        """small: overview (C, y, x); f: (fy, fx) full pixels per overview pixel; um: (ux, uy) um per full pixel."""
        self.onh, self.um, self.f = onh_um, um, f
        self.mask, self.k = tissue_mask(small)
        self.scale = (f[0] * um[1] + f[1] * um[0]) / 2 * self.k        # um per mask pixel
        self.dist = geodesic(self.mask, self._px(onh_um))
        ys, xs = np.nonzero(self.dist == 0)                             # where the path starts (on tissue)
        y, x = self._px(onh_um)
        self.start_off = float(np.hypot(ys[0] - y, xs[0] - x)) * self.scale if ys.size else 0.0

    def _px(self, p):
        return (p[1] / self.um[1] / self.f[0] / self.k, p[0] / self.um[0] / self.f[1] / self.k)

    def __call__(self, p):
        """(straight um, along-tissue um or None when not connected to the ONH through tissue)."""
        straight = float(np.hypot(p[0] - self.onh[0], p[1] - self.onh[1]))
        ys, xs = np.nonzero(np.isfinite(self.dist))
        if ys.size == 0:
            return straight, None
        y, x = self._px(p)
        i = int(np.argmin((ys - y) ** 2 + (xs - x) ** 2))
        off = float(np.hypot(ys[i] - y, xs[i] - x)) * self.scale        # how far the point is from that tissue
        if off > 100:                                                   # not on tissue reachable from the ONH
            return straight, None
        along = self.start_off + float(self.dist[ys[i], xs[i]]) * self.scale + off
        return straight, max(along, straight)          # a path can't be shorter than the straight line
