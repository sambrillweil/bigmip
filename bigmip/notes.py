"""Histology notes that travel with the images: the form's fields, reading / writing the notes file, a checker,
and helpers used by bigmip, bigmip-meta and the form window.

  bigmip-notes IMAGE                 open the notes form window for IMAGE (saved as <name>_histology.toml)
  bigmip-notes                       same, choosing the image in a window first
  bigmip-notes IMAGE --text          plain-text form in Notepad / TextEdit instead of the window
  bigmip-notes IMAGE --text --from OLD_histology.toml   text form starting from an earlier sample's protocol
  bigmip-notes IMAGE --check         list entries that look unusual (e.g. a time without units)
  bigmip-notes IMAGE --show          print the notes as readable tables
  bigmip-notes FOLDER --folder       one shared form (histology.toml) for every image in FOLDER

Every field is optional. Where notes are looked for (first match wins): the --notes path given to bigmip, then
<name>_histology.toml next to the image, then histology.toml in the image's folder.
"""
import argparse
import datetime
import json
import os
import re
import sys

# ------------------------------------------------------------------------------------------------- the form
STEP_NOTE = ("notes", "anything else about this step")
ITEM_NOTE = ("notes", "anything else about this reagent")
# (key, label, kind, [(field, hint), ...]). 'dict' = one block; 'list' = any number of blocks; 'text' = free text.
SECTIONS = [
    ("sample", "Sample and animal", "dict", [
        ("sample_id", "e.g. ID1494  (your label for this tissue)"),
        ("experimenters", "everyone who did the work, e.g. Sam Brill-Weil, A. Smith"),
        ("species", "e.g. mouse, macaque"),
        ("animal_id", "e.g. ear tag / cage card number"),
        ("strain_or_genotype", "e.g. C57BL/6J (wild type), Opn4-Cre; Ai14"),
        ("sex", "M / F / unknown"),
        ("age_or_date_of_birth", "e.g. P60, 8 weeks, or a date"),
        ("tissue_collected", "date the tissue was collected"),
        ("eye", "left / right"),
        ("light_condition", "e.g. dark adapted overnight, light adapted"),
        ("tissue", "e.g. retina"),
        ("preparation", "e.g. whole mount, cryosection, vibratome section"),
        ("section_thickness_um", "sections only, e.g. 20"),
        ("notes", "anything else about the animal or tissue"),
    ]),
    ("dates", "Dates", "dict", [
        ("staining_started", "first day of staining"),
        ("staining_finished", ""),
        ("imaged", ""),
        ("microscope", "e.g. Andor Dragonfly, 40x/1.25 sil"),
    ]),
    ("fixation", "Fixation", "dict", [
        ("fixative", "e.g. 4% PFA in PBS"),
        ("duration", "e.g. 1 h"),
        ("temperature", "e.g. RT, 4 C"),
        STEP_NOTE,
    ]),
    ("blocking", "Blocking / permeabilization", "dict", [
        ("solution", "e.g. 5% normal donkey serum + 0.3% Triton X-100 in PBS"),
        ("duration", "e.g. 2 h"),
        ("temperature", "e.g. RT"),
        STEP_NOTE,
    ]),
    ("primary_antibodies", "Primary antibodies (one block per antibody)", "list", [
        ("target", "e.g. melanopsin"),
        ("host", "e.g. chicken"),
        ("dilution", "e.g. 1:10000  (or a concentration: 2 ug/mL)"),
        ("vendor", "e.g. Advanced Targeting Systems"),
        ("catalog", "catalog number"),
        ("rrid", "e.g. AB_2650487"),
        ("lot", ""),
        ITEM_NOTE,
    ]),
    ("primary_incubation", "Primary incubation", "dict", [
        ("diluent", "e.g. blocking solution"),
        ("duration", "e.g. 4 nights, 72 h, overnight"),
        ("temperature", "e.g. 4 C"),
        STEP_NOTE,
    ]),
    ("washes_after_primary", "Washes after primary", "dict", [
        ("buffer", "e.g. PBS + 0.1% Triton X-100"),
        ("scheme", "e.g. 3 x 10 min"),
        STEP_NOTE,
    ]),
    ("secondary_antibodies", "Secondary antibodies / nanobodies (one block per reagent)", "list", [
        ("target", "what it binds: anti-<primary host>, or e.g. RFP for a nanobody"),
        ("host", "e.g. donkey, alpaca (nanobody)"),
        ("fluorophore", "e.g. Alexa 647"),
        ("dilution", "e.g. 1:500"),
        ("vendor", ""),
        ("catalog", ""),
        ITEM_NOTE,
    ]),
    ("secondary_incubation", "Secondary incubation", "dict", [
        ("diluent", "e.g. blocking solution"),
        ("duration", "e.g. 1 h"),
        ("temperature", "e.g. RT"),
        ("protected_from_light", "yes / no"),
        STEP_NOTE,
    ]),
    ("washes_after_secondary", "Washes after secondary", "dict", [
        ("buffer", "e.g. PBS"),
        ("scheme", "e.g. 3 x 10 min"),
        STEP_NOTE,
    ]),
    ("other_stains", "Other stains, e.g. nuclear (one block per stain)", "list", [
        ("stain", "e.g. Hoechst 33342"),
        ("dilution", "e.g. 1:1000  (or 5 ug/mL)"),
        ("duration", "e.g. 5 min"),
        ITEM_NOTE,
    ]),
    ("mounting", "Mounting", "dict", [
        ("medium", "e.g. ProLong Glass, Vectashield"),
        ("coverslip", "e.g. #1.5H"),
        STEP_NOTE,
    ]),
    ("channels", "Image channels: what each one shows", "list", [
        ("image_channel", "channel name as stored in the image"),
        ("shows", "pick from what you entered on the other tabs, or type"),
        ("fluorophore", "filled in from the matching secondary when possible"),
    ]),
    ("notes", "Free notes (anything else worth knowing)", "text", []),
]
DATE_FIELDS = {"tissue_collected", "staining_started", "staining_finished", "imaged"}

