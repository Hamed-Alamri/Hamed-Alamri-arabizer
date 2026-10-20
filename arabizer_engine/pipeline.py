"""Review-first translation pipeline; unknown text is never falsely approved."""
from __future__ import annotations
import re
from dataclasses import dataclass,field
from typing import Callable
from .glossary import Glossary,GlossaryEntry
from .models import Issue,Severity,TextUnit,ValidationReport
from .validators import validate_translation,validate_batch_consistency

@dataclass
class PipelineResult:
    unit: TextUnit
    target: str
    status: str
    issues: list[Issue]=field(default_factory=list)

DEFAULT_ENTRIES=(
 GlossaryEntry("Fuck you!","اللعنة عليك!","insult"),
 GlossaryEntry("Fuck you","اللعنة عليك","insult"),
 GlossaryEntry("What the hell?!","ما هذا بحق الجحيم؟!","exclamation"),
 GlossaryEntry("What the hell?","ما هذا بحق الجحيم؟","exclamation"),
 GlossaryEntry("You're screwed.","لقد انتهى أمرك.","idiom"),
 GlossaryEntry("Get out of here!","اخرج من هنا!","command"),
 GlossaryEntry("Thank you.","شكرًا لك."),GlossaryEntry("Thank you!","شكرًا لك!"),
 GlossaryEntry("Hello.","مرحبًا."),GlossaryEntry("Hello!","مرحبًا!"),GlossaryEntry("Goodbye.","إلى اللقاء."),
)

class LocalizationPipeline:
    def __init__(self,glossary=None,translator: Callable[[TextUnit],str|None]|None=None):
        self.glossary=glossary or Glossary(DEFAULT_ENTRIES); self.translator=translator
    def translate_one(self,unit):
        exact=self.glossary.lookup_exact(unit.source)
        if exact: target,status=exact.target,"translated"
        elif not unit.source.strip(): target,status=unit.source,"skipped"
        elif not re.search(r"[A-Za-z]",unit.source): target,status=unit.source,"preserved"
        elif self.translator:
            candidate=self.translator(unit)
            if candidate and candidate.strip(): target,status=candidate.strip(),"needs_validation"
            else: target,status=unit.source,"needs_review"
        else: target,status=unit.source,"needs_review"
        report=validate_translation(unit,target) if status not in ("skipped","preserved") else ValidationReport()
        issues=list(report.issues)
        if status=="needs_review":
            issues.append(Issue("NO_APPROVED_TRANSLATION","لا توجد ترجمة معتمدة؛ تُرك النص كما هو ولم يُعتمد.",Severity.WARNING,unit.unit_id,{"source":unit.source,"context":unit.context}))
        if status=="needs_validation" and not report.passed:
            status="blocked"; issues.append(Issue("VALIDATION_FAILED","فشل فحص الترجمة؛ لا تعتمد تلقائيًا.",Severity.BLOCKER,unit.unit_id))
        elif status=="needs_validation": status="translated_unreviewed"
        return PipelineResult(unit,target,status,issues)
    def translate_batch(self,units):
        results=[self.translate_one(unit) for unit in units]
        consistency=validate_batch_consistency([(r.unit,r.target) for r in results])
        for result in results: result.issues.extend(i for i in consistency.issues if i.unit_id==result.unit.unit_id)
        return results
