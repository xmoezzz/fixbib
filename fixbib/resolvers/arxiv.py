from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.normalizer import find_entry_arxiv_id, is_preprint_entry, norm_text
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import (
    DEFAULT_USER_AGENT,
    candidate_from_fields,
    candidates_from_bibtex,
)

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


class ArxivResolver(Resolver):
    name = "arxiv"

    def __init__(
        self,
        bibtex_base: str = "https://arxiv.org/bibtex",
        api_base: str = "https://export.arxiv.org/api/query",
        timeout: float = 15.0,
        title_fallback: bool = True,
    ) -> None:
        self.bibtex_base = bibtex_base.rstrip("/")
        self.api_base = api_base
        self.timeout = timeout
        self.title_fallback = title_fallback

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(find_entry_arxiv_id(entry.fields) or (self.title_fallback and is_preprint_entry(entry.entry_type, entry.fields)))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        arxiv_id = find_entry_arxiv_id(entry.fields)
        if arxiv_id:
            candidate = self._fetch_bibtex(entry, arxiv_id, "exact_arxiv_id")
            return [candidate] if candidate else []

        if not self.title_fallback or not is_preprint_entry(entry.entry_type, entry.fields):
            return []
        title = entry.fields.get("title", "").strip()
        if not title:
            return []

        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    self.api_base,
                    params={"search_query": f'ti:"{title}"', "start": 0, "max_results": 5},
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/atom+xml"},
                )
                response.raise_for_status()
                root = ET.fromstring(response.text)
        except (httpx.HTTPError, ET.ParseError) as exc:
            return [
                BibCandidate(
                    source="arxiv-title-search",
                    source_url=self.api_base,
                    bibtex=None,
                    fields={},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=55,
                    source_kind="repository_native_export",
                    source_family="arxiv",
                )
            ]

        wanted = norm_text(title)
        candidates: list[BibCandidate] = []
        for node in root.findall("atom:entry", ATOM_NS):
            candidate_title = norm_text(node.findtext("atom:title", default="", namespaces=ATOM_NS))
            if not candidate_title:
                continue
            id_url = node.findtext("atom:id", default="", namespaces=ATOM_NS)
            found_id = find_entry_arxiv_id({"url": id_url})
            if not found_id:
                continue
            candidate = self._fetch_bibtex(entry, found_id, "arxiv_title_search")
            if candidate:
                candidate.evidence.append(f"query_title={wanted}")
                candidates.append(candidate)
        return candidates

    def _fetch_bibtex(self, entry: BibInputEntry, arxiv_id: str, evidence: str) -> BibCandidate | None:
        url = f"{self.bibtex_base}/{quote(arxiv_id, safe='/.')}"
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/plain"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            # The arXiv identifier itself deterministically defines the stable
            # abstract URL. Preserve that useful repair even when the BibTeX
            # endpoint is temporarily unavailable.
            fields = dict(entry.fields)
            fields.setdefault("eprint", arxiv_id)
            fields.setdefault("archiveprefix", "arXiv")
            fields["url"] = f"https://arxiv.org/abs/{arxiv_id}"
            return candidate_from_fields(
                entry=entry,
                source="arxiv-local-identifier",
                source_url=fields["url"],
                fields=fields,
                source_priority=104 if evidence == "exact_arxiv_id" else 54,
                entry_type=entry.entry_type,
                canonical_id=arxiv_id,
                extra_evidence=[
                    evidence,
                    "local_arxiv_identifier_normalization",
                    f"arxiv_bibtex_unavailable={type(exc).__name__}: {exc}",
                ],
                source_kind="repository_native_export",
                source_family="arxiv",
            )
        candidates = candidates_from_bibtex(
            entry=entry,
            source="arxiv-bibtex",
            source_url=str(response.url),
            text=response.text,
            source_priority=105 if evidence == "exact_arxiv_id" else 55,
            canonical_id=arxiv_id,
            extra_evidence=[evidence, "repository_native_bibtex"],
            source_kind="repository_native_export",
            source_family="arxiv",
        )
        plausible = [candidate for candidate in candidates if candidate.confidence in {"exact", "high"}]
        if len(plausible) == 1:
            return plausible[0]
        return candidates[0] if len(candidates) == 1 else None
