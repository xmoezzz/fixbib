from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from fixbib.inventory import inspect_entry
from fixbib.parser import parse_bib_text
from fixbib.resolve import ResolverPipeline
from fixbib.writer import write_fixed_bib


class MockHandler(BaseHTTPRequestHandler):
    requested_paths: list[str] = []

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        self.__class__.requested_paths.append(self.path)

        if path == "/springer/10.1007/978-3-031-00000-0_1":
            self._send(
                200,
                "application/x-bibtex",
                """@inproceedings{SpringerGeneratedKey,
  author = {Miller, Carol},
  title = {Conference Metadata Repair},
  booktitle = {Computer Aided Verification},
  pages = {10--20},
  year = {2023},
  doi = {10.1007/978-3-031-00000-0_1}
}
""",
            )
        elif path == "/acl/2024.findings-acl.625.bib":
            self._send(
                200,
                "text/plain",
                """@inproceedings{du-etal-2024-generalization,
  title = {Generalization-Enhanced Code Vulnerability Detection via Multi-Task Instruction Fine-Tuning},
  author = {Du, Xiaohu and Wen, Ming},
  booktitle = {Findings of the Association for Computational Linguistics: ACL 2024},
  year = {2024},
  doi = {10.18653/v1/2024.findings-acl.625}
}
""",
            )
        elif path == "/arxiv-bib/2501.12345":
            self._send(
                200,
                "text/plain",
                """@misc{arxivGenerated,
  title = {A Preprint Only Result},
  author = {Doe, Alice},
  year = {2025},
  eprint = {2501.12345},
  archivePrefix = {arXiv}
}
""",
            )
        elif path == "/doi/10.1145/1234567.1234568":
            self._send(
                200,
                "application/x-bibtex",
                """@inproceedings{CanonicalDoiKey,
  author = {Smith, Alice and Doe, Bob},
  title = {A Reliable BibTeX Checker for LLM-Assisted Papers},
  booktitle = {Proceedings of the International Conference on Software Engineering},
  pages = {1--12},
  year = {2024},
  doi = {10.1145/1234567.1234568}
}
""",
            )
        elif path == "/dblp-search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            if "Conflict Paper" in query:
                info = {
                    "title": "Conflict Paper",
                    "year": "2020",
                    "venue": "ICSE",
                    "doi": "10.5555/conflict.a",
                    "url": f"{self.server.base_url}/dblp-rec/conf/icse/conflict",
                    "authors": {"author": [{"text": "Conflict, Alice"}]},
                }
            else:
                info = {
                    "title": "DBLP Only Paper",
                    "year": "2021",
                    "venue": "ISSTA",
                    "doi": "10.5555/dblp.1",
                    "url": f"{self.server.base_url}/dblp-rec/conf/issta/example",
                    "authors": {
                        "author": [
                            {"text": "Nguyen, Frank"},
                            {"text": "Garcia, Grace"},
                        ]
                    },
                }
            self._send(
                200,
                "application/json",
                json.dumps({"result": {"hits": {"hit": [{"info": info}]}}}),
            )
        elif path == "/dblp-rec/conf/issta/example.bib":
            self._send(
                200,
                "application/x-bibtex",
                """@inproceedings{DBLP:conf/issta/example,
  author = {Nguyen, Frank and Garcia, Grace},
  title = {DBLP Only Paper},
  booktitle = {ISSTA},
  year = {2021},
  doi = {10.5555/dblp.1}
}
""",
            )
        elif path == "/dblp-rec/conf/icse/conflict.bib":
            self._send(
                200,
                "application/x-bibtex",
                """@inproceedings{DBLP:conf/icse/conflict,
  author = {Conflict, Alice},
  title = {Conflict Paper},
  booktitle = {ICSE},
  year = {2020},
  doi = {10.5555/conflict.a}
}
""",
            )
        elif path == "/crossref/works":
            query = parse_qs(parsed.query)
            title = " ".join(query.get("query.bibliographic", [""]))
            if "DBLP Only Paper" in title:
                item = {
                    "DOI": "10.5555/dblp.1",
                    "title": ["DBLP Only Paper"],
                    "author": [{"family": "Nguyen", "given": "Frank"}],
                    "container-title": ["ISSTA"],
                    "type": "proceedings-article",
                    "issued": {"date-parts": [[2021]]},
                    "URL": "https://doi.org/10.5555/dblp.1",
                }
            elif "Conflict Paper" in title:
                item = {
                    "DOI": "10.5555/conflict.b",
                    "title": ["Conflict Paper"],
                    "author": [{"family": "Conflict", "given": "Alice"}],
                    "container-title": ["ICSE"],
                    "type": "proceedings-article",
                    "issued": {"date-parts": [[2020]]},
                    "URL": "https://doi.org/10.5555/conflict.b",
                }
            elif "Crossref Search Paper" in title:
                item = {
                    "DOI": "10.5555/crossref.1",
                    "title": ["Crossref Search Paper"],
                    "author": [{"family": "Taylor", "given": "Dana"}],
                    "container-title": ["Journal of Tests"],
                    "type": "journal-article",
                    "issued": {"date-parts": [[2022]]},
                    "URL": "https://doi.org/10.5555/crossref.1",
                }
            else:
                item = {
                    "DOI": "10.5555/unrelated",
                    "title": ["Unrelated"],
                    "author": [{"family": "Other", "given": "Person"}],
                    "container-title": ["Other Venue"],
                    "type": "journal-article",
                    "issued": {"date-parts": [[1999]]},
                    "URL": "https://doi.org/10.5555/unrelated",
                }
            self._send(200, "application/json", json.dumps({"message": {"items": [item]}}))
        elif path == "/openlibrary/api/books":
            isbn = parse_qs(parsed.query).get("bibkeys", ["ISBN:9780262032704"])[0]
            self._send(
                200,
                "application/json",
                json.dumps({
                    isbn: {
                        "title": "Model Checking",
                        "authors": [
                            {"name": "Edmund M. Clarke"},
                            {"name": "Orna Grumberg"},
                            {"name": "Doron A. Peled"},
                        ],
                        "publishers": [{"name": "MIT Press"}],
                        "publish_date": "1999",
                        "url": "https://openlibrary.org/books/OL-test/Model_Checking",
                    }
                }),
            )
        elif path == "/openlibrary/search.json":
            self._send(200, "application/json", json.dumps({"docs": []}))
        elif path == "/acm/10.1145/acm.multi":
            self._send(
                200,
                "application/x-bibtex",
                """@proceedings{ACMParent,
  title = {Proceedings of the Multi-Entry Conference},
  year = {2024},
  doi = {10.1145/acm.parent}
}

@inproceedings{ACMTargetGenerated,
  author = {Bundle, Alice and Record, Bob},
  title = {The Target Paper in an ACM Bundle},
  booktitle = {Proceedings of the Multi-Entry Conference},
  year = {2024},
  doi = {10.1145/acm.multi}
}

@inproceedings{ACMSimilarSibling,
  author = {Bundle, Alice and Record, Bob},
  title = {The Target Paper in an ACM Bundle},
  booktitle = {Companion Proceedings},
  year = {2024}
}
""",
            )
        elif path == "/multi-ambiguous":
            self._send(
                200,
                "application/x-bibtex",
                """@inproceedings{FirstExportedKey,
  author = {Ambiguous, Alice},
  title = {A Paper with Two Plausible Export Records},
  booktitle = {Main Proceedings},
  year = {2023}
}

@inproceedings{SecondExportedKey,
  author = {Ambiguous, Alice},
  title = {A Paper with Two Plausible Export Records},
  booktitle = {Companion Proceedings},
  year = {2023}
}
""",
            )
        elif path == "/acm/10.1145/acm.fallback":
            self._send(
                200,
                "text/html",
                """<html><head>
<meta name="citation_title" content="ACM Fallback Paper">
<meta name="citation_author" content="Fallback, Alice">
<meta name="citation_conference_title" content="Unknown ACM Conference">
<meta name="citation_publication_date" content="2020">
<meta name="citation_doi" content="10.1145/acm.fallback">
</head><body>paper</body></html>""",
            )
        elif path == "/obscure-paper":
            self._send(
                200,
                "text/html",
                '<html><body><a href="/obscure-paper.bib">Export BibTeX</a></body></html>',
            )
        elif path == "/obscure-paper.bib":
            self._send(
                200,
                "application/x-bibtex",
                """@inproceedings{siteKey,
  author = {Niche, Nina},
  title = {An Obscure Conference Paper},
  booktitle = {Workshop on Unknown Systems},
  year = {2023}
}
""",
            )
        elif path == "/paper-meta":
            self._send(
                200,
                "text/html",
                """<html><head>
<meta name="citation_title" content="Generic Metadata Extraction">
<meta name="citation_author" content="Taylor, Dana">
<meta name="citation_author" content="Chen, Eve">
<meta name="citation_conference_title" content="ASE">
<meta name="citation_publication_date" content="2022/10/01">
<meta name="citation_doi" content="10.5555/generic.1">
<meta name="citation_firstpage" content="30">
<meta name="citation_lastpage" content="40">
</head><body>paper</body></html>""",
            )
        elif path == "/arxiv-api":
            self._send(200, "application/atom+xml", "<feed xmlns='http://www.w3.org/2005/Atom'></feed>")
        else:
            self._send(404, "text/plain", "not found")

    def log_message(self, *args, **kwargs):
        return

    def _send(self, status: int, content_type: str, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def start_server() -> tuple[HTTPServer, str]:
    MockHandler.requested_paths = []
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    host, port = server.server_address
    base = f"http://{host}:{port}"
    server.base_url = base  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base


def make_pipeline(base: str, **kwargs) -> ResolverPipeline:
    return ResolverPipeline(
        doi_base_url=f"{base}/doi",
        crossref_api_base=f"{base}/crossref",
        dblp_api_base=f"{base}/dblp-search",
        springer_base_url=f"{base}/springer",
        acl_base_url=f"{base}/acl",
        arxiv_bibtex_base=f"{base}/arxiv-bib",
        arxiv_api_base=f"{base}/arxiv-api",
        acm_base_url=f"{base}/acm",
        openlibrary_api_base=f"{base}/openlibrary",
        timeout=2,
        **kwargs,
        cache_enabled=False,
    )


def test_parser_uses_real_parser_for_nested_braces_strings_and_duplicates():
    parsed = parse_bib_text(
        r'''
% preserved comment
@string{ICSE = "International Conference on Software Engineering"}

@inproceedings{oldKey,
  title = {A {Reliable} BibTeX Checker},
  author = {Smith, Alice and Doe, Bob},
  title = {Duplicate title should be reported},
  year = 2024,
  booktitle = ICSE # " 2024",
}
'''
    )
    assert len(parsed.entries) == 1
    entry = parsed.entries[0]
    assert entry.key == "oldKey"
    assert entry.fields["title"] == "A {Reliable} BibTeX Checker"
    assert entry.fields["booktitle"] == "International Conference on Software Engineering 2024"
    assert entry.duplicate_fields == ["title"]
    assert any(d.kind == "duplicate_fields" for d in parsed.diagnostics)
    assert parsed.blocks[0].kind == "comment"


def test_deterministic_source_exports_preserve_original_keys():
    server, base = start_server()
    try:
        bib = f'''
@inproceedings{{springerOriginal,
  title = {{Conference Metadata Repair}},
  author = {{Miller, Carol}},
  year = {{2023}},
  doi = {{10.1007/978-3-031-00000-0_1}}
}}
@inproceedings{{aclOriginal,
  title = {{Generalization-Enhanced Code Vulnerability Detection via Multi-Task Instruction Fine-Tuning}},
  author = {{Du, Xiaohu}},
  year = {{2024}},
  doi = {{10.18653/v1/2024.findings-acl.625}}
}}
@misc{{preprintOriginal,
  title = {{A Preprint Only Result}},
  author = {{Doe, Alice}},
  year = {{2025}},
  eprint = {{2501.12345}},
  archivePrefix = {{arXiv}}
}}
'''
        parsed = parse_bib_text(bib)
        pipeline = make_pipeline(base)
        results = [pipeline.resolve_one(entry, auto="exact") for entry in parsed.entries]
        by_key = {result.key: result for result in results}

        assert by_key["springerOriginal"].selected.source == "springer-bibtex"
        assert "@inproceedings{springerOriginal," in by_key["springerOriginal"].selected.bibtex
        assert "SpringerGeneratedKey" not in by_key["springerOriginal"].selected.bibtex

        assert by_key["aclOriginal"].selected.source == "acl-anthology-bibtex"
        assert "@inproceedings{aclOriginal," in by_key["aclOriginal"].selected.bibtex

        assert by_key["preprintOriginal"].selected.source == "arxiv-bibtex"
        assert "@misc{preprintOriginal," in by_key["preprintOriginal"].selected.bibtex
    finally:
        server.shutdown()


def test_doi_then_dblp_then_crossref_then_page_fallback():
    server, base = start_server()
    try:
        bib = f'''
@inproceedings{{doiKey,
  title = {{A Reliable BibTeX Checker for LLM-Assisted Papers}},
  author = {{Smith, Alice}},
  year = {{2024}},
  doi = {{10.1145/1234567.1234568}}
}}
@inproceedings{{dblpKey,
  title = {{DBLP Only Paper}},
  author = {{Nguyen, Frank}},
  year = {{2021}}
}}
@article{{crossrefKey,
  title = {{Crossref Search Paper}},
  author = {{Taylor, Dana}},
  year = {{2022}}
}}
@inproceedings{{metaKey,
  title = {{Generic Metadata Extraction}},
  author = {{Taylor, Dana}},
  year = {{2022}},
  url = {{{base}/paper-meta}}
}}
'''
        parsed = parse_bib_text(bib)
        pipeline = make_pipeline(base)
        results = [pipeline.resolve_one(entry, auto="exact") for entry in parsed.entries]
        by_key = {result.key: result for result in results}

        assert by_key["doiKey"].selected.source == "doi-landing-page-direct-bibtex"
        assert by_key["doiKey"].action == "replace"
        assert by_key["dblpKey"].selected.source == "dblp-bibtex"
        assert by_key["dblpKey"].selected.confidence == "exact"
        assert by_key["dblpKey"].action == "replace"
        assert any("corroborated_by_independent_sources" in item for item in by_key["dblpKey"].selected.evidence)
        assert by_key["crossrefKey"].selected.source == "crossref-search"
        assert by_key["crossrefKey"].selected.confidence == "high"
        assert by_key["crossrefKey"].action == "report_only"
        assert by_key["metaKey"].selected.source == "generic-page-metadata"
    finally:
        server.shutdown()


def test_arxiv_title_search_is_not_generic_fallback_for_published_papers():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            '''@inproceedings{published,
  title = {A Published Conference Paper Missing a DOI},
  author = {Example, Alice},
  year = {2024}
}
'''
        )
        pipeline = make_pipeline(base)
        pipeline.resolve_one(parsed.entries[0], auto="none")
        assert not any(path.startswith("/arxiv-api") for path in MockHandler.requested_paths)
    finally:
        server.shutdown()


