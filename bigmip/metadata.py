"""Readable metadata report for an image: an Imaris .ims file (as written by Andor Fusion / Dragonfly or Imaris) in
full; for a TIFF, what bigmip reads from it (size, pixel size, channels). The histology notes are included.

  bigmip-meta FILE [-o OUTDIR]          (or:  python -m bigmip.metadata FILE)

Writes, into the image's own output folder (NAME_bigmip_output, next to the image; or into OUTDIR):
  <name>_metadata.md                  readable summary: image, channels, objective, camera, lasers, filters, Z scan
  <name>_metadata.json                every attribute in the file + the parsed acquisition protocol (nothing dropped)
  <name>_acquisition_settings.csv     every hardware setting stored per channel (one row per device feature; .ims)

The report is made automatically whenever bigmip does something with the image (notes saved, MIP or zoom made,
renamed), so it always matches the current notes; each time it replaces the previous report (refresh()).

About the acquisition settings: Fusion stores the settings of EVERY device known to the rig, including ones that were
not used. The summary shows the camera that acquired each channel (named in the file) and the laser lines that were
switched on in the laser combiner whose shutter was open; everything else (filter wheels, disk speed, other
cameras, ...) is in the CSV. Values are as recorded by
the software (e.g. an objective changed by hand may not be reflected).
"""
import argparse
import csv
import datetime
import io
import json
import os
import re
import threading
import time

import numpy as np

from .output import default_outdir
from .readers import _attr

REPORT_SUFFIXES = ("_metadata.md", "_metadata.json", "_acquisition_settings.csv")
# Held while a report is written and while files are renamed: a rename never moves a folder a report is being
# written into, and never meets the image held open by a report being made.
REPORT_LOCK = threading.RLock()


def report_path(image, suffix="_metadata.md"):
    """The report file for `image`: in its output folder (NAME_bigmip_output). A report made by an older bigmip, next
    to the image, is returned while there is none in the output folder yet."""
    stem = os.path.splitext(os.path.basename(image))[0]
    new = os.path.join(default_outdir(image), stem + suffix)
    old = os.path.join(os.path.dirname(os.path.abspath(image)), stem + suffix)
    return old if not os.path.exists(new) and os.path.exists(old) else new


def _attrs(group):
    return {k: _attr(group, k) for k in group.attrs.keys()}


def _json_dataset(ds):
    raw = ds[()]
    if isinstance(raw, np.ndarray):
        raw = b"".join(raw.ravel().tolist()) if raw.dtype.kind == "S" else raw.tobytes()
    txt = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    try:
        return json.loads(txt)
    except ValueError:
        return txt


def _num(text):
    m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


def _hex(rgb_text):
    vals = [float(v) for v in re.findall(r"[-+0-9.eE]+", rgb_text or "")[:3]]
    return "#%02x%02x%02x" % tuple(int(round(255 * v)) for v in vals) if len(vals) == 3 else ""


def read_ims_metadata(path):
    import h5py
    try:
        import hdf5plugin  # noqa: F401
    except ImportError:
        pass
    md = {"file": os.path.abspath(path), "size_GB": round(os.path.getsize(path) / 1e9, 2)}
    with h5py.File(path, "r") as f:
        md["root"] = _attrs(f)
        info = f["DataSetInfo"]
        md["groups"] = {}
        for k in info.keys():
            g = info[k]
            if isinstance(g, h5py.Group):
                md["groups"][k] = _attrs(g)
        custom = info.get("CustomData")
        md["custom_json"] = {}
        if custom is not None:
            for k in custom.keys():
                if isinstance(custom[k], h5py.Dataset):
                    md["custom_json"][k] = _json_dataset(custom[k])
        md["levels"] = sorted(k for k in f["DataSet"].keys())
    return md


def read_basic_metadata(path, meta=None):
    """For a TIFF: what bigmip's reader finds in it (size, pixel size, channels, colors, display ranges)."""
    if meta is None:
        from .readers import open_stack
        st = open_stack(path)
        meta = st.meta
        st.close()
    return {"file": os.path.abspath(path), "size_GB": round(os.path.getsize(path) / 1e9, 2), "reader": dict(meta)}


