"""bigmip app: one window for everything -- pick an image, fill in its histology notes, make the MIP, make the
metadata report, rename files.

  py -m bigmip.gui [IMAGE]          (or double-click start_bigmip.bat / start_bigmip.command)

The MIP and the metadata report are made by running the same command-line tools (`py -m bigmip ...`) in the
background; the exact command is shown in the window, so anything done here can be repeated from a terminal.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from .notes import OUTSIDE, changes_path, check, completeness, find_notes, load, notes_path_for, track
from .notes_gui import GREEN, GRAY, ORANGE, RED, NotesForm, ScrollFrame, _dpi_aware, apply_style, open_path

SETTINGS = os.path.join(os.path.expanduser("~"), ".bigmip_settings.json")


def load_settings():
    try:
        with open(SETTINGS, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(d):
    try:
        with open(SETTINGS, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
    except OSError:
        pass


def find_fiji():
    home = os.path.expanduser("~")
    cands = [r"C:\Fiji.app\ImageJ-win64.exe", os.path.join(home, "Fiji.app", "ImageJ-win64.exe"),
             os.path.join(home, "Documents", "Fiji.app", "ImageJ-win64.exe"),
             os.path.join(home, "Desktop", "Fiji.app", "ImageJ-win64.exe"),
             os.path.join(home, "Downloads", "Fiji.app", "ImageJ-win64.exe"),
             "/Applications/Fiji.app", os.path.join(home, "Applications", "Fiji.app")]
    return next((c for c in cands if os.path.exists(c)), "")


def python_exe():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):                # pythonw has no console output; use python.exe
        alt = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            return alt
    return exe


def hexcolor(rgb):
    return "#%02x%02x%02x" % tuple(int(round(255 * max(0, min(1, v)))) for v in rgb)


class App:
    def __init__(self, root, image=None):
        self.root = root
        self.settings = load_settings()
        self.meta = None
        self.proc = None
        self.q = queue.Queue()
        self.notes_form = None
        self.outputs = []
        self.image = None
        root.title("bigmip")
        root.geometry("1120x900")
        root.minsize(900, 680)
        apply_style(root)
        self._build()
        root.after(100, self._poll)
        if image:
            self.set_image(image)

    # ================================================================== layout
    def _build(self):
        r = self.root
        head = ttk.Frame(r, padding=(16, 12, 16, 4))
        head.pack(fill="x")
        ttk.Label(head, text="bigmip", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="   max projections of large stacks, with histology notes and metadata",
                  style="Sub.TLabel").pack(side="left", pady=(6, 0))

        pick = ttk.Frame(r, padding=(16, 4))
        pick.pack(fill="x")
        ttk.Label(pick, text="Image", font=("Segoe UI", 10, "bold")).pack(side="left")
        self.image_var = tk.StringVar()
        e = ttk.Entry(pick, textvariable=self.image_var)
        e.pack(side="left", fill="x", expand=True, padx=8)
        e.bind("<Return>", lambda ev: self.set_image(self.image_var.get()))
        ttk.Button(pick, text="Choose...", command=self.choose_image).pack(side="left")
        ttk.Button(pick, text="Open folder", command=lambda: self.image and open_path(os.path.dirname(self.image))
                   ).pack(side="left", padx=(6, 0))

        self.info = ttk.Label(r, text="Choose an .ims or .tif file to start.", style="Sub.TLabel",
                              padding=(16, 2), justify="left")
        self.info.pack(fill="x")
        self.badge = ttk.Label(r, text="", padding=(16, 0, 16, 4), font=("Segoe UI", 10, "bold"))
        self.badge.pack(fill="x")

        pw = ttk.PanedWindow(r, orient="vertical")
        pw.pack(fill="both", expand=True, padx=12, pady=(2, 0))
        self.nb = ttk.Notebook(pw)
        pw.add(self.nb, weight=4)
        self.tab_notes = ttk.Frame(self.nb, padding=4)
        self.tab_mip = ttk.Frame(self.nb)
        self.tab_meta = ttk.Frame(self.nb, padding=8)
        self.tab_rename = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.tab_notes, text="1  Histology notes")
        self.nb.add(self.tab_mip, text="2  Make MIP")
        self.nb.add(self.tab_meta, text="3  Metadata report")
        self.nb.add(self.tab_rename, text="4  Rename")
        ttk.Label(self.tab_notes, text="Choose an image first.", style="Sub.TLabel").pack(pady=40)
        self._build_mip()
        self._build_meta()
        self._build_rename()

        logf = ttk.Frame(pw)
        pw.add(logf, weight=1)
        bar = ttk.Frame(logf)
        bar.pack(fill="x", pady=(4, 2))
        self.prog = ttk.Progressbar(bar, maximum=1000, length=300)
        self.prog.pack(side="left")
        self.prog_label = ttk.Label(bar, text="", style="Sub.TLabel")
        self.prog_label.pack(side="left", padx=10)
        self.log = tk.Text(logf, height=6, wrap="none", font=("Consolas", 9), relief="solid", borderwidth=1,
                           background="#fafafa")
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    # ---------------------------------------------------------------- MIP tab
    def _build_mip(self):
        go = ttk.Frame(self.tab_mip, padding=(14, 8))
        go.pack(side="bottom", fill="x")                 # buttons stay visible; the options above scroll
        sf = ScrollFrame(self.tab_mip)
        sf.pack(fill="both", expand=True)
        f = sf.inner
        box = ttk.LabelFrame(f, text="What to project", padding=10, style="Section.TLabelframe")
        box.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="Resolution").pack(side="left")
        self.level_var = tk.StringVar()
        self.level_box = ttk.Combobox(row, textvariable=self.level_var, state="readonly", width=34)
        self.level_box.pack(side="left", padx=(8, 20))
        ttk.Label(row, text="Planes").pack(side="left")
        self.z0_var, self.z1_var = tk.StringVar(), tk.StringVar()
        self.z0 = ttk.Spinbox(row, from_=1, to=1, width=6, textvariable=self.z0_var)
        self.z0.pack(side="left", padx=(8, 4))
        ttk.Label(row, text="to").pack(side="left")
        self.z1 = ttk.Spinbox(row, from_=1, to=1, width=6, textvariable=self.z1_var)
        self.z1.pack(side="left", padx=(4, 4))
        self.zinfo = ttk.Label(row, text="", style="Hint.TLabel")
        self.zinfo.pack(side="left", padx=6)

        self.chan_box = ttk.LabelFrame(f, text="Channels: include, color, contrast", padding=10,
                                       style="Section.TLabelframe")
        self.chan_box.pack(fill="x", pady=(0, 10))
        self.chan_rows = []

        out = ttk.LabelFrame(f, text="Outputs", padding=10, style="Section.TLabelframe")
        out.pack(fill="x", pady=(0, 10))
        r1 = ttk.Frame(out)
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="Also smaller copies:").pack(side="left")
        self.ds = {k: tk.BooleanVar(value=(k == 4)) for k in (2, 4, 8)}
        for k, v in self.ds.items():
            ttk.Checkbutton(r1, text=f"{k}x smaller", variable=v, command=self.update_command).pack(side="left",
                                                                                                    padx=6)
        r2 = ttk.Frame(out)
        r2.pack(fill="x", pady=2)
        self.rgb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="Color picture for slides (RGB TIFF + PNG)", variable=self.rgb_var,
                        command=self.update_command).pack(side="left")
        ttk.Label(r2, text="   scale bar (um, 0 = none)").pack(side="left")
        self.bar_var = tk.StringVar(value="0")
        ttk.Entry(r2, textvariable=self.bar_var, width=7).pack(side="left", padx=6)
        self.prev_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r2, text="Small preview picture", variable=self.prev_var,
                        command=self.update_command).pack(side="left", padx=(18, 0))
        r5 = ttk.Frame(out)
        r5.pack(fill="x", pady=2)
        self.notes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r5, text="Include the histology notes", variable=self.notes_var,
                        command=self.update_command).pack(side="left")
        ttk.Label(r5, text="channel names in Fiji, the notes inside the TIFF (Image > Show Info) and a copy of the "
                           "notes file", style="Hint.TLabel").pack(side="left", padx=8)
        r3 = ttk.Frame(out)
        r3.pack(fill="x", pady=2)
        ttk.Label(r3, text="Save to").pack(side="left")
        self.out_var = tk.StringVar()
        ttk.Entry(r3, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(r3, text="Choose...", command=self.choose_outdir).pack(side="left")
        r4 = ttk.Frame(out)
        r4.pack(fill="x", pady=2)
        ttk.Label(r4, text="Parallel workers (.ims)").pack(side="left")
        self.workers_var = tk.StringVar(value=str(max(1, min(8, (os.cpu_count() or 2) - 2))))
        ttk.Spinbox(r4, from_=1, to=max(1, os.cpu_count() or 1), width=5, textvariable=self.workers_var
                    ).pack(side="left", padx=8)
        ttk.Label(r4, text="lower this if the computer runs out of memory", style="Hint.TLabel").pack(side="left")

        cmdbox = ttk.LabelFrame(f, text="The same, as a command (to run in a terminal)", padding=8,
                                style="Section.TLabelframe")
        cmdbox.pack(fill="x", pady=(0, 10))
        self.cmd_text = tk.Text(cmdbox, height=3, wrap="word", font=("Consolas", 9), relief="flat",
                                background="#f3f3f3")
        self.cmd_text.pack(fill="x")
        ttk.Button(cmdbox, text="Copy", command=self.copy_command).pack(anchor="e", pady=(4, 0))

        self.go_btn = ttk.Button(go, text="Make MIP", style="Big.TButton", command=self.make_mip)
        self.go_btn.pack(side="left")
        self.stop_btn = ttk.Button(go, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(go, text="Open output folder", command=self.open_outdir).pack(side="left", padx=8)
        self.fiji_btn = ttk.Button(go, text="Open MIP in Fiji", command=self.open_in_fiji, state="disabled")
        self.fiji_btn.pack(side="left", padx=8)
        fj = ttk.LabelFrame(f, text="Fiji (for the 'Open MIP in Fiji' button)", padding=8,
                            style="Section.TLabelframe")
        fj.pack(fill="x", pady=(4, 0))
        self.fiji_var = tk.StringVar(value=self.settings.get("fiji") or find_fiji())
        ttk.Entry(fj, textvariable=self.fiji_var, width=60).pack(side="left", padx=8)
        ttk.Button(fj, text="Choose Fiji...", command=self.choose_fiji).pack(side="left")
        for v in (self.level_var, self.z0_var, self.z1_var, self.bar_var, self.out_var, self.workers_var):
            v.trace_add("write", lambda *a: self.update_command())

    def _build_channel_rows(self):
        for w in self.chan_box.winfo_children():
            w.destroy()
        self.chan_rows = []
        m = self.meta
        hdr = ttk.Frame(self.chan_box)
        hdr.pack(fill="x")
        for text, w in (("use", 4), ("channel", 30), ("color", 8), ("contrast", 12), ("low", 7), ("high", 7),
                        ("gamma (for pictures)", 20)):
            ttk.Label(hdr, text=text, style="Hint.TLabel", width=w).pack(side="left", padx=3)
        for c, name in enumerate(m["names"]):
            row = ttk.Frame(self.chan_box)
            row.pack(fill="x", pady=2)
            d = {"use": tk.BooleanVar(value=True), "color": hexcolor(m["colors"][c] or (1, 1, 1)),
                 "file_color": hexcolor(m["colors"][c] or (1, 1, 1)), "mode": tk.StringVar(),
                 "lo": tk.StringVar(), "hi": tk.StringVar(), "gamma": tk.StringVar(value="1")}
            ttk.Checkbutton(row, variable=d["use"], command=self.update_command, width=2).pack(side="left", padx=3)
            ttk.Label(row, text=name, width=30).pack(side="left", padx=3)
            btn = tk.Button(row, width=6, relief="solid", borderwidth=1, background=d["color"],
                            activebackground=d["color"])
            btn.configure(command=lambda d=d, b=btn: self.pick_color(d, b))
            btn.pack(side="left", padx=(3, 10))
            stored = m["ranges"][c]
            modes = (["from file"] if stored else []) + ["auto", "custom"]
            d["mode"].set(modes[0])
            cb = ttk.Combobox(row, textvariable=d["mode"], values=modes, state="readonly", width=10)
            cb.pack(side="left", padx=3)
            if stored:
                d["lo"].set(f"{stored[0]:g}"); d["hi"].set(f"{stored[1]:g}")
            for k in ("lo", "hi"):
                ttk.Entry(row, textvariable=d[k], width=8).pack(side="left", padx=3)
            ttk.Entry(row, textvariable=d["gamma"], width=6).pack(side="left", padx=(3, 16))
            for k in ("mode", "lo", "hi", "gamma"):
                d[k].trace_add("write", lambda *a: self.update_command())
            self.chan_rows.append(d)

    def pick_color(self, d, btn):
        res = colorchooser.askcolor(color=d["color"], parent=self.root, title="Channel color")
        if res and res[1]:
            d["color"] = res[1]
            btn.configure(background=res[1], activebackground=res[1])
            self.update_command()

    # ---------------------------------------------------------------- metadata tab
    def _build_meta(self):
        top = ttk.Frame(self.tab_meta)
        top.pack(fill="x")
        self.meta_btn = ttk.Button(top, text="Make metadata report", style="Big.TButton", command=self.make_meta)
        self.meta_btn.pack(side="left")
        ttk.Button(top, text="Open report", command=lambda: self._open_meta("_metadata.md")).pack(side="left",
                                                                                                padx=8)
        ttk.Button(top, text="Open all settings (Excel)",
                   command=lambda: self._open_meta("_acquisition_settings.csv")).pack(side="left")
        ttk.Label(self.tab_meta, text="For .ims files: image, channels, exposure, lasers, objective, camera, Z-scan "
                                      "and the histology notes, as a readable report next to the image.",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 4))
        self.meta_view = tk.Text(self.tab_meta, wrap="none", font=("Consolas", 9), relief="solid", borderwidth=1)
        self.meta_view.pack(fill="both", expand=True)

    def _open_meta(self, suffix):
        if not self.image:
            return
        p = os.path.splitext(self.image)[0] + suffix
        if os.path.exists(p):
            open_path(p)
        else:
            messagebox.showinfo("bigmip", "Make the report first.", parent=self.root)

    # ---------------------------------------------------------------- rename tab
    def _build_rename(self):
        f = self.tab_rename
        ttk.Label(f, text="Rename the image and every file made from it (notes, MIPs, reports). The image data "
                          "itself is not changed.", style="Sub.TLabel").pack(anchor="w")
        g = ttk.Frame(f, padding=(0, 8))
        g.pack(fill="x")
        self.rn_mode = tk.StringVar(value="new")
        ttk.Radiobutton(g, text="New name", variable=self.rn_mode, value="new").grid(row=0, column=0, sticky="w")
        self.rn_new = tk.StringVar()
        ttk.Entry(g, textvariable=self.rn_new, width=70).grid(row=0, column=1, columnspan=3, sticky="ew", padx=8)
        ttk.Radiobutton(g, text="Replace", variable=self.rn_mode, value="replace").grid(row=1, column=0,
                                                                                        sticky="w", pady=4)
        self.rn_a, self.rn_b = tk.StringVar(), tk.StringVar()
        ttk.Entry(g, textvariable=self.rn_a, width=18).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(g, text="with").grid(row=1, column=2, sticky="w")
        ttk.Entry(g, textvariable=self.rn_b, width=18).grid(row=1, column=3, sticky="w", padx=8)
        b = ttk.Frame(f)
        b.pack(fill="x", pady=4)
        ttk.Button(b, text="Preview", command=self.rename_preview).pack(side="left")
        ttk.Button(b, text="Rename", style="Big.TButton", command=self.rename_do).pack(side="left", padx=8)
        ttk.Button(b, text="Undo last rename", command=self.rename_undo).pack(side="left", padx=8)
        self.rn_tree = ttk.Treeview(f, columns=("old", "new"), show="headings", height=12)
        self.rn_tree.heading("old", text="current file (in the image's folder)")
        self.rn_tree.heading("new", text="new name")
        self.rn_tree.column("old", width=520)
        self.rn_tree.column("new", width=420)
        self.rn_tree.pack(fill="both", expand=True, pady=6)

    # ================================================================== image
    def choose_image(self):
        p = filedialog.askopenfilename(parent=self.root, title="Choose an image",
                                       initialdir=self.settings.get("last_dir") or os.path.expanduser("~"),
                                       filetypes=[("Images", "*.ims *.tif *.tiff"), ("All files", "*.*")])
        if p:
            self.set_image(p)

    def set_image(self, p):
        p = os.path.abspath(p.strip().strip('"'))
        if not os.path.isfile(p):
            messagebox.showerror("bigmip", f"File not found:\n{p}", parent=self.root)
            return
        from .readers import open_stack
        self.root.configure(cursor="watch")
        self.root.update()
        try:
            st = open_stack(p)
            self.meta = dict(st.meta)
            st.close()
        except Exception as e:
            self.root.configure(cursor="")
            messagebox.showerror("bigmip", f"Could not read this file:\n{e}", parent=self.root)
            return
        self.root.configure(cursor="")
        self.image = p
        self.image_var.set(p)
        self.settings["last_dir"] = os.path.dirname(p)
        save_settings(self.settings)
        m = self.meta
        Z, Y, X = m["shape"]
        px = m["pixel_um"][0]
        gb = os.path.getsize(p) / 1e9
        parts = [f"{X} x {Y} px, {Z} planes, {len(m['names'])} channels, {m['dtype']}"]
        if px:
            parts.append(f"pixel {px:.4g} um" + (f", plane step {m['z_um']:.3g} um" if m["z_um"] else "")
                         + f",  field {X * px / 1000:.2f} x {Y * px / 1000:.2f} mm")
        parts.append(f"file {gb:.1f} GB")
        self.info.configure(text="    ".join(parts))
        levels = m["levels"]
        self.level_box.configure(values=[f"{'Full resolution' if l == 0 else f'1/{2 ** l} size'} (level {l}): "
                                         f"{max(1, X // 2 ** l)} x {max(1, Y // 2 ** l)} px" for l in levels])
        self.level_box.current(0)
        self.level_box.configure(state="readonly" if len(levels) > 1 else "disabled")
        for sp in (self.z0, self.z1):
            sp.configure(to=Z)
        self.z0_var.set("1")
        self.z1_var.set(str(Z))
        self.zinfo.configure(text=f"of {Z}")
        self.out_var.set(os.path.join(os.path.dirname(p), "bigmip_output"))
        self._build_channel_rows()
        self.rn_new.set(os.path.splitext(os.path.basename(p))[0])
        self._load_notes()
        self.fiji_btn.configure(state="disabled")
        self.outputs = []
        self.update_command()
        self._show_existing_report()
        self.log_line(f"--- {p}")

    def _load_notes(self):
        for w in self.tab_notes.winfo_children():
            w.destroy()
        path = find_notes(self.image) or notes_path_for(self.image)
        holder = ttk.Frame(self.tab_notes)
        holder.pack(fill="both", expand=True)
        self.notes_form = None
        try:
            self.notes_form = NotesForm(holder, self.image, path, list(self.meta["names"]), embedded=True,
                                        on_saved=self.refresh_badge)
        except SystemExit as e:                      # a hand-edited notes file that is not valid TOML
            ttk.Label(holder, text=str(e).strip(), style="Sub.TLabel", justify="left", wraplength=900
                      ).pack(anchor="w", pady=(20, 8))
            ttk.Button(holder, text="Open the notes file in a text editor", command=lambda: open_path(path)
                       ).pack(anchor="w")
            ttk.Button(holder, text="Reload", command=self._load_notes).pack(anchor="w", pady=6)
        self.refresh_badge()

    def notes_state(self):
        """(state, level, message, n_odd): state = 'none' | 'saved' | 'unreadable'; level = notes.completeness()'s
        'none' | 'sparse' | 'ok'. Notes are optional, but missing / largely missing ones are flagged."""
        p = find_notes(self.image)
        if not p:
            return "none", "none", "histology notes: none yet", 0
        logged = track(p)                            # edits made outside bigmip go into the change log
        if logged and logged[0][2] == OUTSIDE:
            self.log_line(f"{len(logged)} change(s) made outside bigmip added to the change log {changes_path(p)}")
        try:
            d = load(p)
        except SystemExit:
            return "unreadable", "none", "histology notes: the notes file could not be read (see tab 1)", 0
        issues, sugg = check(d, list(self.meta["names"]))
        level, msg = completeness(d)
        return "saved", level, msg, len(issues) + len(sugg)

    def refresh_badge(self):
        s, level, msg, odd = self.notes_state()
        txt = msg[0].upper() + msg[1:]
        if level == "none":
            txt, col = txt + ". Optional, but the MIP will carry no staining record (fill them in on tab 1)", RED
        elif level == "sparse":
            col = ORANGE
        else:
            col = ORANGE if (odd or "nothing yet" in msg) else GREEN
        if odd:
            txt += f"; {odd} thing(s) to look at (tab 1, Check)"
        self.badge.configure(text=txt, foreground=col)

    def _show_existing_report(self):
        self.meta_view.delete("1.0", "end")
        p = os.path.splitext(self.image)[0] + "_metadata.md"
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                self.meta_view.insert("1.0", fh.read())
        elif not self.image.lower().endswith(".ims"):
            self.meta_view.insert("1.0", "The metadata report reads .ims files. For TIFFs, the basics are shown at "
                                         "the top of the window.")
        self.meta_btn.configure(state="normal" if self.image.lower().endswith(".ims") else "disabled")

    # ================================================================== MIP
    def mip_args(self):
        a = [self.image]
        if not self.meta:
            return a
        lvl = self.level_box.current()
        if lvl > 0:
            a += ["--level", str(self.meta["levels"][lvl])]
        Z = self.meta["shape"][0]
        try:
            z0, z1 = int(self.z0_var.get()), int(self.z1_var.get())
            if (z0, z1) != (1, Z):
                a += ["--z", str(z0), str(z1)]
        except ValueError:
            pass
        used = [i for i, d in enumerate(self.chan_rows) if d["use"].get()]
        if len(used) != len(self.chan_rows):
            a += ["--channels", ",".join(str(i + 1) for i in used)]
        cols, ranges, gam = [], [], []
        any_col = any_rng = False
        for i in used:
            d = self.chan_rows[i]
            cols.append(d["color"])
            any_col |= d["color"].lower() != d["file_color"].lower()
            stored = self.meta["ranges"][i]
            mode = d["mode"].get()
            if mode == "custom":
                ranges.append(f"{d['lo'].get().strip()}-{d['hi'].get().strip()}")
                any_rng = True
            elif mode == "auto":
                ranges.append("auto")
                any_rng |= bool(stored)          # 'auto' differs from the default only when the file has a range
            else:                                # 'from file' = the default; spelled out if --ranges is needed
                ranges.append(f"{stored[0]:g}-{stored[1]:g}")
            gam.append(d["gamma"].get().strip() or "1")
        if any_col:
            a += ["--colors", ",".join(cols)]
        if any_rng:
            a += ["--ranges", ",".join(ranges)]
        if any(g not in ("1", "1.0") for g in gam):
            a += ["--gamma", ",".join(gam)]
        ks = [str(k) for k, v in self.ds.items() if v.get()]
        if ks:
            a += ["--downsample"] + ks
        if self.rgb_var.get():
            a.append("--rgb")
        try:
            if float(self.bar_var.get() or 0) > 0:
                a += ["--scalebar", self.bar_var.get().strip()]
        except ValueError:
            pass
        if not self.prev_var.get():
            a.append("--no-preview")
        default_out = os.path.join(os.path.dirname(self.image), "bigmip_output")
        if os.path.abspath(self.out_var.get() or default_out) != os.path.abspath(default_out):
            a += ["-o", self.out_var.get()]
        if self.image.lower().endswith(".ims"):
            a += ["--workers", self.workers_var.get()]
        if not self.notes_var.get():
            a.append("--skip-notes")
        return a

    def update_command(self):
        if not getattr(self, "image", None) or not self.meta:
            return
        import re
        q = lambda s: s if re.fullmatch(r"[\w.:\/-]+", s) else f'"{s}"'  # noqa: E731  (quote #, spaces, commas)
        cmd = ("py" if sys.platform.startswith("win") else "python3") + " -m bigmip " +             " ".join(q(x) for x in self.mip_args())
        self.cmd_text.delete("1.0", "end")
        self.cmd_text.insert("1.0", cmd)

    def copy_command(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.cmd_text.get("1.0", "end").strip())

    def make_mip(self):
        if not getattr(self, "image", None):
            messagebox.showinfo("bigmip", "Choose an image first.", parent=self.root)
            return
        if self.notes_form and self.notes_form.dirty and self.notes_var.get():
            if messagebox.askyesno("bigmip", "Save the histology notes first (so the MIP includes them)?",
                                   parent=self.root):
                self.notes_form.save()
        s, level, msg, _ = self.notes_state()
        if self.notes_var.get() and level != "ok" and not messagebox.askyesno(
                "bigmip", f"{msg[0].upper() + msg[1:]}.\n\nThe notes are optional, but without them the MIP carries no "
                          "record of the staining (what each channel shows, antibodies, dilutions, dates).\n\n"
                          "Make the MIP anyway?\n(No = go to the notes tab)", icon="warning", parent=self.root):
            self.nb.select(self.tab_notes)
            return
        self.outputs = []
        self.run_tool(["-m", "bigmip"] + self.mip_args(), "Making the MIP")

    def run_tool(self, args, what):
        if self.proc:
            return
        env = dict(os.environ, BIGMIP_GUI="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = 0x08000000 if sys.platform.startswith("win") else 0     # no extra console window
        self.log_line(f"> py {' '.join(args)}")
        try:
            self.proc = subprocess.Popen([python_exe()] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                                         creationflags=flags)
        except OSError as e:
            messagebox.showerror("bigmip", f"Could not start Python:\n{e}", parent=self.root)
            return
        self.what = what
        self.go_btn.configure(state="disabled")
        self.meta_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.prog.configure(value=0)
        self.prog_label.configure(text=f"{what}...")

        def reader(p=self.proc):
            for line in p.stdout:
                self.q.put(("line", line.rstrip("\n")))
            self.q.put(("exit", p.wait()))
        threading.Thread(target=reader, daemon=True).start()

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.log_line("stopped by user")

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "line":
                    if val.startswith("@@progress"):
                        _, frac, el, left, label = val.split(" ", 4)
                        self.prog.configure(value=float(frac) * 1000)
                        t = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}"  # noqa: E731
                        self.prog_label.configure(text=f"{100 * float(frac):.0f}%   {label}   elapsed {t(el)}"
                                                       + (f"   left ~{t(left)}" if int(left) >= 0 else ""))
                    else:
                        self.log_line(val)
                        if val.strip().startswith("wrote "):
                            self.outputs.append(val.strip()[6:])
                else:
                    ok = val == 0
                    self.proc = None
                    self.go_btn.configure(state="normal")
                    self.meta_btn.configure(state="normal" if self.image.lower().endswith(".ims") else "disabled")
                    self.stop_btn.configure(state="disabled")
                    if ok:
                        self.prog.configure(value=1000)
                    self.prog_label.configure(text=f"{self.what}: {'done' if ok else 'stopped / failed (see log)'}",
                                              foreground=GREEN if ok else RED)
                    if ok and self.what.startswith("Making the MIP"):
                        self.fiji_btn.configure(state="normal")
                    if ok and self.what.startswith("Making the metadata"):
                        self._show_existing_report()
                        self.refresh_badge()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def log_line(self, s):
        self.log.configure(state="normal")
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose_outdir(self):
        d = filedialog.askdirectory(parent=self.root, initialdir=self.out_var.get() or None)
        if d:
            self.out_var.set(d)

    def open_outdir(self):
        d = self.out_var.get()
        if d and os.path.isdir(d):
            open_path(d)

    def choose_fiji(self):
        p = filedialog.askopenfilename(parent=self.root, title="Where is Fiji? (ImageJ-win64.exe, or Fiji.app on a "
                                                               "Mac)")
        if p:
            self.fiji_var.set(p)
            self.settings["fiji"] = p
            save_settings(self.settings)

    def open_in_fiji(self):
        mips = [o for o in self.outputs if o.lower().endswith(".tif") and "_RGB" not in o]
        if not mips:
            return
        target = next((o for o in mips if "smaller" in o), mips[0]) if len(mips) > 1 else mips[0]
        fiji = self.fiji_var.get()
        if not fiji or not os.path.exists(fiji):
            messagebox.showinfo("bigmip", "Tell bigmip where Fiji is (Choose Fiji...), or open the file from Fiji: "
                                          f"\n{target}", parent=self.root)
            return
        self.settings["fiji"] = fiji
        save_settings(self.settings)
        if sys.platform == "darwin" and fiji.endswith(".app"):
            subprocess.Popen(["open", "-a", fiji, target])
        else:
            subprocess.Popen([fiji, target])
        self.log_line(f"opening in Fiji: {target}")

    # ================================================================== metadata
    def make_meta(self):
        if not getattr(self, "image", None):
            return
        if self.notes_form and self.notes_form.dirty and messagebox.askyesno(
                "bigmip", "Save the histology notes first (they go into the report)?", parent=self.root):
            self.notes_form.save()
        self.run_tool(["-m", "bigmip.metadata", self.image], "Making the metadata report")

    # ================================================================== rename
    def _rename_plan(self):
        from .rename import plan_rename, split_stem
        stem = split_stem(os.path.basename(self.image))[0]
        if self.rn_mode.get() == "replace":
            a_, b_ = self.rn_a.get(), self.rn_b.get()
            if not a_ or a_ not in stem:
                messagebox.showinfo("bigmip", f"'{a_}' is not in the name '{stem}'.", parent=self.root)
                return None
            new = stem.replace(a_, b_)
        else:
            new = split_stem(self.rn_new.get().strip())[0]
        if not new or new == stem:
            messagebox.showinfo("bigmip", "Type a different name.", parent=self.root)
            return None
        if any(ch in new for ch in '\\/:*?"<>|'):
            messagebox.showerror("bigmip", "File names cannot contain \\ / : * ? \" < > |", parent=self.root)
            return None
        also = [self.out_var.get()] if self.out_var.get() else []
        plan, old = plan_rename(self.image, new, also)
        return plan, old, new

    def rename_preview(self):
        if not getattr(self, "image", None):
            return None
        res = self._rename_plan()
        self.rn_tree.delete(*self.rn_tree.get_children())
        if not res:
            return None
        plan, old, new = res
        base = os.path.dirname(self.image)
        for o, n in plan:
            shown = os.path.relpath(o, base) if os.path.splitdrive(o)[0] == os.path.splitdrive(base)[0] else o
            self.rn_tree.insert("", "end", values=(shown, os.path.basename(n)))
        return res

    def _busy(self):
        """A MIP / report is running: its files are open, so renaming them would fail."""
        if self.proc:
            messagebox.showinfo("bigmip", f"Wait until '{self.what}' has finished (or Stop it): the files are in "
                                          "use while it runs.", parent=self.root)
        return bool(self.proc)

    def _renamed(self, done, error):
        for o, n in done:
            self.log_line(f"renamed {o} -> {os.path.basename(n)}")
        if error:
            self.log_line(error)
            messagebox.showerror("bigmip", error, parent=self.root)
        if done:
            self.set_image(done[0][1])
            self.rn_tree.delete(*self.rn_tree.get_children())
            if not error:
                messagebox.showinfo("bigmip", f"Renamed {len(done)} file(s).", parent=self.root)

    def rename_do(self):
        if self._busy():
            return
        res = self.rename_preview()
        if not res:
            return
        plan, old, new = res
        if any(os.path.exists(n) for _, n in plan):
            messagebox.showerror("bigmip", "Some new names already exist; nothing was renamed.", parent=self.root)
            return
        if not messagebox.askyesno("bigmip", f"Rename {len(plan)} file(s)\n\n{old}\n->\n{new} ?", parent=self.root):
            return
        if self.notes_form and self.notes_form.dirty:
            self.notes_form.save()
        from .rename import do_rename
        self._renamed(*do_rename(plan, old, new, assume_yes=True))

    def rename_undo(self):
        if not getattr(self, "image", None) or self._busy():
            return
        from .rename import undo
        if not messagebox.askyesno("bigmip", "Undo the last rename of this image?", parent=self.root):
            return
        try:
            res = undo(self.image, assume_yes=True)
        except SystemExit as e:
            messagebox.showinfo("bigmip", str(e), parent=self.root)
            return
        self._renamed(*res)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    _dpi_aware()
    root = tk.Tk()
    App(root, argv[0] if argv else None)
    root.mainloop()


if __name__ == "__main__":
    main()
