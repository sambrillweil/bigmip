"""Readers for large stacks. Each reader exposes the same metadata and a max projection over Z for one channel.

Supported inputs
  .ims            Imaris 5.5 HDF5 (e.g. Andor Fusion / Dragonfly, Imaris). Uses any pyramid level.
  .tif / .tiff    OME-TIFF or ImageJ TIFF with axes ZYX, CZYX, ZCYX (one timepoint), read one plane at a time.

Metadata (Stack.meta)
  shape     (Z, Y, X) at the chosen level          names   channel names
  pixel_um  (x, y) pixel size in micron            colors  per-channel (r, g, b) in 0..1, or None if not stored
  z_um      plane spacing in micron (or None)      ranges  per-channel (min, max) display range, or None
"""
import os
import re
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from .colors import color_from_ome, color_from_wavelength


# ----------------------------------------------------------------------------------------------- Imaris (.ims)
def _attr(group, key, default=None):
    """Imaris stores attributes as arrays of single bytes; join them into a string."""
    if key not in group.attrs:
        return default
    v = group.attrs[key]
    if isinstance(v, np.ndarray):
        v = b"".join(v.ravel().tolist()) if v.dtype.kind == "S" else v.tobytes()
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).replace("\x00", "").strip()


def _ims_block_max(args):
    """Worker: max over Z of one XY block of one channel, reading chunk-aligned Z slabs."""
    path, dset, z0, z1, y0, y1, x0, x1, zstep = args
    import h5py
    try:
        import hdf5plugin  # noqa: F401  (registers extra HDF5 compression filters)
    except ImportError:
        pass
    with h5py.File(path, "r") as f:
        d = f[dset]
        acc = None
        for z in range(z0, z1, zstep):
            slab = d[z:min(z1, z + zstep), y0:y1, x0:x1]
            m = slab.max(axis=0)
            acc = m if acc is None else np.maximum(acc, m)
    return y0, x0, acc


