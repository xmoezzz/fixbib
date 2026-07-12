from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Confidence = Literal["exact", "high", "low", "conflict", "not_found"]
SourceKind = Literal[
    "publisher_native_export",
    "repository_native_export",
    "publisher_page_metadata",
    "registry_metadata",
    "registry_transform",
    "bibliographic_index_export",
    "bibliographic_index",
    "book_catalog",
    "generic_web_metadata",
    "probe",
    "unknown",
]

ACADEMIC_TYPES = {
    "article",
    "inproceedings",
    "proceedings",
    "incollection",
    "book",
    "inbook",
    "phdthesis",
    "mastersthesis",
    "techreport",
}


@dataclass
class BibBlock:
    kind: str
    raw: str
    start_line: int | None = None


@dataclass
class BibInputEntry(BibBlock):
    entry_type: str = ""
    key: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    field_order: list[str] = field(default_factory=list)
    duplicate_fields: list[str] = field(default_factory=list)
    duplicate_key: bool = False

    def __post_init__(self) -> None:
        self.kind = "entry"
        self.entry_type = self.entry_type.lower()


@dataclass
class BibString(BibBlock):
    name: str = ""
    value: str = ""


@dataclass
class BibPreamble(BibBlock):
    value: str = ""


@dataclass
class BibDiagnostic:
    kind: str
    message: str
    start_line: int | None = None
    raw: str | None = None


@dataclass
class ParsedBibFile:
    path: str
    blocks: list[BibBlock]
    entries: list[BibInputEntry]
    diagnostics: list[BibDiagnostic]


@dataclass
class CandidateBibParse:
    """Sandboxed parse result for untrusted publisher/export BibTeX."""

    entries: list[BibInputEntry]
    diagnostics: list[BibDiagnostic]
    transformations: list[str] = field(default_factory=list)
    truncated: bool = False


@dataclass
class BibCandidate:
    source: str
    source_url: str | None
    bibtex: str | None
    fields: dict[str, str]
    confidence: Confidence
    score: float
    evidence: list[str] = field(default_factory=list)
    source_priority: int = 0
    canonical_id: str | None = None
    stage: str = ""
    source_kind: SourceKind = "unknown"
    source_family: str = ""


@dataclass
class ResolveResult:
    key: str
    original: BibInputEntry
    candidates: list[BibCandidate]
    selected: BibCandidate | None
    diagnostics: list[str] = field(default_factory=list)
    action: str = "report_only"
    applied_bibtex: str | None = None
    merge_report: dict[str, object] | None = None
