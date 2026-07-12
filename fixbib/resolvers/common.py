from __future__ import annotations

from typing import Any

from fixbib.bib_build import build_bibtex_from_fields
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_arxiv_id, find_entry_doi, normalize_doi
from fixbib.provenance import infer_source_family
from fixbib.score import score_candidate
from fixbib.util_bibtex import rekey_candidate_bibtex_entries

DEFAULT_USER_AGENT = "FixBib/0.4.9"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36"
)


def candidates_from_bibtex(
    *,
    entry: BibInputEntry,
    source: str,
    source_url: str | None,
    text: str,
    source_priority: int,
    canonical_id: str | None = None,
    extra_evidence: list[str] | None = None,
    source_kind: str = "unknown",
    source_family: str | None = None,
) -> list[BibCandidate]:
    """Parse and score every entry in a publisher BibTeX response.

    ACM and other publisher export endpoints can return a bundle with multiple
    records. All records are retained as candidates. The pipeline later chooses
    the uniquely supported record, or reports ambiguity instead of silently
    taking the first entry.
    """

    effective_family = source_family or infer_source_family(source, source_url)

    rekeyed, parsed_bundle = rekey_candidate_bibtex_entries(text, entry.key)
    parse_evidence = [
        f"candidate_parse_diagnostics={len(parsed_bundle.diagnostics)}",
        f"candidate_parse_transformations={','.join(parsed_bundle.transformations) or 'none'}",
    ]
    if parsed_bundle.truncated:
        parse_evidence.append("candidate_export_truncated")
    diagnostic_kinds = sorted({item.kind for item in parsed_bundle.diagnostics})
    if diagnostic_kinds:
        parse_evidence.append("candidate_parse_diagnostic_kinds=" + ",".join(diagnostic_kinds))
    if not rekeyed:
        return [
            BibCandidate(
                source=source,
                source_url=source_url,
                bibtex=None,
                fields={},
                confidence="not_found",
                score=0.0,
                evidence=list(extra_evidence or [])
                + parse_evidence
                + ["malformed_or_empty_bibtex_export"],
                source_priority=source_priority,
                canonical_id=canonical_id,
                source_kind=source_kind,
                source_family=effective_family,
            )
        ]

    bundle_size = len(rekeyed)
    candidates: list[BibCandidate] = []
    deterministic_markers = {
        "resolved_by_exact_doi",
        "springer_deterministic_export",
        "acl_deterministic_bib_endpoint",
        "exact_arxiv_id",
        "usenix_export_link",
    }
    deterministic = bool(set(extra_evidence or []) & deterministic_markers)

    for index, (bibtex, parsed, exported_key) in enumerate(rekeyed, start=1):
        score, confidence, evidence = score_candidate(entry, parsed.fields)
        if parsed.duplicate_fields:
            evidence.append(
                "candidate_duplicate_fields=" + ",".join(parsed.duplicate_fields)
            )
            if confidence in {"exact", "high"}:
                confidence = "low"
                evidence.append("candidate_confidence_capped_due_to_duplicate_fields")
        if parsed.duplicate_key:
            evidence.append("candidate_exported_key_was_duplicated")
        evidence.extend(
            [
                f"bibtex_bundle_size={bundle_size}",
                f"bibtex_bundle_index={index}",
                f"exported_key={exported_key}",
            ]
        )
        if extra_evidence:
            evidence.extend(extra_evidence)
        evidence.extend(parse_evidence)

        identifier_match = _matches_canonical_identifier(
            entry=entry,
            candidate_fields=parsed.fields,
            canonical_id=canonical_id,
        )
        if identifier_match:
            evidence.append("bundle_candidate_identifier_match")

        candidates.append(
            BibCandidate(
                source=source,
                source_url=source_url,
                bibtex=bibtex,
                fields=parsed.fields,
                confidence=confidence,
                score=score,
                evidence=evidence,
                source_priority=source_priority,
                canonical_id=canonical_id,
                source_kind=source_kind,
                source_family=effective_family,
            )
        )

    # Remove exact duplicate records while preserving source order.
    candidates = _deduplicate_candidates(candidates)

    identifier_matches = [
        candidate
        for candidate in candidates
        if "bundle_candidate_identifier_match" in candidate.evidence
    ]
    if len(identifier_matches) == 1:
        chosen = identifier_matches[0]
        for candidate in candidates:
            if candidate is chosen:
                continue
            if candidate.confidence != "not_found":
                candidate.confidence = "low"
                candidate.evidence.append("bundle_sibling_rejected_by_unique_identifier_match")

    plausible = [
        candidate
        for candidate in candidates
        if candidate.confidence in {"exact", "high"}
    ]
    if deterministic and len(plausible) == 1 and plausible[0].confidence == "high":
        # A deterministic endpoint may elevate a single unambiguous high match,
        # but never one of several plausible records in a multi-entry bundle.
        plausible[0].confidence = "exact"
        plausible[0].evidence.append("unique_match_from_deterministic_bundle")

    if len(plausible) > 1:
        for candidate in plausible:
            candidate.evidence.append(f"bibtex_bundle_plausible_matches={len(plausible)}")

    return candidates


