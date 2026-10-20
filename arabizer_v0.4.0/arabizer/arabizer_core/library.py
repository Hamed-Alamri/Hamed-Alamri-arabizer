"""مكتبة الألعاب: تحفظ الألعاب التي فُحصت أو عُرّبت (تبقى بعد إغلاق البرنامج).

الملف: ~/.arabizer/library.json
كل لعبة: id, name, folder, exe, engine, applied, applied_at, added, replaced, pack, missing, history
الحقيقة الفعلية للتعريب هي ملف arabizer_manifest.json داخل مجلد اللعبة؛ sync() تزامن المكتبة معه
(مثلاً لو حُذف التعريب يدوياً أو انحذف المجلد).
"""
import hashlib
import json
import os
import time
from pathlib import Path

from .constants import CONFIG_DIR, MANIFEST

HISTORY_CAP = 50


def game_id(folder):
    return hashlib.sha1(str(Path(folder).resolve()).lower().encode("utf-8")).hexdigest()[:12]


class Library:
    def __init__(self, path=None):
        self.path = Path(path) if path else CONFIG_DIR / "library.json"
        self.games = []
        self.load()

    # ----- حفظ وتحميل -----
    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            games = data.get("games", []) if isinstance(data, dict) else []
            self.games = [g for g in games if isinstance(g, dict) and g.get("folder") and g.get("id")]
        except Exception:  # noqa  (ملف غير موجود أو تالف: نبدأ فارغاً بدون انهيار)
            self.games = []

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"version": 1, "games": self.games}, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # ----- استعلام -----
    def get(self, folder):
        gid = game_id(folder)
        return next((g for g in self.games if g["id"] == gid), None)

    def by_id(self, gid):
        return next((g for g in self.games if g["id"] == gid), None)

    def applied_games(self):
        return [g for g in self.games if g.get("applied")]

    # ----- تحديث -----
    def _ensure(self, folder):
        g = self.get(folder)
        if g is None:
            g = {"id": game_id(folder), "name": Path(folder).name, "folder": str(Path(folder).resolve()),
                 "exe": "", "engine": "", "applied": False, "applied_at": "", "added": 0,
                 "replaced": 0, "pack": "", "missing": False, "history": []}
            self.games.append(g)
        return g

    @staticmethod
    def _hist(g, action):
        g["history"].append({"action": action, "time": time.strftime("%Y-%m-%d %H:%M")})
        del g["history"][:-HISTORY_CAP]

    def record_scan(self, folder, summary):
        g = self._ensure(folder)
        g["name"] = summary.get("game") or g["name"]
        g["engine"] = summary.get("engine", g["engine"])
        g["exe"] = summary.get("exe", g["exe"])
        g["missing"] = False
        self.save()
        return g

    def record_apply(self, folder, manifest, pack=""):
        g = self._ensure(folder)
        g.update(applied=True, applied_at=manifest.get("created", time.strftime("%Y-%m-%d %H:%M:%S")),
                 added=len(manifest.get("added", [])), replaced=len(manifest.get("replaced", [])),
                 pack=str(pack or manifest.get("pack", "")), missing=False)
        self._hist(g, "apply")
        self.save()
        return g

    def record_undo(self, folder):
        g = self._ensure(folder)
        g.update(applied=False, added=0, replaced=0)
        self._hist(g, "undo")
        self.save()
        return g

    def remove(self, gid):
        n = len(self.games)
        self.games = [g for g in self.games if g["id"] != gid]
        if len(self.games) != n:
            self.save()
            return True
        return False

    def sync(self):
        """يزامن الحالة مع القرص. يرجع True إذا تغير شيء."""
        changed = False
        for g in self.games:
            folder = Path(g["folder"])
            missing = not folder.exists()
            if g.get("missing") != missing:
                g["missing"] = missing
                changed = True
            has_manifest = (not missing) and (folder / MANIFEST).exists()
            if g.get("applied") and not missing and not has_manifest:
                g.update(applied=False, added=0, replaced=0)  # أُزيل التعريب خارج البرنامج
                self._hist(g, "removed-outside")
                changed = True
            elif not g.get("applied") and has_manifest:
                try:
                    m = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
                    g.update(applied=True, added=len(m.get("added", [])), replaced=len(m.get("replaced", [])),
                             applied_at=m.get("created", ""))
                    self._hist(g, "found-on-disk")
                    changed = True
                except Exception:  # noqa
                    pass
        if changed:
            self.save()
        return changed

    @staticmethod
    def status_text(g):
        if g.get("missing"):
            return "المجلد غير موجود"
        return "معرّبة" if g.get("applied") else "غير معرّبة"