def test_duplicate_keys_are_never_replaced():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            '''@inproceedings{same,
  title = {A Reliable BibTeX Checker for LLM-Assisted Papers},
  author = {Smith, Alice},
  year = {2024},
  doi = {10.1145/1234567.1234568}
}
@inproceedings{same,
  title = {Another Paper},
  author = {Other, Bob},
  year = {2023}
}
'''
        )
        pipeline = make_pipeline(base)
        results = [pipeline.resolve_one(entry, auto="exact") for entry in parsed.entries]
        assert all(result.action == "report_only_duplicate_key" for result in results if result.selected)
        fixed = write_fixed_bib(parsed, results)
        assert fixed.count("@inproceedings{same,") == 2
        assert "CanonicalDoiKey" not in fixed
    finally:
        server.shutdown()


def test_conflicting_dois_from_structured_sources_disable_replacement():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            """@inproceedings{conflictKey,
  title = {Conflict Paper},
  author = {Conflict, Alice},
  year = {2020}
}
"""
        )
        result = make_pipeline(base).resolve_one(parsed.entries[0], auto="high")
        assert result.selected is not None
        assert result.action == "report_only_conflict"
        assert any(message.startswith("candidate_doi_conflict") for message in result.diagnostics)
    finally:
        server.shutdown()



