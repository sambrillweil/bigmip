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
    assert tuple(md["LUTs"][2][:, -1]) == (255, 0, 0)            # file colour kept (channel 3 red)
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
    assert s["channels"][1]["colour"] == "#00ff00" and s["channels"][0]["display range"] == "90 - 3100"
    md = open(written[0], encoding="utf-8").read()
    assert "## Channels" in md and "405_ch" in md


def test_notes_required_and_checked(tmp_path):
    from bigmip.notes import check, load, main as notes_main
    vol = synthetic_volume()
    p = str(tmp_path / "s.ims")
    write_ims(p, vol)
    with pytest.raises(SystemExit):                                  # no notes -> refuses, with instructions
        main([p, "-o", str(tmp_path / "o"), "--workers", "1"])
    notes_main([p, "--text", "--no-open"])                           # creates the blank form
    form = tmp_path / "s_histology.toml"
    d = load(str(form))
    assert [c["image_channel"] for c in d["channels"]] == ["405_ch", "488_ch", "561_ch"]
    errors, _ = check(d, ["405_ch", "488_ch", "561_ch"])
    assert any("sample_id" in e for e in errors) and any("dilution" in e for e in errors)
    with pytest.raises(SystemExit):                                  # incomplete -> refuses
        main([p, "-o", str(tmp_path / "o"), "--workers", "1"])
    write_notes(str(form), ["405_ch", "488_ch", "561_ch"], primary_incubation__duration="four")
    errors, _ = check(load(str(form)), ["405_ch", "488_ch", "561_ch"])
    assert errors == ["Primary incubation: 'duration' = 'four' -- expected a time with units, e.g. 10 min, 2 h, "
                      "3 nights, overnight"]
    write_notes(str(form), ["405_ch", "488_ch", "561_ch"])
    assert check(load(str(form)), ["405_ch", "488_ch", "561_ch"])[0] == []
    notes_main([p, "--check"])                                       # complete -> no exit
    # --from copies the protocol but clears the animal and dates
    p2 = str(tmp_path / "t.ims")
    write_ims(p2, vol)
    notes_main([p2, "--text", "--from", str(form), "--no-open"])
    d2 = load(str(tmp_path / "t_histology.toml"))
    assert d2["primary_antibodies"][0]["dilution"] == "1:10000" and d2["sample"]["sample_id"] == ""
    assert d2["dates"]["staining_started"] == "" and d2["channels"][0]["shows"] == "thing0"


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
