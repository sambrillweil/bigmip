"""A form window for the histology notes (built-in Tk; nothing extra to install).

  bigmip-notes IMAGE          opens this window for IMAGE's notes (creates them if needed)
  bigmip-notes                asks which image first

It reads and writes the same <name>_histology.toml file as the text form, so both can be used interchangeably.
"""
import datetime
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .notes import SECTIONS, check, find_notes, load, notes_path_for, render

SUGGEST = {
    "species": ["mouse", "rat", "macaque", "marmoset", "human", "zebrafish"],
    "sex": ["M", "F", "unknown"],
    "temperature": ["RT", "4 C", "37 C", "on ice"],
    "preparation": ["whole mount", "cryosection", "vibratome section", "paraffin section"],
    "tissue": ["retina", "brain", "optic nerve", "spinal cord"],
    "eye": ["left", "right"],
    "light_condition": ["dark adapted overnight", "light adapted"],
    "host": ["chicken", "rabbit", "goat", "guinea pig", "mouse", "rat", "donkey", "sheep", "alpaca"],
    "fluorophore": ["Alexa 405", "Alexa 488", "Alexa 568", "Alexa 594", "Alexa 647", "Cy3", "Cy5", "Hoechst", "DAPI"],
    "dilution": ["1:100", "1:250", "1:500", "1:1000", "1:2000", "1:5000", "1:10000"],
    "duration": ["5 min", "10 min", "30 min", "1 h", "2 h", "overnight", "2 nights", "3 nights", "4 nights"],
    "scheme": ["3 x 5 min", "3 x 10 min", "3 x 15 min", "3 x 30 min"],
    "buffer": ["PBS", "PBS + 0.1% Triton X-100", "PBST"],
    "diluent": ["blocking solution", "PBS + 0.3% Triton X-100"],
    "fixative": ["4% PFA in PBS", "4% PFA", "methanol"],
    "medium": ["ProLong Glass", "ProLong Gold", "Vectashield", "Fluoromount-G"],
    "stain": ["Hoechst 33342", "DAPI"],
    "coverslip": ["#1.5", "#1.5H"],
    "protected_from_light": ["yes", "no"],
}
DATE_FIELDS = {"tissue_collected", "staining_started", "staining_finished", "imaged"}
TABS = [("Sample & dates", ["sample", "dates"]),
        ("Fixation & blocking", ["fixation", "blocking"]),
        ("Primary antibodies", ["primary_antibodies", "primary_incubation", "washes_after_primary"]),
        ("Secondary antibodies", ["secondary_antibodies", "secondary_incubation", "washes_after_secondary"]),
        ("Other stains & mounting", ["other_stains", "mounting"]),
        ("Channels", ["channels"]),
        ("Notes", ["notes"])]
SPEC = {k: (label, kind, fields) for k, label, kind, fields in SECTIONS}
RED, GREY, ACCENT = "#c62828", "#6b6b6b", "#1f5fa8"


class ScrollFrame(ttk.Frame):
    """A frame with a vertical scrollbar (for long tabs)."""

    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=(14, 10))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")


def apply_style(root):
    """Shared look for every bigmip window."""
    st = ttk.Style(root)
    for theme in ("vista", "aqua", "clam"):
        if theme in st.theme_names():
            st.theme_use(theme)
            break
    st.configure("Head.TLabel", font=("Segoe UI", 15, "bold"))
    st.configure("Sub.TLabel", foreground=GREY)
    st.configure("Section.TLabelframe.Label", font=("Segoe UI", 11, "bold"), foreground=ACCENT)
    st.configure("Req.TLabel", font=("Segoe UI", 10, "bold"))
    st.configure("Opt.TLabel", font=("Segoe UI", 10))
    st.configure("Bad.TLabel", font=("Segoe UI", 10, "bold"), foreground=RED)
    st.configure("Hint.TLabel", foreground=GREY, font=("Segoe UI", 9))
    st.configure("TNotebook.Tab", padding=(12, 6), font=("Segoe UI", 10))
    st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 6))
    return st


