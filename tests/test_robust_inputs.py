from __future__ import annotations

import base64
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from fixbib.parser import parse_bib_text
from fixbib.resolvers.common import candidates_from_bibtex
from fixbib.resolvers.page import resolve_publication_url
from fixbib.util_bibtex import decode_bibtex_data_uri, parse_candidate_bibtex


def test_input_parser_accepts_parenthesized_entries_and_preserves_raw():
    parsed = parse_bib_text(
        '@article(parenthesized, title={A Parenthesized Entry}, year={2024})\n'
    )
    assert len(parsed.entries) == 1
    assert parsed.entries[0].key == "parenthesized"
    assert parsed.entries[0].fields["title"] == "A Parenthesized Entry"
    assert parsed.entries[0].raw.startswith("@article(")
    assert any(d.kind == "normalized_entry_delimiter" for d in parsed.diagnostics)


def test_remote_candidate_keeps_valid_entries_and_sandboxes_bad_blocks(caplog):
    original = parse_bib_text(
        '''@inproceedings{original,
  author={Valid, Alice},
  title={Valid Remote Record},
  year={2024},
  doi={10.5555/valid}
}'''
    ).entries[0]
    remote = '''
@inproceedings{broken,
  title,
  year={2020}
}
@article{duplicate,
  title={Unrelated One},
  year={2020}
}
@article{duplicate,
  title={Unrelated Two},
  year={2021}
}
@inproceedings{publisherGenerated,
  author={Valid, Alice},
  title={Valid Remote Record},
  year={2024},
  doi={10.5555/valid}
}
'''
    with caplog.at_level(logging.WARNING):
        candidates = candidates_from_bibtex(
            entry=original,
            source="hostile-export",
            source_url="https://example.test/export",
            text=remote,
            source_priority=50,
        )

    assert not [r for r in caplog.records if r.name.startswith("bibtexparser")]
    selected = [c for c in candidates if c.fields.get("doi") == "10.5555/valid"]
    assert len(selected) == 1
    assert selected[0].confidence == "exact"
    assert "candidate_parse_diagnostic_kinds=duplicate_entry_key,parse_error" in selected[0].evidence


def test_candidate_parser_understands_json_html_and_markdown_wrappers():
    bib = "@article{wrapped, title={Wrapped Record}, year={2024}}"
    payload = '{"result": {"citation": ' + repr(bib).replace("'", '"') + "}}"
    parsed_json = parse_candidate_bibtex(payload)
    assert [entry.key for entry in parsed_json.entries] == ["wrapped"]

    parsed_html = parse_candidate_bibtex(f"<html><body><pre>{bib}</pre></body></html>")
    assert [entry.key for entry in parsed_html.entries] == ["wrapped"]

    parsed_markdown = parse_candidate_bibtex(f"```bibtex\n{bib}\n```")
    assert [entry.key for entry in parsed_markdown.entries] == ["wrapped"]


def test_data_uri_decoder_supports_percent_base64_and_malformed_prefix():
    bib = "@article{dataKey, title={Data URI}, year={2024}}"
    percent_uri = "data:application/x-bibtex;charset=utf-8," + quote(bib)
    decoded, error = decode_bibtex_data_uri(percent_uri)
    assert error is None
    assert decoded == bib

    base64_uri = (
        "data:application/x-bibtex;base64,"
        + base64.b64encode(bib.encode()).decode()
    )
    decoded, error = decode_bibtex_data_uri(base64_uri)
    assert error is None
    assert decoded == bib

    malformed = "/application/x-bibtex;charset=utf-8," + quote(bib)
    decoded, error = decode_bibtex_data_uri(malformed)
    assert error is None
    assert decoded == bib


