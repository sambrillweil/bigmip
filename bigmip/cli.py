"""Command line:  bigmip FILE [options]      (or:  python -m bigmip FILE [options])

Run  bigmip --help  for all options. See README.md for a walk-through.
"""
import argparse
import os
import shutil
import sys
import time

import numpy as np

from . import __version__
from .colors import parse_color_list
from .output import (add_scalebar, auto_range, block_max, render_rgb, write_composite, write_preview, write_rgb)
from .readers import open_stack

EXAMPLES = """examples:
  bigmip "D:\\data\\stack.ims" --info
  bigmip "D:\\data\\stack.ims"
  bigmip "D:\\data\\stack.ims" --level 2                         quick low-resolution test
  bigmip "D:\\data\\stack.ims" --z 10 30                         only planes 10..30
  bigmip "D:\\data\\stack.ims" --colors blue,green,red,magenta --downsample 2 4
  bigmip "D:\\data\\stack.tif" --colors "405=gray,638=magenta" --ranges "auto,auto,100-600,auto" --rgb --scalebar 50
"""


def build_parser():
    p = argparse.ArgumentParser(
        prog="bigmip", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXAMPLES,
        description="Max-intensity projection of a large .ims or TIFF stack, keeping pixel size, channel names and "
                    "channel colors. Output: a Fiji-ready composite TIFF (one channel per color).")
    p.add_argument("file", help="input .ims or .tif/.tiff (OME-TIFF, ImageJ TIFF)")
    p.add_argument("-o", "--outdir", help="output folder (default: a 'bigmip_output' folder next to the input)")
    p.add_argument("--info", action="store_true", help="only print what is in the file, write nothing")
    p.add_argument("--level", type=int, default=0, help=".ims only: pyramid level (0 = full resolution, "
                                                        "1 = half, 2 = quarter, ...). Default 0")
    p.add_argument("--z", type=int, nargs=2, metavar=("FIRST", "LAST"),
                   help="project only planes FIRST..LAST (1-based, inclusive). Default: all planes")
    p.add_argument("--channels", help="keep only these channels, 1-based, e.g. '1,3,4'. Default: all")
    p.add_argument("--colors", help="recolor channels: a list in channel order ('blue,green,red,magenta') or "
                                    "assignments ('1=blue,4=gray' or '405=cyan'). Names: red green blue cyan magenta "
                                    "yellow orange gray, or hex like #ff8000. Default: the colors stored in the file")
    p.add_argument("--ranges", help="display range per channel in channel order, 'auto' or 'LOW-HIGH', e.g. "
                                    "'auto,100-800,auto'. Default: from the file if stored, else auto")
    p.add_argument("--auto-ranges", action="store_true",
                   help="ignore display ranges stored in the file and compute them from the projection")
    p.add_argument("--downsample", type=int, nargs="*", default=[], metavar="K",
                   help="also write copies shrunk K-fold (max of each KxK block), e.g. --downsample 2 4")
    p.add_argument("--rgb", action="store_true", help="also write a flattened RGB TIFF + PNG (for slides / figures)")
    p.add_argument("--gamma", help="per-channel gamma for --rgb/preview, e.g. '1,1,0.7,1' (<1 brightens dim parts)")
    p.add_argument("--scalebar", type=float, default=0, metavar="UM",
                   help="draw a scale bar of this many microns on the RGB/preview images")
    p.add_argument("--no-preview", action="store_true", help="skip the small preview PNG")
    p.add_argument("--notes", help="histology notes file (default: NAME_histology.toml next to the image, or "
                                   "histology.toml in its folder). Create one with: bigmip-notes FILE")
    p.add_argument("--skip-notes", action="store_true", help="do not include the histology notes in the outputs")
    p.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 2)),
                   help=".ims only: parallel processes (default: up to 8). Lower it if RAM is short")
    p.add_argument("--version", action="version", version=f"bigmip {__version__}")
    return p


def hms(sec):
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


