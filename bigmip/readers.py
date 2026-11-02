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
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

RETRIES = 4          # network / cloud drives (Google Drive, servers) sometimes fail a read; try again before giving up


def _retry(read, what):
    """Run read(); on an OS / HDF5 read error wait and retry, then fail with an explanation."""
    for attempt in range(RETRIES + 1):
        try:
            return read()
        except OSError as e:
            if attempt == RETRIES:
                raise OSError(f"could not read {what} after {RETRIES + 1} tries ({e}). If the file is on a network or "
                              f"cloud drive (e.g. Google Drive), copy it to a local disk and run again.") from e
            time.sleep(2 * (attempt + 1))

from .colors import color_from_ome, display_color


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


def _watch_parent():
    """Pool initializer: a worker exits as soon as the run that started it ends, also when that run was killed (Stop,
    closing the app, a crash). Otherwise idle workers stay behind, holding memory and the file."""
    import multiprocessing
    import threading
    parent = multiprocessing.parent_process()
    if parent is None:
        return

    def watch():
        parent.join()
        os._exit(1)
    threading.Thread(target=watch, daemon=True).start()


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
            slab = _retry(lambda: d[z:min(z1, z + zstep), y0:y1, x0:x1], f"planes {z}+ of block ({y0}, {x0})")
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
            colors.append(display_color(name, col))
            rng = None
            if ci is not None and _attr(ci, "ColorRange"):
                vals = [float(v) for v in re.findall(r"[-+0-9.eE]+", _attr(ci, "ColorRange"))[:2]]
                rng = tuple(vals) if len(vals) == 2 and vals[1] > vals[0] else None
            ranges.append(rng)
        self.meta = dict(shape=self.shape, pixel_um=(px * scale if px else None, py * scale if py else None),
                         z_um=pz * scale if pz else None, names=names, colors=colors, ranges=ranges,
                         dtype=str(self.dtype), levels=levels, source="Imaris .ims")

    def max_projection(self, channel, z0=0, z1=None, workers=4, block=2048, progress=None, region=None):
        """Max over planes z0..z1-1 of one channel (index into self.channels). Streams chunk-aligned XY blocks.
        region = (y0, y1, x0, x1) in pixels: project only that part of the image (for zoomed examples)."""
        Z, Y, X = self.shape
        z1 = Z if z1 is None else min(z1, Z)
        ry0, ry1, rx0, rx1 = region or (0, Y, 0, X)
        cz, cy, cx = self.chunks
        by, bx = max(cy, block // cy * cy), max(cx, block // cx * cx)
        dset = f"DataSet/ResolutionLevel {self.level}/TimePoint 0/Channel {self.channels[channel]}/Data"
        tasks = [(self.path, dset, z0, z1, max(y, ry0), min(Y, y + by, ry1), max(x, rx0), min(X, x + bx, rx1), cz * 4)
                 for y in range(ry0 // by * by, ry1, by) for x in range(rx0 // bx * bx, rx1, bx)]
        out = np.zeros((ry1 - ry0, rx1 - rx0), self.dtype)
        if workers <= 1:              # in this process (small reads, e.g. the app's overview: no worker start-up)
            for n, t in enumerate(tasks, 1):
                y, x, acc = _ims_block_max(t)
                out[y - ry0:y - ry0 + acc.shape[0], x - rx0:x - rx0 + acc.shape[1]] = acc
                if progress:
                    progress(n, len(tasks))
            return out
        with ProcessPoolExecutor(max_workers=max(1, min(workers, len(tasks))), initializer=_watch_parent) as ex:
            futures = [ex.submit(_ims_block_max, t) for t in tasks]
            try:
                for n, fut in enumerate(as_completed(futures), 1):    # counted as they finish, in any order
                    y, x, acc = fut.result()
                    out[y - ry0:y - ry0 + acc.shape[0], x - rx0:x - rx0 + acc.shape[1]] = acc
                    if progress:
                        progress(n, len(tasks))
            except BaseException:
                ex.shutdown(wait=False, cancel_futures=True)           # don't run the remaining blocks after a failure
                raise
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
        # Fusion / Bitplane OME-TIFFs store colors as 0x00BBGGRR, everyone else as standard RGBA
        bgr = bool(re.search(r"bitplane|Creator\s*=\s*\"Fusion", desc, re.I))
        colors = []
        for c in range(self.nc):
            col = color_from_ome(ome_cols[c], bgr) if c < len(ome_cols) else None
            if col is None and ij.get("LUTs") is not None and c < len(ij["LUTs"]):
                lut = np.asarray(ij["LUTs"][c]); col = tuple(lut[:, -1] / 255.0)
            colors.append(display_color(names[c], col))
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

    def max_projection(self, channel, z0=0, z1=None, workers=1, block=None, progress=None, region=None):
        """Max over planes z0..z1-1 of one channel, one plane in memory at a time. region = (y0, y1, x0, x1)."""
        z1 = self.nz if z1 is None else min(z1, self.nz)
        ry0, ry1, rx0, rx1 = region or (0, self.shape[1], 0, self.shape[2])
        out = None
        for n, z in enumerate(range(z0, z1), 1):
            plane = _retry(lambda: self.pages[self._page_index(channel, z)].asarray()[ry0:ry1, rx0:rx1],
                           f"plane {z + 1} of channel {channel + 1}")
            out = plane.copy() if out is None else np.maximum(out, plane, out=out)
            if progress:
                progress(n, z1 - z0)
        return out

    def close(self):
        self.tif.close()


def overview(path, max_side=1600):
    """A small max projection of the whole image, for picking regions: ((C, y, x) array, (fy, fx)), where fy, fx are
    full-resolution pixels per overview pixel. First choice: a MIP bigmip already made for this image (the smallest
    copy; usually on this computer even when the image itself is on a cloud drive). Otherwise, for .ims: a smaller
    version stored in the file. None if there is nothing to use (a TIFF without a MIP yet)."""
    import glob
    import tifffile
    from .output import block_max, default_outdir
    st = open_stack(path)
    Z, Y, X = st.shape
    levels = st.levels
    nc = len(st.channels)
    st.close()
    stem = os.path.splitext(os.path.basename(path))[0]
    folders = [default_outdir(path), os.path.join(os.path.dirname(os.path.abspath(path)), "bigmip_output")]
    cands = [c for d in folders for c in glob.glob(os.path.join(d, f"{glob.escape(stem)}_MIP*.tif"))
             if "_RGB" not in c and "_z" not in os.path.basename(c)[len(stem):]]   # all planes only
    small = None
    for best in sorted(cands, key=os.path.getsize):               # the whole field, every channel, all planes
        try:
            mm = tifffile.memmap(best) if os.path.getsize(best) > 2**30 else tifffile.imread(best)
        except (ValueError, OSError):
            continue
        mm = mm[None] if mm.ndim == 2 else mm
        if mm.shape[0] != nc:
            continue
        k = max(1, int(np.ceil(max(mm.shape[-2:]) / (2 * max_side))))
        small = np.asarray(mm[:, ::k, ::k])
        break
    if small is None and path.lower().endswith(".ims"):
        level = next((l for l in levels if max(X, Y) / 2 ** l <= 2 * max_side), levels[-1])
        st = open_stack(path, level)
        small = np.stack([st.max_projection(c, workers=1) for c in range(len(st.channels))])
        st.close()
    if small is None:
        return None
    k = max(1, int(np.ceil(max(small.shape[-2:]) / max_side)))
    small = block_max(small, k) if k > 1 else small
    return small, (Y / small.shape[-2], X / small.shape[-1])


def open_stack(path, level=0):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ims":
        return ImsStack(path, level)
    if ext in (".tif", ".tiff", ".btf", ".tf2", ".tf8"):
        return TiffStack(path, level)
    raise ValueError(f"unsupported file type '{ext}' (use .ims or .tif)")
