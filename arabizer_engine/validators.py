"""Translation safety gates for placeholders, markup and residual Latin text."""
from __future__ import annotations
import re
from collections import Counter
from .models import Severity, TextUnit, ValidationReport

TOKEN_PATTERNS=(re.compile(r"\{[^{}]+\}"),re.compile(r"%\d*\$?[sdifouxXeEgGc]"),re.compile(r"<\/?[A-Za-z][^>]*>"),re.compile(r"\[[A-Za-z_][^\]]*\]"),re.compile(r"\\[nrt]"))
LATIN_RE=re.compile(r"[A-Za-z]")
ARABIC_RE=re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")

def protected_tokens(text):
    tokens=Counter()
    for pattern in TOKEN_PATTERNS: tokens.update(pattern.findall(text))
    return tokens

def validate_translation(unit: TextUnit,target: str) -> ValidationReport:
    report=ValidationReport()
    if not target.strip():
        report.add("EMPTY_TRANSLATION","الترجمة فارغة.",Severity.BLOCKER,unit.unit_id); return report
    source_tokens,target_tokens=protected_tokens(unit.source),protected_tokens(target)
    if source_tokens!=target_tokens:
        report.add("TOKEN_MISMATCH","تغيّرت متغيرات أو وسوم تنسيق مقارنة بالنص الأصلي.",Severity.BLOCKER,unit.unit_id,source_tokens=dict(source_tokens),target_tokens=dict(target_tokens))
    if target!=target.strip(): report.add("OUTER_WHITESPACE","مسافات زائدة في بداية الترجمة أو نهايتها.",Severity.WARNING,unit.unit_id)
    if LATIN_RE.search(target): report.add("LATIN_REMAINS","بقيت أحرف لاتينية؛ قد تكون اسمًا أو نصًا غير مترجم.",Severity.WARNING,unit.unit_id)
    if not ARABIC_RE.search(target) and ARABIC_RE.search(unit.source): report.add("NO_ARABIC_OUTPUT","النص المستهدف لا يحتوي على أحرف عربية.",Severity.ERROR,unit.unit_id)
    if "\ufffd" in target or "\x00" in target: report.add("INVALID_TEXT_MARKER","النص يحتوي على محرف غير صالح.",Severity.BLOCKER,unit.unit_id)
    if re.search(r"\s{2,}",target): report.add("REPEATED_SPACES","توجد مسافات متكررة تستحق المراجعة.",Severity.WARNING,unit.unit_id)
    return report

def validate_batch_consistency(items):
    report,seen=ValidationReport(),{}
    for unit,target in items:
        key=" ".join(unit.source.casefold().split())
        if key in seen and seen[key][0]!=target:
            report.add("INCONSISTENT_DUPLICATE","النص نفسه تُرجم بصيغ مختلفة؛ تحقق من السياق.",Severity.WARNING,unit.unit_id,previous_unit_id=seen[key][1],previous_target=seen[key][0],current_target=target)
        else: seen[key]=(target,unit.unit_id)
    return report
