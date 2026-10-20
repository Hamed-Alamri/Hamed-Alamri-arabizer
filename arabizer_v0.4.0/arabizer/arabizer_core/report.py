"""تقرير الفحص: مستقبِل رسائل + عدّاد أخطاء/تنبيهات + ملخص + تقدم."""


class Report:
    def __init__(self, sink=None, progress_cb=None):
        self.sink = sink
        self.progress_cb = progress_cb
        self.errors = 0
        self.warnings = 0
        self.summary = {}

    def _emit(self, level, msg):
        if self.sink:
            self.sink(level, msg)

    def info(self, m): self._emit("info", m)
    def ok(self, m): self._emit("ok", m)
    def warn(self, m): self.warnings += 1; self._emit("warn", m)
    def err(self, m): self.errors += 1; self._emit("err", m)
    def path(self, m): self._emit("path", m)

    def progress(self, done, total=None, label=""):
        """تقدم العملية: total=None يعني غير معروف (شريط متحرك)."""
        if self.progress_cb:
            self.progress_cb(done, total, label)