class ImsStack:
    def __init__(self, path, level=0):
        import h5py
        try:
            import hdf5plugin  # noqa: F401
        except ImportError:
            pass
        self.path = path
        self.f = h5py.File(path, "r")
        levels = sorted(int(k.split()[-1]) for k in self.f["DataSet"].keys() if k.startswith("ResolutionLevel"))
        if level not in levels:
            raise ValueError(f"level {level} not in file (levels {levels})")
        self.level = level
        self.levels = levels
        tp = self.f[f"DataSet/ResolutionLevel {level}/TimePoint 0"]
        chans = sorted(int(k.split()[-1]) for k in tp.keys() if k.startswith("Channel"))
        self.channels = chans
        info = self.f["DataSetInfo/Image"]
        X0, Y0, Z0 = (int(float(_attr(info, k))) for k in ("X", "Y", "Z"))
        d = tp[f"Channel {chans[0]}/Data"]
        # the stored arrays are padded to the chunk grid; the true image size halves (rounding up) per level
        Z, Y, X = Z0, Y0, X0
        for _ in range(level):
            X, Y = (X + 1) // 2, (Y + 1) // 2
            Z = Z if d.shape[0] >= Z else (Z + 1) // 2
        Z, Y, X = min(Z, d.shape[0]), min(Y, d.shape[1]), min(X, d.shape[2])
        g = tp[f"Channel {chans[0]}"]
        if "ImageSizeX" in g.attrs:                               # exact size at this level, when stored
            X = int(float(_attr(g, "ImageSizeX"))); Y = int(float(_attr(g, "ImageSizeY")))
            Z = int(float(_attr(g, "ImageSizeZ")))
        self.shape = (Z, Y, X)
        self.dtype = d.dtype
        self.chunks = d.chunks or (1, 256, 256)
        ext = [float(_attr(info, k, "nan") or "nan") for k in ("ExtMin0", "ExtMax0", "ExtMin1", "ExtMax1", "ExtMin2",
                                                               "ExtMax2")]
        px = (ext[1] - ext[0]) / X if np.isfinite(ext[1] - ext[0]) else None
        py = (ext[3] - ext[2]) / Y if np.isfinite(ext[3] - ext[2]) else None
        pz = (ext[5] - ext[4]) / Z if np.isfinite(ext[5] - ext[4]) and Z > 1 else None
        unit = (_attr(info, "Unit", "") or "um").lower()
        scale = {"um": 1, "µm": 1, "micron": 1, "nm": 1e-3, "mm": 1e3}.get(unit, 1)
        names, colors, ranges = [], [], []
        for c in chans:
            ci = self.f.get(f"DataSetInfo/Channel {c}")
            name = _attr(ci, "Name", "") if ci is not None else ""
            names.append(name or f"Channel {c + 1}")
            col = None
            if ci is not None and _attr(ci, "Color"):
                vals = [float(v) for v in re.findall(r"[-+0-9.eE]+", _attr(ci, "Color"))[:3]]
                col = tuple(vals) if len(vals) == 3 and max(vals) > 0 else None
            colors.append(col or color_from_wavelength(name))
            rng = None
            if ci is not None and _attr(ci, "ColorRange"):
                vals = [float(v) for v in re.findall(r"[-+0-9.eE]+", _attr(ci, "ColorRange"))[:2]]
                rng = tuple(vals) if len(vals) == 2 and vals[1] > vals[0] else None
            ranges.append(rng)
        self.meta = dict(shape=self.shape, pixel_um=(px * scale if px else None, py * scale if py else None),
                         z_um=pz * scale if pz else None, names=names, colors=colors, ranges=ranges,
                         dtype=str(self.dtype), levels=levels, source="Imaris .ims")

    def max_projection(self, channel, z0=0, z1=None, workers=4, block=2048, progress=None):
        """Max over planes z0..z1-1 of one channel (index into self.channels). Streams chunk-aligned XY blocks."""
        Z, Y, X = self.shape
        z1 = Z if z1 is None else min(z1, Z)
        cz, cy, cx = self.chunks
        by, bx = max(cy, block // cy * cy), max(cx, block // cx * cx)
        dset = f"DataSet/ResolutionLevel {self.level}/TimePoint 0/Channel {self.channels[channel]}/Data"
        tasks = [(self.path, dset, z0, z1, y, min(Y, y + by), x, min(X, x + bx), cz * 4)
                 for y in range(0, Y, by) for x in range(0, X, bx)]
        out = np.zeros((Y, X), self.dtype)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for n, (y, x, acc) in enumerate(ex.map(_ims_block_max, tasks), 1):
                out[y:y + acc.shape[0], x:x + acc.shape[1]] = acc
                if progress:
                    progress(n, len(tasks))
        return out

    def close(self):
        self.f.close()


# ----------------------------------------------------------------------------------------------- TIFF / OME-TIFF
class TiffStack:
    def __init__(self, path, level=0):
        import tifffile
        if level != 0:
            raise ValueError("--level applies to .ims files only")
        self.path = path
        self.tif = tifffile.TiffFile(path)
        s = self.tif.series[0]
        raw = s.axes.upper()
        dims = dict(zip(raw, s.shape))
        if dims.get("S", 1) > 1:
            raise ValueError(f"RGB TIFFs are not supported ({s.axes} {s.shape})")
        if dims.get("T", 1) > 1:
            raise ValueError(f"time series ({s.axes} {s.shape}) not supported: one timepoint only")
        if not raw.replace("S", "").endswith("YX"):
            raise ValueError(f"unsupported TIFF axes {s.axes} {s.shape}")
        # the leading (non-YX) axes index the pages, last axis fastest; a generic sequence axis (I/Q) counts as Z
        self.lead = [a for a in raw if a not in "YXS"]
        zaxis = "Z" if "Z" in self.lead else next((a for a in self.lead if a in "IQ"), None)
        if any(a not in ("C", "T", zaxis) for a in self.lead):
            raise ValueError(f"unsupported TIFF axes {s.axes} {s.shape}")
        self.zaxis = zaxis
        self.lead_sizes = [dims[a] for a in self.lead]
        self.nc = dims.get("C", 1)
        self.nz = dims.get(zaxis, 1) if zaxis else 1
        self.shape = (self.nz, dims["Y"], dims["X"])
        self.pages = s.pages
        self.dtype = s.dtype
        self.channels = list(range(self.nc))
        desc = self.tif.pages[0].description or ""
        names = re.findall(r'<(?:OME:)?Channel\b[^>]*?\bName\s*=\s*"([^"]*)"', desc)
        ome_cols = re.findall(r'<(?:OME:)?Channel\b[^>]*?\bColor\s*=\s*"(-?\d+)"', desc)
        ij = self.tif.imagej_metadata or {}
        if not names and ij.get("Labels"):
            names = list(ij["Labels"])[:self.nc]
        names = (names + [f"Channel {c + 1}" for c in range(self.nc)])[:self.nc]
        # Fusion / Bitplane OME-TIFFs store colours as 0x00BBGGRR, everyone else as standard RGBA
        bgr = bool(re.search(r"bitplane|Creator\s*=\s*\"Fusion", desc, re.I))
        colors = []
        for c in range(self.nc):
            col = color_from_ome(ome_cols[c], bgr) if c < len(ome_cols) else None
            if col is None and ij.get("LUTs") is not None and c < len(ij["LUTs"]):
                lut = np.asarray(ij["LUTs"][c]); col = tuple(lut[:, -1] / 255.0)
            colors.append(col or color_from_wavelength(names[c]))
        ranges = [None] * self.nc
        if ij.get("Ranges") and len(ij["Ranges"]) >= 2 * self.nc:
            r = ij["Ranges"]; ranges = [(float(r[2 * c]), float(r[2 * c + 1])) for c in range(self.nc)]
        px = py = pz = None
        m = re.search(r'PhysicalSizeX\s*=\s*"([0-9.eE+-]+)"', desc)
        if m:
            px = float(m.group(1))
            my = re.search(r'PhysicalSizeY\s*=\s*"([0-9.eE+-]+)"', desc); py = float(my.group(1)) if my else px
            mz = re.search(r'PhysicalSizeZ\s*=\s*"([0-9.eE+-]+)"', desc); pz = float(mz.group(1)) if mz else None
        else:
            p0 = self.tif.pages[0]
            xr, yr = p0.tags.get("XResolution"), p0.tags.get("YResolution")
            unit = p0.tags.get("ResolutionUnit")
            if xr and xr.value[0]:
                f = {2: 25400.0, 3: 10000.0}.get(int(unit.value) if unit else 1, 1.0)   # inch / cm -> um
                if ij.get("unit") in ("micron", "um", "µm"):
                    f = 1.0
                px = f * xr.value[1] / xr.value[0]
                py = f * yr.value[1] / yr.value[0] if yr else px
            pz = ij.get("spacing")
        self.meta = dict(shape=self.shape, pixel_um=(px, py), z_um=pz, names=names, colors=colors, ranges=ranges,
                         dtype=str(self.dtype), levels=[0], source=f"TIFF ({s.axes} {s.shape})")

    def _page_index(self, c, z):
        idx = [c if a == "C" else z if a == self.zaxis else 0 for a in self.lead]
        return int(np.ravel_multi_index(idx, self.lead_sizes)) if self.lead else 0

    def max_projection(self, channel, z0=0, z1=None, workers=1, block=None, progress=None):
        """Max over planes z0..z1-1 of one channel, one plane in memory at a time."""
        z1 = self.nz if z1 is None else min(z1, self.nz)
        out = None
        for n, z in enumerate(range(z0, z1), 1):
            plane = self.pages[self._page_index(channel, z)].asarray()
            out = plane.copy() if out is None else np.maximum(out, plane, out=out)
            if progress:
                progress(n, z1 - z0)
        return out

    def close(self):
        self.tif.close()


def open_stack(path, level=0):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ims":
        return ImsStack(path, level)
    if ext in (".tif", ".tiff", ".btf", ".tf2", ".tf8"):
        return TiffStack(path, level)
    raise ValueError(f"unsupported file type '{ext}' (use .ims or .tif)")
