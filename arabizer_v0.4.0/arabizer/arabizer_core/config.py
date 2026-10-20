"""إعدادات المستخدم وسجل الأعطال."""
import json
import time

from . import __version__ as APP_VERSION
from .constants import CONFIG_DIR, CONFIG_FILE


def log_crash(where, exc=None):
    """يكتب العطل في ~/.arabizer/crash.log (لا يرمي أخطاء أبداً)."""
    try:
        import traceback
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_DIR / "crash.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} | {where} | v{APP_VERSION}\n")
            if exc is None:
                f.write(traceback.format_exc())
            else:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return str(CONFIG_DIR / "crash.log")
    except Exception:  # noqa
        return ""


def mark_step(text):
    """آخر خطوة بدأ بها الفحص: لو انهار البرنامج بشكل كامل نعرف عند أي ملف."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        (CONFIG_DIR / "last_step.txt").write_text(f"{time.strftime('%H:%M:%S')} | {text}\n", encoding="utf-8")
    except Exception:  # noqa
        pass


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


