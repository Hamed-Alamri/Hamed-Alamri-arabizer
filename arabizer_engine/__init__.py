"""Deterministic, extensible game-localization core for Arabizer."""
__version__ = "1.0.0-dev"

from .models import Issue, Severity, TextUnit, ValidationReport
from .pipeline import LocalizationPipeline, PipelineResult

__all__ = [
    "Issue", "Severity", "TextUnit", "ValidationReport",
    "LocalizationPipeline", "PipelineResult",
]