class StatusBar:
    """One self-updating line:  [########----------]  42.0%  channel 2/4 (488_GFP)  elapsed 0:31  left ~0:43"""

    def __init__(self, n_parts, width=30):
        self.n, self.width, self.t0, self.last = n_parts, width, time.time(), 0.0
        self.tty = sys.stdout.isatty()
        self.gui = os.environ.get("BIGMIP_GUI") == "1"      # the bigmip app reads '@@progress' lines

    def update(self, part, frac, label, force=False):
        now = time.time()
        if not force and now - self.last < (0.25 if (self.tty or self.gui) else 15):   # files: a line every 15 s
            return
        self.last = now
        total = min(1.0, (part + frac) / self.n)
        el = now - self.t0
        left = el / total * (1 - total) if total > 0.01 else None
        if self.gui:
            print(f"@@progress {total:.4f} {el:.0f} {left if left is not None else -1:.0f} {label}", flush=True)
            return
        k = int(round(total * self.width))
        line = (f"[{'#' * k}{'-' * (self.width - k)}] {100 * total:5.1f}%  {label}  elapsed {hms(el)}"
                + (f"  left ~{hms(left)}" if left is not None else ""))
        if self.tty:
            print("\r" + line.ljust(110)[:150], end="", flush=True)
        else:
            print(line, flush=True)

    def done(self):
        if self.tty:
            print()


