from __future__ import annotations

from dataclasses import replace

from fixbib.merge import merge_candidate_into_entry
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.parser import parse_bib_text
from fixbib.resolve import ResolverPipeline
from fixbib.writer import write_fixed_bib


def _entry(fields: dict[str, str], *, key: str = "paper", entry_type: str = "inproceedings") -> BibInputEntry:
    return BibInputEntry(
        kind="entry",
        raw="@inproceedings{paper, title={Paper}}",
        entry_type=entry_type,
        key=key,
        fields=fields,
        field_order=list(fields),
    )


def test_arxiv_wrapper_fields_are_not_imported_into_explicit_preprint():
    original = _entry(
        {
            "title": "A Preprint",
            "author": "Alice Example",
            "year": "2025",
            "eprint": "2501.01234",
            "archiveprefix": "arXiv",
            "primaryclass": "cs.SE",
        },
        entry_type="misc",
    )
    candidate = BibCandidate(
        source="dblp-bibtex",
        source_url="https://dblp.org/rec/journals/corr/abs-2501-01234.bib",
        bibtex="@article{x, title={A Preprint}}",
        fields={
            "title": "A Preprint",
            "author": "Alice Example",
            "year": "2025",
            "journal": "CoRR",
            "volume": "abs/2501.01234",
            "doi": "10.48550/arXiv.2501.01234",
            "url": "https://arxiv.org/abs/2501.01234",
        },
        confidence="high",
        score=0.7,
        source_priority=90,
        source_kind="bibliographic_index_export",
        source_family="dblp",
    )
    merged = merge_candidate_into_entry(original, candidate)
    assert merged.fields["url"] == "https://arxiv.org/abs/2501.01234"
    assert "journal" not in merged.fields
    assert "volume" not in merged.fields
    assert "doi" not in merged.fields
    assert "journal:synthetic_preprint_representation" in merged.skipped_remote


def test_writer_removes_empty_fields_even_without_network_replacement():
    parsed = parse_bib_text(
        """@inproceedings{emptyFields,
  author = {Example, Alice},
  title = {Paper},
  year = {2025},
  volume = {},
  number = {   }
}
"""
    )
    output = write_fixed_bib(parsed, [])
    assert "volume" not in output
    assert "number" not in output
    assert "title = {Paper}" in output


class _IndexResolver:
    name = "fake-index"

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return True

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        return [
            BibCandidate(
                source=self.name,
                source_url="https://dblp.org/rec/conf/uss/Example25",
                bibtex="@inproceedings{paper, title={Paper}}",
                fields={
                    "title": entry.fields["title"],
                    "author": entry.fields["author"],
                    "year": entry.fields["year"],
                    "url": "https://www.usenix.org/conference/usenixsecurity25/presentation/example",
                },
                confidence="high",
                score=0.7,
                source_priority=90,
                source_kind="bibliographic_index_export",
                source_family="dblp",
                evidence=["title_similarity=100.0", "year_match", "first_author_match", "dblp_deterministic_bib_endpoint"],
            )
        ]


class _DiscoveredUsenixResolver:
    name = "fake-usenix"

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return "usenix.org" in entry.fields.get("url", "")

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        return [
            BibCandidate(
                source=self.name,
                source_url=entry.fields["url"],
                bibtex="@inproceedings{paper, title={Paper}}",
                fields={
                    "title": entry.fields["title"],
                    "author": entry.fields["author"],
                    "year": entry.fields["year"],
                    "url": entry.fields["url"],
                    "abstract": "Publisher abstract",
                    "isbn": "978-1-939133-00-0",
                },
                confidence="exact",
                score=1.0,
                source_priority=110,
                source_kind="publisher_native_export",
                source_family="usenix",
                evidence=["usenix_export_link"],
            )
        ]


def test_discovered_usenix_url_runs_native_export_after_index_match():
    pipeline = ResolverPipeline(
        doi_landing=False,
        page_fallback=False,
        acm_fallback=False,
        discovered_page_fallback=False,
        cache_enabled=False,
    )
    pipeline.exact_resolvers = []
    pipeline.publisher_resolvers = []
    pipeline.search_resolvers = [_IndexResolver()]
    pipeline.candidate_doi_resolvers = []
    pipeline.usenix_resolver = _DiscoveredUsenixResolver()

    entry = _entry(
        {"title": "Paper", "author": "Alice Example", "year": "2025"}
    )
    result = pipeline.resolve_one(entry, auto="verified")
    assert result.action == "replace"
    assert result.selected is not None
    assert result.selected.source == "fake-usenix"
    assert result.merge_report is not None
    assert "abstract" in result.merge_report["fields_added"]
    assert any(
        candidate.stage == "discovered_publisher_native_export"
        for candidate in result.candidates
    )


