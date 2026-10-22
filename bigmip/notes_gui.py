"""The histology-notes form (built-in Tk; nothing extra to install). Used inside the bigmip app (tab 1), or on its
own:  bigmip-notes IMAGE.  It reads and writes the same <name>_histology.toml file as the text form.

Every field is optional. Entries that look unusual (e.g. a time without units) are marked orange as a hint only.
"""
import calendar
import datetime
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .notes import (DATE_FIELDS, OUTSIDE, SECTIONS, carry_channels, changes_path, check, completeness, find_notes,
                    fluorophore_for, fluorophore_options, load, normalize_date, notes_path_for, save,
                    secondary_target_options, shows_options, track)

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
TABS = [("Sample & dates", ["sample", "dates"]),
        ("Fixation & blocking", ["fixation", "blocking"]),
        ("Primary antibodies", ["primary_antibodies", "primary_incubation", "washes_after_primary"]),
        ("Secondary antibodies & stains", ["secondary_antibodies", "secondary_incubation", "washes_after_secondary",
                                           "other_stains"]),
        ("Mounting", ["mounting"]),
        ("Channels", ["channels"]),
        ("Notes", ["notes"])]
SPEC = {k: (label, kind, fields) for k, label, kind, fields in SECTIONS}
NOUN = {"primary_antibodies": "Primary antibody", "secondary_antibodies": "Secondary antibody / nanobody",
        "other_stains": "Stain", "channels": "Channel"}
RED, GRAY, ACCENT, ORANGE, GREEN = "#c62828", "#6b6b6b", "#1f5fa8", "#d17a00", "#2e7d32"


def apply_style(root):
    """Shared look for every bigmip window."""
    st = ttk.Style(root)
    for theme in ("vista", "aqua", "clam"):
        if theme in st.theme_names():
            st.theme_use(theme)
            break
    st.configure("Head.TLabel", font=("Segoe UI", 15, "bold"))
    st.configure("Sub.TLabel", foreground=GRAY)
    st.configure("Section.TLabelframe.Label", font=("Segoe UI", 11, "bold"), foreground=ACCENT)
    st.configure("Field.TLabel", font=("Segoe UI", 10))
    st.configure("Odd.TLabel", font=("Segoe UI", 10, "bold"), foreground=ORANGE)
    st.configure("Hint.TLabel", foreground=GRAY, font=("Segoe UI", 9))
    st.configure("TNotebook.Tab", padding=(12, 6), font=("Segoe UI", 10))
    st.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=(14, 6))
    # The mouse wheel only ever scrolls the page. Tk's own drop-downs and number boxes change their value on the
    # wheel, which silently changed fields while scrolling past them.
    for cls in ("TCombobox", "TSpinbox", "Spinbox"):
        for seq in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_class(cls, seq, "")
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind_all(seq, _wheel)
    return st


def _wheel(e):
    """Scroll the scrollable page under the pointer (lists and text boxes scroll themselves)."""
    try:
        w = e.widget.winfo_containing(e.x_root, e.y_root)
    except (AttributeError, KeyError, tk.TclError):      # e.g. the open list of a drop-down: it scrolls itself
        return
    while w is not None:
        if isinstance(w, (tk.Text, tk.Listbox, ttk.Treeview)):
            return
        if isinstance(w, ScrollFrame):
            num = getattr(e, "num", 0)
            n = -1 if num == 4 else 1 if num == 5 else (int(-e.delta / 120) or (-1 if e.delta > 0 else 1))
            w.canvas.yview_scroll(n, "units")
            return
        w = w.master


class ScrollFrame(ttk.Frame):
    """A frame with a vertical scrollbar (for long tabs). The mouse wheel scrolls it (see _wheel)."""

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


