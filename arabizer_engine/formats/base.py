"""Safe format adapter contract."""
from __future__ import annotations
from abc import ABC,abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..models import TextUnit

@dataclass
class ExtractedDocument:
    path: Path
    format_name: str
    units: list[TextUnit]
    original_bytes: bytes
    metadata: dict[str,Any]

class FormatAdapter(ABC):
    name: str
    @abstractmethod
    def can_read(self,path: Path,data: bytes)->bool: raise NotImplementedError
    @abstractmethod
    def extract(self,path: Path,data: bytes)->ExtractedDocument: raise NotImplementedError
    @abstractmethod
    def rebuild(self,document: ExtractedDocument,translations: dict[str,str])->bytes:
        """Return rebuilt bytes; never overwrite source files here."""
        raise NotImplementedError
