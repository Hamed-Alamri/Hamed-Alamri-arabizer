"""Conservative JSON extraction and rebuilding adapter."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from ..models import TextUnit
from .base import ExtractedDocument,FormatAdapter
DEFAULT_TEXT_KEYS={"text","subtitle","dialogue","description","title","message","content","name"}

class JsonAdapter(FormatAdapter):
    name="json"
    def __init__(self,text_keys=None): self.text_keys={k.casefold() for k in (text_keys or DEFAULT_TEXT_KEYS)}
    def can_read(self,path: Path,data: bytes)->bool:
        if path.suffix.casefold()!=".json": return False
        try: json.loads(data.decode("utf-8-sig")); return True
        except (UnicodeDecodeError,json.JSONDecodeError): return False
    def extract(self,path: Path,data: bytes)->ExtractedDocument:
        parsed=json.loads(data.decode("utf-8-sig")); units=[]
        def walk(value: Any,pointer="$",parent_key=""):
            if isinstance(value,dict):
                for key,child in value.items():
                    p=f"{pointer}/{key.replace('~','~0').replace('/','~1')}"
                    if isinstance(child,str) and key.casefold() in self.text_keys and child.strip():
                        units.append(TextUnit(p,child,context=f"JSON key: {key}",source_file=str(path)))
                    else: walk(child,p,key)
            elif isinstance(value,list):
                for i,child in enumerate(value): walk(child,f"{pointer}/{i}",parent_key)
        walk(parsed)
        return ExtractedDocument(path,self.name,units,data,{"parsed":parsed,"encoding":"utf-8-sig"})
    def rebuild(self,document,translations):
        def walk(value: Any,pointer="$",parent_key=""):
            if isinstance(value,dict):
                result={}
                for key,child in value.items():
                    p=f"{pointer}/{key.replace('~','~0').replace('/','~1')}"
                    if isinstance(child,str) and key.casefold() in self.text_keys and p in translations: result[key]=translations[p]
                    else: result[key]=walk(child,p,key)
                return result
            if isinstance(value,list): return [walk(child,f"{pointer}/{i}",parent_key) for i,child in enumerate(value)]
            return value
        return (json.dumps(walk(document.metadata["parsed"]),ensure_ascii=False,indent=2)+"\n").encode("utf-8")