DILUTION = re.compile(r"^\s*1\s*:\s*\d[\d,.]*\s*$"
                      r"|^\s*(\d+(\.\d*)?|\.\d+)\s*(ng|ug|µg|mg|g|nM|uM|µM|mM|M|U|%)(\s*/\s*m?l)?\s*$", re.I)
DURATION = re.compile(r"\d\s*(s|sec|secs|seconds?|min|mins|minutes?|h|hr|hrs|hours?|d|days?|nights?|wk|weeks?)\b"
                      r"|overnight|\bo/?n\b|\b(mon|tues|wednes|thurs|fri|satur|sun)day\b", re.I)
TEMPERATURE = re.compile(r"\brt\b|room\s*temp|\d+\s*°?\s*c\b|on ice", re.I)
FORMATS = {"dilution": (DILUTION, "a dilution like 1:500 or a concentration like 2 ug/mL"),
           "duration": (DURATION, "a time with units, e.g. 10 min, 2 h, 3 nights, overnight"),
           "temperature": (TEMPERATURE, "e.g. RT, 4 C"),
           "sex": (re.compile(r"^(m|f|male|female|unknown|mixed)$", re.I), "M, F or unknown")}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
for _f in DATE_FIELDS:
    FORMATS[_f] = (ISO_DATE, "a date (pick it from the calendar, or YYYY-MM-DD)")

# usual abbreviations, so 'anti-gp' matches a guinea-pig primary
SPECIES_ALIASES = {"gp": "guinea pig", "guineapig": "guinea pig", "rb": "rabbit", "rbt": "rabbit", "ck": "chicken",
                   "chk": "chicken", "chick": "chicken", "gt": "goat", "ms": "mouse", "mo": "mouse", "rt": "rat",
                   "dk": "donkey", "sh": "sheep", "hu": "human", "hm": "hamster"}
