from __future__ import annotations

from fixbib.merge import merge_candidate_into_entry
from fixbib.model import BibCandidate, BibInputEntry, ParsedBibFile, ResolveResult
from fixbib.writer import write_fixed_bib


def _entry(fields: dict[str, str], *, entry_type: str = "inproceedings") -> BibInputEntry:
    return BibInputEntry(
        kind="entry",
        raw="@inproceedings{original, title={Original}}",
        entry_type=entry_type,
        key="original",
        fields=fields,
        field_order=list(fields),
    )


def _candidate(fields: dict[str, str], bibtex: str = "@inproceedings{x, title={Canonical}}") -> BibCandidate:
    return BibCandidate(
        source="test",
        source_url="https://example.test",
        bibtex=bibtex,
        fields=fields,
        confidence="exact",
        score=1.0,
    )


def test_merge_never_deletes_original_only_fields_or_doi():
    original = _entry({
        "author": "Alice Example",
        "title": "A Paper",
        "year": "2024",
        "doi": "10.1007/example",
        "abstract": "Important local abstract",
        "keywords": "symbolic execution",
        "numpages": "12",
        "location": "Vienna, Austria",
        "customfield": "keep me",
    })
    candidate = _candidate({
        "author": "Example, Alice",
        "title": "A Paper",
        "year": "2024",
        "booktitle": "Canonical Proceedings",
        # Publisher export omitted DOI and every rich/local field.
    })

    merged = merge_candidate_into_entry(original, candidate)

    for field in ("doi", "abstract", "keywords", "numpages", "location", "customfield"):
        assert merged.fields[field] == original.fields[field]
        assert f"{field} =" in merged.bibtex
    assert merged.fields["booktitle"] == "Canonical Proceedings"
    assert "booktitle" in merged.added


def test_merge_does_not_import_remote_noise_and_normalizes_doi_url():
    original = _entry({"title": "A Paper", "year": "2024", "doi": "10.5555/test"})
    candidate = _candidate({
        "title": "A Paper",
        "year": "2024",
        "doi": "10.5555/test",
        "url": "http://dx.doi.org/10.5555/test",
        "collection": "Publisher UI Collection",
        "timestamp": "yesterday",
    })
    merged = merge_candidate_into_entry(original, candidate)
    assert merged.fields["url"] == "https://doi.org/10.5555/test"
    assert "collection" not in merged.fields
    assert "timestamp" not in merged.fields
    assert set(merged.skipped_remote) >= {"collection", "timestamp"}


def test_semantically_equal_values_keep_original_spelling_and_name_style():
    original = _entry({
        "author": "Yufeng Zhang and Zhenbang Chen",
        "title": "Multiplex Symbolic Execution: Exploring Multiple Paths by Solving Once",
        "pages": "846--857",
        "year": "2020",
    })
    candidate = _candidate({
        "author": "Zhang, Yufeng and Chen, Zhenbang",
        "title": "Multiplex symbolic execution: exploring multiple paths by solving once",
        "pages": "846–857",
        "year": "2020",
    })
    merged = merge_candidate_into_entry(original, candidate)
    assert merged.fields == original.fields
    assert not merged.changed


def test_writer_uses_merged_entry_not_remote_replacement():
    original = _entry({
        "title": "Original",
        "doi": "10.1007/original",
        "abstract": "must survive",
    })
    candidate = _candidate({"title": "Corrected"})
    merged = merge_candidate_into_entry(original, candidate)
    result = ResolveResult(
        key=original.key,
        original=original,
        candidates=[candidate],
        selected=candidate,
        action="replace",
        applied_bibtex=merged.bibtex,
        merge_report=merged.to_jsonable(),
    )
    parsed = ParsedBibFile(path="x.bib", blocks=[original], entries=[original], diagnostics=[])
    output = write_fixed_bib(parsed, [result])
    assert "title = {Corrected}" in output
    assert "doi = {10.1007/original}" in output
    assert "abstract = {must survive}" in output
