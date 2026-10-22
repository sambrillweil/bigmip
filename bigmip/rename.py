"""Rename an image AND every file bigmip made from it, so the names stay matched.

  bigmip-rename IMAGE NEW_NAME                  e.g.  bigmip-rename "D:\\x\\ID1494_25X_5.ims" ID1494_63X_5
  bigmip-rename IMAGE [IMAGE ...] --replace OLD NEW    e.g.  --replace 25X 63X   (wildcards allowed: "D:\\x\\*25X*.ims")
  bigmip-rename IMAGE --undo                    reverse the last rename of this image (from rename_log.csv)

Related files = files in the image's folder, its 'bigmip_output' folder and any --also FOLDER whose names are the
image name followed by one of bigmip's suffixes (_histology, _MIP..., _metadata, _acquisition_settings). The old name
is also replaced INSIDE those files when they are text (.toml .md .json .csv .txt), except the notes' change log, which
keeps the history as it was (the rename itself is logged there). The image data itself is never changed, only its file
name. A plan is shown and confirmed first; nothing is overwritten.
"""
import argparse
import csv
import datetime
import glob
import os
import sys

SUFFIXES = ("_histology", "_MIP", "_metadata", "_acquisition_settings")
TEXT_EXT = (".toml", ".md", ".json", ".csv", ".txt")
LOG = "rename_log.csv"


def split_stem(name):
    """'a.ome.tif' -> ('a', '.ome.tif');  'a.ims' -> ('a', '.ims')."""
    low = name.lower()
    for ext in (".ome.tiff", ".ome.tif"):
        if low.endswith(ext):
            return name[:-len(ext)], name[-len(ext):]
    stem, ext = os.path.splitext(name)
    return stem, ext


def related_files(image, also=()):
    folder = os.path.dirname(os.path.abspath(image))
    stem, _ = split_stem(os.path.basename(image))
    dirs = [folder, os.path.join(folder, "bigmip_output")] + [os.path.abspath(d) for d in also]
    found = [os.path.abspath(image)]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if p in found or not os.path.isfile(p):
                continue
            s, _ = split_stem(name)
            for prefix in (stem, stem + ".ome"):
                if s.startswith(prefix) and any(s[len(prefix):].startswith(suf) for suf in SUFFIXES):
                    found.append(p)
                    break
    return found, stem


def plan_rename(image, new_stem, also=()):
    files, old_stem = related_files(image, also)
    plan = []
    for p in files:
        d, name = os.path.split(p)
        plan.append((p, os.path.join(d, new_stem + name[len(old_stem):])))
    return plan, old_stem


def _replace_inside(path, old, new):
    if not path.lower().endswith(TEXT_EXT) or path.lower().endswith("_changes.csv"):   # the change log keeps history
        return False
    try:
        with open(path, encoding="utf-8-sig" if path.lower().endswith(".csv") else "utf-8") as fh:
            txt = fh.read()
    except (UnicodeDecodeError, OSError):
        return False
    if old not in txt:
        return False
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(txt.replace(old, new))
    return True


def _note_history(path, old_stem, when):
    """Append the old name to the notes form, so the original name stays on record."""
    if not path.lower().endswith("_histology.toml"):
        return
    with open(path, encoding="utf-8") as fh:
        txt = fh.read()
    line = f'renamed_from = "{old_stem} (renamed {when})"'
    if "[file_history]" in txt:
        txt = txt.replace("[file_history]", "[file_history]\n" + line.replace("renamed_from", "renamed_from_"
                                                                            + when.replace("-", "").replace(":", "")
                                                                            .replace(" ", "_")), 1)
    else:
        txt = txt.rstrip("\n") + "\n\n# ==== File history (added by bigmip-rename)\n[file_history]\n" + line + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(txt)


def _log(folder, rows):
    p = os.path.join(folder, LOG)
    new = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["when", "old_path", "new_path", "batch"])
        w.writerows(rows)


