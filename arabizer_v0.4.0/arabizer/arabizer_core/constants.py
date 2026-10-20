"""ثوابت مشتركة."""
from pathlib import Path

APP_NAME = "Arabizer"
REPO = "Hamed-Alamri/Hamed-Alamri-arabizer"
BRANCH = "main"
CONFIG_DIR = Path.home() / ".arabizer"
CONFIG_FILE = CONFIG_DIR / "config.json"
MANIFEST = "arabizer_manifest.json"
BACKUP_DIR = "_arabizer_backup"

TEXT_EXT = {".json", ".csv", ".tsv", ".xml", ".po", ".pot", ".txt", ".loc", ".locres",
            ".srt", ".ini", ".yaml", ".yml", ".strings", ".lang", ".tbl", ".bin"}
KEYWORDS = ("loc", "text", "subtitle", "string", "lang", "dialog", "dialogue", "menu", "caption")
ARCHIVE_EXT = {".psarc", ".pak", ".pck", ".vpk", ".bank", ".arc", ".rpf", ".big", ".wad", ".zip"}
SKIP_EXE_WORDS = ("unins", "crash", "redist", "setup", "vc_redist", "dxsetup", "helper", "handler", "uploader")
