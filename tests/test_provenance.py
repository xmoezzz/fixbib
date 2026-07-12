from __future__ import annotations

from fixbib.cli import candidate_to_jsonable
from fixbib.merge import merge_candidate_into_entry
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolve import corroborate_candidates, decide_action, select_best_candidate
from fixbib.resolvers.doi import _transform_backend


def _entry(fields: dict[str, str]) -> BibInputEntry:
    return BibInputEntry(
        kind="entry",
        raw="@inproceedings{x, title={x}}",
        entry_type="inproceedings",
        key="x",
        fields=fields,
        field_order=list(fields),
    )


def _candidate(
    *,
    source: str,
    kind: str,
    family: str,
    fields: dict[str, str],
    priority: int,
    confidence: str = "exact",
) -> BibCandidate:
    return BibCandidate(
        source=source,
        source_url="https://example.test/record",
        bibtex="@inproceedings{x, title={x}}",
        fields=fields,
        confidence=confidence,  # type: ignore[arg-type]
        score=1.0 if confidence == "exact" else 0.8,
        source_priority=priority,
        source_kind=kind,  # type: ignore[arg-type]
        source_family=family,
    )


def test_registry_json_outranks_crossref_generated_bibtex_for_same_exact_doi():
    transform = _candidate(
        source="crossref-transform",
        kind="registry_transform",
        family="crossref",
        fields={"doi": "10.1145/test", "title": "A Paper"},
        priority=100,
    )
    registry = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={"doi": "10.1145/test", "title": "A Paper"},
        priority=95,
    )
    assert select_best_candidate([transform, registry]) is registry


def test_crossref_transform_and_crossref_json_are_not_independent_sources():
    transform = _candidate(
        source="crossref-transform",
        kind="registry_transform",
        family="crossref",
        fields={"doi": "10.1145/test", "title": "A Paper"},
        priority=100,
        confidence="high",
    )
    registry = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={"doi": "10.1145/test", "title": "A Paper"},
        priority=95,
        confidence="high",
    )
    corroborate_candidates([transform, registry])
    assert transform.confidence == "high"
    assert registry.confidence == "high"
    assert not any("corroborated_by_independent_sources" in x for x in transform.evidence)


def test_registry_metadata_does_not_truncate_title_or_reduce_full_author_names():
    original = _entry(
        {
            "author": "Sicheng Luo and Hui Xu and Yanxiang Bi and Xin Wang and Yangfan Zhou",
            "title": "Boosting Symbolic Execution via Constraint Solving Time Prediction (Experience Paper)",
            "year": "2021",
            "doi": "10.1145/3460319.3464813",
        }
    )
    registry = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={
            "author": "Luo, S. and Xu, H. and Bi, Y. and Wang, X. and Zhou, Y.",
            "title": "Boosting symbolic execution",
            "year": "2021",
            "doi": "10.1145/3460319.3464813",
            "month": "July",
        },
        priority=95,
    )
    merged = merge_candidate_into_entry(original, registry)
    assert merged.fields["author"] == original.fields["author"]
    assert merged.fields["title"] == original.fields["title"]
    assert merged.fields["month"] == "July"
    assert "author" not in merged.updated
    assert "title" not in merged.updated



def test_registry_metadata_does_not_remove_richer_venue_suffix():
    original = _entry(
        {
            "title": "A Paper",
            "author": "Alice Example",
            "booktitle": "Proceedings of the Example Conference (EXAMPLE 2024)",
            "year": "2024",
            "doi": "10.1145/test",
        }
    )
    registry = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={
            "title": "A Paper",
            "author": "Example, Alice",
            "booktitle": "Proceedings of the Example Conference",
            "year": "2024",
            "doi": "10.1145/test",
            "url": "https://doi.org/10.1145/test",
        },
        priority=95,
    )
    merged = merge_candidate_into_entry(original, registry)
    assert merged.fields["booktitle"] == original.fields["booktitle"]
    assert "booktitle" not in merged.updated
    assert "booktitle:less_informative_remote" in merged.skipped_remote
    assert merged.fields["url"] == "https://doi.org/10.1145/test"

def test_registry_transform_is_evidence_only_and_never_supplements_fields():
    original = _entry(
        {
            "author": "Sicheng Luo and Hui Xu",
            "title": "A Complete Paper Title",
            "year": "2021",
            "doi": "10.1145/test",
        }
    )
    registry = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={
            "author": "Luo, Sicheng and Xu, Hui",
            "title": "A Complete Paper Title",
            "year": "2021",
            "doi": "10.1145/test",
        },
        priority=95,
    )
    transform = _candidate(
        source="crossref-transform",
        kind="registry_transform",
        family="crossref",
        fields={
            "author": "Luo, S. and Xu, H.",
            "title": "A Complete Paper Title",
            "year": "2021",
            "doi": "10.1145/test",
            "month": "July",
            "series": "ISSTA '21",
            "collection": "ISSTA '21",
        },
        priority=100,
    )
    merged = merge_candidate_into_entry(
        original,
        registry,
        supplemental_candidates=[transform],
    )
    assert merged.fields == original.fields
    assert "month" not in merged.fields
    assert "series" not in merged.fields
    assert merged.field_sources == {}
    assert merged.supplemental_sources == []
    assert "registry_transform:evidence_only" in merged.skipped_remote



