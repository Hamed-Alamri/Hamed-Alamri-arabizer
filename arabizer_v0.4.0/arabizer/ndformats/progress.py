# -*- coding: utf-8 -*-
"""شريط تقدم ديناميكي لسطر الأوامر (سطر واحد يتحدث بدل مئات الأسطر).

- في الطرفية التفاعلية: سطر واحد بـ \r، وبحد أقصى ~10 تحديثات بالثانية.
- خارج الطرفية (ملف/أنبوب): سطر عند كل 10% فقط.
- ASCII فقط عمداً (لا اتجاه نص مختلط يربك الطرفية).
"""
import sys
import time


class ProgressBar:
    def __init__(self, stream=None, width=28, min_interval=0.1, clock=time.monotonic):
        self.stream = stream or sys.stdout
        self.width = width
        self.min_interval = min_interval
        self.clock = clock
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._label = None
        self._last_t = 0.0
        self._last_pct = -1
        self._last_len = 0
        self._start = None
        self._open = False

    def update(self, done, total, label=""):
        if not total or total <= 0:
            return
        if label != self._label:
            self.close()
            self._label = label
            self._start = self.clock()
            self._last_pct = -1
            self._last_t = 0.0
        pct = min(100, int(done * 100 / total))
        now = self.clock()
        final = done >= total
        if self.tty:
            if not final and now - self._last_t < self.min_interval:
                return
            self._last_t = now
            filled = int(self.width * pct / 100)
            bar = "#" * filled + "-" * (self.width - filled)
            eta = ""
            elapsed = now - self._start
            if 0 < done < total and elapsed > 1:
                eta = f" ETA {int(elapsed * (total - done) / done)}s"
            line = f"[{bar}] {pct:3d}% {done}/{total}{eta}"
            pad = " " * max(0, self._last_len - len(line))
            self.stream.write("\r" + line + pad)
            self.stream.flush()
            self._last_len = len(line)
            self._open = True
        else:
            step = pct // 10
            if step != self._last_pct or final:
                if step != self._last_pct:
                    self._last_pct = step
                    self.stream.write(f"{pct}% ({done}/{total})\n")
                    self.stream.flush()

    def close(self):
        if self._open:
            self.stream.write("\n")
            self.stream.flush()
        self._open = False
        self._last_len = 0