def candidate_from_bibtex(
    *,
    entry: BibInputEntry,
    source: str,
    source_url: str | None,
    text: str,
    source_priority: int,
    canonical_id: str | None = None,
    extra_evidence: list[str] | None = None,
    source_kind: str = "unknown",
    source_family: str | None = None,
) -> BibCandidate | None:
    """Compatibility wrapper returning a candidate only when unambiguous."""

    candidates = candidates_from_bibtex(
        entry=entry,
        source=source,
        source_url=source_url,
        text=text,
        source_priority=source_priority,
        canonical_id=canonical_id,
        extra_evidence=extra_evidence,
        source_kind=source_kind,
        source_family=source_family,
    )
    plausible = [candidate for candidate in candidates if candidate.confidence in {"exact", "high"}]
    if len(plausible) == 1:
        return plausible[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def candidate_from_fields(
    *,
    entry: BibInputEntry,
    source: str,
    source_url: str | None,
    fields: dict[str, str],
    source_priority: int,
    entry_type: str | None = None,
    canonical_id: str | None = None,
    extra_evidence: list[str] | None = None,
    source_kind: str = "unknown",
    source_family: str | None = None,
) -> BibCandidate:
    normalized = {str(k).lower(): str(v) for k, v in fields.items() if v not in (None, "")}
    score, confidence, evidence = score_candidate(entry, normalized)
    if extra_evidence:
        evidence.extend(extra_evidence)
    effective_family = source_family or infer_source_family(source, source_url)
    return BibCandidate(
        source=source,
        source_url=source_url,
        bibtex=build_bibtex_from_fields(entry_type or infer_entry_type(normalized, entry.entry_type), entry.key, normalized),
        fields=normalized,
        confidence=confidence,
        score=score,
        evidence=evidence,
        source_priority=source_priority,
        canonical_id=canonical_id,
        source_kind=source_kind,
        source_family=effective_family,
    )


def infer_entry_type(fields: dict[str, str], fallback: str = "misc") -> str:
    if fields.get("booktitle"):
        return "inproceedings"
    if fields.get("journal"):
        return "article"
    return fallback or "misc"


def first_nonempty(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            if item:
                return str(item)
        return ""
    return str(value or "")


def _matches_canonical_identifier(
    *,
    entry: BibInputEntry,
    candidate_fields: dict[str, str],
    canonical_id: str | None,
) -> bool:
    candidate_doi = normalize_doi(candidate_fields.get("doi", "")) if candidate_fields.get("doi") else ""
    original_doi = find_entry_doi(entry.fields) or ""
    if candidate_doi and original_doi and candidate_doi == original_doi:
        return True

    if canonical_id:
        normalized_id = normalize_doi(canonical_id)
        if candidate_doi and normalized_id and candidate_doi == normalized_id:
            return True
        candidate_arxiv = find_entry_arxiv_id(candidate_fields)
        if candidate_arxiv and candidate_arxiv == canonical_id:
            return True
    return False


def _deduplicate_candidates(candidates: list[BibCandidate]) -> list[BibCandidate]:
    seen: set[tuple[str, ...]] = set()
    result: list[BibCandidate] = []
    for candidate in candidates:
        signature = (
            normalize_doi(candidate.fields.get("doi", "")) if candidate.fields.get("doi") else "",
            candidate.fields.get("title", "").strip().lower(),
            candidate.fields.get("year", "").strip(),
            candidate.fields.get("author", "").strip().lower(),
            candidate.fields.get("booktitle", "").strip().lower(),
            candidate.fields.get("journal", "").strip().lower(),
            candidate.fields.get("pages", "").strip().lower(),
        )
        if signature in seen:
            continue
        seen.add(signature)
        result.append(candidate)
    return result
