from __future__ import annotations

import json

import httpx

from fixbib.merge import merge_candidate_into_entry
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolve import ResolverPipeline
from fixbib.resolvers.acm import AcmResolver
from fixbib.resolvers.ieee import IeeeResolver
from fixbib.score import score_candidate


def _entry(fields: dict[str, str], *, key: str = "smtimer2021issta") -> BibInputEntry:
    return BibInputEntry(
        kind="entry",
        raw="@inproceedings{x, title={x}}",
        entry_type="inproceedings",
        key=key,
        fields=fields,
        field_order=list(fields),
    )


def test_acm_native_export_maps_and_merges_rich_metadata():
    doi = "10.1145/3460319.3464813"
    payload = {
        "items": [
            {
                doi: {
                    "type": "PAPER_CONFERENCE",
                    "title": "Boosting symbolic execution via constraint solving time prediction (experience paper)",
                    "author": [
                        {"family": "Luo", "given": "Sicheng"},
                        {"family": "Xu", "given": "Hui"},
                    ],
                    "container-title": "Proceedings of the 30th ACM SIGSOFT International Symposium on Software Testing and Analysis",
                    "publisher": "Association for Computing Machinery",
                    "publisher-place": "New York, NY, USA",
                    "page": "336–347",
                    "ISBN": "9781450384599",
                    "DOI": doi,
                    "URL": f"https://doi.org/{doi}",
                    "abstract": "Publisher abstract.",
                    "keyword": ["Symbolic execution", "SMT solving"],
                    "event-place": "Virtual, Denmark",
                    "collection-title": "ISSTA 2021",
                    "number-of-pages": "12",
                    "issued": {"date-parts": [[2021, 7]]},
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path == f"/doi/{doi}"
            assert request.headers["user-agent"].startswith("Mozilla/5.0")
            return httpx.Response(
                403,
                text="challenge",
                headers={"set-cookie": "acm_session=1; Path=/"},
                request=request,
            )
        assert request.method == "POST"
        assert request.url.path == "/action/exportCiteProcCitation"
        assert request.headers["origin"] == "https://dl.acm.org"
        assert request.headers["x-requested-with"] == "XMLHttpRequest"
        assert "acm_session=1" in request.headers.get("cookie", "")
        return httpx.Response(200, json=payload, request=request)

    original = _entry(
        {
            "author": "Sicheng Luo and Hui Xu",
            "title": "Boosting Symbolic Execution via Constraint Solving Time Prediction (Experience Paper)",
            "booktitle": "Proceedings of the 30th ACM SIGSOFT International Symposium on Software Testing and Analysis (ISSTA 2021)",
            "pages": "336--347",
            "year": "2021",
            "publisher": "ACM",
            "doi": doi,
        }
    )
    resolver = AcmResolver(transport=httpx.MockTransport(handler))
    candidates = resolver.resolve(original)
    selected = next(c for c in candidates if c.bibtex)
    assert selected.source_kind == "publisher_native_export"
    assert selected.fields["isbn"] == "9781450384599"
    assert selected.fields["abstract"] == "Publisher abstract."
    assert selected.fields["keywords"] == "Symbolic execution, SMT solving"
    assert selected.fields["numpages"] == "12"
    assert selected.fields["location"] == "Virtual, Denmark"

    merged = merge_candidate_into_entry(original, selected)
    assert merged.fields["isbn"] == "9781450384599"
    assert merged.fields["abstract"] == "Publisher abstract."
    assert merged.fields["keywords"] == "Symbolic execution, SMT solving"
    assert merged.fields["numpages"] == "12"
    assert merged.fields["location"] == "Virtual, Denmark"
    assert merged.fields["address"] == "New York, NY, USA"
    assert merged.fields["series"] == "ISSTA 2021"
    assert merged.fields["month"] == "July"
    assert merged.fields["author"] == original.fields["author"]
    assert merged.fields["pages"] == original.fields["pages"]
    assert "@inproceedings{smtimer2021issta," in merged.bibtex


def test_ieee_native_export_uses_document_redirect_and_rich_bibtex():
    doi = "10.1109/SP61157.2025.00190"
    bibtex = r"""@inproceedings{Yao2025,
      author={Yao, Shuangjie and She, Dongdong},
      booktitle={2025 IEEE Symposium on Security and Privacy (SP)},
      title={Empc: Effective Path Prioritization for Symbolic Execution with Path Cover},
      year={2025},
      pages={2995-3013},
      abstract={Publisher abstract},
      keywords={Privacy;Codes;Runtime},
      doi={10.1109/SP61157.2025.00190},
      publisher={IEEE Computer Society},
      address={Los Alamitos, CA, USA},
      month={May}
    }"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "doi.org":
            return httpx.Response(
                302,
                headers={"Location": "https://ieeexplore.ieee.org/document/11023434/"},
                request=request,
            )
        if request.url.path == "/document/11023434/":
            return httpx.Response(202, text="", request=request)
        if request.url.path == "/rest/search/citation/format":
            return httpx.Response(200, json={"data": bibtex}, request=request)
        raise AssertionError(str(request.url))

    original = _entry(
        {
            "author": "Yao, Shuangjie and She, Dongdong",
            "title": "Empc: Effective Path Prioritization for Symbolic Execution with Path Cover",
            "year": "2025",
            "doi": doi,
        },
        key="empc2025",
    )
    resolver = IeeeResolver(transport=httpx.MockTransport(handler))
    candidates = resolver.resolve(original)
    selected = next(c for c in candidates if c.bibtex)
    assert selected.source_kind == "publisher_native_export"
    assert selected.fields["abstract"] == "Publisher abstract"
    assert selected.fields["keywords"] == "Privacy;Codes;Runtime"
    assert selected.fields["address"] == "Los Alamitos, CA, USA"

    merged = merge_candidate_into_entry(original, selected)
    assert merged.fields["abstract"] == "Publisher abstract"
    assert merged.fields["keywords"] == "Privacy;Codes;Runtime"
    assert merged.fields["publisher"] == "IEEE Computer Society"
    assert merged.fields["month"] == "May"
    assert "@inproceedings{empc2025," in merged.bibtex


def test_registry_metadata_cannot_add_rich_publisher_only_fields():
    original = _entry({"title": "A Paper", "author": "Alice Example", "year": "2024", "doi": "10.1/x"})
    candidate = BibCandidate(
        source="crossref-doi",
        source_url="https://doi.org/10.1/x",
        bibtex="@article{x,title={A Paper}}",
        fields={
            "title": "A Paper",
            "author": "Example, Alice",
            "year": "2024",
            "doi": "10.1/x",
            "abstract": "Registry abstract",
            "keywords": "one, two",
        },
        confidence="exact",
        score=1.0,
        source_priority=95,
        source_kind="registry_metadata",
        source_family="crossref",
    )
    merged = merge_candidate_into_entry(original, candidate)
    assert "abstract" not in merged.fields
    assert "keywords" not in merged.fields
    assert "abstract:source_not_authoritative" in merged.skipped_remote


def test_exact_doi_with_unrelated_title_and_author_is_conflict():
    original = _entry(
        {
            "title": "Propagation-Based Vulnerability Impact Assessment for Software Supply Chains",
            "author": "Ruan, Example",
            "year": "2025",
            "doi": "10.1109/ase63991.2025.00066",
        }
    )
    _, confidence, evidence = score_candidate(
        original,
        {
            "title": "Improving LLM-based Log Parsing by Learning from Errors in Reasoning Traces",
            "author": "Wang, Jialai",
            "year": "2025",
            "doi": "10.1109/ase63991.2025.00066",
        },
    )
    assert confidence == "conflict"
    assert any(item.startswith("identifier_content_conflict=") for item in evidence)


def test_pipeline_queries_and_prefers_publisher_native_after_exact_registry():
    doi = "10.1145/test"
    entry = _entry({"title": "A Paper", "author": "Alice Example", "year": "2024", "doi": doi})

    class FakeResolver:
        def __init__(self, candidate: BibCandidate):
            self.candidate = candidate
            self.name = candidate.source

        def can_resolve(self, _: BibInputEntry) -> bool:
            return True

        def resolve(self, _: BibInputEntry) -> list[BibCandidate]:
            return [self.candidate]

    registry = BibCandidate(
        source="crossref-doi",
        source_url=f"https://doi.org/{doi}",
        bibtex="@inproceedings{x,title={A Paper}}",
        fields={"title": "A Paper", "author": "Example, Alice", "year": "2024", "doi": doi},
        confidence="exact",
        score=1.0,
        source_priority=95,
        source_kind="registry_metadata",
        source_family="crossref",
    )
    publisher = BibCandidate(
        source="acm-native-export",
        source_url="https://dl.acm.org/action/exportCiteProcCitation",
        bibtex="@inproceedings{x,title={A Paper}}",
        fields={
            "title": "A Paper",
            "author": "Example, Alice",
            "year": "2024",
            "doi": doi,
            "isbn": "9780000000000",
            "abstract": "Full abstract",
        },
        confidence="exact",
        score=1.0,
        source_priority=115,
        source_kind="publisher_native_export",
        source_family="acm",
    )

    pipeline = ResolverPipeline(doi_landing=False, page_fallback=False, acm_fallback=True, cache_enabled=False)
    pipeline.exact_resolvers = [FakeResolver(registry)]
    pipeline.publisher_resolvers = [FakeResolver(publisher)]
    pipeline.search_resolvers = []
    pipeline.book_resolvers = []
    pipeline.preprint_search_resolver = FakeResolver(registry)
    result = pipeline.resolve_one(entry, auto="verified")
    assert result.selected is publisher
    assert result.merge_report is not None
    assert result.merge_report["field_sources"]["abstract"] == "acm-native-export"


def test_acm_page_supplies_abstract_keywords_and_page_count_missing_from_csl():
    doi = "10.1145/3460319.3464813"
    payload = {
        "items": [
            {
                doi: {
                    "type": "PAPER_CONFERENCE",
                    "title": "Boosting symbolic execution via constraint solving time prediction (experience paper)",
                    "author": [{"family": "Luo", "given": "Sicheng"}],
                    "container-title": "Proceedings of ISSTA",
                    "publisher": "Association for Computing Machinery",
                    "page": "336–347",
                    "DOI": doi,
                    "issued": {"date-parts": [[2021, 7]]},
                }
            }
        ]
    }
    page = """
    <html><head><meta name="citation_journal_title" content="Full Journal Name"></head>
    <body>
      <div class="article__abstract"><p>First abstract paragraph.</p><p>Second paragraph.</p></div>
      <div class="pages-info"><span>12 pages</span></div>
      <div class="tags-widget"><a>Symbolic execution</a><a>SMT solving</a></div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=page, request=request)
        return httpx.Response(200, json=payload, request=request)

    original = _entry(
        {
            "author": "Sicheng Luo",
            "title": "Boosting Symbolic Execution via Constraint Solving Time Prediction (Experience Paper)",
            "year": "2021",
            "doi": doi,
        }
    )
    selected = next(
        candidate
        for candidate in AcmResolver(transport=httpx.MockTransport(handler)).resolve(original)
        if candidate.bibtex
    )
    assert selected.fields["abstract"] == "First abstract paragraph.\n\nSecond paragraph."
    assert selected.fields["keywords"] == "Symbolic execution, SMT solving"
    assert selected.fields["numpages"] == "12"
    assert "acm_page_rich_metadata" in selected.evidence
