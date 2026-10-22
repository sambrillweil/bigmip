"""Run with:  python -m pytest -q     (from the repository folder)"""
import os
import sys

import numpy as np
import tifffile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bigmip.cli import main  # noqa: E402
from bigmip.colors import color_from_ome, parse_color_list  # noqa: E402
from bigmip.metadata import write_report  # noqa: E402
from bigmip.readers import open_stack  # noqa: E402
from make_test_data import synthetic_volume, write_ims, write_notes, write_ome_tiff  # noqa: E402
import pytest  # noqa: E402


def _read_composite(path):
    t = tifffile.TiffFile(path)
    md = t.imagej_metadata
    xr = t.pages[0].tags["XResolution"].value
    return t.asarray(), md, xr[1] / xr[0]


def test_ims_projection_and_metadata(tmp_path):
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    st = open_stack(p)
    assert st.meta["shape"] == vol.shape[1:]
    assert abs(st.meta["pixel_um"][0] - 0.25) < 1e-9 and abs(st.meta["z_um"] - 1.0) < 1e-9
    assert st.meta["colors"][1] == (0.0, 1.0, 0.0)
    for c in range(vol.shape[0]):
        assert np.array_equal(st.max_projection(c, workers=2, block=128), vol[c].max(0))
    assert np.array_equal(st.max_projection(0, 2, 5, workers=1, block=128), vol[0, 2:5].max(0))
    st.close()