def test_book_lookup_uses_isbn_and_preserves_original_key():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            """@book{modelBook,
  author = {Clarke, Edmund M. and Grumberg, Orna and Peled, Doron A.},
  title = {Model Checking},
  publisher = {MIT Press},
  year = {1999},
  isbn = {9780262032704}
}
"""
        )
        result = make_pipeline(base).resolve_one(parsed.entries[0], auto="exact")
        assert result.selected is not None
        assert result.selected.source == "openlibrary-isbn"
        assert result.selected.stage == "book_lookup"
        assert result.selected.confidence == "exact"
        assert result.action == "replace"
        assert "@book{modelBook," in result.selected.bibtex
    finally:
        server.shutdown()


def test_acm_dl_is_tried_as_late_fallback():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            """@inproceedings{acmFallback,
  author = {Fallback, Alice},
  title = {ACM Fallback Paper},
  year = {2020},
  doi = {10.1145/acm.fallback}
}
"""
        )
        result = make_pipeline(base).resolve_one(parsed.entries[0], auto="exact")
        assert result.selected is not None
        assert result.selected.source == "acm-dl-fallback"
        assert result.selected.stage == "publisher_fallback"
        assert any(path.startswith("/acm/10.1145/acm.fallback") for path in MockHandler.requested_paths)
    finally:
        server.shutdown()


