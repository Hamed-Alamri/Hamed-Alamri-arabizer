"""Versionable phrase glossary with conflict detection."""
from __future__ import annotations
import json, re, unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

def normalize_key(value: str) -> str:
    value=unicodedata.normalize("NFKC",value).replace("\u2019","'").replace("\u2018","'")
    return re.sub(r"\s+"," ",value).strip().casefold()

@dataclass(frozen=True)
class GlossaryEntry:
    source: str
    target: str
    category: str = "general"
    context: str = ""
    notes: str = ""
    approved: bool = True
    case_sensitive: bool = False

class GlossaryConflict(ValueError): pass

class Glossary:
    def __init__(self, entries: Iterable[GlossaryEntry] = ()):
        self._entries={}
        for entry in entries: self.add(entry)
    def add(self, entry):
        if not entry.source.strip() or not entry.target.strip(): raise ValueError("Glossary source and target must not be empty")
        key=entry.source if entry.case_sensitive else normalize_key(entry.source)
        old=self._entries.get(key)
        if old and old.target != entry.target and old.approved and entry.approved:
            raise GlossaryConflict(f"Conflicting approved translations for {entry.source!r}")
        self._entries[key]=entry
    def lookup_exact(self, phrase):
        entry=self._entries.get(normalize_key(phrase))
        if entry and entry.approved: return entry
        for candidate in self._entries.values():
            if candidate.case_sensitive and candidate.source==phrase and candidate.approved: return candidate
        return None
    def entries(self): return tuple(self._entries.values())
    @classmethod
    def from_json(cls,path):
        data=json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data,list): raise ValueError("Glossary JSON root must be a list")
        return cls(GlossaryEntry(source=str(x.get("source","")),target=str(x.get("target","")),category=str(x.get("category","general")),context=str(x.get("context","")),notes=str(x.get("notes","")),approved=bool(x.get("approved",True)),case_sensitive=bool(x.get("case_sensitive",False))) for x in data)
    def save_json(self,path):
        payload=[e.__dict__ for e in sorted(self._entries.values(),key=lambda x:normalize_key(x.source))]
        Path(path).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
