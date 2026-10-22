"""Histology notes that travel with the images: a fill-in form, a checker, and helpers used by bigmip / bigmip-meta.

  bigmip-notes IMAGE                 open the notes form window for IMAGE (saved as <name>_histology.toml)
  bigmip-notes                       same, choosing the image in a window first
  bigmip-notes IMAGE --text          plain-text form in Notepad / TextEdit instead of the window
  bigmip-notes IMAGE --text --from OLD_histology.toml   text form starting from an earlier sample's protocol
  bigmip-notes IMAGE --check         check the notes: lists every missing or malformed field
  bigmip-notes IMAGE --show          print the notes as readable tables
  bigmip-notes FOLDER --folder       one shared form (histology.toml) for every image in FOLDER

Where notes are looked for (first match wins): the --notes path given to bigmip, then <name>_histology.toml next to
the image, then histology.toml in the image's folder.
"""
import argparse
import datetime
import json
import os
import re
import sys

# ------------------------------------------------------------------------------------------------- the form
# (key, required, hint). Sections are dicts; LIST sections hold any number of items with the given fields.
SECTIONS = [
    ("sample", "Sample and animal", "dict", [
        ("sample_id", True, "e.g. ID1494  (your label for this tissue)"),
        ("experimenters", True, "everyone who did the work, e.g. Sam Brill-Weil, A. Smith"),
        ("species", True, "e.g. mouse, macaque"),
        ("animal_id", True, "e.g. ear tag / cage card number"),
        ("strain_or_genotype", True, "e.g. C57BL/6J (wild type), Opn4-Cre; Ai14"),
        ("sex", True, "M / F / unknown"),
        ("age_or_date_of_birth", True, "e.g. P60, 8 weeks, or 2026-07-01"),
        ("tissue_collected", True, "date the tissue was collected, YYYY-MM-DD"),
        ("eye", False, "left / right"),
        ("light_condition", False, "e.g. dark adapted overnight, light adapted"),
        ("tissue", True, "e.g. retina"),
        ("preparation", True, "e.g. whole mount, cryosection, vibratome section"),
        ("section_thickness_um", False, "sections only, e.g. 20"),
    ]),
    ("dates", "Dates", "dict", [
        ("staining_started", True, "YYYY-MM-DD"),
        ("staining_finished", False, "YYYY-MM-DD"),
        ("imaged", False, "YYYY-MM-DD"),
        ("microscope", False, "e.g. Andor Dragonfly, 40x/1.25 sil"),
    ]),
    ("fixation", "Fixation", "dict", [
        ("fixative", True, "e.g. 4% PFA in PBS"),
        ("duration", True, "e.g. 1 h"),
        ("temperature", True, "e.g. RT, 4 C"),
    ]),
    ("blocking", "Blocking / permeabilization", "dict", [
        ("solution", True, "e.g. 5% normal donkey serum + 0.3% Triton X-100 in PBS"),
        ("duration", True, "e.g. 2 h"),
        ("temperature", True, "e.g. RT"),
    ]),
    ("primary_antibodies", "Primary antibodies (one block per antibody)", "list", [
        ("target", True, "e.g. melanopsin"),
        ("host", True, "e.g. chicken"),
        ("dilution", True, "e.g. 1:10000  (or a concentration: 2 ug/mL)"),
        ("vendor", False, "e.g. Advanced Targeting Systems"),
        ("catalog", False, "catalog number"),
        ("rrid", False, "e.g. AB_2650487"),
        ("lot", False, ""),
    ]),
    ("primary_incubation", "Primary incubation", "dict", [
        ("diluent", True, "e.g. blocking solution"),
        ("duration", True, "e.g. 4 nights, 72 h, overnight"),
        ("temperature", True, "e.g. 4 C"),
    ]),
    ("washes_after_primary", "Washes after primary", "dict", [
        ("buffer", True, "e.g. PBS + 0.1% Triton X-100"),
        ("scheme", True, "e.g. 3 x 10 min"),
    ]),
    ("secondary_antibodies", "Secondary antibodies / nanobodies (one block per reagent)", "list", [
        ("target", True, "e.g. anti-chicken, or RFP (for a nanobody)"),
        ("host", True, "e.g. donkey, alpaca (nanobody)"),
        ("fluorophore", True, "e.g. Alexa 647"),
        ("dilution", True, "e.g. 1:500"),
        ("vendor", False, ""),
        ("catalog", False, ""),
    ]),
    ("secondary_incubation", "Secondary incubation", "dict", [
        ("diluent", True, "e.g. blocking solution"),
        ("duration", True, "e.g. 1 h"),
        ("temperature", True, "e.g. RT"),
        ("protected_from_light", False, "yes / no"),
    ]),
    ("washes_after_secondary", "Washes after secondary", "dict", [
        ("buffer", True, "e.g. PBS"),
        ("scheme", True, "e.g. 3 x 10 min"),
    ]),
    ("other_stains", "Other stains, e.g. nuclear (one block per stain; delete the block if none)", "list", [
        ("stain", True, "e.g. Hoechst 33342"),
        ("dilution", True, "e.g. 1:1000  (or 1 ug/mL)"),
        ("duration", True, "e.g. 5 min"),
    ]),
    ("mounting", "Mounting", "dict", [
        ("medium", True, "e.g. ProLong Glass, Vectashield"),
        ("coverslip", False, "e.g. #1.5H"),
    ]),
    ("channels", "Image channels: what each one shows", "list", [
        ("image_channel", True, "channel name as stored in the image (pre-filled)"),
        ("shows", True, "e.g. melanopsin, nuclei, beta3-tubulin, RFP"),
        ("fluorophore", True, "e.g. Alexa 647, Hoechst"),
    ]),
    ("notes", "Free notes (anything else worth knowing)", "text", []),
]