def test_unknown_conference_page_can_export_bibtex():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            f"""@inproceedings{{nicheKey,
  author = {{Niche, Nina}},
  title = {{An Obscure Conference Paper}},
  year = {{2023}},
  url = {{{base}/obscure-paper}}
}}
"""
        )
        result = make_pipeline(base).resolve_one(parsed.entries[0], auto="high")
        assert result.selected is not None
        assert result.selected.source == "generic-page-metadata-bibtex-link"
        assert result.selected.stage == "entry_url"
        assert "@inproceedings{nicheKey," in result.selected.bibtex
    finally:
        server.shutdown()


def test_inventory_flags_entries_without_doi_or_url():
    parsed = parse_bib_text(
        """@book{plainBook,
  author = {Sipser, Michael},
  title = {Introduction to the Theory of Computation},
  publisher = {Cengage Learning},
  year = {2012}
}
"""
    )
    inventory = inspect_entry(parsed.entries[0])
    assert inventory.has_doi_or_url is False
    assert inventory.has_any_locator is False
    assert inventory.locator_label == "none"


def test_invalid_doi_can_fall_back_to_bibliographic_search():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            """@article{wrongDoi,
  title = {Crossref Search Paper},
  author = {Taylor, Dana},
  year = {2022},
  doi = {10.9999/does-not-exist}
}
"""
        )
        result = make_pipeline(base).resolve_one(parsed.entries[0], auto="high")
        assert result.selected is not None
        assert result.selected.fields.get("doi") == "10.5555/crossref.1"
        assert any(candidate.source == "crossref-search" for candidate in result.candidates)
        assert result.action == "replace"
        assert "identifier_recovery_verified" in result.selected.evidence
        assert result.applied_bibtex is not None
        assert "doi = {10.5555/crossref.1}" in result.applied_bibtex
    finally:
        server.shutdown()