class _DataUriHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/paper":
            self.send_response(404)
            self.end_headers()
            return
        unrelated = quote(
            "@article{wrong, author={Other, Person}, title={Unrelated Record}, "
            "year={1999}, doi={10.9999/wrong}}"
        )
        body = f'''<html><head>
<meta name="citation_title" content="Target Metadata Record">
<meta name="citation_author" content="Target, Alice">
<meta name="citation_publication_date" content="2024">
<meta name="citation_doi" content="10.5555/target">
<meta name="citation_journal_title" content="Reliable Journal">
</head><body>
<a href="/application/x-bibtex;charset=utf-8,{unrelated}">BibTeX</a>
<a href="javascript:void(0)">Export BibTeX</a>
</body></html>'''.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        return


def test_irrelevant_data_uri_and_javascript_link_do_not_break_metadata_fallback():
    server = HTTPServer(("127.0.0.1", 0), _DataUriHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        original = parse_bib_text(
            '''@article{original,
  author={Target, Alice},
  title={Target Metadata Record},
  year={2024},
  doi={10.5555/target}
}'''
        ).entries[0]
        base = f"http://127.0.0.1:{server.server_address[1]}/paper"
        candidates = resolve_publication_url(
            original,
            base,
            source="test-page",
            source_priority=30,
            timeout=2,
        )
        metadata = [c for c in candidates if c.fields.get("doi") == "10.5555/target"]
        assert len(metadata) == 1
        assert metadata[0].confidence == "exact"
        assert any("unsupported_export_scheme=javascript" in item for c in candidates for item in c.evidence)
        assert any(c.fields.get("doi") == "10.9999/wrong" and c.confidence == "conflict" for c in candidates)
    finally:
        server.shutdown()


def test_empty_or_malformed_export_becomes_audit_candidate_not_exception():
    original = parse_bib_text('@article{x, title={Expected}, year={2024}}').entries[0]
    candidates = candidates_from_bibtex(
        entry=original,
        source="malformed-export",
        source_url="https://example.test/bad",
        text="@article{broken, title, year={2024}}",
        source_priority=20,
    )
    assert len(candidates) == 1
    assert candidates[0].bibtex is None
    assert candidates[0].confidence == "not_found"
    assert "malformed_or_empty_bibtex_export" in candidates[0].evidence
    assert "candidate_parse_diagnostic_kinds=candidate_no_bibtex_entry,parse_error" in candidates[0].evidence


def test_pipeline_isolates_a_crashing_resolver():
    from fixbib.resolve import ResolverPipeline

    class CrashingResolver:
        name = "crashing-test-resolver"

        def can_resolve(self, entry):
            return True

        def resolve(self, entry):
            raise RuntimeError("hostile response parser crashed")

    entry = parse_bib_text('@article{x, title={Still Process Me}, year={2024}}').entries[0]
    pipeline = ResolverPipeline(
        doi_landing=False,
        page_fallback=False,
        acm_fallback=False,
        discovered_page_fallback=False,
        timeout=0.1,
        cache_enabled=False,
    )
    pipeline.exact_resolvers = [CrashingResolver()]
    pipeline.search_resolvers = []
    pipeline.book_resolvers = []
    pipeline.candidate_doi_resolvers = []

    result = pipeline.resolve_one(entry)
    assert result.selected is None
    assert any("resolver_error[exact_identifier/crashing-test-resolver]" in d for d in result.diagnostics)
    assert any("resolver_failure_isolated" in c.evidence for c in result.candidates)


def test_duplicate_fields_in_remote_candidate_cannot_be_auto_exact():
    original = parse_bib_text(
        '@article{x, title={Expected Title}, year={2024}, doi={10.5555/x}}'
    ).entries[0]
    candidates = candidates_from_bibtex(
        entry=original,
        source="duplicate-field-export",
        source_url="https://example.test/export",
        text='''@article{generated,
 title={Expected Title},
 title={Different Title},
 year={2024},
 doi={10.5555/x}
}''',
        source_priority=50,
    )
    assert len(candidates) == 1
    assert candidates[0].confidence == "low"
    assert "candidate_confidence_capped_due_to_duplicate_fields" in candidates[0].evidence