DILUTION = re.compile(r"^\s*1\s*:\s*\d[\d,.]*\s*$|^\s*\d+(\.\d+)?\s*(ng|ug|µg|mg|g|nM|uM|µM|mM|M|U|%)(\s*/\s*m?l)?\s*$",
                      re.I)
DURATION = re.compile(r"\d\s*(s|sec|secs|seconds?|min|mins|minutes?|h|hr|hrs|hours?|d|days?|nights?)\b"
                      r"|overnight|\bo/?n\b", re.I)
TEMPERATURE = re.compile(r"\brt\b|room\s*temp|\d+\s*°?\s*c\b|on ice", re.I)
CHECKS = {"dilution": (DILUTION, "a dilution like 1:500 or a concentration like 2 ug/mL"),
          "duration": (DURATION, "a time with units, e.g. 10 min, 2 h, 3 nights, overnight"),
          "temperature": (TEMPERATURE, "e.g. RT, 4 C"),
          "sex": (re.compile(r"^(m|f|male|female|unknown|mixed)$", re.I), "M, F or unknown")}
DATE = (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "a date like 2026-10-19 (YYYY-MM-DD)")
for _f in ("tissue_collected", "staining_started", "staining_finished", "imaged"):
    CHECKS[_f] = DATE


def notes_path_for(image):
    stem = os.path.splitext(os.path.abspath(image))[0]
    if stem.lower().endswith(".ome"):
        stem = stem[:-4]
    return stem + "_histology.toml"


def find_notes(image, explicit=None):
    """Path of the notes for an image, or None."""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for p in (notes_path_for(image), os.path.join(os.path.dirname(os.path.abspath(image)), "histology.toml")):
        if os.path.exists(p):
            return p
    return None