def test_deterministic_formal_high_match_upgrades_misc_but_not_corr():
    original = _entry(
        {
            "title": "Formal Paper",
            "author": "Alice Example",
            "year": "2025",
            "eprint": "2501.01234",
            "archiveprefix": "arXiv",
        },
        entry_type="misc",
    )
    formal = BibCandidate(
        source="dblp-bibtex",
        source_url="https://dblp.org/rec/conf/test/Example25.bib",
        bibtex="@inproceedings{x, title={Formal Paper}}",
        fields={
            "title": "Formal Paper",
            "author": "Alice Example",
            "year": "2025",
            "booktitle": "Proceedings of Test 2025",
        },
        confidence="high",
        score=0.7,
        source_priority=90,
        source_kind="bibliographic_index_export",
        source_family="dblp",
        evidence=[
            "title_similarity=100.0",
            "year_match",
            "first_author_match",
            "dblp_deterministic_bib_endpoint",
        ],
    )
    merged = merge_candidate_into_entry(original, formal)
    assert merged.entry_type == "inproceedings"
    assert merged.entry_type_changed is True

    corr = replace(
        formal,
        source_url="https://dblp.org/rec/journals/corr/abs-2501-01234.bib",
        bibtex="@article{x, title={Formal Paper}}",
        fields={**formal.fields, "journal": "CoRR", "doi": "10.48550/arXiv.2501.01234"},
    )
    merged_corr = merge_candidate_into_entry(original, corr)
    assert merged_corr.entry_type == "misc"


def test_acm_article_identifier_does_not_replace_issue_number():
    original = _entry(
        {
            "title": "A Journal Paper",
            "author": "Alice Example",
            "year": "2025",
            "journal": "Proc. ACM Softw. Eng.",
            "number": "FSE",
            "articleno": "110",
        },
        entry_type="article",
    )
    candidate = BibCandidate(
        source="acm-native-export",
        source_url="https://dl.acm.org/doi/10.1145/example",
        bibtex="@article{x, title={A Journal Paper}}",
        fields={
            "title": "A Journal Paper",
            "author": "Alice Example",
            "year": "2025",
            "journal": "Proc. ACM Softw. Eng.",
            "number": "Article 110",
            "articleno": "110",
        },
        confidence="exact",
        score=1.0,
        source_priority=120,
        source_kind="publisher_native_export",
        source_family="acm",
    )
    merged = merge_candidate_into_entry(original, candidate)
    assert merged.fields["number"] == "FSE"
    assert merged.fields["articleno"] == "110"
    assert "number" not in merged.updated


def test_acm_article_identifier_populates_articleno_when_missing():
    original = _entry(
        {"title": "A Journal Paper", "author": "Alice Example", "year": "2025"},
        entry_type="article",
    )
    candidate = BibCandidate(
        source="acm-native-export",
        source_url="https://dl.acm.org/doi/10.1145/example",
        bibtex="@article{x, title={A Journal Paper}}",
        fields={
            "title": "A Journal Paper",
            "author": "Alice Example",
            "year": "2025",
            "number": "Article FSE037",
        },
        confidence="exact",
        score=1.0,
        source_priority=120,
        source_kind="publisher_native_export",
        source_family="acm",
    )
    merged = merge_candidate_into_entry(original, candidate)
    assert "number" not in merged.fields
    assert merged.fields["articleno"] == "FSE037"


def test_publication_day_is_not_imported_from_remote_metadata():
    original = _entry({"title": "Paper", "author": "Alice Example", "year": "2025"})
    candidate = BibCandidate(
        source="acm-native-export",
        source_url="https://dl.acm.org/doi/10.1145/example",
        bibtex="@inproceedings{x, title={Paper}}",
        fields={
            "title": "Paper",
            "author": "Alice Example",
            "year": "2025",
            "month": "July",
            "day": "11",
        },
        confidence="exact",
        score=1.0,
        source_priority=120,
        source_kind="publisher_native_export",
        source_family="acm",
    )
    merged = merge_candidate_into_entry(original, candidate)
    assert merged.fields["month"] == "July"
    assert "day" not in merged.fields
    assert "day" in merged.skipped_remote


def test_supplemental_corr_fields_do_not_pollute_explicit_arxiv_entry():
    original = _entry(
        {
            "title": "A Preprint",
            "author": "Alice Example",
            "year": "2025",
            "eprint": "2501.01234",
            "archiveprefix": "arXiv",
            "primaryclass": "cs.SE",
        },
        entry_type="misc",
    )
    selected = BibCandidate(
        source="arxiv-api",
        source_url="https://arxiv.org/abs/2501.01234",
        bibtex="@misc{x, title={A Preprint}}",
        fields={
            "title": "A Preprint",
            "author": "Alice Example",
            "year": "2025",
            "url": "https://arxiv.org/abs/2501.01234",
        },
        confidence="exact",
        score=1.0,
        source_priority=100,
        source_kind="repository_native_export",
        source_family="arxiv",
    )
    corr = BibCandidate(
        source="dblp-bibtex",
        source_url="https://dblp.org/rec/journals/corr/abs-2501-01234.bib",
        bibtex="@article{x, title={A Preprint}}",
        fields={
            "title": "A Preprint",
            "author": "Alice Example",
            "year": "2025",
            "journal": "CoRR",
            "volume": "abs/2501.01234",
            "doi": "10.48550/arXiv.2501.01234",
            "url": "https://arxiv.org/abs/2501.01234",
        },
        confidence="high",
        score=0.7,
        source_priority=90,
        source_kind="bibliographic_index_export",
        source_family="dblp",
    )
    merged = merge_candidate_into_entry(original, selected, supplemental_candidates=[corr])
    assert merged.fields["url"] == "https://arxiv.org/abs/2501.01234"
    assert "journal" not in merged.fields
    assert "volume" not in merged.fields
    assert "doi" not in merged.fields