def test_multi_entry_acm_export_selects_unique_doi_and_preserves_original_key():
    server, base = start_server()
    try:
        parsed = parse_bib_text(
            f"""
@inproceedings{{originalAcmKey,
  author = {{Bundle, Alice and Record, Bob}},
  title = {{The Target Paper in an ACM Bundle}},
  year = {{2024}},
  doi = {{10.1145/acm.multi}},
  url = {{{base}/acm/10.1145/acm.multi}}
}}
"""
        )
        pipeline = make_pipeline(base, thorough=True)
        result = pipeline.resolve_one(parsed.entries[0], auto="exact")

        assert result.action == "replace"
        assert result.selected is not None
        assert result.selected.fields["doi"] == "10.1145/acm.multi"
        assert "@inproceedings{originalAcmKey," in result.selected.bibtex
        assert "ACMTargetGenerated" not in result.selected.bibtex
        bundle = [c for c in result.candidates if c.source.startswith("acm-dl-fallback-direct-bibtex")]
        assert len(bundle) == 3
        assert any("bibtex_bundle_size=3" in c.evidence for c in bundle)
        assert sum(c.confidence in {"exact", "high"} for c in bundle) == 1
        assert not any(d.startswith("ambiguous_bibtex_bundle") for d in result.diagnostics)
    finally:
        server.shutdown()