SPECIES = set(SPECIES_ALIASES.values()) | {"horse", "llama", "alpaca", "pig", "cow", "bovine", "armenian hamster",
                                           "syrian hamster", "macaque", "monkey"}


def fields_of(key):
    return next(f for k, _, _, f in SECTIONS if k == key)


# ------------------------------------------------------------------------------------------------- files
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
    return json.dumps("" if v is None else str(v), ensure_ascii=False)


def _text(d, key="notes"):
    v = d.get(key)
    if isinstance(v, dict):
        v = v.get("text")
    return "" if v is None else str(v)


def _line(f, val, hint):
    return f"{f} = {_q(val)}".ljust(46) + (f"  # {hint}" if hint else "")


def normalize_date(s):
    """'10/12/26', '10/12/2026', '2026/10/12' or '2026-10-12' -> '2026-10-12'. Anything else is returned unchanged.
    Slash dates with the year last are read as month/day/year (US)."""
    t = str(s or "").strip()
    if not t or ISO_DATE.match(t):
        return t
    m = re.fullmatch(r"(\d{4})[/.](\d{1,2})[/.](\d{1,2})", t)
    if m:
        y, mo, d = (int(v) for v in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})", t)
        if not m:
            return t
        mo, d, y = (int(v) for v in m.groups())
        y += 2000 if y < 100 else 0
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return t


def normalize(values):
    """Clean values before saving: dates to YYYY-MM-DD."""
    for key, _, kind, fields in SECTIONS:
        if kind == "dict":
            for f, _ in fields:
                if f in DATE_FIELDS and (values.get(key) or {}).get(f):
                    values[key][f] = normalize_date(values[key][f])
    return values


def render(values, title=""):
    """The notes as a commented TOML text file. `values` = dict like the parsed file (may be partial)."""
    L = ["# HISTOLOGY NOTES" + (f" -- {title}" if title else ""),
         "#",
         "# Easiest: edit these in the bigmip app (py -m bigmip.gui). In this text file, type each answer BETWEEN",
         "# THE QUOTES, e.g.   dilution = \"1:500\".   Every field is optional; leave \"\" if unknown.",
         "# Times with units (10 min, 2 h, 3 nights, overnight); dilutions like 1:500 or 2 ug/mL; dates YYYY-MM-DD.",
         "# More antibodies / stains: copy a whole block, from its [[...]] line down to the empty line.", ""]
    for key, label, kind, fields in SECTIONS:
        L.append(f"# ==== {label}")
        if kind == "text":
            txt = _text(values, key).strip("\n")
            L += [f"[{key}]", "text = '''", *(txt.splitlines() if txt else []), "'''", ""]
            continue
        if kind == "dict":
            cur = values.get(key) or {}
            L.append(f"[{key}]")
            L += [_line(f, cur.get(f, ""), hint) for f, hint in fields]
            L.append("")
        else:
            items = values.get(key)
            if items is None:
                items = [{}]
            for item in items:
                L.append(f"[[{key}]]")
                L += [_line(f, (item or {}).get(f, ""), hint) for f, hint in fields]
                L.append("")
    known = {k for k, _, _, _ in SECTIONS}
    for key, v in values.items():                     # sections added by other tools, e.g. [file_history]
        if key not in known and isinstance(v, dict):
            L += [f"# ==== {key.replace('_', ' ')}", f"[{key}]"] + [f"{k} = {_q(x)}" for k, x in v.items()] + [""]
    return "\n".join(L) + "\n"


def save(path, values, title=""):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(normalize(values), title))