def _q(v):
    """A TOML string value (JSON-style quoting is valid TOML)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return json.dumps(str(v))
    return json.dumps("" if v is None else str(v), ensure_ascii=False)


def _text(d, key="notes"):
    v = d.get(key)
    if isinstance(v, dict):
        v = v.get("text")
    return "" if v is None else str(v)


def _line(f, val, req, hint):
    return (f"{f} = {_q(val)}".ljust(46) + f"  # {'REQUIRED' if req else 'optional'}" + (f"  {hint}" if hint else ""))


def render(values, title=""):
    """The notes as a commented TOML form. `values` = dict like the parsed file (may be partial)."""
    L = ["# HISTOLOGY NOTES" + (f" -- {title}" if title else ""),
         "#",
         "# Type each answer BETWEEN THE QUOTES, e.g.   dilution = \"1:500\"",
         "# Fields marked REQUIRED must be filled in; optional ones can stay empty (\"\").",
         "# Times need units (10 min, 2 h, 3 nights, overnight); dilutions like 1:500 or 2 ug/mL;",
         "# temperatures RT or 4 C; dates YYYY-MM-DD.",
         "# More antibodies / stains: copy a whole block, from its [[...]] line down to the empty line.",
         "# No other stains? Delete that block.",
         "# Check the form with:   py -m bigmip.notes IMAGE --check", ""]
    for key, label, kind, fields in SECTIONS:
        L.append(f"# ==== {label}")
        if kind == "text":
            txt = _text(values, key).strip("\n")
            L += [f"[{key}]", "text = '''", *(txt.splitlines() if txt else []), "'''", ""]
            continue
        if kind == "dict":
            cur = values.get(key) or {}
            L.append(f"[{key}]")
            L += [_line(f, cur.get(f, ""), req, hint) for f, req, hint in fields]
            L.append("")
        else:
            items = values.get(key)
            if items is None:
                items = [{}]
            for item in items:
                L.append(f"[[{key}]]")
                L += [_line(f, (item or {}).get(f, ""), req, hint) for f, req, hint in fields]
                L.append("")
    known = {k for k, _, _, _ in SECTIONS}
    for key, v in values.items():                     # sections added by other tools, e.g. [file_history]
        if key not in known and isinstance(v, dict):
            L += [f"# ==== {key.replace('_', ' ')}", f"[{key}]"] + [f"{k} = {_q(x)}" for k, x in v.items()] + [""]
    return "\n".join(L) + "\n"


def load(path):
    import tomllib
    with open(path, "rb") as fh:
        try:
            return tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"\nCould not read {path}:\n  {e}\n"
                             "Most often an answer is missing its quotes: write  dilution = \"1:500\"  "
                             "not  dilution = 1:500") from None


def check(d, image_channels=None):
    """Return (errors, warnings). Errors = required fields missing or malformed."""
    errors, warnings = [], []

    def need(sec, f, val, where):
        s = "" if val is None else str(val).strip()
        if not s:
            errors.append(f"{where}: '{f}' is required")
            return
        if f in CHECKS:
            rx, what = CHECKS[f]
            if not rx.search(s):
                errors.append(f"{where}: '{f}' = '{s}' -- expected {what}")

    for key, label, kind, fields in SECTIONS:
        if kind == "text":
            continue
        val = d.get(key)
        if kind == "dict":
            val = val or {}
            for f, req, _ in fields:
                if req:
                    need(key, f, val.get(f), label)
                elif f in CHECKS and str(val.get(f) or "").strip():
                    need(key, f, val.get(f), label)
        else:
            items = val or []
            if not items and key in ("primary_antibodies", "secondary_antibodies", "channels"):
                errors.append(f"{label}: at least one entry is required")
            for i, item in enumerate(items, 1):
                item = item or {}
                for f, req, _ in fields:
                    if req:
                        need(key, f, item.get(f), f"{label} #{i}")
                if key == "primary_antibodies" and not (item.get("catalog") or item.get("rrid")):
                    warnings.append(f"{label} #{i}: no catalog number or RRID (recommended for publication)")
    if image_channels:
        named = [str((c or {}).get("image_channel", "")).strip() for c in (d.get("channels") or [])]
        missing = [c for c in image_channels if c not in named]
        extra = [c for c in named if c and c not in image_channels]
        if missing:
            errors.append(f"Image channels: no entry for {missing} (the image's channels are {image_channels})")
        if extra:
            warnings.append(f"Image channels: {extra} are not channels of this image")
    return errors, warnings


def channel_labels(d, names):
    """'638_Cy5_CF40_Zyla | melanopsin (Alexa 647)' for each image channel that has an entry."""
    m = {str((c or {}).get("image_channel", "")).strip(): c for c in (d.get("channels") or [])}
    out = []
    for n in names:
        c = m.get(n)
        if c and c.get("shows"):
            out.append(f"{n} | {c['shows']}" + (f" ({c['fluorophore']})" if c.get("fluorophore") else ""))
        else:
            out.append(n)
    return out


def summary_text(d):
    """Plain-text summary (embedded in the TIFF; Fiji: Image > Show Info)."""
    L = ["HISTOLOGY NOTES"]
    s = d.get("sample") or {}
    L.append("Sample: " + ", ".join(f"{k}: {v}" for k, v in s.items() if v))
    for key, label in (("fixation", "Fixation"), ("blocking", "Blocking")):
        v = d.get(key) or {}
        L.append(f"{label}: " + ", ".join(str(x) for x in v.values() if x))
    for a in d.get("primary_antibodies") or []:
        L.append(f"Primary: {a.get('target')} ({a.get('host')}) {a.get('dilution')}"
                 + "".join(f", {k} {a[k]}" for k in ("vendor", "catalog", "rrid", "lot") if a.get(k)))
    v = d.get("primary_incubation") or {}
    L.append("Primary incubation: " + ", ".join(str(x) for x in v.values() if x))
    v = d.get("washes_after_primary") or {}
    L.append("Washes: " + ", ".join(str(x) for x in v.values() if x))
    for a in d.get("secondary_antibodies") or []:
        L.append(f"Secondary: {a.get('target')} ({a.get('host')}) {a.get('fluorophore')} {a.get('dilution')}"
                 + "".join(f", {k} {a[k]}" for k in ("vendor", "catalog") if a.get(k)))
    v = d.get("secondary_incubation") or {}
    L.append("Secondary incubation: " + ", ".join(str(x) for x in v.values() if x))
    v = d.get("washes_after_secondary") or {}
    L.append("Washes: " + ", ".join(str(x) for x in v.values() if x))
    for a in d.get("other_stains") or []:
        L.append(f"Stain: {a.get('stain')} {a.get('dilution')}, {a.get('duration')}")
    v = d.get("mounting") or {}
    L.append("Mounting: " + ", ".join(str(x) for x in v.values() if x))
    for c in d.get("channels") or []:
        L.append(f"Channel {c.get('image_channel')}: {c.get('shows')} ({c.get('fluorophore')})")
    if _text(d).strip():
        L.append("Notes: " + " ".join(_text(d).split()))
    return "\n".join(L)


def markdown(d):
    """The notes as Markdown tables (used in the metadata report and by --show)."""
    L = ["## Histology", ""]
    for key, label, kind, fields in SECTIONS:
        v = d.get(key)
        if kind == "text":
            if _text(d, key).strip():
                L += [f"**{label.split(' (')[0]}:** {' '.join(_text(d, key).split())}", ""]
            continue
        if kind == "dict":
            v = v or {}
            row = ", ".join(f"{f.replace('_', ' ')}: {v.get(f)}" for f, _, _ in fields if v.get(f))
            L += [f"**{label}:** {row or '-'}", ""]
        else:
            items = v or []
            if not items:
                continue
            cols = [f for f, _, _ in fields if any((it or {}).get(f) for it in items)]
            L += [f"**{label.split(' (')[0]}**", "", "| " + " | ".join(c.replace("_", " ") for c in cols) + " |",
                  "|" + "---|" * len(cols)]
            L += ["| " + " | ".join(str((it or {}).get(c, "")) for c in cols) + " |" for it in items]
            L.append("")
    return "\n".join(L) + "\n"


def require_notes(image, explicit=None, image_channels=None):
    """For bigmip: return (notes dict, path) or exit with instructions."""
    p = find_notes(image, explicit)
    cmd = f'py -m bigmip.notes "{image}"'
    if not p:
        sys.exit(f"\nNo histology notes found for this image.\n"
                 f"  1. Create them:   {cmd}\n"
                 f"     (a form opens; fill it in and save. Use --from OLD_histology.toml to copy an earlier protocol)\n"
                 f"  2. Run bigmip again.\n"
                 f"  To make a MIP without notes anyway, add --skip-notes.")
    d = load(p)
    errors, warnings = check(d, image_channels)
    for w in warnings:
        print(f"  note: {w}")
    if errors:
        sys.exit(f"\nThe histology notes are incomplete ({p}):\n" + "\n".join(f"  - {e}" for e in errors)
                 + f"\nFix them (check with: {cmd} --check), or add --skip-notes.")
    return d, p


# ------------------------------------------------------------------------------------------------- command line
def _image_channels(image):
    try:
        from .readers import open_stack
        st = open_stack(image)
        names = list(st.meta["names"])
        st.close()
        return names
    except Exception:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bigmip-notes",
                                 description="Fill in, check or show the histology notes for an image. With no options "
                                             "a form window opens.")
    ap.add_argument("target", nargs="?", help="the image (.ims / .tif), or a folder with --folder. "
                                              "Leave out to choose the image in a window")
    ap.add_argument("--check", action="store_true", help="check the notes and list problems")
    ap.add_argument("--show", action="store_true", help="print the notes as readable tables")
    ap.add_argument("--text", action="store_true",
                    help="use the plain-text form (Notepad / TextEdit) instead of the form window")
    ap.add_argument("--from", dest="src", help="text form: copy the protocol from an earlier notes file "
                                               "(in the window: 'Copy protocol from...')")
    ap.add_argument("--folder", action="store_true", help="one shared histology.toml for every image in the folder")
    ap.add_argument("--no-open", action="store_true", help="text form: create it but do not open it")
    a = ap.parse_args(argv)
    if a.target is None:
        if a.check or a.show or a.text or a.src:
            sys.exit("give the image file")
        from .notes_gui import run
        run()
        return
    if a.folder:
        path = os.path.join(os.path.abspath(a.target), "histology.toml")
        chans = []
    else:
        if not os.path.exists(a.target):
            sys.exit(f"file not found: {a.target}")
        path = find_notes(a.target) or notes_path_for(a.target)
        chans = _image_channels(a.target)
    if a.check or a.show:
        if not os.path.exists(path):
            sys.exit(f"no notes yet: create them with  py -m bigmip.notes \"{a.target}\"")
        d = load(path)
        if a.show:
            print(markdown(d))
        errors, warnings = check(d, chans)
        for w in warnings:
            print(f"  note: {w}")
        if errors:
            print(f"{len(errors)} problem(s) in {path}:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        print(f"OK: {path} is complete.")
        return
    if not (a.text or a.src or a.no_open):
        from .notes_gui import run
        run(image=None if a.folder else a.target, notes_path=path, image_channels=chans)
        return
    if os.path.exists(path):
        print(f"Notes already exist: {path}\n(edit that file, or check it with --check)")
    else:
        values = {}
        if a.src:
            values = load(a.src)
            values.setdefault("sample", {})
            for f in ("sample_id", "animal_id", "sex", "age_or_date_of_birth", "tissue_collected", "eye"):
                values["sample"][f] = ""
            values["dates"] = {}                      # new staining: dates start empty
            values.pop("file_history", None)
        if chans:
            old = {str((c or {}).get("image_channel", "")): c for c in (values.get("channels") or [])}
            by_prefix = {str((c or {}).get("image_channel", ""))[:3]: c for c in (values.get("channels") or [])}
            values["channels"] = [dict(old.get(n) or by_prefix.get(n[:3]) or {}, image_channel=n) for n in chans]
        title = os.path.basename(a.target) if not a.folder else f"all images in {os.path.abspath(a.target)}"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(values, title))
        print(f"Created {path}" + (f"  (protocol copied from {a.src})" if a.src else ""))
        print("Fill in the REQUIRED fields and save, then check with:  "
              f"py -m bigmip.notes \"{a.target}\"{' --folder' if a.folder else ''} --check")
    if not a.no_open:
        try:
            if sys.platform.startswith("win"):
                import subprocess
                subprocess.Popen(["notepad.exe", path])
            elif sys.platform == "darwin":
                os.system(f'open -t "{path}"')
            else:
                os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
        except OSError:
            print("(open the file in any text editor, e.g. Notepad or TextEdit)")


if __name__ == "__main__":
    main()