def test_multi_entry_export_without_unique_identifier_is_ambiguous_and_not_replaced():
    server, base = start_server()
    try:
        original_text = f"""
@inproceedings{{keepThisKey,
  author = {{Ambiguous, Alice}},
  title = {{A Paper with Two Plausible Export Records}},
  year = {{2023}},
  url = {{{base}/multi-ambiguous}}
}}
"""
        parsed = parse_bib_text(original_text)
        pipeline = make_pipeline(base)
        result = pipeline.resolve_one(parsed.entries[0], auto="high")

        assert result.selected is not None
        assert result.action == "report_only_ambiguous"
        assert any(d.startswith("ambiguous_bibtex_bundle") for d in result.diagnostics)
        bundle = [c for c in result.candidates if c.source.endswith("direct-bibtex")]
        assert len(bundle) == 2
        assert all("bibtex_bundle_plausible_matches=2" in c.evidence for c in bundle)

        fixed = write_fixed_bib(parsed, [result])
        assert "FirstExportedKey" not in fixed
        assert "SecondExportedKey" not in fixed
        assert "@inproceedings{keepThisKey," in fixed
    finally:
        server.shutdown()


def test_verified_policy_applies_authoritative_deterministic_high_match():
    from fixbib.model import BibCandidate, BibInputEntry
    from fixbib.resolve import decide_action

    entry = BibInputEntry(
        kind="entry",
        raw="@inproceedings{x,title={A Paper}}",
        entry_type="inproceedings",
        key="x",
        fields={"title": "A Paper", "author": "Alice Example", "year": "2024"},
        field_order=["title", "author", "year"],
    )
    candidate = BibCandidate(
        source="dblp-bibtex",
        source_url="https://dblp.org/rec/example.bib",
        bibtex="@inproceedings{remote,title={A Paper}}",
        fields={"title": "A Paper", "author": "Example, Alice", "year": "2024"},
        confidence="high",
        score=0.7,
        source_priority=90,
        evidence=[
            "title_similarity=100.0",
            "year_match",
            "first_author_match",
            "dblp_deterministic_bib_endpoint",
        ],
    )
    assert decide_action(entry, candidate, "verified") == "replace"


def test_verified_policy_rejects_uncorroborated_generic_high_match():
    from fixbib.model import BibCandidate, BibInputEntry
    from fixbib.resolve import decide_action

    entry = BibInputEntry(
        kind="entry",
        raw="@article{x,title={A Paper}}",
        entry_type="article",
        key="x",
        fields={"title": "A Paper", "author": "Alice Example", "year": "2024"},
        field_order=["title", "author", "year"],
    )
    candidate = BibCandidate(
        source="crossref-search",
        source_url="https://example.test",
        bibtex="@article{remote,title={A Paper}}",
        fields={"title": "A Paper", "author": "Example, Alice", "year": "2024"},
        confidence="high",
        score=0.7,
        source_priority=75,
        evidence=["title_similarity=100.0", "year_match", "first_author_match"],
    )
    assert decide_action(entry, candidate, "verified") == "report_only"
