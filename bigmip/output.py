"""Writing results: Fiji-ready composite TIFF, block-max downsampled copies, an RGB rendering and a preview PNG."""
import os

import numpy as np
import tifffile

from .colors import imagej_lut


def free_name(path):
    """Never overwrite: 'x.tif' -> 'x_2.tif', 'x_3.tif', ... if it exists."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    k = 2
    while os.path.exists(f"{stem}_{k}{ext}"):
        k += 1
    return f"{stem}_{k}{ext}"


def auto_range(img, lo_pct=0.5, hi_pct=99.9):
    """Display range from percentiles of the non-zero pixels (zero = outside the acquired area in stitched data)."""
    step = max(1, int(np.sqrt(img.size / 4e6)))
    s = img[::step, ::step]
    nz = s[s > 0]
    if nz.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(nz, [lo_pct, hi_pct])
    return float(lo), float(max(hi, lo + 1))


def write_composite(path, stack, names, colors, ranges, pixel_um, info=None):
    """(C, Y, X) array -> ImageJ hyperstack TIFF that Fiji opens as a coloured composite with the right scale,
    channel names and display ranges. `info` (text) is shown in Fiji under Image > Show Info. Returns the path."""
    stack = np.asarray(stack)
    if stack.ndim == 2:
        stack = stack[None]
    meta = {"axes": "CYX", "mode": "composite", "unit": "micron",
            "LUTs": [imagej_lut(c) for c in colors],
            "Ranges": tuple(v for r in ranges for v in r),
            "Labels": list(names)}
    if info:
        meta["Info"] = info
    res = None
    if pixel_um and pixel_um[0]:
        res = (1.0 / pixel_um[0], 1.0 / (pixel_um[1] or pixel_um[0]))
    path = free_name(path)
    tifffile.imwrite(path, stack, imagej=True, resolution=res, metadata=meta)
    return path


def block_max(stack, k):
    """Downsample by an integer factor keeping the MAXIMUM of each k x k block, so thin bright structures survive
    (an average would wash them out). Works on (Y, X) or (C, Y, X)."""
    a = np.asarray(stack)
    y, x = a.shape[-2] // k * k, a.shape[-1] // k * k
    a = a[..., :y, :x]
    return a.reshape(a.shape[:-2] + (y // k, k, x // k, k)).max(axis=(-3, -1))


def render_rgb(stack, colors, ranges, gammas=None, rows=2048):
    """Blend channels into one 8-bit RGB image: each channel scaled to its display range (and gamma), tinted with its
    colour, and the colours added. Done in row strips so very large images fit in memory."""
    C, Y, X = stack.shape
    gammas = gammas or [1.0] * C
    out = np.zeros((Y, X, 3), np.uint8)
    for y0 in range(0, Y, rows):
        acc = np.zeros((min(rows, Y - y0), X, 3), np.float32)
        for c in range(C):
            lo, hi = ranges[c]
            v = (stack[c, y0:y0 + rows].astype(np.float32) - lo) / max(hi - lo, 1e-6)
            v = np.clip(v, 0, 1) ** gammas[c]
            acc += v[..., None] * np.asarray(colors[c], np.float32)
        out[y0:y0 + rows] = (np.clip(acc, 0, 1) * 255 + 0.5).astype(np.uint8)
    return out


def add_scalebar(rgb, um, pixel_um, color=(255, 255, 255)):
    """Draw a solid scale bar of `um` microns in the lower-right corner (in place)."""
    Y, X = rgb.shape[:2]
    n = int(round(um / pixel_um))
    th = max(2, Y // 150)
    m = max(4, int(0.03 * min(X, Y)))
    rgb[Y - m - th:Y - m, X - m - n:X - m] = color
    return rgb


def write_rgb(path, rgb, pixel_um):
    """8-bit RGB TIFF with the micron scale (ImageJ style, so Fiji shows the scale)."""
    res = (1.0 / pixel_um[0], 1.0 / (pixel_um[1] or pixel_um[0])) if pixel_um and pixel_um[0] else None
    path = free_name(path)
    tifffile.imwrite(path, rgb, imagej=True, photometric="rgb", resolution=res, metadata={"unit": "micron"})
    return path


def write_preview(path, rgb, max_side=2400):
    """Small PNG for a quick look (block-max reduced)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    k = max(1, int(np.ceil(max(rgb.shape[:2]) / max_side)))
    small = rgb if k == 1 else block_max(np.moveaxis(rgb, -1, 0), k).transpose(1, 2, 0)
    path = free_name(path)
    plt.imsave(path, small)
    return path