def test_authoritative_high_match_outranks_exact_registry_transform():
    transform = _candidate(
        source="crossref-transform",
        kind="registry_transform",
        family="crossref",
        fields={
            "doi": "10.48550/arxiv.2507.16585",
            "title": "LLMxCPG: Context-Aware Vulnerability Detection Through Code Property Graph-Guided Large Language Models",
            "publisher": "arXiv",
        },
        priority=70,
        confidence="exact",
    )
    dblp = _candidate(
        source="dblp-bibtex",
        kind="bibliographic_index_export",
        family="dblp",
        fields={
            "title": "LLMxCPG: Context-Aware Vulnerability Detection Through Code Property Graph-Guided Large Language Models",
            "booktitle": "34th USENIX Security Symposium",
            "publisher": "USENIX Association",
            "year": "2025",
        },
        priority=90,
        confidence="high",
    )
    dblp.evidence.extend([
        "title_similarity=100.0",
        "year_match",
        "first_author_match",
        "dblp_deterministic_bib_endpoint",
    ])
    assert select_best_candidate([transform, dblp]) is dblp
    assert decide_action(_entry({"title": "x", "year": "2025"}), dblp, "verified") == "replace"


def test_formal_publication_is_not_supplemented_with_preprint_fields():
    original = _entry(
        {
            "author": "Ahmed Lekssays and Hamza Mouhcine",
            "title": "LLMxCPG",
            "booktitle": "USENIX Security 2025",
            "publisher": "USENIX Association",
            "year": "2025",
        }
    )
    formal = _candidate(
        source="dblp-bibtex",
        kind="bibliographic_index_export",
        family="dblp",
        fields={
            "author": "Ahmed Lekssays and Hamza Mouhcine",
            "title": "LLMxCPG",
            "booktitle": "34th USENIX Security Symposium",
            "publisher": "USENIX Association",
            "year": "2025",
            "url": "https://www.usenix.org/conference/usenixsecurity25/presentation/lekssays",
        },
        priority=90,
        confidence="high",
    )
    preprint = _candidate(
        source="dblp-bibtex",
        kind="bibliographic_index_export",
        family="dblp",
        fields={
            "author": "Ahmed Lekssays and Hamza Mouhcine",
            "title": "LLMxCPG",
            "journal": "CoRR",
            "volume": "abs/2507.16585",
            "doi": "10.48550/arXiv.2507.16585",
            "eprint": "2507.16585",
            "year": "2025",
        },
        priority=90,
        confidence="high",
    )
    preprint.source_url = "https://dblp.org/rec/journals/corr/abs-2507-16585.bib"
    from fixbib.resolve import _supplemental_candidates_for
    supplements = _supplemental_candidates_for(formal, [formal, preprint])
    assert preprint not in supplements
    merged = merge_candidate_into_entry(original, formal, supplemental_candidates=supplements)
    assert "journal" not in merged.fields
    assert "volume" not in merged.fields
    assert "doi" not in merged.fields
    assert "eprint" not in merged.fields
    assert merged.fields["publisher"] == "USENIX Association"

def test_registry_transform_cannot_be_applied_even_when_it_is_only_exact_candidate():
    original = _entry(
        {
            "author": "Sicheng Luo and Hui Xu",
            "title": "A Complete Paper Title",
            "year": "2021",
            "doi": "10.1145/test",
        }
    )
    transform = _candidate(
        source="crossref-transform",
        kind="registry_transform",
        family="crossref",
        fields={
            "author": "Luo, S. and Xu, H.",
            "title": "A Different Renderer Title",
            "year": "2021",
            "doi": "10.1145/test",
            "month": "July",
        },
        priority=100,
    )
    assert decide_action(original, transform, "exact") == "report_only_registry_transform"
    assert decide_action(original, transform, "verified") == "report_only_registry_transform"
    assert decide_action(original, transform, "high") == "report_only_registry_transform"
    merged = merge_candidate_into_entry(original, transform)
    assert merged.fields == original.fields
    assert not merged.changed
    assert merged.skipped_remote == ["registry_transform:evidence_only"]


def test_audit_exposes_source_kind_family_and_authority():
    candidate = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={"doi": "10.1145/test"},
        priority=95,
    )
    payload = candidate_to_jsonable(candidate)
    assert payload is not None
    assert payload["source_kind"] == "registry_metadata"
    assert payload["source_family"] == "crossref"
    assert payload["source_authority"] > 0


def test_content_negotiation_backend_is_labeled_as_transform_not_publisher_export():
    assert _transform_backend(
        "https://api.crossref.org/v1/works/10.1145%2Ftest/transform"
    ) == ("crossref-transform", "crossref")
    assert _transform_backend(
        "https://api.datacite.org/dois/10.1234%2Ftest"
    ) == ("datacite-transform", "datacite")


def test_verification_summary_separates_blocked_acm_page_from_crossref_selection():
    from fixbib.cli import _verification_summary
    from fixbib.model import ResolveResult

    original = _entry(
        {
            "title": "A Paper",
            "author": "Alice Example",
            "year": "2024",
            "doi": "10.1145/test",
        }
    )
    registry = _candidate(
        source="crossref-doi",
        kind="registry_metadata",
        family="crossref",
        fields={"title": "A Paper", "doi": "10.1145/test"},
        priority=95,
    )
    registry.evidence.append("doi_exact_match")
    blocked = BibCandidate(
        source="doi-landing-page",
        source_url="https://dl.acm.org/doi/10.1145/test",
        bibtex=None,
        fields={},
        confidence="not_found",
        score=0.0,
        evidence=["http_status=403", "blocked_or_challenge_page"],
        source_kind="probe",
        source_family="acm",
    )
    result = ResolveResult(
        key=original.key,
        original=original,
        candidates=[blocked, registry],
        selected=registry,
    )
    summary = _verification_summary(result)
    assert summary["selected_source_kind"] == "registry_metadata"
    assert summary["selected_source_family"] == "crossref"
    assert summary["publisher_native_export_available"] is False
    assert summary["publisher_page_checks"] == {"acm": "blocked"}
