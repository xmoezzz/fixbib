from __future__ import annotations

import re

import httpx

from fixbib.model import BibCandidate, BibInputEntry
from fixbib.resolvers.base import Resolver
from fixbib.resolvers.common import DEFAULT_USER_AGENT, candidates_from_bibtex

ACL_ID_RE = re.compile(r"(?:aclanthology\.org/|10\.18653/v1/)([A-Za-z0-9._-]+)", re.I)


class AclAnthologyResolver(Resolver):
    name = "acl-anthology-bibtex"

    def __init__(self, base_url: str = "https://aclanthology.org", timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def can_resolve(self, entry: BibInputEntry) -> bool:
        return _find_acl_id(entry) is not None

    def resolve(self, entry: BibInputEntry) -> list[BibCandidate]:
        anthology_id = _find_acl_id(entry)
        if not anthology_id:
            return []
        url = f"{self.base_url}/{anthology_id}.bib"
        try:
            with httpx.Client(follow_redirects=True, timeout=self.timeout) as client:
                response = client.get(url, headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/plain"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return [
                BibCandidate(
                    source=self.name,
                    source_url=url,
                    bibtex=None,
                    fields={},
                    confidence="not_found",
                    score=0.0,
                    evidence=[f"request_failed={type(exc).__name__}: {exc}"],
                    source_priority=110,
                    canonical_id=anthology_id,
                    source_kind="publisher_native_export",
                    source_family="acl",
                )
            ]
        return candidates_from_bibtex(
            entry=entry,
            source=self.name,
            source_url=str(response.url),
            text=response.text,
            source_priority=110,
            canonical_id=anthology_id,
            extra_evidence=["acl_deterministic_bib_endpoint", "publisher_native_bibtex"],
            source_kind="publisher_native_export",
            source_family="acl",
        )


def _find_acl_id(entry: BibInputEntry) -> str | None:
    for key in ("doi", "url", "howpublished"):
        match = ACL_ID_RE.search(entry.fields.get(key, ""))
        if match:
            return match.group(1).rstrip("./")
    return None