def load(path):
    import tomllib
    with open(path, "rb") as fh:
        try:
            return tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            raise SystemExit(f"\nCould not read {path}:\n  {e}\n"
                             "Most often an answer is missing its quotes: write  dilution = \"1:500\"  "
                             "not  dilution = 1:500   (or edit the notes in the bigmip app instead)") from None


# ------------------------------------------------------------------------------------------------- smart options
def _species(s):
    t = re.sub(r"^anti[\s_-]*", "", str(s or "").strip().lower())
    t = re.sub(r"\s+(igg|igy|igm|ig)\b.*$", "", t).strip()
    return SPECIES_ALIASES.get(t.replace(" ", ""), t)


def _binds_species(target):
    """Species a secondary binds ('anti-gp', 'gp' or 'guinea pig' -> 'guinea pig'); None for e.g. a nanobody's RFP."""
    t = str(target or "").strip()
    sp = _species(t)
    return sp if t and (t.lower().startswith("anti") or sp in SPECIES) else None


def _uniq(seq):
    out = []
    for s in seq:
        s = str(s or "").strip()
        if s and s.lower() not in [o.lower() for o in out]:
            out.append(s)
    return out


def secondary_target_options(d):
    """For the secondary 'target' drop-down: anti-<host> of each primary, plus common nanobody targets."""
    hosts = _uniq(p.get("host") for p in d.get("primary_antibodies") or [])
    return _uniq([f"anti-{_species(h)}" for h in hosts] + ["RFP", "GFP", "mCherry"])


def _nanobody_targets(d):
    """Secondary targets that are not a species (e.g. RFP for an anti-RFP nanobody): they show up in a channel."""
    return [s.get("target") for s in d.get("secondary_antibodies") or []
            if s.get("target") and _binds_species(s["target"]) is None]


def shows_options(d):
    """For the channel 'shows' drop-down: everything the notes say could be in an image channel."""
    prim = [p.get("target") for p in d.get("primary_antibodies") or []]
    stains = [f"nuclei ({s.get('stain')})" if re.search(r"hoechst|dapi|draq|sytox|to-?pro", str(s.get("stain")), re.I)
              else s.get("stain") for s in d.get("other_stains") or []]
    return _uniq(prim + _nanobody_targets(d) + stains + ["autofluorescence", "brightfield"])


def fluorophore_options(d):
    return _uniq([s.get("fluorophore") for s in d.get("secondary_antibodies") or []]
                 + [s.get("stain") for s in d.get("other_stains") or []])


def fluorophore_for(d, shows):
    """Fluorophore that goes with a 'shows' choice, from the secondaries / stains entered (or '' if unclear)."""
    shows = str(shows or "").strip()
    if not shows:
        return ""
    secs = d.get("secondary_antibodies") or []
    for p in d.get("primary_antibodies") or []:
        if str(p.get("target", "")).strip().lower() == shows.lower():
            host = _species(p.get("host"))
            hits = [s.get("fluorophore") for s in secs if host and _binds_species(s.get("target")) == host]
            return hits[0] if len(_uniq(hits)) == 1 else ""
    for s in secs:
        if str(s.get("target", "")).strip().lower() == shows.lower():
            return s.get("fluorophore") or ""
    m = re.match(r"nuclei \((.+)\)$", shows)
    stain = m.group(1) if m else shows
    for s in d.get("other_stains") or []:
        if str(s.get("stain", "")).strip().lower() == stain.lower():
            return s.get("stain")
    return ""