class DatePicker(tk.Toplevel):
    """A small month calendar under a date field; click a day to fill in YYYY-MM-DD."""

    def __init__(self, anchor, var):
        super().__init__(anchor)
        self.var = var
        self.overrideredirect(True)
        self.attributes("-topmost", True)            # a popup: never hidden behind the window it belongs to
        self.configure(background="#ffffff", highlightthickness=1, highlightbackground="#9a9a9a")
        cur = normalize_date(var.get())
        try:
            d = datetime.date.fromisoformat(cur)
        except ValueError:
            d = datetime.date.today()
        self.y, self.m, self.sel = d.year, d.month, d
        self.body = tk.Frame(self, background="#ffffff", padx=6, pady=6)
        self.body.pack()
        self.draw()
        self.geometry(f"+{anchor.winfo_rootx()}+{anchor.winfo_rooty() + anchor.winfo_height()}")
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<FocusOut>", lambda e: self.after(150, self._maybe_close))
        self.focus_force()

    def _maybe_close(self):
        try:
            if self.focus_get() is None or not str(self.focus_get()).startswith(str(self)):
                self.destroy()
        except (tk.TclError, KeyError):
            pass

    def draw(self):
        for w in self.body.winfo_children():
            w.destroy()
        top = tk.Frame(self.body, background="#ffffff")
        top.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 4))
        btn = dict(relief="flat", background="#ffffff", activebackground="#e6eef8", font=("Segoe UI", 10), bd=0)
        tk.Button(top, text="<<", command=lambda: self.shift(-12), **btn).pack(side="left")
        tk.Button(top, text="<", command=lambda: self.shift(-1), **btn).pack(side="left")
        tk.Label(top, text=f"{calendar.month_name[self.m]} {self.y}", background="#ffffff",
                 font=("Segoe UI", 10, "bold"), width=16).pack(side="left", expand=True)
        tk.Button(top, text=">", command=lambda: self.shift(1), **btn).pack(side="left")
        tk.Button(top, text=">>", command=lambda: self.shift(12), **btn).pack(side="left")
        for i, wd in enumerate(["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
            tk.Label(self.body, text=wd, background="#ffffff", foreground=GRAY, font=("Segoe UI", 9)).grid(
                row=1, column=i, padx=1)
        today = datetime.date.today()
        for r, week in enumerate(calendar.Calendar().monthdayscalendar(self.y, self.m), start=2):
            for c, day in enumerate(week):
                if not day:
                    continue
                d = datetime.date(self.y, self.m, day)
                bg = "#1f5fa8" if d == self.sel else ("#e6eef8" if d == today else "#ffffff")
                fg = "#ffffff" if d == self.sel else "#000000"
                tk.Button(self.body, text=str(day), width=3, relief="flat", background=bg, foreground=fg,
                          activebackground="#cfe0f5", font=("Segoe UI", 9),
                          command=lambda d=d: self.pick(d)).grid(row=r, column=c, padx=1, pady=1)
        tk.Button(self.body, text="Today", command=lambda: self.pick(today), relief="flat", background="#f0f0f0",
                  font=("Segoe UI", 9)).grid(row=9, column=0, columnspan=3, sticky="w", pady=(4, 0))
        tk.Button(self.body, text="Clear", command=lambda: self.pick(None), relief="flat", background="#f0f0f0",
                  font=("Segoe UI", 9)).grid(row=9, column=4, columnspan=3, sticky="e", pady=(4, 0))

    def shift(self, months):
        k = self.y * 12 + self.m - 1 + months
        self.y, self.m = k // 12, k % 12 + 1
        self.draw()

    def pick(self, d):
        self.var.set(d.isoformat() if d else "")
        self.destroy()


class NotesForm:
    """The notes editor. Standalone (its own window) or embedded in a frame of the main bigmip app."""

    def __init__(self, root, image=None, path=None, image_channels=None, embedded=False, on_saved=None):
        self.root, self.image, self.path = root, image, path
        self.embedded, self.on_saved = embedded, on_saved
        self.win = root.winfo_toplevel()
        self.image_channels = image_channels or []
        self.vars = {}          # (section, index or None, field) -> StringVar
        self.labels = {}        # same key -> label widget (turns orange when the entry looks unusual)
        self.lists = {}         # list section -> container frame
        self.notes_text = None
        self.extra = {}         # sections not edited here (e.g. file_history), written back unchanged
        self.dirty = False
        self._loading = False
        self._how = "bigmip app"  # how the next save is described in the change log
        logged = track(path) if path else []      # edits made outside bigmip since the last logged save
        values = load(path) if path and os.path.exists(path) else {}
        if self.image_channels:
            values["channels"] = carry_channels(values.get("channels"), self.image_channels)
        self.extra = {k: v for k, v in values.items() if k not in SPEC}
        self._build()
        self.fill(values)
        self.dirty = False
        self.update_status()
        if logged and logged[0][2] == OUTSIDE:
            self.status.configure(text=f"{len(logged)} change(s) made outside bigmip since the last save were added "
                                       "to the change log.  " + self.status.cget("text"), foreground=ACCENT)

    # ------------------------------------------------------------------ layout
    def _build(self):
        r = self.root
        if not self.embedded:
            r.title("Histology notes" + (f" - {os.path.basename(self.image)}" if self.image else ""))
            r.geometry("1000x780")
            r.minsize(760, 520)
            apply_style(r)
        top = ttk.Frame(r, padding=(16, 12, 16, 4) if not self.embedded else (4, 4, 4, 0))
        top.pack(fill="x")
        if not self.embedded:
            ttk.Label(top, text="Histology notes", style="Head.TLabel").pack(anchor="w")
        ttk.Label(top, text=(f"saved as {self.path}" if self.path else "") + "     Everything is optional; fill in "
                  "what you know. Pick from the drop-downs or type your own answer.", style="Sub.TLabel"
                  ).pack(anchor="w")
        self.nb = ttk.Notebook(r)
        self.nb.pack(fill="both", expand=True, padx=12 if not self.embedded else 0, pady=6)
        for title, keys in TABS:
            sf = ScrollFrame(self.nb)
            self.nb.add(sf, text=title)
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
        ttk.Button(bottom, text="Change log", command=self.open_log).pack(side="right", padx=(6, 0))
        if not self.embedded:
            r.protocol("WM_DELETE_WINDOW", self.close)

    def _section(self, parent, key):
        label, kind, fields = SPEC[key]
        box = ttk.LabelFrame(parent, text=label, padding=(12, 8), style="Section.TLabelframe")
        box.pack(fill="x", pady=(0, 12))
        if kind == "text":
            self.notes_text = tk.Text(box, height=16, wrap="word", font=("Segoe UI", 10), relief="solid",
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
        if key != "channels" or not self.image_channels:
            ttk.Button(box, text=f"+ Add {NOUN[key].lower()}", command=lambda: self.add_item(key, {})).pack(
                anchor="w", pady=(6, 0))

    def _options(self, key, f):
        """Drop-down values; for some fields they come from what was entered on the other tabs."""
        if key == "secondary_antibodies" and f == "target":
            return lambda: secondary_target_options(self.collect())
        if key == "channels" and f == "shows":
            return lambda: shows_options(self.collect())
        if key == "channels" and f == "fluorophore":
            return lambda: fluorophore_options(self.collect()) + [s for s in SUGGEST["fluorophore"]]
        if f in SUGGEST:
            return lambda: SUGGEST[f]
        return None

    def _fields(self, parent, key, idx, fields):
        grid = ttk.Frame(parent)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for row, (f, hint) in enumerate(fields):
            lab = ttk.Label(grid, text=f.replace("_", " ").replace(" um", " (um)"), style="Field.TLabel")
            lab.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
            var = tk.StringVar()
            var.trace_add("write", lambda *a, k=(key, idx, f): self._changed(k))
            opts = self._options(key, f)
            fixed = key == "channels" and f == "image_channel" and self.image_channels
            cell = ttk.Frame(grid)
            cell.grid(row=row, column=1, sticky="ew", pady=3)
            if fixed:
                w = ttk.Entry(cell, textvariable=var, state="readonly")
            elif opts:
                w = ttk.Combobox(cell, textvariable=var, values=opts())
                w.configure(postcommand=lambda w=w, o=opts: w.configure(values=o()))
            else:
                w = ttk.Entry(cell, textvariable=var)
            w.pack(side="left", fill="x", expand=True)
            if f in DATE_FIELDS:
                ttk.Button(cell, text="Pick date", width=10,
                           command=lambda v=var, a=w: DatePicker(a, v)).pack(side="left", padx=(6, 0))
                w.bind("<FocusOut>", lambda e, v=var: v.set(normalize_date(v.get())))
            if key == "channels" and f == "shows":
                w.bind("<<ComboboxSelected>>", lambda e, i=idx: self._auto_fluor(i))
                w.bind("<FocusOut>", lambda e, i=idx: self._auto_fluor(i), add="+")
            ttk.Label(grid, text=hint, style="Hint.TLabel").grid(row=row, column=2, sticky="w", padx=(10, 0))
            self.vars[(key, idx, f)] = var
            self.labels[(key, idx, f)] = lab

    def _auto_fluor(self, idx):
        """After a 'shows' choice, fill the fluorophore from the matching secondary / stain (if empty)."""
        fv = self.vars.get(("channels", idx, "fluorophore"))
        sv = self.vars.get(("channels", idx, "shows"))
        if fv is not None and sv is not None and not fv.get().strip():
            fl = fluorophore_for(self.collect(), sv.get())
            if fl:
                fv.set(fl)

    # ------------------------------------------------------------------ list items
    def add_item(self, key, item, mark=True):
        label, kind, fields = SPEC[key]
        holder = self.lists[key]
        idx = len(holder.winfo_children())
        card = ttk.LabelFrame(holder, text=f"{NOUN[key]} {idx + 1}", padding=(10, 6))
        card.pack(fill="x", pady=(0, 8))
        card._idx = idx
        self._fields(card, key, idx, fields)
        if not (key == "channels" and self.image_channels):
            ttk.Button(card, text="Remove", command=lambda: self.remove_item(key, card)).pack(anchor="e")
        self._loading = True
        for f, _ in fields:
            self.vars[(key, idx, f)].set(str((item or {}).get(f, "") or ""))
        self._loading = False
        if mark:
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
            self.add_item(key, it, mark=False)

    # ------------------------------------------------------------------ values in / out
    def fill(self, values):
        self._loading = True
        for key, (label, kind, fields) in SPEC.items():
            if kind == "dict":
                cur = values.get(key) or {}
                for f, _ in fields:
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
        self._loading = False

    def collect(self):
        """The form's values. Also called by the drop-downs while a card is still being built, so fields that do not
        exist yet count as empty."""
        def val(k):
            v = self.vars.get(k)
            return v.get().strip() if v is not None else ""
        out = {}
        for key, (label, kind, fields) in SPEC.items():
            if kind == "dict":
                out[key] = {f: val((key, None, f)) for f, _ in fields}
            elif kind == "list":
                n = len(self.lists[key].winfo_children()) if key in self.lists else 0
                out[key] = [{f: val((key, i, f)) for f, _ in fields} for i in range(n)]
            else:
                out[key] = {"text": self.notes_text.get("1.0", "end").rstrip() if self.notes_text else ""}
        out.update(self.extra)
        return out

    # ------------------------------------------------------------------ checking / saving
    def _changed(self, k=None):
        if self._loading:
            return
        self.dirty = True
        self.update_status()

    def _text_modified(self, *_):
        if self.notes_text.edit_modified():
            self.dirty = True
            self.notes_text.edit_modified(False)

    def update_status(self):
        d = self.collect()
        issues, sugg = check(d, self.image_channels)
        for lab in self.labels.values():
            lab.configure(style="Field.TLabel")
        for k in self._odd_keys(issues):
            if k in self.labels:
                self.labels[k].configure(style="Odd.TLabel")
        level, msg = completeness(d)
        if level == "none":
            msg = ("Nothing filled in yet. The notes are optional, but without them the MIP carries no record of the "
                   "staining.")
        else:
            msg = msg[0].upper() + msg[1:] + "."
        if issues or sugg:
            msg += f"  {len(issues) + len(sugg)} thing(s) to look at (orange) -- click Check."
        self.status.configure(text=("Unsaved changes.  " if self.dirty else "") + msg,
                              foreground=ORANGE if (issues or sugg or level != "ok" or "nothing yet" in msg)
                              else GRAY)

    def _odd_keys(self, issues):
        """Map check() messages ('Label #2: field name '...' -- ...') back to fields."""
        bad = set()
        by_label = {lab.split(" (")[0]: k for k, (lab, kind, f) in SPEC.items()}
        for e in issues:
            head, _, rest = e.partition(": ")
            idx = None
            if " #" in head:
                head, n = head.rsplit(" #", 1)
                idx = int(n) - 1 if n.isdigit() else None
            key = by_label.get(head.split(" (")[0])
            if not key:
                continue
            fname = rest.split(" '")[0].replace(" ", "_")
            bad.add((key, idx, fname))
        return bad

    def show_problems(self):
        issues, sugg = check(self.collect(), self.image_channels)
        if not issues and not sugg:
            messagebox.showinfo("Histology notes", "Nothing looks unusual.", parent=self.win)
            return
        msg = ""
        if issues:
            msg += "Entries in an unexpected form:\n" + "\n".join(f"  - {e}" for e in issues)
        if sugg:
            msg += ("\n\n" if msg else "") + "Worth a look:\n" + "\n".join(f"  - {w}" for w in sugg)
        messagebox.showinfo("Histology notes", msg, parent=self.win)

    def save(self):
        if not self.path:
            self.path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".toml",
                                                     filetypes=[("Histology notes", "*_histology.toml *.toml")])
            if not self.path:
                return False
        d = self.collect()
        try:
            save(self.path, d, os.path.basename(self.image) if self.image else "", how=self._how)
        except OSError as e:
            messagebox.showerror("Histology notes", f"Could not save:\n{e}", parent=self.win)
            return False
        self._how = "bigmip app"
        self.fill(load(self.path))            # show normalized values (e.g. dates as YYYY-MM-DD)
        self.dirty = False
        self.update_status()
        self.status.configure(text=f"Saved {self.path}.  " + self.status.cget("text"))
        if self.on_saved:
            self.on_saved()
        return True

    def save_close(self):
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
        keep = {f: cur["sample"].get(f, "") for f in ("sample_id", "animal_id", "sex", "age_or_date_of_birth",
                                                      "tissue_collected", "eye")}
        new = dict(old)
        new["sample"] = dict(old.get("sample") or {}, **keep)
        new["dates"] = cur["dates"]
        new.pop("file_history", None)
        if self.image_channels:
            new["channels"] = carry_channels(old.get("channels"), self.image_channels)
        self.fill(new)
        self._changed()
        self._how = f"bigmip app (protocol copied from {os.path.basename(src)})"
        self.status.configure(text=f"Protocol copied from {os.path.basename(src)}. Fill in this sample's animal and "
                                   f"dates, then Save.", foreground=ACCENT)

    def open_log(self):
        p = changes_path(self.path) if self.path else ""
        if p and os.path.exists(p):
            open_path(p)
        else:
            messagebox.showinfo("Histology notes", "No changes logged yet: the change log starts with the first save "
                                                   f"(it will be {os.path.basename(p) or 'next to the notes'}).",
                                parent=self.win)


def open_path(p):
    """Open a file or folder with the system's default program (a .csv opens in Excel)."""
    if sys.platform.startswith("win"):
        os.startfile(p)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.Popen(["open", p])
    else:
        import subprocess
        subprocess.Popen(["xdg-open", p])


def _dpi_aware():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)     # sharp text on high-resolution screens
        except Exception:
            pass


def run(image=None, notes_path=None, image_channels=None, screenshot=None, prefill=None):
    """Open the form on its own. `screenshot`: save a PNG of each tab and close (for documentation / tests)."""
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
