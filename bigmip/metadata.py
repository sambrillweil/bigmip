"""Readable metadata report for an Imaris .ims file (as written by Andor Fusion / Dragonfly or Imaris).

  bigmip-meta FILE.ims [-o OUTDIR]          (or:  python -m bigmip.metadata FILE.ims)

Writes, next to the file (or into OUTDIR):
  <name>_metadata.md                  readable summary: image, channels, objective, camera, lasers, filters, Z scan
  <name>_metadata.json                every attribute in the file + the parsed acquisition protocol (nothing dropped)
  <name>_acquisition_settings.csv     every hardware setting stored per channel (one row per device feature)

About the acquisition settings: Fusion stores the settings of EVERY device known to the rig, including ones that were
not used. The summary shows the camera that acquired each channel (named in the file) and the laser lines that were
switched on in the laser combiner whose shutter was open; everything else (filter wheels, disk speed, other
cameras, ...) is in the CSV. Values are as recorded by
the software (e.g. an objective changed by hand may not be reflected).
"""
import argparse
import csv
import json
import os
import re

import numpy as np

from .readers import _attr


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
    L = [f"# {os.path.basename(md['file'])}", ""]
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
    else:
        L += ["", "## Histology", "", "No histology notes found (create them with: py -m bigmip.notes FILE)."]
    if s["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in s["notes"]]
    L += ["", "Full metadata: the matching `_metadata.json`; every device setting per channel: "
              "`_acquisition_settings.csv`."]
    return "\n".join(L) + "\n"


def write_report(path, outdir=None, notes_path=None):
    from .notes import find_notes, load
    from .output import free_name
    md = read_ims_metadata(path)
    s = summarize(md)
    np_ = find_notes(path, notes_path)
    notes = load(np_) if np_ else None
    s["histology_notes_file"] = np_
    outdir = outdir or os.path.dirname(os.path.abspath(path))
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0])
    written = []
    p = free_name(stem + "_metadata.md")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(to_markdown(s, md, notes))
    written.append(p)
    p = free_name(stem + "_metadata.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"summary": s, "histology": notes, "raw": md}, fh, indent=1, ensure_ascii=False, default=str)
    written.append(p)
    chans, rows = feature_rows(md)
    if rows:
        p = free_name(stem + "_acquisition_settings.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["device id", "setting", "differs between channels"] + chans)
            w.writerows(rows)
        written.append(p)
    return s, written


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bigmip-meta", description="Readable metadata report for an Imaris .ims file.")
    ap.add_argument("file")
    ap.add_argument("-o", "--outdir", help="output folder (default: next to the file)")
    ap.add_argument("--notes", help="histology notes file (default: NAME_histology.toml or histology.toml next to it)")
    a = ap.parse_args(argv)
    if not a.file.lower().endswith(".ims"):
        raise SystemExit("bigmip-meta reads .ims files (for TIFFs, 'bigmip FILE --info' prints the basics)")
    s, written = write_report(a.file, a.outdir, a.notes)
    print(f"{os.path.basename(a.file)}")
    for k, v in s["image"].items():
        if v:
            print(f"  {k:28s} {v}")
    for c in s["channels"]:
        pc = s["per_channel"].get(c["name"], {})
        print(f"  ch{c['#']} {c['name']:<24s} ex {c['excitation']:<8s} em {c['emission']:<8s} "
              f"exposure {pc.get('exposure', '?'):<8} lasers {pc.get('lasers on', '?')}")
    print(f"  histology notes: {s['histology_notes_file'] or 'none (create with: py -m bigmip.notes FILE)'}")
    for w in written:
        print(f"  wrote {w}")


if __name__ == "__main__":
    main()