# ------------------------------------------------------------------------------------------------- checking
def check(d, image_channels=None):
    """Nothing is required. Returns (issues, suggestions):
    issues      = filled-in entries in an unexpected form (e.g. a time without units, an unreadable date);
    suggestions = possible mix-ups, e.g. a secondary whose target matches no primary host."""
    issues, sugg = [], []
    for key, label, kind, fields in SECTIONS:
        if kind == "text":
            continue
        blocks = [(label, d.get(key) or {})] if kind == "dict" else \
            [(f"{label.split(' (')[0]} #{i}", it or {}) for i, it in enumerate(d.get(key) or [], 1)]
        for where, block in blocks:
            for f, _ in fields:
                s = str(block.get(f) or "").strip()
                if s and f in FORMATS:
                    rx, what = FORMATS[f]
                    if not rx.search(normalize_date(s) if f in DATE_FIELDS else s):
                        issues.append(f"{where}: {f.replace('_', ' ')} '{s}' -- expected {what}")
    hosts = {_species(p.get("host")) for p in d.get("primary_antibodies") or [] if p.get("host")}
    for i, s in enumerate(d.get("secondary_antibodies") or [], 1):
        sp = _binds_species(s.get("target"))
        if sp and hosts and sp not in hosts:
            sugg.append(f"Secondary #{i} binds '{sp}', but no primary antibody has that host "
                        f"(primary hosts: {', '.join(sorted(hosts))})")
        if sp and s.get("host") and _species(s.get("host")) == sp:
            sugg.append(f"Secondary #{i}: host and target are both '{sp}'")
    if image_channels:
        named = [str((c or {}).get("image_channel", "")).strip() for c in (d.get("channels") or [])]
        extra = [c for c in named if c and c not in image_channels]
        if extra:
            sugg.append(f"Image channels: {extra} are not channels of this image")
    return issues, sugg


def fill_stats(d):
    """(filled, total) over the fields of the form (not the notes fields or the pre-filled image channel names).
    Blank antibody / stain blocks don't count, and a section with none counts as one empty block, so the total is the
    same in the form window and in the saved file. Every image channel counts."""
    n = tot = 0
    for key, _, kind, fields in SECTIONS:
        if kind == "text":
            continue
        if kind == "dict":
            blocks = [d.get(key) or {}]
        else:
            blocks = [b for b in d.get(key) or [] if key == "channels"
                      or _any([b], *(f for f, _ in fields if f != "notes"))] or [{}]
        for b in blocks:
            for f, _ in fields:
                if f in ("notes", "image_channel"):
                    continue
                tot += 1
                n += bool(str((b or {}).get(f) or "").strip())
    return n, tot


def _any(blocks, *fields):
    return any(str((b or {}).get(f) or "").strip() for b in blocks for f in fields)


# the record's core: (what it's called, filled in when...). Notes with none of these are missing their substance.
CORE = [("the sample", lambda d: _any([d.get("sample")], "sample_id", "animal_id", "species", "tissue")),
        ("dates", lambda d: _any([d.get("dates"), d.get("sample")], *DATE_FIELDS)),
        ("primary antibodies", lambda d: _any(d.get("primary_antibodies") or [], "target")),
        ("secondary antibodies or stains", lambda d: _any(d.get("secondary_antibodies") or [], "target", "fluorophore")
         or _any(d.get("other_stains") or [], "stain")),
        ("what each image channel shows", lambda d: _any(d.get("channels") or [], "shows"))]
SPARSE_FRACTION, SPARSE_CORE = 0.25, 3       # 'largely missing': < 25% of fields, or 3+ of the 5 core items empty


def completeness(d):
    """Notes are optional, but a missing or mostly empty record is flagged. Returns (level, message):
    level = 'none' (no notes / nothing filled in), 'sparse' (largely missing) or 'ok'; message says what is empty."""
    if not d:
        return "none", "no histology notes"
    n, tot = fill_stats(d)
    missing = [name for name, ok in CORE if not ok(d)]
    if n == 0 and not _text(d).strip():
        return "none", "histology notes empty"
    level = "sparse" if n < SPARSE_FRACTION * tot or len(missing) >= SPARSE_CORE else "ok"
    msg = f"histology notes{' largely missing' if level == 'sparse' else ''}: {n} of {tot} fields filled in"
    return level, msg + (f"; nothing yet on {', '.join(missing)}" if missing else "")


# ------------------------------------------------------------------------------------------------- outputs
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


