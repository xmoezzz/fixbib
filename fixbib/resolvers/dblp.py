from __future__ import annotations

from urllib.parse import urlparse

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidates_from_bibtex, candidate_from_fields


class DblpResolver(Resolver):
    name = "dblp"

    def __init__(
        self,
        api_base: str = "https://dblp.org/search/publ/api",
        timeout: float = 15.0,
        max_results: int = 5,
    ) -> None:
        self.api_base = api_base
        self.timeout = timeout
        self.max_results = max_results

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return bool(entry.fields.get("title"))

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        title = entry.fields.get("title", "").strip()
        if not title:
            return []
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(
                    self.api_base,
                    params={"q": title, "format": "json", "h": self.max_results},
                    headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=self.api_base,
                    bibtex=None,
                    fields={},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=85,
                    source_kind="bibliographic_index",
                    source_family="dblp",
                )
            ]

        hits = payload.get("result", {}).get("hits", {}).get("hit", []) or []
        candidates: list[BibCandidate] = []
        for hit in hits[: self.max_results]:
            info = hit.get("info", {}) or {}
            record_url = str(info.get("url") or "")

            # DBLP record URLs expose a deterministic .bib endpoint. Prefer it
            # over rebuilding from abbreviated search metadata.
            parsed_record_url = urlparse(record_url)
            if record_url and (
                parsed_record_url.netloc.lower() == "dblp.org"
                or "/rec/" in parsed_record_url.path
                or "/dblp-rec/" in parsed_record_url.path
            ):
                bib_url = record_url.rstrip("/") + ".bib"
                try:
                    with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                        bib_response = client.get(
                            bib_url,
                            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/x-bibtex,text/plain"},
                        )
                        bib_response.raise_for_status()
                    bundle = candidates_from_bibtex(
                        entry=entry,
                        source="dblp-bibtex",
                        source_url=str(bib_response.url),
                        text=bib_response.text,
                        source_priority=90,
                        canonical_id=record_url,
                        extra_evidence=["dblp_title_search", "dblp_deterministic_bib_endpoint"],
                        source_kind="bibliographic_index_export",
                        source_family="dblp",
                    )
                    if bundle:
                        candidates.extend(bundle)
                        continue
                except httpx.HTTPError:
                    pass

            fields = _dblp_info_to_fields(info, entry.entry_type)
            candidates.append(
                candidate_from_fields(
                    entry=entry,
                    source="dblp-search",
                    source_url=record_url or str(response.url),
                    fields=fields,
                    source_priority=85,
                    entry_type=entry.entry_type,
                    canonical_id=record_url or None,
                    extra_evidence=["dblp_title_search"],
                    source_kind="bibliographic_index",
                    source_family="dblp",
                )
            )
        return candidates


def _dblp_info_to_fields(info: dict, fallback_type: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for source_key, target_key in (("title", "title"), ("year", "year"), ("doi", "doi")):
        value = info.get(source_key)
        if value:
            fields[target_key] = str(value)

    authors_value = (info.get("authors") or {}).get("author", [])
    if isinstance(authors_value, (str, dict)):
        authors_value = [authors_value]
    authors: list[str] = []
    for author in authors_value:
        if isinstance(author, dict):
            name = str(author.get("text") or "")
        else:
            name = str(author)
        if name:
            authors.append(name)
    if authors:
        fields["author"] = " and ".join(authors)

    venue = info.get("venue")
    if venue:
        fields["journal" if fallback_type == "article" else "booktitle"] = str(venue)
    if info.get("pages"):
        fields["pages"] = str(info["pages"])
    if info.get("url"):
        fields["url"] = str(info["url"])
    return fields
