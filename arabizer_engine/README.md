# Arabizer Engine (development)

This package is the start of a new, testable localization core. It is intentionally separate from the existing desktop application until adapters and safety gates are tested.

## Current modules

- `models.py`: stable text-unit and validation issue models.
- `glossary.py`: editable JSON glossary, normalized exact matching, and conflicting approved-term rejection.
- `pipeline.py`: deterministic approved-phrase matching; unknown text stays unchanged and is marked for review. An optional translator callback can be supplied by a future translation provider.
- `validators.py`: checks placeholders/markup, empty output, residual Latin letters, and suspicious whitespace.
- `formats/base.py`: adapter contract for format-specific extraction/rebuild.
- `formats/json_adapter.py`: conservative JSON text-key extraction and structural rebuilding.
- `glossaries/ar_formal_core.json`: starter glossary in formal Arabic.
- `../tests/test_engine.py`: unit tests for the first supported behaviors.

## Run tests

From the repository root, with Python 3.10+:

```bash
python -m unittest discover -s tests -v
```

## Safety rules

1. Unknown phrases are not described as translated; they are marked `needs_review`.
2. A proposed translation that changes protected tokens is blocked.
3. Format adapters return rebuilt bytes; they must not overwrite the original game files.
4. JSON support is limited to configured human-facing keys. It is not a universal game-file parser.
5. Text quality cannot be guaranteed by code alone. New glossary entries and provider output need contextual and linguistic evaluation.

## Current limitations

This is not yet wired into the existing desktop GUI, does not yet apply translations to game folders, and does not support binary game formats, Unreal localization packages, Unity bundles, or proprietary archives through this new engine. Each format requires a dedicated adapter and round-trip tests before it can be enabled.
