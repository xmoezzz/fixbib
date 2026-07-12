from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import BibCandidate, BibInputEntry
from .normalizer import normalize_doi
from .score import score_candidate
from .util_bibtex import rekey_candidate_bibtex_entries

CACHE_SCHEMA_VERSION = 1
DEFAULT_CACHE_DIR = Path.home() / ".bibfix_cache"

_SCORE_EVIDENCE_EXACT = {
    "doi_exact_match",
    "isbn_exact_match",
    "year_match",
    "first_author_match",
    "identifier_recovery_candidate",
}
_SCORE_EVIDENCE_PREFIXES = (
    "title_similarity=",
    "doi_mismatch=",
    "isbn_mismatch=",
    "year_mismatch=",
    "first_author_mismatch=",
    "identifier_content_conflict=",
    "corroborated_",
)


@dataclass
class CacheStats:
    enabled: bool
    directory: str
    hits: int = 0
    misses: int = 0
    writes: int = 0
    skipped_without_doi: int = 0
    read_errors: int = 0
    write_errors: int = 0

    def to_jsonable(self) -> dict[str, object]:
        return asdict(self)


class DoiCache:
    """Persistent source-result cache keyed by DOI and resolver identity.

    The cache stores remote candidate snapshots, never a merged local BibTeX
    entry. Cached candidates are re-keyed and re-scored against the current
    input entry on every use, so a bad DOI or contradictory local metadata is
    still detected instead of being hidden by an earlier run.
    """

    def __init__(self, *, enabled: bool = True, root: Path | None = None) -> None:
        self.enabled = enabled
        self.root = (root or DEFAULT_CACHE_DIR).expanduser()
        self.stats = CacheStats(enabled=enabled, directory=str(self.root))

    def load(
        self,
        *,
        doi: str,
        resolver: object,
        entry: BibInputEntry,
    ) -> list[BibCandidate] | None:
        normalized = normalize_doi(doi)
        if not self.enabled or not normalized:
            if not normalized:
                self.stats.skipped_without_doi += 1
            return None

        path = self._path(normalized, resolver)
        if not path.exists():
            self.stats.misses += 1
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
                self.stats.misses += 1
                return None
            if normalize_doi(str(payload.get("doi", ""))) != normalized:
                self.stats.misses += 1
                return None
            raw_candidates = payload.get("candidates")
            if not isinstance(raw_candidates, list):
                raise ValueError("cache candidates must be a list")
            candidates = [
                self._candidate_from_json(item, entry)
                for item in raw_candidates
                if isinstance(item, dict)
            ]
            candidates = [candidate for candidate in candidates if candidate is not None]
            if not candidates:
                self.stats.misses += 1
                return None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.stats.read_errors += 1
            self.stats.misses += 1
            return None

        self.stats.hits += 1
        return candidates

    def store(
        self,
        *,
        doi: str,
        resolver: object,
        candidates: list[BibCandidate],
    ) -> bool:
        normalized = normalize_doi(doi)
        if not self.enabled or not normalized:
            return False

        # Do not persist transient failures, challenge pages, or empty probes.
        # They should be retried on a later run rather than becoming permanent.
        useful = [
            candidate
            for candidate in candidates
            if candidate.confidence != "not_found"
            and bool(candidate.bibtex or candidate.fields)
            and candidate.source_kind != "probe"
        ]
        if not useful:
            return False

        path = self._path(normalized, resolver)
        payload = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "doi": normalized,
            "resolver": self._resolver_descriptor(resolver),
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "candidates": [self._candidate_to_json(candidate) for candidate in useful],
        }
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
            self.stats.writes += 1
            return True
        except OSError:
            self.stats.write_errors += 1
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def summary(self) -> dict[str, object]:
        result = self.stats.to_jsonable()
        result["schema_version"] = CACHE_SCHEMA_VERSION
        result["policy"] = (
            "DOI-keyed remote source snapshots; cached candidates are re-scored "
            "against every current entry; --no-cache bypasses reads and writes"
        )
        return result

    def _path(self, doi: str, resolver: object) -> Path:
        doi_hash = hashlib.sha256(doi.encode("utf-8")).hexdigest()
        resolver_descriptor = self._resolver_descriptor(resolver)
        resolver_json = json.dumps(resolver_descriptor, ensure_ascii=True, sort_keys=True)
        resolver_hash = hashlib.sha256(resolver_json.encode("utf-8")).hexdigest()[:20]
        name = _safe_name(str(resolver_descriptor.get("name", "resolver")))
        return self.root / "doi" / doi_hash[:2] / doi_hash / f"{name}-{resolver_hash}.json"

    @staticmethod
    def _resolver_descriptor(resolver: object) -> dict[str, object]:
        descriptor: dict[str, object] = {
            "name": str(getattr(resolver, "name", type(resolver).__name__)),
            "class": f"{type(resolver).__module__}.{type(resolver).__qualname__}",
        }
        for attribute in (
            "base_url",
            "api_base",
            "bibtex_base",
            "export_url",
            "doi_base_url",
            "mode",
        ):
            value = getattr(resolver, attribute, None)
            if isinstance(value, (str, int, float, bool)) and value != "":
                descriptor[attribute] = value
        return descriptor

    @staticmethod
    def _candidate_to_json(candidate: BibCandidate) -> dict[str, object]:
        return {
            "source": candidate.source,
            "source_url": candidate.source_url,
            "bibtex": candidate.bibtex,
            "fields": candidate.fields,
            "confidence": candidate.confidence,
            "score": candidate.score,
            "evidence": candidate.evidence,
            "source_priority": candidate.source_priority,
            "canonical_id": candidate.canonical_id,
            "source_kind": candidate.source_kind,
            "source_family": candidate.source_family,
        }

    @staticmethod
    def _candidate_from_json(
        raw: dict[str, Any],
        entry: BibInputEntry,
    ) -> BibCandidate | None:
        fields_raw = raw.get("fields", {})
        if not isinstance(fields_raw, dict):
            return None
        fields = {
            str(key).lower(): str(value)
            for key, value in fields_raw.items()
            if value not in (None, "")
        }
        if not fields and not raw.get("bibtex"):
            return None

        bibtex = str(raw.get("bibtex") or "") or None
        if bibtex:
            rekeyed, _ = rekey_candidate_bibtex_entries(bibtex, entry.key)
            if rekeyed:
                bibtex = rekeyed[0][0]

        score, confidence, scoring_evidence = score_candidate(entry, fields)
        original_evidence = raw.get("evidence", [])
        if not isinstance(original_evidence, list):
            original_evidence = []
        evidence = [
            str(item)
            for item in original_evidence
            if not _is_scoring_evidence(str(item))
            and not str(item).startswith("pipeline_stage=")
        ]
        evidence.extend(scoring_evidence)

        # Preserve parser/bundle safety caps produced by the original resolver.
        if "candidate_confidence_capped_due_to_duplicate_fields" in evidence:
            if confidence in {"exact", "high"}:
                confidence = "low"
        if "bundle_sibling_rejected_by_unique_identifier_match" in evidence:
            if confidence != "not_found":
                confidence = "low"
        if "unique_match_from_deterministic_bundle" in evidence and confidence == "high":
            confidence = "exact"

        evidence.extend(
            [
                "doi_cache_hit",
                f"doi_cache_schema={CACHE_SCHEMA_VERSION}",
                "cached_candidate_rescored_against_current_entry",
            ]
        )
        return BibCandidate(
            source=str(raw.get("source") or "cache"),
            source_url=str(raw.get("source_url")) if raw.get("source_url") else None,
            bibtex=bibtex,
            fields=fields,
            confidence=confidence,
            score=score,
            evidence=_dedupe(evidence),
            source_priority=int(raw.get("source_priority") or 0),
            canonical_id=str(raw.get("canonical_id")) if raw.get("canonical_id") else None,
            source_kind=str(raw.get("source_kind") or "unknown"),  # type: ignore[arg-type]
            source_family=str(raw.get("source_family") or ""),
        )


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.")
    return cleaned or "resolver"


def _is_scoring_evidence(value: str) -> bool:
    return value in _SCORE_EVIDENCE_EXACT or value.startswith(_SCORE_EVIDENCE_PREFIXES)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
