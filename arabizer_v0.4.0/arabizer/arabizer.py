# -*- coding: utf-8 -*-
"""نقطة الدخول: python arabizer.py  (الواجهة)  |  --release [ملاحظات]  |  --version"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arabizer_core import __version__  # noqa: E402
from arabizer_core.config import log_crash  # noqa: E402


def run():
    if "--version" in sys.argv:
        print(__version__)
        return
    if "--release" in sys.argv:
        from arabizer_core.updater import make_release
        i = sys.argv.index("--release")
        make_release(" ".join(sys.argv[i + 1:]))
        return
    from arabizer_core.gui import main
    main()


if __name__ == "__main__":
    def _hook(t, v, tb):
        log_crash("uncaught", v)
        sys.__excepthook__(t, v, tb)
    sys.excepthook = _hook
    try:
        import threading
        threading.excepthook = lambda a: log_crash("thread", a.exc_value)
    except Exception:  # noqa
        pass
    run()