def step(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt_px(p):
    return f"{p:.4g} um" if p else "unknown"


def main(argv=None):
    a = build_parser().parse_args(argv)
    path = os.path.abspath(a.file)
    if not os.path.exists(path):
        sys.exit(f"file not found: {path}")
    t_start = time.time()
    step(f"reading the file header: {path}")
    st = open_stack(path, a.level)
    m = st.meta
    Z, Y, X = m["shape"]
    nC = len(m["names"])
    print(f"  type: {m['source']}")
    print(f"  size: {X} x {Y} px, {Z} planes, {nC} channel(s), {m['dtype']}"
          + (f", pyramid levels {m['levels']}" if len(m["levels"]) > 1 else ""))
    print(f"  pixel: {fmt_px(m['pixel_um'][0])} (x) x {fmt_px(m['pixel_um'][1])} (y); plane step {fmt_px(m['z_um'])}")
    for c in range(nC):
        col = m["colors"][c]
        rng = m["ranges"][c]
        print(f"  channel {c + 1}: {m['names'][c]:<28s} color {tuple(round(v, 2) for v in col) if col else 'none'}"
              f"   stored display range {rng if rng else 'none'}")

    # ---- histology notes (optional): used for channel labels, embedded in the TIFF, copied to the outputs
    from .notes import channel_labels, find_notes, summary_text, use_notes
    if a.info:
        p = find_notes(path, a.notes)
        print(f"  histology notes: {p if p else 'none (optional; add them with: py -m bigmip.notes FILE)'}")
        return
    notes = notes_path = None
    if a.skip_notes:
        step("histology notes not included (--skip-notes)")
    else:
        notes, notes_path, remarks = use_notes(path, a.notes, m["names"])
        step(f"histology notes: {notes_path}" if notes_path else
             "WARNING: no histology notes for this image, so the MIP carries no staining record "
             f"(optional; add them with:  py -m bigmip.notes \"{path}\")")
        for r in remarks:
            print(f"  {r}")

    # ---- which channels / planes
    chans = list(range(nC))
    if a.channels:
        chans = [int(s) - 1 for s in a.channels.split(",") if s.strip()]
        bad = [c + 1 for c in chans if not 0 <= c < nC]
        if bad:
            sys.exit(f"--channels: no channel {bad} (file has {nC})")
    z0, z1 = 0, Z
    if a.z:
        z0, z1 = max(0, a.z[0] - 1), min(Z, a.z[1])
        if z1 <= z0:
            sys.exit(f"--z: empty plane range (file has planes 1..{Z})")
    names = [m["names"][c] for c in chans]

    # ---- colors: file -> overrides; fall back to a fixed cycle for channels without one
    cycle = [(0, 0, 1), (0, 1, 0), (1, 0, 0), (1, 0, 1), (0, 1, 1), (1, 1, 0), (1, 1, 1)]
    colors = [m["colors"][c] or cycle[i % len(cycle)] for i, c in enumerate(chans)]
    if a.colors:
        for i, col in parse_color_list(a.colors, names).items():
            if not 0 <= i < len(chans):
                sys.exit(f"--colors: channel {i + 1} not in the output (it has {len(chans)})")
            colors[i] = col

    # ---- project
    outdir = a.outdir or os.path.join(os.path.dirname(path), "bigmip_output")
    os.makedirs(outdir, exist_ok=True)
    gb = len(chans) * (z1 - z0) * Y * X * np.dtype(st.dtype).itemsize / 2**30
    step(f"projecting {len(chans)} channel(s) x planes {z0 + 1}-{z1} ({gb:.1f} GB of image data to read)"
         + (f" with {a.workers} parallel workers" if path.lower().endswith(".ims") else ""))
    bar = StatusBar(len(chans))
    t0 = time.time()
    proj = np.zeros((len(chans), Y, X), st.dtype)
    for i, c in enumerate(chans):
        label = f"channel {i + 1}/{len(chans)} ({names[i][:20]})"
        bar.update(i, 0.0, label, force=True)
        proj[i] = st.max_projection(c, z0, z1, workers=a.workers,
                                    progress=lambda n, total, i=i, label=label: bar.update(i, n / total, label))
        bar.update(i, 1.0, label, force=True)
    bar.done()
    st.close()
    rate = gb / max(time.time() - t0, 1e-6) * 1024
    step(f"projection done in {hms(time.time() - t0)} ({rate:.0f} MB/s)")
    if rate < 150 and time.time() - t0 > 60:
        print("  note: the file was read slowly. If it is on a network or cloud drive (Google Drive, a server), copy it "
              "to a local disk first, or mark the folder 'Available offline' in Google Drive -- later runs are then "
              "several times faster.")

    # ---- display ranges: file -> auto -> explicit overrides
    ranges = []
    for i, c in enumerate(chans):
        r = None if a.auto_ranges else m["ranges"][c]
        ranges.append(r or auto_range(proj[i]))
    if a.ranges:
        items = [s.strip() for s in a.ranges.split(",")]
        for i, s in enumerate(items[:len(chans)]):
            if s and s.lower() != "auto":
                lo, hi = (float(v) for v in s.split("-", 1))
                ranges[i] = (lo, hi)
            elif s.lower() == "auto":
                ranges[i] = auto_range(proj[i])
    gammas = [float(v) for v in a.gamma.split(",")] if a.gamma else [1.0] * len(chans)
    gammas = (gammas + [1.0] * len(chans))[:len(chans)]

    # ---- write
    stem = os.path.splitext(os.path.basename(path))[0]
    tag = "MIP" + (f"_z{z0 + 1}-{z1}" if a.z else "") + (f"_level{a.level}" if a.level else "")
    px = m["pixel_um"]
    labels = channel_labels(notes, names) if notes else names
    info = summary_text(notes) if notes else None
    written = []
    step(f"writing the projection ({proj.nbytes / 2**30:.2f} GB) to {outdir}")
    written.append(write_composite(os.path.join(outdir, f"{stem}_{tag}.tif"), proj, labels, colors, ranges, px, info))
    for k in a.downsample:
        if k > 1:
            step(f"writing the {k}x smaller copy")
            pk = (px[0] * k, px[1] * k) if px[0] else px
            written.append(write_composite(os.path.join(outdir, f"{stem}_{tag}_x{k}smaller.tif"),
                                           block_max(proj, k), labels, colors, ranges, pk, info))
    if a.rgb or not a.no_preview:
        step("rendering the color image" + (" (RGB TIFF + PNG + preview)" if a.rgb else " (preview)"))
        rgb = render_rgb(proj, colors, ranges, gammas)
        if a.scalebar and px[0]:
            add_scalebar(rgb, a.scalebar, px[0])
        if a.rgb:
            written.append(write_rgb(os.path.join(outdir, f"{stem}_{tag}_RGB.tif"), rgb, px))
            written.append(write_preview(os.path.join(outdir, f"{stem}_{tag}_RGB.png"), rgb, max_side=10**9))
        if not a.no_preview:
            written.append(write_preview(os.path.join(outdir, f"{stem}_{tag}_preview.png"), rgb))
    if notes_path:
        dst = os.path.join(outdir, os.path.basename(notes_path))
        if os.path.abspath(dst) != os.path.abspath(notes_path):
            from .output import free_name
            dst = free_name(dst)
            shutil.copy2(notes_path, dst)
            written.append(dst)
        from .notes import log_event
        log_event(notes_path, "MIP made with these notes", written[0])
    step(f"done in {hms(time.time() - t_start)}")
    for i in range(len(chans)):
        print(f"  {labels[i]:<44s} display range {ranges[i][0]:.0f}-{ranges[i][1]:.0f}")
    for w in written:
        print(f"  wrote {w}")


if __name__ == "__main__":
    main()
