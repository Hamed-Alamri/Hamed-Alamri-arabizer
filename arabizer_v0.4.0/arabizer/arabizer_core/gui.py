"""الواجهة الرسومية (tkinter)."""
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__ as APP_VERSION
from .config import load_config, log_crash, save_config
from .constants import MANIFEST
from .library import Library
from .patcher import apply_pack, load_manifest, undo
from .report import Report
from .scan import full_scan
from .updater import apply_update, check_update


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import subprocess

    BG, CARD, FG, MUTED = "#12141a", "#1c2029", "#e8ecf3", "#8b93a5"
    ACC, GOOD, WARN, BAD = "#4f8cff", "#3ddc84", "#ffb020", "#ff5c5c"
    COLORS = {"ok": GOOD, "warn": WARN, "err": BAD, "info": FG, "path": MUTED}
    SYMS = {"ok": "✔ ", "warn": "⚠ ", "err": "✖ ", "info": "• ", "path": ""}

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"مُعرِّب | أداة تعريب الألعاب  v{APP_VERSION}")
            self.geometry("980x760")
            self.minsize(820, 620)
            self.configure(bg=BG)
            self.cfg = load_config()
            self.q = queue.Queue()
            self.folder = None
            self.ready = False
            self.busy = False
            self.mode = "apply"
            self.upd = None
            self.last = self.cfg.get("last_scan", {}) or {}
            self.path_var = tk.StringVar(value=self.cfg.get("game_path", ""))
            self.pack_var = tk.StringVar(value=self.cfg.get("pack_path", ""))
            self.prog_label = tk.StringVar(value="")
            self.lib = Library()
            self.lib.sync()
            self._style()
            self._build()
            self._restore_folder()
            self._refresh()
            self.after(80, self._pump)
            self.after(1500, lambda: self._check_update(silent=True))

        # ───── الستايل ─────
        def _style(self):
            s = ttk.Style(self)
            s.theme_use("clam")
            s.configure("TFrame", background=BG)
            s.configure("Card.TFrame", background=CARD)
            s.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
            s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
            s.configure("H.TLabel", background=BG, foreground=FG, font=("Segoe UI", 20, "bold"))
            s.configure("TEntry", fieldbackground="#262b36", foreground=FG, insertcolor=FG, bordercolor="#323848")
            s.configure("TButton", background="#2a3040", foreground=FG, borderwidth=0, padding=8, font=("Segoe UI", 10))
            s.map("TButton", background=[("active", "#353c50"), ("disabled", "#20242e")], foreground=[("disabled", "#5b6275")])
            s.configure("Act.TButton", background=ACC, foreground="white", font=("Segoe UI", 12, "bold"), padding=12)
            s.map("Act.TButton", background=[("active", "#6a9eff"), ("disabled", "#2b3550")], foreground=[("disabled", "#6b7490")])
            s.configure("Undo.TButton", background=BAD, foreground="white", font=("Segoe UI", 12, "bold"), padding=12)
            s.map("Undo.TButton", background=[("active", "#ff7676"), ("disabled", "#4a2b30")])
            s.configure("Horizontal.TProgressbar", background=ACC, troughcolor="#262b36", borderwidth=0)
            s.configure("TNotebook", background=BG, borderwidth=0)
            s.configure("TNotebook.Tab", background="#20242e", foreground=MUTED, padding=(18, 8), font=("Segoe UI", 10, "bold"))
            s.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])
            s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, rowheight=28,
                        borderwidth=0, font=("Segoe UI", 10))
            s.configure("Treeview.Heading", background="#262b36", foreground=MUTED, borderwidth=0,
                        font=("Segoe UI", 9, "bold"))
            s.map("Treeview", background=[("selected", "#2b3550")], foreground=[("selected", FG)])

        # ───── البناء ─────
        def _build(self):
            top = ttk.Frame(self, padding=(24, 16, 24, 4))
            top.pack(fill="x")
            ttk.Label(top, text="مُعرِّب", style="H.TLabel").pack(anchor="e")
            ttk.Label(top, text=f"أداة تعريب الألعاب  ·  الإصدار {APP_VERSION}", foreground=MUTED).pack(anchor="e")

            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=24, pady=(8, 20))
            self.tab_upd = ttk.Frame(self.nb, padding=18)
            self.tab_scan = ttk.Frame(self.nb, padding=18)
            self.tab_dash = ttk.Frame(self.nb, padding=18)
            # الترتيب معكوس عشان لوحة التحكم تطلع أقصى اليمين
            self.nb.add(self.tab_upd, text="التحديثات")
            self.nb.add(self.tab_scan, text="الفحص والتعريب")
            self.nb.add(self.tab_dash, text="لوحة التحكم")
            self.nb.select(self.tab_dash)
            self._build_dash()
            self._build_scan()
            self._build_upd()

        def _tile(self, parent, col, title):
            f = tk.Frame(parent, bg=CARD, padx=16, pady=14, highlightthickness=1, highlightbackground="#2a3040")
            f.grid(row=0, column=col, sticky="nsew", padx=6, pady=4)
            parent.columnconfigure(col, weight=1, uniform="t")
            tk.Label(f, text=title, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="e").pack(fill="x")
            val = tk.Label(f, text="—", bg=CARD, fg=FG, font=("Segoe UI", 15, "bold"), anchor="e", wraplength=190, justify="right")
            val.pack(fill="x", pady=(6, 2))
            sub = tk.Label(f, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="e", wraplength=190, justify="right")
            sub.pack(fill="x")
            return val, sub

        def _build_dash(self):
            d = self.tab_dash
            self.banner = tk.Frame(d, bg="#2b3550", padx=14, pady=10)
            self.banner_lbl = tk.Label(self.banner, text="", bg="#2b3550", fg=FG, font=("Segoe UI", 10, "bold"), anchor="e")
            ttk.Button(self.banner, text="تحديث الآن", command=self._do_update).pack(side="left")
            self.banner_lbl.pack(side="right", fill="x", expand=True)
            self.tiles_fr = ttk.Frame(d)
            self.tiles_fr.pack(fill="x", pady=(6, 10))
            # col 0 أقصى اليمين: نحط البلاطات بترتيب معكوس
            self.t_status = self._tile(self.tiles_fr, 3, "حالة التعريب")
            self.t_game = self._tile(self.tiles_fr, 2, "اللعبة")
            self.t_scan = self._tile(self.tiles_fr, 1, "آخر فحص")
            self.t_ver = self._tile(self.tiles_fr, 0, "الإصدار")
            libf = ttk.Frame(d, style="Card.TFrame", padding=14)
            libf.pack(fill="both", expand=True, pady=(4, 8))
            ttk.Label(libf, text="مكتبة الألعاب (المعرّبة وما فُحص سابقاً)", style="Muted.TLabel").pack(anchor="e")
            cols = ("files", "date", "status", "name")
            self.tree = ttk.Treeview(libf, columns=cols, displaycolumns=cols, show="headings", height=5,
                                     selectmode="browse")
            for cid, title, width in (("name", "اللعبة", 320), ("status", "الحالة", 120),
                                      ("date", "تاريخ التعريب", 160), ("files", "الملفات", 120)):
                self.tree.heading(cid, text=title, anchor="e")
                self.tree.column(cid, width=width, anchor="e", stretch=(cid == "name"))
            self.tree.pack(fill="both", expand=True, pady=(8, 8))
            lb = ttk.Frame(libf, style="Card.TFrame")
            lb.pack(fill="x")
            ttk.Button(lb, text="اختيار للفحص", command=self._lib_use).pack(side="right")
            ttk.Button(lb, text="فتح المجلد", command=self._lib_open).pack(side="right", padx=8)
            ttk.Button(lb, text="حذف من القائمة", command=self._lib_remove).pack(side="right")
            card = ttk.Frame(d, style="Card.TFrame", padding=14)
            card.pack(fill="x", pady=(0, 0))
            ttk.Label(card, text="تفاصيل آخر فحص", style="Muted.TLabel").pack(anchor="e")
            self.details = tk.Label(card, text="", bg=CARD, fg=FG, font=("Segoe UI", 10), anchor="ne", justify="right", wraplength=820)
            self.details.pack(fill="x", pady=(6, 0), anchor="e")
            quick = ttk.Frame(d)
            quick.pack(fill="x", pady=(12, 0))
            ttk.Button(quick, text="الذهاب للفحص", command=lambda: self.nb.select(self.tab_scan)).pack(side="right")
            ttk.Button(quick, text="تحقق من التحديث", command=lambda: self._check_update(silent=False)).pack(side="right", padx=8)

        def _row(self, parent, label, var, buttons):
            r = ttk.Frame(parent, style="Card.TFrame")
            r.pack(fill="x", pady=6)
            ttk.Label(r, text=label, style="Muted.TLabel").pack(anchor="e")
            line = ttk.Frame(r, style="Card.TFrame")
            line.pack(fill="x", pady=(4, 0))
            for text, cmd in buttons:
                ttk.Button(line, text=text, command=cmd).pack(side="right", padx=(6, 0))
            ttk.Entry(line, textvariable=var).pack(side="right", fill="x", expand=True, ipady=5)

        def _make_log(self, parent):
            fr = ttk.Frame(parent)
            txt = tk.Text(fr, bg="#0d0f14", fg=FG, bd=0, wrap="word", font=("Segoe UI", 10),
                          padx=14, pady=12, state="disabled", height=8)
            sb = ttk.Scrollbar(fr, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="left", fill="y")
            txt.pack(side="right", fill="both", expand=True)
            for tag, col in COLORS.items():
                txt.tag_configure(tag, foreground=col, justify="right", spacing3=3)
            txt.tag_configure("path", justify="left", font=("Consolas", 9))
            return fr, txt

        def _build_scan(self):
            t = self.tab_scan
            card = ttk.Frame(t, style="Card.TFrame", padding=16)
            card.pack(fill="x")
            self._row(card, "مجلد اللعبة أو ملف exe", self.path_var,
                      [("ملف exe", self._pick_exe), ("مجلد", self._pick_folder)])
            self._row(card, "مجلد حزمة الترجمة", self.pack_var, [("اختيار", self._pick_pack)])
            btns = ttk.Frame(t)
            btns.pack(fill="x", pady=8)
            self.act = ttk.Button(btns, text="تطبيق التعريب", style="Act.TButton", command=self._act, state="disabled")
            self.act.pack(side="right", padx=(8, 0))
            self.scan_btn = ttk.Button(btns, text="فحص اللعبة", command=self._scan)
            self.scan_btn.pack(side="right")
            self.prog = ttk.Progressbar(btns, mode="determinate", length=240)
            self.prog.pack(side="left")
            ttk.Label(btns, textvariable=self.prog_label, foreground=MUTED).pack(side="left", padx=10)
            fr, self.log = self._make_log(t)
            fr.pack(fill="both", expand=True)
            self._write(self.log, "info", "جاهز. حدد اللعبة واضغط «فحص اللعبة».")

        def _build_upd(self):
            u = self.tab_upd
            card = ttk.Frame(u, style="Card.TFrame", padding=18)
            card.pack(fill="x")
            self.u_cur = tk.Label(card, text=f"الإصدار الحالي: {APP_VERSION}", bg=CARD, fg=FG, font=("Segoe UI", 11), anchor="e")
            self.u_cur.pack(fill="x")
            self.u_new = tk.Label(card, text="آخر إصدار: لم يتم التحقق بعد", bg=CARD, fg=MUTED, font=("Segoe UI", 11), anchor="e")
            self.u_new.pack(fill="x", pady=(4, 0))
            self.u_notes = tk.Label(card, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 10), anchor="e", justify="right", wraplength=800)
            self.u_notes.pack(fill="x", pady=(8, 0))
            btns = ttk.Frame(u)
            btns.pack(fill="x", pady=10)
            self.u_btn = ttk.Button(btns, text="تحديث الآن", style="Act.TButton", command=self._do_update, state="disabled")
            self.u_btn.pack(side="right", padx=(8, 0))
            ttk.Button(btns, text="تحقق من التحديث", command=lambda: self._check_update(silent=False)).pack(side="right")
            fr, self.ulog = self._make_log(u)
            fr.pack(fill="both", expand=True)
            self._write(self.ulog, "info", "اضغط «تحقق من التحديث» لمعرفة إذا فيه إصدار جديد.")

        # ───── مسارات ─────
        def _pick_exe(self):
            p = filedialog.askopenfilename(filetypes=[("ملف تشغيل", "*.exe")])
            if p:
                self.path_var.set(p); self._reset()

        def _pick_folder(self):
            p = filedialog.askdirectory()
            if p:
                self.path_var.set(p); self._reset()

        def _pick_pack(self):
            p = filedialog.askdirectory()
            if p:
                self.pack_var.set(p)

        def _reset(self):
            self.ready = False
            self._restore_folder()
            self._refresh()

        def _restore_folder(self):
            raw = self.path_var.get().strip().strip('"')
            self.folder = None
            if raw:
                try:
                    p = Path(raw)
                    f = p.parent if p.is_file() else p
                    if f.exists():
                        self.folder = f
                except OSError:
                    pass

        # ───── السجل والطابور ─────
        def _write(self, widget, level, msg):
            widget.configure(state="normal")
            widget.insert("end", SYMS[level] + msg + "\n", level)
            widget.see("end")
            widget.configure(state="disabled")

        def _prog_cb(self, done, total, label):
            self.q.put(("prog", done, total, label))

        def _sink(self, target):
            return lambda level, msg: self.q.put(("log", target, level, msg))

        def _pump(self):
            try:
                while True:
                    try:
                        item = self.q.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        self._handle(item)
                    except Exception as e:  # noqa  (خطأ في عنصر واحد لا يوقف الواجهة)
                        path = log_crash("pump", e)
                        try:
                            self._write(self.log, "err", f"خطأ داخلي في الواجهة (سُجل في {path}): {e}")
                        except Exception:  # noqa
                            pass
                    if item[0] == "restart":
                        return
            finally:
                if self.winfo_exists():
                    self.after(80, self._pump)

        def _handle(self, item):
            kind = item[0]
            if kind == "log":
                text = str(item[3])
                # Tk يرفض الرموز خارج BMP؛ نستبدلها بدل ما ينهار
                text = "".join(ch if ord(ch) <= 0xFFFF else "?" for ch in text)
                self._write(self.log if item[1] == "scan" else self.ulog, item[2], text)
            elif kind == "prog":
                done, total, label = item[1], item[2], item[3]
                if total:
                    if str(self.prog.cget("mode")) != "determinate":
                        self.prog.stop()
                        self.prog.configure(mode="determinate")
                    self.prog.configure(maximum=total, value=done)
                    self.prog_label.set(f"{label}  {done}/{total}")
                else:
                    if str(self.prog.cget("mode")) != "indeterminate":
                        self.prog.configure(mode="indeterminate")
                        self.prog.start(12)
                    self.prog_label.set(str(label))
            elif kind == "done":
                self.busy = False
                self.prog.stop()
                self.prog.configure(mode="determinate", value=0)
                self.prog_label.set("")
                self.lib.sync()
                self._refresh()
            elif kind == "upd":
                self.upd = item[1]
                self._render_upd(item[2])
            elif kind == "restart":
                if messagebox.askyesno("إعادة التشغيل", "تم التحديث. تبي تعيد تشغيل البرنامج الحين؟"):
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "arabizer.py")])
                    self.destroy()

        def report_callback_exception(self, exc, val, tb):
            path = log_crash("tk-callback", val)
            try:
                self._write(self.log, "err", f"خطأ داخلي (سُجل في {path}): {val}")
            except Exception:  # noqa
                pass

        # ───── العرض ─────
        def _refresh(self):
            m = None
            if self.folder:
                try:
                    m = load_manifest(self.folder)
                except Exception:
                    m = None
            if m:
                self.mode = "undo"
                self.act.configure(text="التراجع عن التعريب", style="Undo.TButton")
            else:
                self.mode = "apply"
                self.act.configure(text="تطبيق التعريب", style="Act.TButton")
            enabled = (not self.busy) and (self.ready or bool(m))
            self.act.configure(state="normal" if enabled else "disabled")
            self.scan_btn.configure(state="disabled" if self.busy else "normal")
            self._dash(m)

        def _dash(self, m):
            v, s = self.t_status
            if m:
                v.configure(text="مطبّق", fg=GOOD)
                s.configure(text=f"{len(m.get('added', []))} ملف مضاف · {len(m.get('replaced', []))} مستبدل\n{m.get('created', '')}")
            else:
                v.configure(text="غير مطبّق", fg=MUTED)
                s.configure(text="")
            L = self.last
            v, s = self.t_game
            name = L.get("game") or (self.folder.name if self.folder else "لم تُحدد")
            v.configure(text=name)
            s.configure(text=L.get("engine", ""))
            v, s = self.t_scan
            v.configure(text=L.get("time", "لم يتم بعد"))
            if L:
                bad = L.get("errors", 0)
                v.configure(fg=BAD if bad else FG)
                s.configure(text=f"مشاكل: {bad} · تنبيهات: {L.get('warnings', 0)}")
            else:
                s.configure(text="")
            v, s = self.t_ver
            v.configure(text=f"v{APP_VERSION}")
            u = self.upd
            if u and u["status"] == "new":
                s.configure(text=f"يتوفر تحديث: {u['latest']}", fg=WARN)
            elif u and u["status"] == "current":
                s.configure(text="أنت على آخر إصدار", fg=GOOD)
            elif u:
                s.configure(text="تعذر التحقق", fg=MUTED)
            else:
                s.configure(text="لم يتم التحقق", fg=MUTED)
            lines = []
            if L:
                lines.append(f"ملف التشغيل: {L.get('exe', '—')}")
                lines.append(f"المحرك: {L.get('engine', '—')}")
                lines.append(f"ملفات نصية مكشوفة: {L.get('loose', 0)}")
                lines.append(f"ملفات نصية محتملة داخل الأرشيفات: {L.get('archive_hits', 0)}")
                tot, bad = L.get("psarc_total", 0), L.get("psarc_unreadable", 0)
                lines.append(f"أرشيفات PSARC: {tot - bad} مقروءة من {tot}")
                if bad:
                    lines.append(f"⚠ {bad} أرشيف ما انقرى (صيغة مختلفة، تحتاج وحدة استخراج خاصة)")
            else:
                lines.append("ما فيه فحص بعد. روح لتبويب «الفحص والتعريب».")
            self.details.configure(text="\n".join(lines))
            self._render_lib()

        def _render_lib(self):
            sel = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            for g in sorted(self.lib.games, key=lambda g: (not g.get("applied"), g.get("name", "").lower())):
                files = f"{g.get('added', 0)} + {g.get('replaced', 0)}" if g.get("applied") else "—"
                self.tree.insert("", "end", iid=g["id"],
                                 values=(files, g.get("applied_at", "")[:16] if g.get("applied") else "—",
                                         Library.status_text(g), g.get("name", "")))
            if sel and self.tree.exists(sel[0]):
                self.tree.selection_set(sel[0])

        def _lib_selected(self):
            sel = self.tree.selection()
            return self.lib.by_id(sel[0]) if sel else None

        def _lib_use(self):
            g = self._lib_selected()
            if not g:
                messagebox.showinfo("المكتبة", "اختر لعبة من القائمة أولاً.")
                return
            self.path_var.set(g["folder"])
            self._reset()
            self.nb.select(self.tab_scan)

        def _lib_open(self):
            g = self._lib_selected()
            if not g or g.get("missing"):
                messagebox.showinfo("المكتبة", "اختر لعبة موجود مجلدها.")
                return
            try:
                if os.name == "nt":
                    os.startfile(g["folder"])  # noqa
                else:
                    subprocess.Popen(["xdg-open", g["folder"]])
            except Exception as e:  # noqa
                messagebox.showerror("المكتبة", f"تعذر فتح المجلد: {e}")

        def _lib_remove(self):
            g = self._lib_selected()
            if not g:
                messagebox.showinfo("المكتبة", "اختر لعبة من القائمة أولاً.")
                return
            if g.get("applied"):
                messagebox.showinfo("المكتبة", "هذي اللعبة معرّبة. تراجع عن التعريب أولاً ثم احذفها من القائمة.")
                return
            if messagebox.askyesno("المكتبة", "حذف اللعبة من القائمة؟ (ما يمس ملفات اللعبة)"):
                self.lib.remove(g["id"])
                self._render_lib()

        def _render_upd(self, silent):
            u = self.upd
            if u["status"] == "error":
                self.u_new.configure(text="آخر إصدار: تعذر التحقق", fg=MUTED)
                self.u_btn.configure(state="disabled")
                self.banner.pack_forget()
                if not silent:
                    self._write(self.ulog, "err", u["msg"])
            elif u["status"] == "new":
                info = u["info"]
                self.u_new.configure(text=f"آخر إصدار: {u['latest']}  (يتوفر تحديث)", fg=WARN)
                self.u_notes.configure(text=("ملاحظات الإصدار: " + str(info.get("notes", ""))) if info.get("notes") else "")
                self.u_btn.configure(state="normal")
                self.banner_lbl.configure(text=f"يتوفر إصدار جديد: {u['latest']}")
                self.banner.pack(fill="x", pady=(0, 6), before=self.tiles_fr)
                if not silent:
                    self._write(self.ulog, "warn", f"يتوفر إصدار جديد: {u['latest']}")
            else:
                self.u_new.configure(text=f"آخر إصدار: {u['latest']}", fg=GOOD)
                self.u_btn.configure(state="disabled")
                self.banner.pack_forget()
                if not silent:
                    self._write(self.ulog, "ok", "أنت على آخر إصدار.")
            self._dash(None if not self.folder else self._safe_manifest())

        def _safe_manifest(self):
            try:
                return load_manifest(self.folder)
            except Exception:
                return None

        # ───── العمليات ─────
        def _run(self, fn):
            self.busy = True
            self.prog.configure(mode="indeterminate")
            self.prog.start(12)
            self._refresh()

            def work():
                try:
                    fn()
                except BaseException as e:  # noqa
                    path = log_crash("worker", e)
                    self.q.put(("log", "scan", "err", f"خطأ غير متوقع: {e} (سُجل في {path})"))
                self.q.put(("done",))
            threading.Thread(target=work, daemon=True).start()

        def _scan(self):
            path = self.path_var.get()
            pack = self.pack_var.get()

            def job():
                rep = Report(self._sink("scan"), self._prog_cb)
                folder, _ = full_scan(path, rep)
                self.folder = folder
                self.ready = bool(folder) and rep.errors == 0
                if folder:
                    self.last = dict(rep.summary)
                    self.last.update(time=time.strftime("%Y-%m-%d %H:%M"), errors=rep.errors,
                                     warnings=rep.warnings, folder=str(folder))
                    self.cfg.update(game_path=path, pack_path=pack, last_scan=self.last)
                    save_config(self.cfg)
                    self.lib.record_scan(folder, rep.summary)
            self._run(job)

        def _act(self):
            if not self.folder:
                return
            folder = self.folder
            if self.mode == "undo":
                if not messagebox.askyesno("تأكيد", "بيتم حذف الملفات المضافة واسترجاع الأصلية. تكمل؟"):
                    return

                def do_undo():
                    if undo(folder, Report(self._sink("scan"), self._prog_cb)):
                        self.lib.record_undo(folder)
                self._run(do_undo)
            else:
                raw = self.pack_var.get().strip().strip('"')
                pack = Path(raw) if raw else None
                self.cfg.update(pack_path=raw)
                save_config(self.cfg)

                def do_apply():
                    if apply_pack(folder, pack, Report(self._sink("scan"), self._prog_cb)):
                        self.lib.record_apply(folder, load_manifest(folder) or {}, pack)
                self._run(do_apply)

        def _check_update(self, silent):
            if not silent:
                self._write(self.ulog, "info", "جاري التحقق من التحديث...")

            def work():
                self.q.put(("upd", check_update(), silent))
            threading.Thread(target=work, daemon=True).start()

        def _do_update(self):
            u = self.upd
            if not u or u["status"] != "new":
                return
            if not messagebox.askyesno("تحديث", f"تحديث البرنامج إلى {u['latest']}؟ بنحتفظ بنسخة من الإصدار الحالي."):
                return
            self.nb.select(self.tab_upd)
            info = u["info"]

            def work():
                rep = Report(self._sink("upd"))
                if apply_update(info, rep):
                    self.q.put(("restart",))
            threading.Thread(target=work, daemon=True).start()

    app = App()
    app.mainloop()
    return app