def summarize_basic(md):
    m = md["reader"]
    Z, Y, X = m["shape"]
    px, py = m["pixel_um"]
    py = py or px
    hexcol = lambda c: "#%02x%02x%02x" % tuple(int(round(255 * v)) for v in c) if c else ""  # noqa: E731
    return {
        "image": {"size (X x Y x Z)": f"{X} x {Y} x {Z}",
                  "pixel size (um)": f"{px:.4f} x {py:.4f}" if px else "unknown",
                  "Z step (um)": f"{m['z_um']:.3f}" if m.get("z_um") else "n/a",
                  "field size (um)": f"{X * px:.1f} x {Y * py:.1f}" if px else "",
                  "file type": m.get("source", ""), "data type": m.get("dtype", ""),
                  "file size (GB)": str(md["size_GB"])},
        "channels": [{"#": str(i + 1), "name": n, "color": hexcol(c),
                      "display range": " - ".join(f"{v:g}" for v in r) if r else ""}
                     for i, (n, c, r) in enumerate(zip(m["names"], m["colors"], m["ranges"]))],
        "camera": {}, "objective_psf": {}, "zscan": {}, "per_channel": {},
        "notes": ["A TIFF holds only the basics (size, pixel size, channels); the acquisition settings (lasers, "
                  "exposure, objective) are in the original .ims file."],
    }


def summarize(md):
    """Pick the useful, reliable fields out of the raw metadata."""
    g = md["groups"]
    img = g.get("Image", {})
    X, Y, Z = (int(float(img.get(k, "0") or 0)) for k in ("X", "Y", "Z"))
    ext = [_num(img.get(k)) for k in ("ExtMin0", "ExtMax0", "ExtMin1", "ExtMax1", "ExtMin2", "ExtMax2")]
    px = (ext[1] - ext[0]) / X if X and None not in ext[:2] else None
    py = (ext[3] - ext[2]) / Y if Y and None not in ext[2:4] else None
    pz = (ext[5] - ext[4]) / Z if Z > 1 and None not in ext[4:6] else None
    custom = g.get("CustomData", {})
    s = {
        "image": {
            "size (X x Y x Z)": f"{X} x {Y} x {Z}",
            "pixel size (um)": f"{px:.4f} x {py:.4f}" if px and py else "unknown",
            "Z step (um)": f"{pz:.3f}" if pz else "n/a",
            "field size (um)": f"{ext[1] - ext[0]:.1f} x {ext[3] - ext[2]:.1f}" if None not in ext[:4] else "",
            "stage position min (um)": ", ".join(f"{v:.1f}" for v in ext[0::2] if v is not None),
            "objective (from image info)": " ".join(x for x in (f"{img.get('LensPower', '')}x" if img.get("LensPower")
                                                               else "", f"NA {img.get('NumericalAperture')}"
                                                               if img.get("NumericalAperture") else "") if x),
            "microscope mode": img.get("MicroscopeMode", ""),
            "recording date": img.get("RecordingDate", ""),
            "acquisition start": g.get("TimeInfo", {}).get("AcquisitionStart", ""),
            "time points": g.get("TimeInfo", {}).get("DatasetTimePoints", ""),
            "pyramid levels": str(len(md["levels"])),
            "software": " / ".join(v for v in (f"Fusion {custom['FusionVersion']}" if custom.get("FusionVersion") else "",
                                               img.get("OriginalFormatFileIOVersion", "")) if v),
            "file size (GB)": str(md["size_GB"]),
        },
        "channels": [],
        "camera": {},
        "objective_psf": {},
        "zscan": {},
        "per_channel": {},
        "notes": [],
    }
    i = 0
    while f"Channel {i}" in g:
        c = g[f"Channel {i}"]
        s["channels"].append({
            "#": str(i + 1), "name": c.get("Name", ""), "color": _hex(c.get("Color")),
            "display range": " - ".join(f"{v:g}" for v in map(float, re.findall(r"[-+0-9.eE]+",
                                                                                c.get("ColorRange", ""))[:2])),
            "gamma": c.get("GammaCorrection", ""),
            "excitation": c.get("LSMExcitationWavelength", ""), "emission": c.get("LSMEmissionWavelength", ""),
            "pinhole": c.get("LSMPinhole", ""), "mode": c.get("MicroscopeMode", ""),
        })
        i += 1
    for k, label in (("SensorWidth", "sensor width (px)"), ("SensorHeight", "sensor height (px)"),
                     ("Width", "tile width (px)"), ("Height", "tile height (px)"), ("BinningX", "binning X"),
                     ("BinningY", "binning Y"), ("XPosition", "stage X (mm)"), ("YPosition", "stage Y (mm)"),
                     ("NumberOfZPoints", "Z planes per tile"), ("NumberOfChannels", "channels"),
                     ("DateAndTime", "protocol date")):
        if k in custom:
            s["camera"][label] = custom[k]
    psf = md["custom_json"].get("PSF Settings Configuration V2")
    if isinstance(psf, dict):
        for k, label in (("ObjectiveLensMagnification", "objective magnification"),
                         ("ObjectiveLensNA", "objective NA"), ("ImmersionMedium", "immersion medium (code)"),
                         ("SpecimenRefractiveIndex", "specimen refractive index"),
                         ("AcquisitionDistanceFromCoverslip", "distance from coverslip (um)"),
                         ("ResolutionX", "pixel X (um)"), ("ResolutionY", "pixel Y (um)"),
                         ("ResolutionZ", "Z step (um)")):
            if k in psf:
                v = psf[k]
                s["objective_psf"][label] = round(v, 4) if isinstance(v, float) else v
    proto = md["custom_json"].get("Protocol Configuration V2")
    if isinstance(proto, dict):
        _summarize_protocol(proto, s)
    s["notes"].append("Values are as recorded by the acquisition software. The 'objective' entries come from different "
                      "places in the file and can disagree if the objective was changed by hand -- check the pixel size.")
    return s


