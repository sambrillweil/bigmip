# bigmip

Turn a very large 3-D microscopy stack into a **maximum-intensity projection (MIP)** that keeps the **pixel size
(scale)**, the **channel names** and the **channel colours**. The result is a TIFF that **Fiji** opens as a coloured
multi-channel image with the right scale. Your original file is only ever read, never changed.

bigmip is built for files too big to open normally: a 100–200 GB stitched spinning-disk stack is projected while only
a few blocks are held in memory at a time.

It also keeps your records together:
- **Histology notes:** a form for the staining details (animal, dates, antibodies, dilutions, incubations, washes,
  mounting, what each channel shows). It is required before a MIP is made, and it travels with every output.
- **Metadata report:** a readable summary of what the microscope recorded in an Imaris `.ims` file (channels,
  exposure, laser power, objective, camera, Z-step, ...).
- **Rename:** rename an image and every file made from it in one step, e.g. when the file name has the wrong
  objective in it.

Everything runs in **one window** (the bigmip app), or from the command line. It is plain Python: you need only
Python, a few free Python packages, and Fiji to look at the results.

![The bigmip app](docs/app_mip.png)

---

## Contents
1. [One-time setup](#1-one-time-setup-install)
2. [The bigmip app](#2-the-bigmip-app)
3. [What it reads and writes](#3-what-it-reads-and-writes)
4. [Command line (optional)](#4-command-line-optional)
5. [Looking at the results in Fiji](#5-looking-at-the-results-in-fiji)
6. [Browsing the full 3-D stack in BigDataViewer](#6-browsing-the-full-3-d-stack-in-bigdataviewer)
7. [Troubleshooting](#7-troubleshooting)
8. [How it works](#8-how-it-works)

---

## 1. One-time setup (install)

You do this once per computer. It takes about 10 minutes.

### 1a. Install Python
- **Windows:** go to <https://www.python.org/downloads/>, click the yellow "Download Python 3.x" button and run the
  installer. **On the first screen, tick "Add python.exe to PATH"**, then click "Install Now".
- **Mac:** download the macOS installer from the same page, open it and click through.

bigmip needs Python 3.11 or newer; the current download is fine.

### 1b. Install Fiji
Go to <https://fiji.sc>, download Fiji for your computer and unzip it somewhere you'll find it again (e.g. your
Documents folder). There is no installer: start it by double-clicking `Fiji.app` (Mac) or `ImageJ-win64.exe` inside
the `Fiji.app` folder (Windows). Fiji already includes BigDataViewer.

### 1c. Get bigmip
Either:
- on the GitHub page, click the green **Code** button, then **Download ZIP**, and unzip it (e.g. to
  `Documents\bigmip`); or
- with GitHub Desktop: **File › Clone repository**.

### 1d. Install bigmip
Open a terminal **in the bigmip folder**:
- **Windows:** open the folder in File Explorer, click the address bar, type `powershell` and press Enter.
- **Mac:** open Terminal, type `cd ` (with a space), drag the bigmip folder onto the window, and press Enter.

Then type this and press Enter:

```
py -m pip install .
```

(On a Mac, type `python3` instead of `py`, here and everywhere below.) This downloads the free packages bigmip uses
(numpy, h5py, hdf5plugin, tifffile, matplotlib) and installs bigmip. Setup is done.

---

## 2. The bigmip app

**Start it:** double-click **`start_bigmip.bat`** (Windows) or **`start_bigmip.command`** (Mac) in the bigmip folder.
On a Mac, the first time, right-click it and choose Open. Or type `py -m bigmip.gui` in a terminal.

At the top, click **Choose...** and pick your `.ims` or `.tif` file. bigmip shows the image size, pixel size, plane
step and channels, and whether its histology notes are complete. Then work through the four tabs.

### Tab 1: Histology notes
![Histology notes](docs/app_notes.png)

Fill in the staining details. Bold fields are required, and anything still missing or in the wrong form is red.
- Pick common answers from the drop-downs, or type your own.
- The **Today** buttons fill in dates.
- **+ Add primary antibody** (and similar buttons) add more antibodies or stains.
- On the **Channels** tab, say what each image channel shows (e.g. 638_Cy5 → melanopsin, Alexa 647).
- **Copy protocol from...** loads an earlier sample's notes (protocol, antibodies, channels), so a repeat staining
  only needs the animal and dates changed.
- Click **Save notes**.

The notes are saved as `NAME_histology.toml` next to the image. It's a plain text file and can be opened in any
editor. A `histology.toml` in a folder applies to every image in that folder that has no notes of its own.

**Notes are required:** bigmip asks for them before making a MIP. You can still go ahead without them for a quick look.

### Tab 2: Make MIP
- **Resolution:** `.ims` files contain smaller copies of the data. "1/4 size" is a quick test that takes seconds.
- **Planes:** project only some planes, e.g. to skip a bright surface layer.
- **Channels:** untick a channel to leave it out.
  - Click the colour box to change a channel's colour.
  - Choose the contrast: as stored in the file, automatic, or your own low/high values. Contrast only sets how Fiji
    first displays the image; pixel values are never changed.
- **Outputs:**
  - smaller copies (2×, 4×, 8× smaller; each keeps the brightest pixel of every block, so thin processes survive);
  - a flattened colour picture for slides, with an optional scale bar;
  - a small preview.
- Click **Make MIP**. A progress bar shows % done and time left, and the log shows each step.
- **Open MIP in Fiji** opens the result. The first time, point bigmip to Fiji with **Choose Fiji...**.

The box **"The same, as a command"** shows the exact command that the window runs. You can copy it into a terminal
to repeat the run later or on another computer.

### Tab 3: Metadata report
For `.ims` files: **Make metadata report** writes a readable summary next to the image and shows it. It covers:
- size, pixel size, Z-step and dates;
- each channel's colour, excitation/emission, **exposure, laser line and power, camera gain/bit depth, readout**;
- objective and camera;
- the Z scan;
- the histology notes.

It also writes the complete metadata (`_metadata.json`) and every hardware setting per channel
(`_acquisition_settings.csv`, which opens in Excel).

Andor Fusion stores the settings of every device on the microscope, including unused ones. The report shows the
camera that recorded each channel and the laser lines that were switched on. The full list is in the CSV.

### Tab 4: Rename
![Rename](docs/app_rename.png)

Type a new name, or "Replace ... with ..." (e.g. 25X → 63X). Click **Preview** to see every file that will be
renamed, then **Rename**. This covers:
- the image itself;
- its notes;
- all MIP outputs;
- the metadata report.

The old name is also updated inside the notes and the report. The notes keep a record of the old name, every rename
is logged in `rename_log.csv` next to the image, and **Undo last rename** reverses the last rename. The image data is
never changed, only file names.

---

## 3. What it reads and writes

**Input**
- **`.ims`**: Imaris files, including Andor Fusion / Dragonfly stitched files.
- **`.tif` / `.tiff`**: OME-TIFF (e.g. exported from Fusion) or ImageJ TIFF stacks. Axes Z·Y·X, C·Z·Y·X or Z·C·Y·X,
  one time point.

**Output** goes to a folder called `bigmip_output` next to your image, unless you choose another. Nothing is ever
overwritten: if a file already exists, a new one is written with `_2`, `_3`, ...

| File | What it is |
|---|---|
| `NAME_MIP.tif` | The projection: all channels, full resolution, original pixel values (16-bit stays 16-bit), with colours, names, contrast, scale and the histology notes. **Use this one for analysis and figures.** In Fiji, channel names read like `638_Cy5 | melanopsin (Alexa 647)`, and **Image › Show Info** shows the notes. |
| `NAME_MIP_x4smaller.tif` | The same, 4× smaller (brightest pixel of each 4×4 block). Opens instantly. |
| `NAME_MIP_RGB.tif` / `.png` | One flattened colour picture (8-bit) for slides, optionally with a scale bar. |
| `NAME_MIP_preview.png` | A small picture for a quick look. |
| `NAME_histology.toml` | A copy of the notes the MIP was made with. |

---

## 4. Command line (optional)

Everything in the app can also be done in a terminal, which is useful for scripts and batches. Put file paths in
quotes. On Windows, Shift + right-click a file and choose "Copy as path".

| Command | What it does |
|---|---|
| `py -m bigmip FILE --info` | Show what's in the file (instant). |
| `py -m bigmip FILE` | Make the MIP. |
| `py -m bigmip.notes FILE` | Open the notes form for FILE. `--check` lists what's missing; `--show` prints the notes; `--text` uses a plain-text form instead of the window. |
| `py -m bigmip.metadata FILE` | Make the metadata report (.ims). |
| `py -m bigmip.rename FILE NEW_NAME` | Rename FILE and everything made from it. Or `--replace 25X 63X` (wildcards allowed, e.g. `"D:\data\*25X*.ims"`), and `--undo` to reverse. |
| `py -m bigmip.gui [FILE]` | Open the app. |

Options for `py -m bigmip FILE` (`py -m bigmip --help` lists them all):

| Option | What it does | Example |
|---|---|---|
| `-o FOLDER` | Where to write results. | `-o "D:\results"` |
| `--level N` | `.ims` only: use a smaller copy of the data (1 = half size, 2 = quarter, ...). | `--level 2` |
| `--z FIRST LAST` | Project only planes FIRST to LAST (counting from 1). | `--z 5 30` |
| `--channels LIST` | Keep only some channels (counting from 1). | `--channels 1,3,4` |
| `--colors LIST` | Change colours: a list in channel order, or by channel number / start of the name. Names: `red green blue cyan magenta yellow orange gray`, or hex `#ff8000`. | `--colors "405=cyan,638=magenta"` |
| `--ranges LIST` | Contrast per channel: `auto` or `LOW-HIGH`. | `--ranges "auto,100-800,auto"` |
| `--auto-ranges` | Ignore the contrast stored in the file. | |
| `--downsample K ...` | Also write smaller copies. | `--downsample 2 4` |
| `--rgb` | Also write the flattened colour picture. | |
| `--scalebar UM` | Scale bar of UM microns on the pictures. | `--scalebar 100` |
| `--gamma LIST` | Per-channel gamma for the pictures (below 1 brightens dim structures). | `--gamma 1,1,0.7` |
| `--notes FILE` / `--skip-notes` | Use this notes file / make the MIP without notes. | |
| `--workers N` | `.ims` only: parallel processes (default up to 8). Fewer if memory is short. | `--workers 4` |

In a terminal, a status bar shows % done, the current channel and the time left, and each step is printed with a
timestamp.

---

## 5. Looking at the results in Fiji

1. Drag `NAME_MIP.tif` onto the Fiji window (or **File › Open**). It opens as a coloured composite with one channel
   per colour and the scale in µm.
2. **Turn channels on and off:** **Image › Color › Channels Tool...**
3. **Contrast per channel:** **Image › Adjust › Brightness/Contrast...** (Ctrl+Shift+C). Use the channel slider
   under the image to pick a channel.
4. **Change a channel's colour:** pick the channel with the slider, then **Image › Lookup Tables**.
5. **Histology notes:** **Image › Show Info...**
6. **Check the scale:** **Image › Properties...**
7. **Scale bar:** **Analyze › Tools › Scale Bar...**
8. **Export a picture:** **Image › Color › Stack to RGB**, then **File › Save As › PNG**.
9. **Same crop in two images** (e.g. full-size and `x4smaller`, or two MIPs of the same size): draw a rectangle in
   one, then in the other use **Edit › Selection › Restore Selection** (Ctrl+Shift+E).

**Big files:** a full-resolution MIP of a large tile scan can be several GB. If Fiji runs out of memory, give it more
in **Edit › Options › Memory & Threads...** (e.g. 3/4 of your RAM) and restart Fiji, or open the `x4smaller` file to
look around.

---

## 6. Browsing the full 3-D stack in BigDataViewer

BigDataViewer (part of Fiji) shows the **whole 3-D stack**, not just the projection, even when it's far larger than
your memory. It loads only what's on screen.

### Opening an `.ims` file
**Plugins › BigDataViewer › Open Imaris** (called "Open Imaris (experimental)" in older Fiji), then choose the file.
It opens directly, with no conversion.

### Opening a big TIFF / OME-TIFF
- **Simplest:** **File › Import › TIFF Virtual Stack...**, then **Plugins › BigDataViewer › Open Current Image**.
  Scrolling can be slow for huge files.
- **Faster:** **Plugins › BigStitcher › BigStitcher**. Define a new dataset with the automatic (Bio-Formats) loader
  and choose to load the data virtually. It saves a small `.xml` that BigDataViewer opens quickly next time.

### Moving around
| Key / mouse | Action |
|---|---|
| Mouse wheel, or `,` and `.` | Step through Z |
| Ctrl + Shift + mouse wheel (Mac: Cmd + wheel) | Zoom |
| Right-drag | Pan |
| Left-drag | Rotate (tilts the view); **Shift+Z** puts it back flat |
| **Shift+Z** / **Shift+X** / **Shift+Y** | XY view / side view (ZY) / side view (XZ) |
| **F1** | List of all shortcuts |

### Several channels at once
- **S** opens **Brightness & Color**. Set min/max per channel, and click a channel's colour to change it.
  BigDataViewer may start with every channel white.
- **F6** opens **Visibility & Grouping**.
  - Tick **"enable fused mode"** (or press **F**) to see several channels together. With it off, only the "current"
    channel shows.
  - In fused mode, the **"active in fused"** boxes choose which channels are shown. Untick a channel to hide it.
- **Shift + 1, 2, 3, ...** shows or hides channel 1, 2, 3, ... **1, 2, 3, ...** makes that channel the current one.
- Leave "enable grouping" off.

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `py` "is not recognized", or the Microsoft Store opens | Python isn't installed, or "Add python.exe to PATH" wasn't ticked. Re-run the Python installer, choose "Modify", and enable "Add Python to environment variables". |
| `start_bigmip.bat` says bigmip is not installed | Do step 1d (install) in the bigmip folder. |
| `No module named ...` | `py -m pip install .` again in the bigmip folder. |
| An error about `imagecodecs` with a TIFF | The TIFF is compressed: `py -m pip install imagecodecs` |
| Very slow | The file is probably on a network or cloud drive (Google Drive, a server). Copy it to a local disk, or test first at a smaller resolution. |
| The computer runs out of memory | Use fewer parallel workers. The MIP itself needs about width × height × channels × 2 bytes of memory, e.g. ~3.5 GB for a 22,000 × 21,000 × 4-channel image. |
| Colours look wrong | Change them in the Make MIP tab (or `--colors`). |
| A bright surface layer hides everything | Project fewer planes. |
| Notes: "Could not read ..." | In the text file, every answer must be in quotes: `dilution = "1:500"`. Or edit the notes in the app instead. |
| Fiji shows the image black or white | Image › Adjust › Brightness/Contrast › **Reset**, per channel. |

---

## 8. How it works

- **`.ims`** files are HDF5 files stored in compressed chunks (typically 8 planes × 256 × 256 pixels). bigmip splits
  the image into XY blocks aligned to those chunks and, for each block, reads a few planes at a time while keeping a
  running maximum. Every chunk is read and decompressed once, and several blocks run in parallel.
- **TIFFs** are read one plane at a time.
- **Scale** comes from the file (Imaris: image extents ÷ pixel count; OME-TIFF: `PhysicalSizeX/Y/Z`; ImageJ TIFF:
  resolution tags). It is written as ImageJ calibration in µm.
- **Colours** come from the file: Imaris channel `Color`, OME `Channel Color` (Andor Fusion writes these in BGR
  order, which is detected), or ImageJ LUTs. A channel with no colour gets one guessed from the wavelength in its
  name. Colours are written as ImageJ LUTs, so Fiji shows them automatically.
- **Contrast** comes from the file when stored (Imaris `ColorRange`). Otherwise it's the 0.5th–99.9th percentile of
  the non-zero pixels; zero is excluded because stitched data are zero outside the imaged tiles. It never changes
  pixel values.
- **Histology notes** are a TOML text file, read with Python's built-in `tomllib`. They're checked for required
  fields and formats: dilutions (`1:500` or `2 ug/mL`), times with units, temperatures, and `YYYY-MM-DD` dates.
- **The app** (Tk, built into Python) runs the same commands in the background and shows their output.

| File | Contents |
|---|---|
| `bigmip/readers.py` | reading .ims / TIFF |
| `bigmip/cli.py` | the MIP command |
| `bigmip/output.py` | writing files |
| `bigmip/colors.py` | colours |
| `bigmip/notes.py`, `notes_gui.py` | the histology notes |
| `bigmip/metadata.py` | the metadata report |
| `bigmip/rename.py` | renaming |
| `bigmip/gui.py` | the app |

### Tests
```
py -m pip install pytest
py -m pytest -q
```
The tests build small synthetic `.ims` and OME-TIFF files and check that projections, scale, names, colours, notes,
reports and renaming come out exactly right.

## License
MIT. See `LICENSE`.
