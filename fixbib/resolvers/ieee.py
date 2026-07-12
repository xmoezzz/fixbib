from __future__ import annotations

import html
import re
from urllib.parse import quote, urlparse

import httpx

from fixbib.inventory import extract_entry_urls
from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_doi
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidates_from_bibtex


_DOCUMENT_RE = re.compile(r"/(?:document|abstract/document)/(\d+)", re.I)
_ARNUMBER_RE = re.compile(r"(?:arnumber[=:\"']+|articleNumber\s*[=:]\s*[\"']?)(\d+)", re.I)


class IeeeResolver(Resolver):
    """Fetch IEEE Xplore's native BibTeX-with-abstract citation export."""

    name = "ieee-xplore-bibtex"

    def __init__(
        self,
        base_url: str = "https://ieeexplore.ieee.org",
        doi_base_url: str = "https://doi.org",
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.doi_base_url = doi_base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def can_resolve(self, entry: BibInputEntry) -> bool:
        doi = find_entry_doi(entry.fields) or ""
        if doi.startswith("10.1109/"):
            return True
        return any("ieeexplore.ieee.org" in urlparse(url).netloc.lower() for url in extract_entry_urls(entry.fields))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        doi = find_entry_doi(entry.fields) or ""
        arnumber = _arnumber_from_urls(extract_entry_urls(entry.fields))
        landing_url = ""
        landing_evidence: list[str] = []

        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=self.timeout,
                transport=self.transport,
            ) as client:
                if not arnumber and doi:
                    doi_url = f"{self.doi_base_url}/{quote(doi, safe='/:;()')}"
                    landing = client.get(
                        doi_url,
                        headers={
                            "User-Agent": DEFAULT_USER_AGENT,
                            "Accept": "text/html,application/xhtml+xml,*/*;q=0.1",
                        },
                    )
                    landing_url = str(landing.url)
                    landing_evidence.extend(
                        [
                            f"doi_redirect_status={landing.status_code}",
                            f"doi_redirect_final_url={landing_url}",
                        ]
                    )
                    arnumber = _arnumber_from_text(landing_url) or _arnumber_from_text(landing.text)

                if not arnumber:
                    return [
                        BibCandidate(
                            source=self.name,
                            source_url=landing_url or None,
                            bibtex=None,
                            fields={"doi": doi} if doi else {},
                            confidence="not_found",
                            score=0.0,
                            evidence=landing_evidence + ["ieee_document_id_not_found"],
                            source_priority=112,
                            canonical_id=doi or None,
                            source_kind="probe",
                            source_family="ieee",
                        )
                    ]

                endpoint = (
                    f"{self.base_url}/rest/search/citation/format"
                    f"?recordIds={arnumber}&fromPage=&citations-format=citation-abstract"
                    f"&download-format=download-bibtex"
                )
                referer = landing_url or f"{self.base_url}/document/{arnumber}/"
                response = client.get(
                    endpoint,
                    headers={
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Accept": "application/json,text/plain,*/*;q=0.1",
                        "Referer": referer,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=landing_url or None,
                    bibtex=None,
                    fields={"doi": doi} if doi else {},
                    confidence="not_found",
                    score=0.0,
                    evidence=landing_evidence + [f"ieee_native_export_failed={type(exc).__name__}: {exc}"],
                    source_priority=112,
                    canonical_id=doi or None,
                    source_kind="probe",
                    source_family="ieee",
                )
            ]

        text = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            return [
                BibCandidate(
                    source=self.name,
                    source_url=str(response.url),
                    bibtex=None,
                    fields={"doi": doi} if doi else {},
                    confidence="not_found",
                    score=0.0,
                    evidence=landing_evidence + ["ieee_export_response_missing_bibtex"],
                    source_priority=112,
                    canonical_id=doi or None,
                    source_kind="probe",
                    source_family="ieee",
                )
            ]

        cleaned = _clean_ieee_bibtex(text)
        return candidates_from_bibtex(
            entry=entry,
            source=self.name,
            source_url=str(response.url),
            text=cleaned,
            source_priority=112,
            canonical_id=doi or None,
            extra_evidence=landing_evidence
            + [
                f"ieee_document_id={arnumber}",
                "ieee_native_citation_endpoint",
                "publisher_native_export",
                "resolved_by_exact_doi",
            ],
            source_kind="publisher_native_export",
            source_family="ieee",
        )


def _arnumber_from_urls(urls: tuple[str, ...]) -> str:
    for url in urls:
        found = _arnumber_from_text(url)
        if found:
            return found
    return ""


def _arnumber_from_text(text: str) -> str:
    match = _DOCUMENT_RE.search(text) or _ARNUMBER_RE.search(text)
    return match.group(1) if match else ""


def _clean_ieee_bibtex(text: str) -> str:
    cleaned = html.unescape(text).replace("\ufeff", "").replace("\x00", "")
    # IEEE has historically emitted a stray semicolon before the closing brace
    # of keywords. Keep the repair narrow and leave all other values untouched.
    cleaned = re.sub(r"(?im)(\bkeywords\s*=\s*\{[^{}]*);\s*\}", r"\1}", cleaned)
    if re.match(r"\s*@null\b", cleaned, flags=re.I):
        cleaned = re.sub(r"(?i)^\s*@null\b", "@article", cleaned, count=1)
    return cleaned