def _summarize_protocol(proto, s):
    for el in proto.get("ProtocolSpecification", []):
        if isinstance(el, dict) and "StepSize" in el and "StepCount" in el:
            for k in ("StartPosition", "EndPosition", "StepSize", "StepCount", "Size"):
                s["zscan"][k] = el.get(k)
    groups = []
    for el in proto.get("ProtocolSpecification", []):
        if isinstance(el, dict) and "ChannelGroups" in el:
            groups = [cg.get("Master", {}) for cg in el["ChannelGroups"]]
    cfv = proto.get("ChannelsFeatureValues", {})
    for name, feats in cfv.items():
        dev_of = {}
        table = {}
        for e in feats:
            fid = e.get("FeatureIdentifier", {})
            dev = fid.get("DeviceUniqueIdentifier", {}).get("DeviceInstanceId", "")
            table.setdefault(dev, {})[fid.get("FeatureName", "")] = e.get("Value")
        grp = next((gr for gr in groups if gr.get("Name") == name), {})
        cam = grp.get("AcquisitionDeviceId", {}).get("DeviceInstanceId")
        out = {"camera": grp.get("AcquisitionDeviceAlias", "")}
        if cam and cam in table:
            t = table[cam]
            for k, label in (("CameraModel", "camera model"), ("ExposureTime", "exposure"),
                             ("PreAmpGain", "gain / bit depth"), ("PixelReadoutRate", "readout rate"),
                             ("ElectronicShutteringMode", "shutter mode"), ("Binning", "binning"),
                             ("DetectorTemperature", "sensor temp (C)")):
                v = t.get(k)
                if v in (None, "", "Unknown"):
                    continue
                out[label] = f"{float(v):.1f}" if k == "DetectorTemperature" and _num(v) is not None else v
        lasers = []
        for dev, t in table.items():                       # laser combiner whose excitation shutter was open
            if str(t.get("ExcitationShutterOpen")) != "True":
                continue
            for k, v in t.items():
                m = re.fullmatch(r"LaserOnOff(\d+)", k)
                if m and str(v) == "True":
                    n = m.group(1)
                    pct = _num(t.get(f"LaserIntensity{n}"))
                    low = " (low-power mode)" if str(t.get("AndorLaserCombinerLowPower")) == "True" else ""
                    lasers.append(f"{t.get(f'LaserWavelength{n}', '?')} nm at {pct:g}%{low}"
                                  if pct is not None else f"{t.get(f'LaserWavelength{n}', '?')} nm")
        out["lasers on"] = "; ".join(lasers) if lasers else "not found"
        icm = grp.get("IntensityColourMapping") or {}
        if icm:
            out["Fusion display range"] = f"{icm.get('MinRange')} - {icm.get('MaxRange')} (gamma {icm.get('Gamma')})"
        s["per_channel"][name] = out


