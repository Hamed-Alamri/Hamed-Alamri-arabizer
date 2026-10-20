# -*- coding: utf-8 -*-
"""فحص التثبيت: يتأكد أن كل ملفات البرنامج موجودة وسليمة، ويعطيك الأمر الصحيح للتشغيل."""
import hashlib
import json
import sys
from pathlib import Path

base = Path(__file__).resolve().parent
DEFAULT = (["arabizer.py", "dsar_reader.py", "check_install.py"]
           + [f"arabizer_core/{n}.py" for n in ("__init__", "checks", "config", "constants", "gui", "library",
                                               "patcher", "report", "scan", "updater", "util")]
           + [f"ndformats/{n}.py" for n in ("__init__", "__main__", "analyze", "cli", "dsar", "errors",
                                            "lz4block", "progress", "psar", "source", "unpack")])
problems = 0
print(f"مجلد البرنامج: {base}")
print(f"إصدار Python: {sys.version.split()[0]}")
if sys.version_info < (3, 10):
    print("✖ يلزم Python 3.10 أو أحدث")
    problems += 1

expected = {r: None for r in DEFAULT}
vj = base / "version.json"
if vj.exists():
    try:
        info = json.loads(vj.read_text(encoding="utf-8"))
        expected = {f["path"]: f["sha256"] for f in info.get("files", [])} or expected
        print(f"إصدار الملفات حسب version.json: {info.get('version')}")
    except Exception as e:  # noqa
        print(f"⚠ تعذر قراءة version.json: {e}")
else:
    print("⚠ ما لقيت version.json (عادي لو ما رفعت إصداراً بعد)")

for rel, sha in expected.items():
    p = base / rel
    if not p.exists():
        print(f"✖ ناقص: {rel}")
        problems += 1
    elif sha and hashlib.sha256(p.read_bytes()).hexdigest() != sha:
        print(f"✖ الملف مختلف عن الإصدار المسجل: {rel}")
        problems += 1

sys.path.insert(0, str(base))
for mod in ("ndformats", "arabizer_core"):
    try:
        m = __import__(mod)
        print(f"✔ وحدة {mod} تعمل (الإصدار {getattr(m, '__version__', '?')})")
    except Exception as e:  # noqa
        print(f"✖ تعذر استيراد {mod}: {e}")
        problems += 1
try:
    import lz4.block  # noqa
    print("✔ مكتبة lz4 مثبتة (فك أسرع)")
except Exception:  # noqa
    print("• مكتبة lz4 غير مثبتة: البرنامج يعمل بالمفك المدمج لكن أبطأ (python -m pip install lz4)")

print("-" * 40)
if problems:
    print(f"النتيجة: فيه {problems} مشكلة. أعد فك الحزمة كاملة في مجلد جديد.")
else:
    print("النتيجة: التثبيت سليم.")
    print("للتشغيل من أي مجلد استخدم:")
    print(f'  python "{base / "dsar_reader.py"}" inspect "<مسار bin.psarc>" --deep')
sys.exit(1 if problems else 0)
