from __future__ import annotations

from pathlib import Path

from fixbib.cache import DEFAULT_CACHE_DIR, DoiCache
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolve import ResolverPipeline


def _entry(*, key: str, title: str, author: str, doi: str) -> BibInputEntry:
    return BibInputEntry(
        kind="entry",
        raw="",
        entry_type="inproceedings",
        key=key,
        fields={
            "title": title,
            "author": author,
            "year": "2024",
            "doi": doi,
        },
        field_order=["title", "author", "year", "doi"],
    )


class CountingResolver:
    name = "counting-doi-source"
    base_url = "https://example.test/doi"

    def __init__(self) -> None:
        self.calls = 0

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(entry.fields.get("doi"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        self.calls += 1
        doi = entry.fields["doi"]
        return [
            BibCandidate(
                source=self.name,
                source_url=f"https://example.test/doi/{doi}",
                bibtex=(
                    f"@inproceedings{{{entry.key},\n"
                    "  author = {Example, Alice},\n"
                    "  title = {A Verified Paper},\n"
                    "  year = {2024},\n"
                    f"  doi = {{{doi}}},\n"
                    "}"
                ),
                fields={
                    "author": "Example, Alice",
                    "title": "A Verified Paper",
                    "year": "2024",
                    "doi": doi,
                },
                confidence="exact",
                score=1.0,
                evidence=["resolved_by_exact_doi"],
                source_priority=100,
                canonical_id=doi,
                source_kind="registry_metadata",
                source_family="example",
            )
        ]


def _pipeline(tmp_path: Path, resolver: CountingResolver, *, enabled: bool = True) -> ResolverPipeline:
    pipeline = ResolverPipeline(
        doi_landing=False,
        page_fallback=False,
        acm_fallback=False,
        cache_enabled=enabled,
        cache_dir=tmp_path / ".bibfix_cache",
    )
    pipeline.exact_resolvers = [resolver]
    pipeline.publisher_resolvers = []
    pipeline.search_resolvers = []
    pipeline.book_resolvers = []
    return pipeline


def test_default_cache_directory_is_user_bibfix_cache():
    assert DEFAULT_CACHE_DIR == Path.home() / ".bibfix_cache"
    assert DoiCache().root == Path.home() / ".bibfix_cache"


def test_second_run_reuses_doi_cache_and_rekeys_candidate(tmp_path: Path):
    doi = "10.5555/cache.test"
    first_resolver = CountingResolver()
    first = _pipeline(tmp_path, first_resolver)
    result1 = first.resolve_one(
        _entry(key="firstKey", title="A Verified Paper", author="Alice Example", doi=doi),
        auto="verified",
    )
    assert first_resolver.calls == 1
    assert result1.selected is not None
    assert first.cache.stats.writes == 1

    second_resolver = CountingResolver()
    second = _pipeline(tmp_path, second_resolver)
    result2 = second.resolve_one(
        _entry(key="secondKey", title="A Verified Paper", author="Alice Example", doi=doi),
        auto="verified",
    )
    assert second_resolver.calls == 0
    assert second.cache.stats.hits == 1
    assert result2.selected is not None
    assert "doi_cache_hit" in result2.selected.evidence
    assert "@inproceedings{secondKey," in (result2.selected.bibtex or "")


def test_cached_candidate_is_rescored_against_current_entry(tmp_path: Path):
    doi = "10.5555/conflicting-cache"
    first_resolver = CountingResolver()
    first = _pipeline(tmp_path, first_resolver)
    first.resolve_one(
        _entry(key="correct", title="A Verified Paper", author="Alice Example", doi=doi),
        auto="verified",
    )

    second_resolver = CountingResolver()
    second = _pipeline(tmp_path, second_resolver)
    result = second.resolve_one(
        _entry(
            key="wrongLocalEntry",
            title="A Completely Different Paper",
            author="Bob Unrelated",
            doi=doi,
        ),
        auto="verified",
    )
    assert second_resolver.calls == 0
    assert result.selected is not None
    assert result.selected.confidence == "conflict"
    assert "cached_candidate_rescored_against_current_entry" in result.selected.evidence
    assert result.action == "report_only_conflict"


def test_disabled_cache_bypasses_reads_and_writes(tmp_path: Path):
    resolver = CountingResolver()
    pipeline = _pipeline(tmp_path, resolver, enabled=False)
    result = pipeline.resolve_one(
        _entry(
            key="uncached",
            title="A Verified Paper",
            author="Alice Example",
            doi="10.5555/no-cache",
        ),
        auto="verified",
    )
    assert resolver.calls == 1
    assert result.selected is not None
    assert pipeline.cache.stats.hits == 0
    assert pipeline.cache.stats.writes == 0
    assert not (tmp_path / ".bibfix_cache").exists()