def feature_rows(md):
    """Every per-channel device setting in the Fusion protocol, as rows: device, feature, value per channel."""
    proto = md["custom_json"].get("Protocol Configuration V2")
    if not isinstance(proto, dict):
        return [], []
    cfv = proto.get("ChannelsFeatureValues", {})
    chans = list(cfv)
    rows = {}
    for ch in chans:
        for e in cfv[ch]:
            fid = e.get("FeatureIdentifier", {})
            key = (fid.get("DeviceUniqueIdentifier", {}).get("DeviceInstanceId", ""), fid.get("FeatureName", ""))
            rows.setdefault(key, {})[ch] = e.get("Value", "")
    out = []
    for (dev, feat), vals in rows.items():
        v = [str(vals.get(c, "")) for c in chans]
        out.append([dev, feat, "yes" if len(set(v)) > 1 else ""] + v)
    out.sort(key=lambda r: (r[2] != "yes", r[0], r[1]))
    return chans, out


def to_markdown(s, md, notes=None):
    L = [f"# {os.path.basename(md['file'])}", "",
         f"Report updated {datetime.datetime.now():%Y-%m-%d %H:%M}. bigmip remakes it whenever the notes are saved, a "
         "MIP or zoom is made, or the image is renamed.", ""]
    L += ["## Image", "", "| | |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in s["image"].items() if v not in ("", None)]
    if s["channels"]:
        cols = list(s["channels"][0].keys())
        L += ["", "## Channels", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        L += ["| " + " | ".join(str(c[k]) for k in cols) + " |" for c in s["channels"]]
    if s["per_channel"]:
        names = list(s["per_channel"])
        keys = []
        for d in s["per_channel"].values():
            keys += [k for k in d if k not in keys]
        L += ["", "## Acquisition settings per channel (Fusion protocol)", "",
              "| setting | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        L += ["| " + k + " | " + " | ".join(str(s["per_channel"][n].get(k, "")) for n in names) + " |" for k in keys]
    for title, key in (("Objective / PSF settings", "objective_psf"), ("Camera frame (per tile)", "camera"),
                       ("Z scan (stage, um)", "zscan")):
        if s[key]:
            L += ["", f"## {title}", "", "| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in s[key].items()]
    if notes:
        from .notes import markdown as notes_md
        L += ["", notes_md(notes).rstrip()]
    elif s.get("histology_problem"):
        L += ["", "## Histology", "", s["histology_problem"]]
    else:
        L += ["", "## Histology", "", "No histology notes found (create them with: py -m bigmip.notes FILE)."]
    if s["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in s["notes"]]
    L += ["", "Full metadata: the matching `_metadata.json`" + ("; every device setting per channel: "
                                                                 "`_acquisition_settings.csv`." if "custom_json" in md
                                                                 else ".")]
    return "\n".join(L) + "\n"


def _replace_file(path, text, encoding="utf-8", newline=None):
    """Write `text` to a temporary file, then swap it in, so the report is never half-written (another bigmip run may
    be writing or reading it). Raises PermissionError if the file is held open (e.g. the CSV open in Excel)."""
    tmp = f"{path}.{os.getpid()}-{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding=encoding, newline=newline) as fh:
            fh.write(text)
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return path
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.2)                  # briefly in use (being read, scanned, or synced): try again
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_report(path, outdir=None, notes_path=None, meta=None):
    """Write the report into the image's output folder (or outdir), replacing the previous report: it is remade from
    the image and the current notes each time. meta: the reader's metadata, if the file is already open (TIFF).
    Returns (summary, files written)."""
    from .notes import find_notes, load
    with REPORT_LOCK:
        if path.lower().endswith(".ims"):
            md = read_ims_metadata(path)
            s = summarize(md)
        else:
            md = read_basic_metadata(path, meta)
            s = summarize_basic(md)
        np_ = find_notes(path, notes_path)
        notes = None
        if np_:
            try:
                notes = load(np_)
            except SystemExit:                   # a hand-edited notes file that is not valid TOML
                s["histology_problem"] = (f"The notes file {os.path.basename(np_)} could not be read (an entry is not "
                                          "in the expected form): open the image in the bigmip app to see where.")
        s["histology_notes_file"] = np_
        outdir = outdir or default_outdir(path)
        os.makedirs(outdir, exist_ok=True)
        stem = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0])
        written, busy = [], []
        files = [(stem + "_metadata.md", to_markdown(s, md, notes), "utf-8", None),
                 (stem + "_metadata.json", json.dumps({"summary": s, "histology": notes, "raw": md}, indent=1,
                                                      ensure_ascii=False, default=str), "utf-8", None)]
        chans, rows = feature_rows(md) if "custom_json" in md else ([], [])
        if rows:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["device id", "setting", "differs between channels"] + chans)
            w.writerows(rows)
            files.append((stem + "_acquisition_settings.csv", buf.getvalue(), "utf-8-sig", ""))
        for p, text, enc, nl in files:
            try:
                written.append(_replace_file(p, text, enc, nl))
            except PermissionError:
                busy.append(os.path.basename(p))
        if busy:
            raise PermissionError(f"{', '.join(busy)} is open in another program (Excel?), so it was not updated; "
                                  "close it and the next bigmip action updates it")
    return s, written


def refresh(image, notes_path=None, meta=None):
    """Remake the image's metadata report in its output folder. Called by every bigmip action on an image (notes saved,
    MIP or zoom made, renamed); never stops that action. Returns (files written, problem or None)."""
    with REPORT_LOCK:
        if not image or not os.path.isfile(image):   # (e.g. renamed meanwhile: the rename makes its own report)
            return [], None
        try:
            return write_report(image, None, notes_path, meta)[1], None
        except Exception as e:                   # noqa: BLE001 -- a report problem must not stop the MIP / save
            return [], f"the metadata report was not updated: {e}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bigmip-meta", description="Readable metadata report for an image (.ims in full; "
                                                                 "TIFF: the basics).")
    ap.add_argument("file")
    ap.add_argument("-o", "--outdir", help="output folder (default: the image's own NAME_bigmip_output folder)")
    ap.add_argument("--notes", help="histology notes file (default: NAME_histology.toml or histology.toml next to it)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.file):
        raise SystemExit(f"file not found: {a.file}")
    s, written = write_report(a.file, a.outdir, a.notes)
    print(f"{os.path.basename(a.file)}")
    for k, v in s["image"].items():
        if v:
            print(f"  {k:28s} {v}")
    for c in s["channels"]:
        pc = s["per_channel"].get(c["name"], {})
        if "excitation" in c:
            print(f"  ch{c['#']} {c['name']:<24s} ex {c['excitation']:<8s} em {c['emission']:<8s} "
                  f"exposure {pc.get('exposure', '?'):<8} lasers {pc.get('lasers on', '?')}")
        else:
            print(f"  ch{c['#']} {c['name']:<24s} color {c['color']:<8s} display range {c['display range']}")
    print(f"  histology notes: {s['histology_notes_file'] or 'none (create with: py -m bigmip.notes FILE)'}")
    for w in written:
        print(f"  wrote {w}")


if __name__ == "__main__":
    main()