def _join(block):
    return ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in (block or {}).items() if v)


def summary_text(d):
    """Plain-text summary (embedded in the TIFF; Fiji: Image > Show Info)."""
    L = ["HISTOLOGY NOTES"]
    for key, label, kind, fields in SECTIONS:
        if kind == "text":
            if _text(d, key).strip():
                L.append("Notes: " + " ".join(_text(d, key).split()))
        elif kind == "dict":
            if _join(d.get(key)):
                L.append(f"{label}: {_join(d.get(key))}")
        else:
            for i, it in enumerate(d.get(key) or [], 1):
                if _join(it):
                    L.append(f"{label.split(' (')[0]} {i}: {_join(it)}")
    return "\n".join(L)


def markdown(d):
    """The notes as Markdown tables (used in the metadata report and by --show)."""
    L = ["## Histology", ""]
    level, msg = completeness(d)
    if level != "ok":
        L += [f"**WARNING: {msg}.**", ""]
    for key, label, kind, fields in SECTIONS:
        v = d.get(key)
        if kind == "text":
            if _text(d, key).strip():
                L += [f"**{label.split(' (')[0]}:** {' '.join(_text(d, key).split())}", ""]
            continue
        if kind == "dict":
            row = _join(v)
            if row:
                L += [f"**{label}:** {row}", ""]
        else:
            items = v or []
            cols = [f for f, _ in fields if any((it or {}).get(f) for it in items)]
            if not cols:
                continue
            L += [f"**{label.split(' (')[0]}**", "", "| " + " | ".join(c.replace("_", " ") for c in cols) + " |",
                  "|" + "---|" * len(cols)]
            L += ["| " + " | ".join(str((it or {}).get(c, "")) for c in cols) + " |" for it in items]
            L.append("")
    return "\n".join(L) + "\n"


def use_notes(image, explicit=None, image_channels=None):
    """For bigmip: (notes, path, remarks) if the image has notes, else (None, None, []). Never stops the run;
    remarks = a WARNING when the notes are largely missing, then any unusual entries."""
    p = find_notes(image, explicit)
    if not p:
        return None, None, []
    d = load(p)
    level, msg = completeness(d)
    issues, sugg = check(d, image_channels)
    warn = [f"WARNING: {msg}. Fill them in with:  py -m bigmip.notes \"{image}\""] if level != "ok" else []
    return d, p, warn + [f"note: {s}" for s in issues + sugg]


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
    ap.add_argument("--check", action="store_true", help="list entries that look unusual")
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
        level, msg = completeness(d)
        issues, sugg = check(d, chans)
        print(f"{path}: " + ("WARNING: " if level != "ok" else "") + msg + ".")
        for s in issues:
            print(f"  unusual: {s}")
        for s in sugg:
            print(f"  check:   {s}")
        if not issues and not sugg:
            print("  nothing unusual.")
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
            values["channels"] = carry_channels(values.get("channels"), chans)
        title = os.path.basename(a.target) if not a.folder else f"all images in {os.path.abspath(a.target)}"
        save(path, values, title)
        print(f"Created {path}" + (f"  (protocol copied from {a.src})" if a.src else ""))
        print(f"Fill it in and save. Check with:  py -m bigmip.notes \"{a.target}\"{' --folder' if a.folder else ''} "
              "--check")
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


def carry_channels(old_channels, names):
    """Channel entries for this image's channel names, keeping 'shows' / fluorophore from earlier notes (matched by
    name, or by the first 3 characters, e.g. the laser line)."""
    old = {str((c or {}).get("image_channel", "")): c for c in (old_channels or [])}
    by3 = {str((c or {}).get("image_channel", ""))[:3]: c for c in (old_channels or [])}
    return [dict(old.get(n) or by3.get(n[:3]) or {}, image_channel=n) for n in names]


if __name__ == "__main__":
    main()