def test_ims_cli_outputs(tmp_path):
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    out = tmp_path / "out"
    write_notes(str(tmp_path / "s_histology.toml"), ["405_ch", "488_ch", "561_ch"])
    main([p, "-o", str(out), "--downsample", "2", "--rgb", "--scalebar", "20", "--workers", "2"])
    arr, md, px = _read_composite(str(out / "s_MIP.tif"))
    assert np.array_equal(arr, vol.max(1))
    assert abs(px - 0.25) < 1e-6 and md["unit"] == "micron"
    assert md["Labels"][0] == "405_ch | thing0 (dye0)"            # notes label the channels
    assert "melanopsin" in md["Info"] and (out / "s_histology.toml").exists()
    assert tuple(md["LUTs"][2][:, -1]) == (255, 0, 0)            # file color kept (channel 3 red)
    small, _, px2 = _read_composite(str(out / "s_MIP_x2smaller.tif"))
    assert small.shape[1:] == (vol.shape[2] // 2, vol.shape[3] // 2) and abs(px2 - 0.5) < 1e-6
    assert (out / "s_MIP_RGB.tif").exists() and (out / "s_MIP_preview.png").exists()
    # never overwrite
    main([p, "-o", str(out), "--no-preview", "--workers", "1"])
    assert (out / "s_MIP_2.tif").exists() and (out / "s_histology_2.toml").exists()


def test_level_and_recolor(tmp_path):
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    out = tmp_path / "o"
    main([p, "-o", str(out), "--level", "1", "--colors", "2=magenta", "--channels", "1,2", "--no-preview",
          "--workers", "1", "--z", "2", "4", "--skip-notes"])
    arr, md, px = _read_composite(str(out / "s_MIP_z2-4_level1.tif"))
    assert arr.shape[0] == 2 and abs(px - 0.5) < 1e-6
    assert tuple(md["LUTs"][1][:, -1]) == (255, 0, 255)


def test_ome_tiff_standard_rgba(tmp_path):
    vol = synthetic_volume(nc=3, nz=3, ny=64, nx=80)
    p = str(tmp_path / "g.ome.tif")
    write_ome_tiff(p, vol, bgr_colors=False)
    st = open_stack(p)
    assert st.meta["colors"] == [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    st.close()


def test_ome_tiff(tmp_path):
    vol = synthetic_volume(nc=3, nz=5, ny=200, nx=260)
    p = str(tmp_path / "f.ome.tif")
    write_ome_tiff(p, vol)
    st = open_stack(p)
    assert st.meta["shape"] == vol.shape[1:]
    assert st.meta["names"] == ["405_test", "488_test", "561_test"]
    assert st.meta["colors"][0] == (0.0, 0.0, 1.0)                # 0x00BBGGRR decoded: DAPI blue
    assert abs(st.meta["pixel_um"][0] - 0.151) < 1e-9
    for c in range(3):
        assert np.array_equal(st.max_projection(c), vol[c].max(0))
    st.close()
    out = tmp_path / "o"
    main([p, "-o", str(out), "--colors", "gray,green,magenta", "--ranges", "auto,100-500,auto", "--no-preview",
          "--skip-notes"])
    arr, md, px = _read_composite(str(out / "f.ome_MIP.tif"))
    assert np.array_equal(arr, vol.max(1)) and abs(px - 0.151) < 1e-6
    assert md["Ranges"][2:4] == (100.0, 500.0)


def test_color_helpers():
    assert color_from_ome(-16776961) == (1.0, 0.0, 0.0)            # RGBA 0xFF0000FF = red
    assert color_from_ome(16711935) == (0.0, 1.0, 0.0)             # RGBA 0x00FF00FF = green
    # Fusion values from a real file: 405 blue, 488 green, 561 orange, 638 red (BGR layout)
    assert color_from_ome(16718336, bgr=True) == (0.0, 26 / 255, 1.0)
    assert color_from_ome(65320, bgr=True) == (40 / 255, 1.0, 0.0)
    assert color_from_ome(51455, bgr=True) == (1.0, 200 / 255, 0.0)
    assert color_from_ome(219, bgr=True) == (219 / 255, 0.0, 0.0)
    assert parse_color_list("405=cyan", ["405_DAPI", "488_GFP"]) == {0: (0.0, 1.0, 1.0)}
    assert parse_color_list("blue,#ff8000", ["a", "b"])[1] == (1.0, 128 / 255, 0.0)


def test_metadata_report(tmp_path):
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    s, written = write_report(p, str(tmp_path / "meta"))
    assert s["image"]["size (X x Y x Z)"] == "520 x 300 x 7"
    assert s["channels"][1]["color"] == "#00ff00" and s["channels"][0]["display range"] == "90 - 3100"
    md = open(written[0], encoding="utf-8").read()
    assert "## Channels" in md and "405_ch" in md


def test_notes_optional_and_checked(tmp_path, capsys):
    from bigmip.notes import check, completeness, load, main as notes_main
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    chans = ["405_ch", "488_ch", "561_ch"]
    main([p, "-o", str(tmp_path / "o"), "--workers", "1", "--no-preview"])     # no notes: the MIP is made anyway
    assert "WARNING: no histology notes" in capsys.readouterr().out          # ... but flagged
    arr, md, _ = _read_composite(str(tmp_path / "o" / "s_MIP.tif"))
    assert list(md["Labels"]) == chans and not (tmp_path / "o" / "s_histology.toml").exists()
    notes_main([p, "--text", "--no-open"])                           # creates the blank form
    form = tmp_path / "s_histology.toml"
    d = load(str(form))
    assert [c["image_channel"] for c in d["channels"]] == chans
    assert check(d, chans) == ([], [])                               # empty fields are fine: nothing is required
    assert completeness(d) == ("none", "histology notes empty")      # but an empty record is flagged
    main([p, "-o", str(tmp_path / "o1"), "--workers", "1", "--no-preview"])
    assert "WARNING: histology notes empty" in capsys.readouterr().out
    write_notes(str(form), chans, primary_incubation__duration="four")
    issues, sugg = check(load(str(form)), chans)
    assert issues == ["Primary incubation: duration 'four' -- expected a time with units, e.g. 10 min, 2 h, "
                      "3 nights, overnight"] and sugg == []
    main([p, "-o", str(tmp_path / "o2"), "--workers", "1", "--no-preview"])    # an odd entry never blocks a MIP
    assert (tmp_path / "o2" / "s_histology.toml").exists()
    write_notes(str(form), chans)
    assert check(load(str(form)), chans) == ([], [])
    notes_main([p, "--check"])
    # --from copies the protocol but clears the animal and dates
    p2 = str(tmp_path / "t.ims")
    write_ims(p2, vol)
    notes_main([p2, "--text", "--from", str(form), "--no-open"])
    d2 = load(str(tmp_path / "t_histology.toml"))
    assert d2["primary_antibodies"][0]["dilution"] == "1:10000" and d2["sample"]["sample_id"] == ""
    assert d2["dates"]["staining_started"] == "" and d2["channels"][0]["shows"] == "thing0"


def test_notes_dates_formats_and_smart_options(tmp_path):
    from make_test_data import FILLED_NOTES
    from bigmip.notes import (check, completeness, fluorophore_for, load, normalize_date, save,
                              secondary_target_options, shows_options)
    assert normalize_date("10/12/26") == "2026-10-12" == normalize_date("10/12/2026")      # US month/day/year
    assert normalize_date("2026/10/12") == "2026-10-12" == normalize_date("2026-10-12")
    assert normalize_date("13/40/26") == "13/40/26" and normalize_date("last week") == "last week"
    f = str(tmp_path / "a_histology.toml")
    save(f, {"dates": {"staining_started": "10/12/26", "imaged": "10/21/2026"}})
    assert load(f)["dates"] == dict(load(f)["dates"], staining_started="2026-10-12", imaged="2026-10-21")
    d = {"dates": {"imaged": "10/21/26"}, "fixation": {"duration": "monday - friday"},
         "other_stains": [{"stain": "hoechst", "dilution": ".005mg/ml", "duration": "5min"}],
         "blocking": {"duration": "1h", "temperature": "rt"}}
    assert check(d) == ([], [])                                       # unusual-looking but valid entries
    # completeness: 'largely missing' = < 25% of fields or 3+ of the 5 core items empty
    assert completeness(d)[0] == "sparse"                             # no sample, primaries or channels
    assert completeness({"sample": {"sample_id": "ID1"}, "notes": {"text": ""}}) == (
        "sparse", "histology notes largely missing: 1 of 54 fields filled in; nothing yet on dates, primary "
                  "antibodies, secondary antibodies or stains, what each image channel shows")
    full = dict(FILLED_NOTES, channels=[{"image_channel": "a", "shows": "melanopsin"}])
    assert completeness(full)[0] == "ok" and "nothing yet" not in completeness(full)[1]
    full["channels"] = [{"image_channel": "a", "shows": ""}]
    assert completeness(full)[0] == "ok" and completeness(full)[1].endswith("nothing yet on what each image "
                                                                            "channel shows")
    assert check({"other_stains": [{"dilution": "1/500"}]})[0]        # 1/500 is not a dilution format
    # drop-downs and auto-filled fluorophores, from what was typed on the other tabs (aliases: gp = guinea pig)
    d = dict(FILLED_NOTES, primary_antibodies=[
        {"target": "melanopsin", "host": "chicken"}, {"target": "beta3-tubulin", "host": "guinea pig"}],
        secondary_antibodies=[{"target": "chicken", "fluorophore": "Alexa 647"},
                              {"target": "anti-gp", "fluorophore": "Alexa 488"},
                              {"target": "RFP", "host": "alpaca", "fluorophore": "Alexa 594"}])
    assert secondary_target_options(d)[:2] == ["anti-chicken", "anti-guinea pig"]
    assert shows_options(d)[:4] == ["melanopsin", "beta3-tubulin", "RFP", "nuclei (Hoechst 33342)"]
    assert fluorophore_for(d, "melanopsin") == "Alexa 647" and fluorophore_for(d, "beta3-tubulin") == "Alexa 488"
    assert fluorophore_for(d, "RFP") == "Alexa 594" and fluorophore_for(d, "nuclei (Hoechst 33342)") == "Hoechst 33342"
    assert check(d) == ([], [])
    # a secondary against a host no primary has is pointed out (e.g. anti-guinea pig with a goat primary)
    d["primary_antibodies"][1]["host"] = "goat"
    d["secondary_antibodies"][1].update(target="gp", host="chicken")
    assert check(d)[1] == ["Secondary #2 binds 'guinea pig', but no primary antibody has that host "
                           "(primary hosts: chicken, goat)"]
    assert fluorophore_for(d, "beta3-tubulin") == ""                  # no matching secondary: left for the user


@pytest.fixture(scope="module")
def _tk():
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display ({e})")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def tk_root(_tk):
    """One hidden window shared by the window tests (a 2nd Tk interpreter in the same run can fail to load ttk)."""
    yield _tk
    for w in _tk.winfo_children():
        w.destroy()


def test_notes_form_window(tmp_path, tk_root):
    from make_test_data import FILLED_NOTES
    from bigmip.notes import load, save
    from bigmip.notes_gui import NotesForm
    f = str(tmp_path / "s_histology.toml")
    chans = ["405_ch", "488_ch", "561_ch"]
    d = dict(FILLED_NOTES, dates={"staining_started": "10/12/26"},
             channels=[{"image_channel": "561_ch", "shows": "melanopsin"}])
    save(f, d)
    form = NotesForm(tk_root, str(tmp_path / "s.ims"), f, chans)      # cards with drop-downs fed by other cards
    got = form.collect()
    assert got["secondary_antibodies"][0]["target"] == "anti-chicken" and got["dates"]["staining_started"] == \
        "2026-10-12"
    assert [c["image_channel"] for c in got["channels"]] == chans
    form._auto_fluor(2)                                                # melanopsin -> the anti-chicken secondary
    assert form.vars[("channels", 2, "fluorophore")].get() == "Alexa 647"
    form.vars[("primary_incubation", None, "duration")].set("four")
    assert form.labels[("primary_incubation", None, "duration")].cget("style") == "Odd.TLabel"
    assert "1 thing(s) to look at" in form.status.cget("text") and form.dirty
    form.add_item("secondary_antibodies", {"target": "RFP", "host": "alpaca", "fluorophore": "Alexa 594"})
    form.vars[("dates", None, "imaged")].set("10/21/26")
    assert form.save() and not form.dirty
    saved = load(f)
    assert saved["dates"]["imaged"] == "2026-10-21" and saved["secondary_antibodies"][1]["target"] == "RFP"
    assert saved["channels"][2]["fluorophore"] == "Alexa 647" and saved["primary_incubation"]["duration"] == "four"


def test_app_notes_optional(tmp_path, tk_root):
    from bigmip.gui import App
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    app = App(tk_root, p)
    assert app.badge.cget("text").startswith("Histology notes: none yet. Optional, but the MIP will carry no")
    assert "--skip-notes" not in app.mip_args()
    app.notes_var.set(False)
    assert app.mip_args()[-1] == "--skip-notes"
    write_notes(str(tmp_path / "s_histology.toml"), ["405_ch", "488_ch", "561_ch"],
                primary_incubation__duration="four")
    app.set_image(p)
    assert app.badge.cget("text").endswith("fields filled in; 1 thing(s) to look at (tab 1, Check)")
    (tmp_path / "s_histology.toml").write_text('[sample]\nsample_id = ID1\n', encoding="utf-8")
    app.set_image(p)                                                   # a broken notes file does not stop the app
    assert app.notes_form is None and "could not be read" in app.badge.cget("text")


def test_unquoted_value_gives_clear_error(tmp_path):
    from bigmip.notes import load
    f = tmp_path / "bad_histology.toml"
    f.write_text('[sample]\nsample_id = ID1\n', encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        load(str(f))
    assert "quotes" in str(e.value)


def test_rename_and_undo(tmp_path):
    from bigmip.metadata import write_report
    from bigmip.notes import load
    from bigmip.rename import main as rename_main
    vol = synthetic_volume()
    p = str(tmp_path / "ID1_25X_s.ims")
    write_ims(p, vol)
    write_notes(str(tmp_path / "ID1_25X_s_histology.toml"), ["405_ch", "488_ch", "561_ch"])
    main([p, "--workers", "1", "--downsample", "2"])                 # outputs in bigmip_output/
    write_report(p)                                                    # _metadata.md/.json next to the image
    (tmp_path / "ID1_25X_s_crop.ims").write_bytes(b"x")              # another image: must NOT be touched
    rename_main([p, "--replace", "25X", "63X", "--yes"])
    new = tmp_path / "ID1_63X_s.ims"
    assert new.exists() and not os.path.exists(p)
    out = tmp_path / "bigmip_output"
    assert (out / "ID1_63X_s_MIP.tif").exists() and (out / "ID1_63X_s_MIP_x2smaller.tif").exists()
    assert (out / "ID1_63X_s_histology.toml").exists() and (tmp_path / "ID1_63X_s_histology.toml").exists()
    md = (tmp_path / "ID1_63X_s_metadata.md").read_text(encoding="utf-8")
    assert "ID1_63X_s.ims" in md and "25X" not in md
    hist = load(str(tmp_path / "ID1_63X_s_histology.toml"))
    assert hist["file_history"]["renamed_from"].startswith("ID1_25X_s")
    assert (tmp_path / "ID1_25X_s_crop.ims").exists()                 # unrelated file untouched
    assert (tmp_path / "rename_log.csv").exists()
    import builtins
    orig = builtins.input
    builtins.input = lambda *_: "y"
    try:
        rename_main([str(new), "--undo"])
    finally:
        builtins.input = orig
    assert os.path.exists(p) and (out / "ID1_25X_s_MIP.tif").exists()


def test_rename_reports_problems(tmp_path, monkeypatch):
    from bigmip.rename import do_rename, plan_rename
    p = tmp_path / "a.ims"
    p.write_bytes(b"x")
    (tmp_path / "a_metadata.md").write_text("a.ims", encoding="utf-8")
    (tmp_path / "b.ims").write_bytes(b"y")
    plan, old = plan_rename(str(p), "b")
    done, error = do_rename(plan, old, "b", assume_yes=True)          # new name taken: nothing renamed, explained
    assert done == [] and "already exist" in error and p.exists()
    real = os.rename

    def busy(o, n):                                                    # 2nd file open in another program
        if o.endswith(".md"):
            raise PermissionError(13, "The process cannot access the file because it is being used by another "
                                      "process")
        real(o, n)
    monkeypatch.setattr(os, "rename", busy)
    plan, old = plan_rename(str(p), "c")
    done, error = do_rename(plan, old, "c", assume_yes=True)
    assert len(done) == 1 and (tmp_path / "c.ims").exists()
    assert "open in another program" in error and "Undo puts them back" in error


def _log_rows(path):
    import csv
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_change_log(tmp_path, monkeypatch):
    import copy
    from make_test_data import FILLED_NOTES
    from bigmip.notes import OUTSIDE, changes_path, load, render, save, track
    from bigmip.rename import main as rename_main
    vol = synthetic_volume()
    img = str(tmp_path / "ID1_25X_s.ims")
    write_ims(img, vol)
    f = str(tmp_path / "ID1_25X_s_histology.toml")
    log = changes_path(f)
    assert log == str(tmp_path / "ID1_25X_s_histology_changes.csv")
    d = copy.deepcopy(FILLED_NOTES)
    d["primary_antibodies"].append({"target": "beta3-tubulin", "host": "guinea pig", "dilution": "1:500"})
    save(f, d)                                                          # created: every filled field logged
    rows = _log_rows(log)
    assert {r["how"] for r in rows} == {"created"} and rows[0]["who"]
    assert ["Primary antibodies", "#2", "target", "", "beta3-tubulin"] in \
        [[r["section"], r["entry"], r["field"], r["old value"], r["new value"]] for r in rows]
    assert save(f, load(f)) is None and len(_log_rows(log)) == len(rows)   # saving unchanged notes logs nothing
    # an edit in the app
    d2 = load(f)
    d2["primary_incubation"]["duration"] = "3 nights"
    del d2["primary_antibodies"][0]                                     # remove antibody 1 of 2
    save(f, d2)
    new = _log_rows(log)[len(rows):]
    got = [[r["how"], r["section"], r["entry"], r["field"], r["old value"], r["new value"]] for r in new]
    assert ["bigmip app", "Primary incubation", "", "duration", "4 nights", "3 nights"] in got
    assert ["bigmip app", "Primary antibodies", "#1", "(whole entry removed)",
            "target: melanopsin, host: chicken, dilution: 1:10000, catalog: X1", ""] in got
    assert len(got) == 2                                                # antibody 2 is not reported as edited
    # an edit in a text editor is found the next time bigmip opens the notes
    d3 = load(f)
    d3["mounting"]["medium"] = "Vectashield"
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(render(d3))
    n0 = len(_log_rows(log))
    logged = track(f)
    assert [r[2:] for r in logged] == [[OUTSIDE, "Mounting", "", "medium", "ProLong Glass", "Vectashield"]]
    assert track(f) == [] and len(_log_rows(log)) == n0 + 1             # logged once
    # a MIP adds a row naming the MIP; the change log is not copied into the output
    main([img, "--workers", "1", "--no-preview"])
    last = _log_rows(log)[-1]
    assert last["how"] == "MIP made with these notes" and last["new value"].endswith("ID1_25X_s_MIP.tif")
    # rename carries the log and the last-logged copy; old names inside the log are kept; the rename is logged
    rename_main([img, "--replace", "25X", "63X", "--yes"])
    log2 = str(tmp_path / "ID1_63X_s_histology_changes.csv")
    assert os.path.exists(log2) and os.path.exists(str(tmp_path / "ID1_63X_s_histology_lastlogged.toml"))
    rows2 = _log_rows(log2)
    assert "ID1_25X_s_MIP.tif" in rows2[-2]["new value"]                 # history unchanged
    assert rows2[-1]["how"] == "renamed ID1_25X_s -> ID1_63X_s" and rows2[-1]["section"] == "file history"
    assert not os.path.exists(str(tmp_path / "bigmip_output" / "ID1_63X_s_histology_changes.csv"))
    # a log that can't be written (e.g. open in Excel) never stops a save; the change is logged later
    f2 = str(tmp_path / "b_histology.toml")
    save(f2, {"sample": {"sample_id": "B"}})
    import bigmip.notes as notes_mod
    real = notes_mod._write_log

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(notes_mod, "_write_log", locked)
    save(f2, {"sample": {"sample_id": "B2"}})
    assert load(f2)["sample"]["sample_id"] == "B2"
    monkeypatch.setattr(notes_mod, "_write_log", real)
    late = track(f2)
    assert [r[2:] for r in late] == [[OUTSIDE, "Sample and animal", "", "sample_id", "B", "B2"]]


def test_change_log_starts_for_existing_notes(tmp_path):
    from bigmip.notes import changes_path, track
    f = str(tmp_path / "x_histology.toml")
    write_notes(f, ["a"])                                               # notes made before the change log existed
    rows = track(f)
    assert rows[0][2] == "change log started" and "earlier changes not recorded" in rows[0][7]
    assert os.path.exists(changes_path(f)) and track(f) == []
