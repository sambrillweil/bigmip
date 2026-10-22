"""Tiny synthetic test files in the same layouts as real data: an Imaris .ims (HDF5) and a Fusion-style OME-TIFF."""
import h5py
import numpy as np
import tifffile


def _s(text):
    """Imaris stores attribute strings as arrays of single characters."""
    return np.array([c.encode() for c in str(text)], dtype="S1")


def synthetic_volume(nc=3, nz=7, ny=300, nx=520, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.integers(90, 130, size=(nc, nz, ny, nx)).astype(np.uint16)
    for c in range(nc):                                   # one bright feature per channel at a known plane
        v[c, (c * 2 + 1) % nz, 50 + 20 * c:60 + 20 * c, 100:400] = 3000 + c
    return v


def write_ims(path, vol, pixel_um=0.25, z_um=1.0, colors=None, names=None, chunks=(4, 64, 64)):
    nc, nz, ny, nx = vol.shape
    names = names or [f"{w}_ch" for w in (405, 488, 561, 638)][:nc]
    colors = colors or ["0.000 0.000 1.000", "0.000 1.000 0.000", "1.000 0.000 0.000"][:nc]
    with h5py.File(path, "w") as f:
        info = f.create_group("DataSetInfo/Image")
        for k, v in dict(X=nx, Y=ny, Z=nz, Unit="um", ExtMin0=100.0, ExtMax0=100.0 + nx * pixel_um,
                         ExtMin1=200.0, ExtMax1=200.0 + ny * pixel_um, ExtMin2=5.0, ExtMax2=5.0 + nz * z_um).items():
            info.attrs[k] = _s(v)
        for lev, f_ in enumerate((1, 2)):
            d = vol[:, :, ::f_, ::f_] if f_ > 1 else vol
            if f_ > 1:      # Imaris-style pyramid: 2x2 average in XY (Z kept here)
                y, x = vol.shape[2] // 2 * 2, vol.shape[3] // 2 * 2
                d = vol[:, :, :y, :x].reshape(nc, nz, y // 2, 2, x // 2, 2).mean((3, 5)).astype(np.uint16)
            for c in range(nc):
                g = f.create_group(f"DataSet/ResolutionLevel {lev}/TimePoint 0/Channel {c}")
                pad = [(0, 0), (0, -d.shape[2] % chunks[1]), (0, -d.shape[3] % chunks[2])]
                g.create_dataset("Data", data=np.pad(d[c], pad), chunks=chunks, compression="gzip")
                for k, v in dict(ImageSizeX=d.shape[3], ImageSizeY=d.shape[2], ImageSizeZ=nz).items():
                    g.attrs[k] = _s(v)
        for c in range(nc):
            ci = f.create_group(f"DataSetInfo/Channel {c}")
            ci.attrs["Name"] = _s(names[c]); ci.attrs["Color"] = _s(colors[c])
            ci.attrs["ColorRange"] = _s("90.000 3100.000")


def write_ome_tiff(path, vol, pixel_um=0.151, z_um=0.972, bgr_colors=True):
    """Fusion-style OME-TIFF: CZYX pages, colors stored as 0x00BBGGRR integers."""
    nc, nz, ny, nx = vol.shape
    cols = [0xFF0000, 0x00FF00, 0x0000FF][:nc] if bgr_colors else [-16776961, 16711935, 65535][:nc]
    chans = "".join(f'<OME:Channel ID="Channel:{c}" Name="{(405, 488, 561)[c]}_test" Color="{cols[c]}" '
                    f'SamplesPerPixel="1"/>' for c in range(nc))
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><OME:OME xmlns:OME="http://www.openmicroscopy.org/Schemas/OME/2016-06"{' Creator="Fusion test"' if bgr_colors else ''}>'
           f'<OME:Image ID="Image:0"><OME:Pixels DimensionOrder="XYZCT" Type="uint16" SizeX="{nx}" SizeY="{ny}" '
           f'SizeZ="{nz}" SizeC="{nc}" SizeT="1" PhysicalSizeX="{pixel_um}" PhysicalSizeY="{pixel_um}" '
           f'PhysicalSizeZ="{z_um}">{chans}<OME:TiffData/></OME:Pixels></OME:Image></OME:OME>')
    with tifffile.TiffWriter(path, bigtiff=False) as tw:
        for c in range(nc):
            for z in range(nz):
                tw.write(vol[c, z], description=xml if (c == 0 and z == 0) else None, metadata=None, contiguous=False)


FILLED_NOTES = {
    "sample": {"sample_id": "T1", "experimenters": "A. Tester", "species": "mouse", "animal_id": "M42",
               "strain_or_genotype": "C57BL/6J", "sex": "F", "age_or_date_of_birth": "P60",
               "tissue_collected": "2026-10-01", "tissue": "retina", "preparation": "whole mount"},
    "dates": {"staining_started": "2026-10-02"},
    "fixation": {"fixative": "4% PFA", "duration": "1 h", "temperature": "RT"},
    "blocking": {"solution": "5% NDS, 0.3% TX-100", "duration": "2 h", "temperature": "RT"},
    "primary_antibodies": [{"target": "melanopsin", "host": "chicken", "dilution": "1:10000", "catalog": "X1"}],
    "primary_incubation": {"diluent": "block", "duration": "4 nights", "temperature": "4 C"},
    "washes_after_primary": {"buffer": "PBS", "scheme": "3 x 10 min"},
    "secondary_antibodies": [{"target": "anti-chicken", "host": "donkey", "fluorophore": "Alexa 647",
                              "dilution": "1:500"}],
    "secondary_incubation": {"diluent": "block", "duration": "1 h", "temperature": "RT"},
    "washes_after_secondary": {"buffer": "PBS", "scheme": "3 x 10 min"},
    "other_stains": [{"stain": "Hoechst 33342", "dilution": "1:1000", "duration": "5 min"}],
    "mounting": {"medium": "ProLong Glass"},
    "notes": {"text": "test notes"},
}


def write_notes(path, channel_names, **changes):
    from bigmip.notes import render
    import copy
    d = copy.deepcopy(FILLED_NOTES)
    d["channels"] = [{"image_channel": n, "shows": f"thing{i}", "fluorophore": f"dye{i}"}
                     for i, n in enumerate(channel_names)]
    for k, v in changes.items():
        sec, f = k.split("__")
        d[sec][f] = v
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(d, "test"))