class NotesForm:
    """The notes editor. Standalone (its own window) or embedded in a frame of the main bigmip app."""

    def __init__(self, root, image=None, path=None, image_channels=None, embedded=False, on_saved=None):
        self.root, self.image, self.path = root, image, path
        self.embedded, self.on_saved = embedded, on_saved
        self.win = root.winfo_toplevel()
        self.image_channels = image_channels or []
        self.vars = {}          # (section, index or None, field) -> StringVar
        self.labels = {}        # same key -> label widget (turns red when invalid)
        self.lists = {}         # list section -> container frame
        self.notes_text = None
        self.extra = {}         # sections not edited here (e.g. file_history), written back unchanged
        self.dirty = False
        values = load(path) if path and os.path.exists(path) else {}
        if not values.get("channels") and self.image_channels:
            values["channels"] = [{"image_channel": n} for n in self.image_channels]
        self.extra = {k: v for k, v in values.items() if k not in SPEC}
        self._build()
        self.fill(values)
        self.dirty = False
        self.update_status()

    # ------------------------------------------------------------------ layout
    def _build(self):
        r = self.root
        if not self.embedded:
            r.title("Histology notes" + (f" - {os.path.basename(self.image)}" if self.image else ""))
            r.geometry("980x760")
            r.minsize(760, 520)
            apply_style(r)
        top = ttk.Frame(r, padding=(16, 12, 16, 4) if not self.embedded else (4, 4, 4, 0))
        top.pack(fill="x")
        if not self.embedded:
            ttk.Label(top, text="Histology notes", style="Head.TLabel").pack(anchor="w")
        ttk.Label(top, text=f"saved as {self.path}" if self.path else "", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(top, text="Fields in bold are required. Pick from the drop-downs or type your own answer.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        self.nb = ttk.Notebook(r)
        self.nb.pack(fill="both", expand=True, padx=12, pady=6)
        self.tab_frames = {}
        for title, keys in TABS:
            sf = ScrollFrame(self.nb)
            self.nb.add(sf, text=title)
            self.tab_frames[title] = sf
            for k in keys:
                self._section(sf.inner, k)

        bottom = ttk.Frame(r, padding=(16, 6, 16, 12) if not self.embedded else (4, 4, 4, 4))
        bottom.pack(fill="x")
        self.status = ttk.Label(bottom, text="", style="Sub.TLabel", wraplength=520, justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        if not self.embedded:
            ttk.Button(bottom, text="Save & close", command=self.save_close).pack(side="right", padx=(6, 0))
        ttk.Button(bottom, text="Save notes", command=self.save).pack(side="right", padx=(6, 0))
        ttk.Button(bottom, text="Check", command=self.show_problems).pack(side="right", padx=(6, 0))
        ttk.Button(bottom, text="Copy protocol from...", command=self.copy_protocol).pack(side="right", padx=(6, 0))
        if not self.embedded:
            r.protocol("WM_DELETE_WINDOW", self.close)

    def _section(self, parent, key):
        label, kind, fields = SPEC[key]
        box = ttk.LabelFrame(parent, text=label, padding=(12, 8), style="Section.TLabelframe")
        box.pack(fill="x", pady=(0, 12))
        if kind == "text":
            self.notes_text = tk.Text(box, height=14, wrap="word", font=("Segoe UI", 10), relief="solid",
                                      borderwidth=1)
            self.notes_text.pack(fill="both", expand=True)
            self.notes_text.bind("<<Modified>>", self._text_modified)
            return
        if kind == "dict":
            self._fields(box, key, None, fields)
            return
        holder = ttk.Frame(box)
        holder.pack(fill="x")
        self.lists[key] = holder
        noun = {"primary_antibodies": "primary antibody", "secondary_antibodies": "secondary antibody / nanobody",
                "other_stains": "stain", "channels": "channel"}[key]
        if key != "channels" or not self.image_channels:
            ttk.Button(box, text=f"+ Add {noun}", command=lambda: self.add_item(key, {})).pack(anchor="w", pady=(6, 0))

    def _fields(self, parent, key, idx, fields):
        grid = ttk.Frame(parent)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for row, (f, req, hint) in enumerate(fields):
            lab = ttk.Label(grid, text=f.replace("_", " ").replace(" um", " (um)") + (" *" if req else ""),
                            style="Req.TLabel" if req else "Opt.TLabel")
            lab.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            var = tk.StringVar()
            var.trace_add("write", lambda *a: self._changed())
            sugg = SUGGEST.get(f)
            fixed = key == "channels" and f == "image_channel" and self.image_channels
            if fixed:
                w = ttk.Entry(grid, textvariable=var, state="readonly")
            elif sugg:
                w = ttk.Combobox(grid, textvariable=var, values=sugg)
            else:
                w = ttk.Entry(grid, textvariable=var)
            w.grid(row=row, column=1, sticky="ew", pady=3)
            extra = ttk.Frame(grid)
            extra.grid(row=row, column=2, sticky="w", padx=(8, 0))
            if f in DATE_FIELDS:
                ttk.Button(extra, text="Today", width=7,
                           command=lambda v=var: v.set(datetime.date.today().isoformat())).pack(side="left")
            ttk.Label(extra, text=hint, style="Hint.TLabel").pack(side="left", padx=(6, 0))
            self.vars[(key, idx, f)] = var
            self.labels[(key, idx, f)] = lab

    # ------------------------------------------------------------------ list items
    def add_item(self, key, item, scroll=True):
        label, kind, fields = SPEC[key]
        holder = self.lists[key]
        idx = len(holder.winfo_children())
        noun = {"primary_antibodies": "Primary antibody", "secondary_antibodies": "Secondary antibody / nanobody",
                "other_stains": "Stain", "channels": "Channel"}[key]
        card = ttk.LabelFrame(holder, text=f"{noun} {idx + 1}", padding=(10, 6))
        card.pack(fill="x", pady=(0, 8))
        card._idx = idx
        self._fields(card, key, idx, fields)
        if not (key == "channels" and self.image_channels):
            ttk.Button(card, text="Remove", command=lambda: self.remove_item(key, card)).pack(anchor="e")
        for f, _, _ in fields:
            self.vars[(key, idx, f)].set(str((item or {}).get(f, "") or ""))
        if scroll:
            self._changed()

    def remove_item(self, key, card):
        values = self.collect()
        items = values.get(key, [])
        del items[card._idx]
        self._rebuild_list(key, items)
        self._changed()

    def _rebuild_list(self, key, items):
        for w in self.lists[key].winfo_children():
            w.destroy()
        for k in [k for k in self.vars if k[0] == key]:
            del self.vars[k], self.labels[k]
        for it in items:
            self.add_item(key, it, scroll=False)

    # ------------------------------------------------------------------ values in / out
    def fill(self, values):
        for key, (label, kind, fields) in SPEC.items():
            if kind == "dict":
                cur = values.get(key) or {}
                for f, _, _ in fields:
                    self.vars[(key, None, f)].set(str(cur.get(f, "") or ""))
            elif kind == "list":
                items = values.get(key)
                if items is None:
                    items = [] if key == "other_stains" else [{}]
                self._rebuild_list(key, items)
            else:
                v = values.get(key)
                v = v.get("text", "") if isinstance(v, dict) else (v or "")
                self.notes_text.delete("1.0", "end")
                self.notes_text.insert("1.0", str(v))
                self.notes_text.edit_modified(False)

    def collect(self):
        out = {}
        for key, (label, kind, fields) in SPEC.items():
            if kind == "dict":
                out[key] = {f: self.vars[(key, None, f)].get().strip() for f, _, _ in fields}
            elif kind == "list":
                n = len(self.lists[key].winfo_children())
                out[key] = [{f: self.vars[(key, i, f)].get().strip() for f, _, _ in fields} for i in range(n)]
            else:
                out[key] = {"text": self.notes_text.get("1.0", "end").rstrip()}
        out.update(self.extra)
        return out

    # ------------------------------------------------------------------ checking / saving
    def _changed(self):
        self.dirty = True
        self.update_status()

    def _text_modified(self, *_):
        if self.notes_text.edit_modified():
            self.dirty = True
            self.notes_text.edit_modified(False)

    def problems(self):
        return check(self.collect(), self.image_channels)

    def update_status(self):
        errors, warnings = self.problems()
        for k, lab in self.labels.items():
            lab.configure(style="Req.TLabel" if str(lab.cget("text")).endswith("*") else "Opt.TLabel")
        bad = self._bad_keys(errors)
        for k in bad:
            if k in self.labels:
                self.labels[k].configure(style="Bad.TLabel")
        if errors:
            self.status.configure(text=f"{len(errors)} required field(s) still missing or not in the expected form "
                                       f"(shown in red). Click Check for the list.", foreground=RED)
        else:
            self.status.configure(text="Complete." + (f"  ({len(warnings)} suggestion(s): click Check)" if warnings
                                                      else ""), foreground="#2e7d32")

    def _bad_keys(self, errors):
        """Map check() messages back to fields: 'Label #2: 'f' ...' / 'Label: 'f' ...'."""
        bad = set()
        by_label = {lab: k for k, (lab, kind, f) in SPEC.items()}
        for e in errors:
            head, _, rest = e.partition(": ")
            idx = None
            if " #" in head:
                head, n = head.rsplit(" #", 1)
                idx = int(n) - 1 if n.isdigit() else None
            key = by_label.get(head)
            f = rest.split("'")[1] if rest.count("'") >= 2 else None
            if key and f:
                bad.add((key, idx, f))
        return bad

    def show_problems(self):
        errors, warnings = self.problems()
        if not errors and not warnings:
            messagebox.showinfo("Histology notes", "Everything required is filled in.", parent=self.win)
            return
        msg = ""
        if errors:
            msg += "Still needed:\n" + "\n".join(f"  - {e}" for e in errors)
        if warnings:
            msg += ("\n\n" if msg else "") + "Suggestions:\n" + "\n".join(f"  - {w}" for w in warnings)
        messagebox.showwarning("Histology notes", msg, parent=self.win)

    def save(self):
        if not self.path:
            self.path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".toml",
                                                     filetypes=[("Histology notes", "*_histology.toml *.toml")])
            if not self.path:
                return False
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(render(self.collect(), os.path.basename(self.image) if self.image else ""))
        self.dirty = False
        errors, _ = self.problems()
        self.status.configure(text=f"Saved {self.path}" + (f"  -- {len(errors)} required field(s) still missing."
                                                          if errors else "  -- complete."),
                              foreground=RED if errors else "#2e7d32")
        if self.on_saved:
            self.on_saved()
        return True

    def save_close(self):
        errors, _ = self.problems()
        if errors and not messagebox.askyesno(
                "Histology notes", f"{len(errors)} required field(s) are still missing.\n\nSave anyway and finish "
                                   "later? (bigmip will not make a MIP until they are filled in.)", parent=self.win):
            return
        if self.save():
            self.win.destroy()

    def close(self):
        if self.dirty:
            ans = messagebox.askyesnocancel("Histology notes", "Save your changes?", parent=self.win)
            if ans is None:
                return
            if ans and not self.save():
                return
        self.win.destroy()

    def copy_protocol(self):
        src = filedialog.askopenfilename(parent=self.win, title="Copy the protocol from an earlier notes file",
                                         filetypes=[("Histology notes", "*.toml")])
        if not src:
            return
        old = load(src)
        cur = self.collect()
        keep_sample = {f: cur["sample"].get(f, "") for f in ("sample_id", "animal_id", "sex", "age_or_date_of_birth",
                                                             "tissue_collected", "eye")}
        new = dict(old)
        new["sample"] = dict(old.get("sample") or {}, **keep_sample)
        new["dates"] = cur["dates"]
        if self.image_channels:          # keep this image's channel names, take 'shows' / fluorophore by name
            prev = {str(c.get("image_channel", "")): c for c in old.get("channels") or []}
            prev3 = {str(c.get("image_channel", ""))[:3]: c for c in old.get("channels") or []}
            new["channels"] = [dict(prev.get(n) or prev3.get(n[:3]) or {}, image_channel=n) for n in self.image_channels]
        self.fill(new)
        self._changed()
        self.status.configure(text=f"Protocol copied from {os.path.basename(src)}. Fill in this sample's animal "
                                   f"and dates.", foreground=ACCENT)


def _dpi_aware():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # sharp text on high-resolution screens
        except Exception:
            pass


def run(image=None, notes_path=None, image_channels=None, screenshot=None, prefill=None):
    """Open the form. `screenshot`: save a PNG of each tab and close (used for documentation / tests)."""
    _dpi_aware()
    root = tk.Tk()
    if image is None and notes_path is None:
        root.withdraw()
        image = filedialog.askopenfilename(title="Choose the image these notes belong to",
                                           filetypes=[("Images", "*.ims *.tif *.tiff"), ("All files", "*.*")])
        if not image:
            root.destroy()
            return
        root.deiconify()
    if image and image_channels is None:
        from .notes import _image_channels
        image_channels = _image_channels(image)
    path = notes_path or (find_notes(image) or notes_path_for(image) if image else None)
    form = NotesForm(root, image, path, image_channels)
    if prefill:
        form.fill(prefill)
        form.update_status()
    if screenshot:
        from PIL import ImageGrab
        root.update()

        def shoot(i=0):
            if i >= len(TABS):
                root.destroy()
                return
            form.nb.select(i)
            root.update()
            root.after(400, lambda: (ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                                          root.winfo_rootx() + root.winfo_width(),
                                                          root.winfo_rooty() + root.winfo_height())
                                                    ).save(f"{screenshot}_{i + 1}.png"), shoot(i + 1)))
        root.after(500, shoot)
    root.mainloop()
    return form.path