def _short(path, base):
    """Path relative to base when possible (a different drive cannot be made relative)."""
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def do_rename(plan, old_stem, new_stem, assume_yes=False, batch=None):
    """Rename the files in `plan` (the image first). Returns (done, error): the renames made, and an explanation if
    something failed (renaming stops at the first failure; Undo puts back what was done)."""
    clashes = [n for _, n in plan if os.path.exists(n)]
    if clashes:
        return [], "These new names already exist, so nothing was renamed:\n" + "\n".join(f"  {c}" for c in clashes)
    base = os.path.dirname(plan[0][0])
    print(f"Rename '{old_stem}'  ->  '{new_stem}'  ({len(plan)} file(s)):")
    for o, n in plan:
        print(f"  {_short(o, base)}\n      -> {os.path.basename(n)}")
    if not assume_yes:
        ans = input("Go ahead? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Nothing renamed.")
            return [], None
    when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = batch or when
    done, error = [], None
    for o, n in plan:
        try:
            os.rename(o, n)
            done.append((o, n))
        except OSError as e:
            busy = getattr(e, "winerror", None) == 32 or "being used" in str(e)
            error = (f"Could not rename {os.path.basename(o)}: "
                     + ("it is open in another program (Fiji, Imaris, a MIP that is running, or Google Drive still "
                        "uploading it). Close it and try again." if busy else str(e))
                     + (f"\n{len(done)} file(s) were renamed before this; Undo puts them back." if done
                        else "\nNothing was renamed."))
            break
    for _, n in done:
        try:
            _replace_inside(n, old_stem, new_stem)
            _note_history(n, old_stem, when)
        except OSError as e:
            print(f"  (could not update the old name inside {n}: {e})")
    if done:      # one log, in the image's folder (the first file of the plan is always the image)
        _log(os.path.dirname(done[0][1]), [[when, o, n, batch] for o, n in done])
        from .notes import track
        for _, n in done:   # the image's own notes: the rename is a change (file history) in their change log
            if n.lower().endswith("_histology.toml") and os.path.dirname(n) == os.path.dirname(done[0][1]):
                track(n, how=f"renamed {old_stem} -> {new_stem}")
        print(f"Renamed {len(done)} file(s). Undo with:  py -m bigmip.rename \"{done[0][1]}\" --undo")
    if error:
        print("\n" + error)
    return done, error


def undo(image, assume_yes=False):
    """Reverse the most recent rename batch that produced this image (logged in its folder)."""
    folder = os.path.dirname(os.path.abspath(image))
    p = os.path.join(folder, LOG)
    if not os.path.exists(p):
        raise SystemExit(f"no {LOG} in {folder}: nothing to undo")
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    target = os.path.abspath(image)
    batch = next((r["batch"] for r in reversed(rows) if os.path.abspath(r["new_path"]) == target), None)
    if batch is None:
        raise SystemExit(f"{image} was not renamed by bigmip-rename (not in {p})")
    entries = [r for r in rows if r["batch"] == batch]
    old_stem = split_stem(os.path.basename(next(r for r in entries if os.path.abspath(r["new_path"]) == target)["old_path"]))[0]
    new_stem = split_stem(os.path.basename(image))[0]
    plan = [(r["new_path"], r["old_path"]) for r in entries if os.path.exists(r["new_path"])]
    return do_rename(plan, new_stem, old_stem, assume_yes=assume_yes, batch=f"undo of {batch}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bigmip-rename", description="Rename an image and all files bigmip made from it.")
    ap.add_argument("images", nargs="+", help="image file(s); wildcards allowed with --replace. With a NEW_NAME: "
                                              "the image followed by the new name (without extension)")
    ap.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"), help="replace text OLD by NEW in the name(s)")
    ap.add_argument("--also", action="append", default=[], help="another folder with outputs of this image")
    ap.add_argument("--undo", action="store_true", help="reverse the last rename of this image")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args(argv)
    if a.undo:
        for img in a.images:
            if undo(img)[1]:
                raise SystemExit(1)
        return
    if a.replace:
        images = []
        for pat in a.images:
            images += sorted(glob.glob(pat)) if any(ch in pat for ch in "*?[") else [pat]
        if not images:
            raise SystemExit("no files match")
        for img in images:
            if not os.path.isfile(img):
                raise SystemExit(f"file not found: {img}")
            stem = split_stem(os.path.basename(img))[0]
            if a.replace[0] not in stem:
                print(f"skip {os.path.basename(img)}: '{a.replace[0]}' not in its name")
                continue
            new_stem = stem.replace(a.replace[0], a.replace[1])
            plan, old = plan_rename(img, new_stem, a.also)
            if do_rename(plan, old, new_stem, a.yes)[1]:
                raise SystemExit(1)
        return
    if len(a.images) != 2:
        raise SystemExit("give the image and the new name:  bigmip-rename IMAGE NEW_NAME   (or use --replace OLD NEW)")
    img, new_stem = a.images
    if not os.path.isfile(img):
        raise SystemExit(f"file not found: {img}")
    new_stem = split_stem(os.path.basename(new_stem))[0]
    if any(ch in new_stem for ch in '\\/:*?"<>|'):
        raise SystemExit(f"'{new_stem}' contains characters not allowed in file names")
    plan, old = plan_rename(img, new_stem, a.also)
    if do_rename(plan, old, new_stem, a.yes)[1]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
