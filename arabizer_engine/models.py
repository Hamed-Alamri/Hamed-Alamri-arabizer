"""Shared typed models."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class Severity(str, Enum):
    INFO="info"; WARNING="warning"; ERROR="error"; BLOCKER="blocker"

@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: Severity
    unit_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class TextUnit:
    unit_id: str
    source: str
    context: str = ""
    speaker: str = ""
    source_file: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    @property
    def blockers(self): return [i for i in self.issues if i.severity == Severity.BLOCKER]
    @property
    def errors(self): return [i for i in self.issues if i.severity == Severity.ERROR]
    @property
    def passed(self): return not any(i.severity in (Severity.ERROR, Severity.BLOCKER) for i in self.issues)
    def add(self, code, message, severity, unit_id=None, **details):
        self.issues.append(Issue(code, message, severity, unit_id, details))
