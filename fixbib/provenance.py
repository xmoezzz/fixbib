from __future__ import annotations

from urllib.parse import urlparse

from .model import BibCandidate

# Identity confidence and source authority are deliberately separate. An exact
# DOI confirms that two records describe the same work; it does not make a
# generated BibTeX representation authoritative formatting.
SOURCE_KIND_AUTHORITY: dict[str, int] = {
    "publisher_native_export": 700,
    "repository_native_export": 680,
    "publisher_page_metadata": 620,
    "bibliographic_index_export": 580,
    "registry_metadata": 560,
    "bibliographic_index": 500,
    "book_catalog": 480,
    "generic_web_metadata": 360,
    "registry_transform": 300,
    "probe": 0,
    "unknown": 200,
}

SOURCE_KIND_LABELS: dict[str, str] = {
    "publisher_native_export": "publisher native export",
    "repository_native_export": "repository native export",
    "publisher_page_metadata": "publisher page metadata",
    "bibliographic_index_export": "bibliographic index export",
    "registry_metadata": "registry metadata",
    "registry_transform": "registry-generated BibTeX",
    "bibliographic_index": "bibliographic index",
    "book_catalog": "book catalog",
    "generic_web_metadata": "web metadata",
    "probe": "page probe",
    "unknown": "unclassified source",
}

KNOWN_FAMILY_DOMAINS: tuple[tuple[str, str], ...] = (
    ("crossref.org", "crossref"),
    ("dl.acm.org", "acm"),
    ("acm.org", "acm"),
    ("springer.com", "springer"),
    ("springerlink.com", "springer"),
    ("ieee.org", "ieee"),
    ("ieeexplore.ieee.org", "ieee"),
    ("aclanthology.org", "acl"),
    ("usenix.org", "usenix"),
    ("dblp.org", "dblp"),
    ("arxiv.org", "arxiv"),
    ("openlibrary.org", "openlibrary"),
    ("openalex.org", "openalex"),
    ("semanticscholar.org", "semantic-scholar"),
    ("datacite.org", "datacite"),
)


def source_authority(candidate: BibCandidate) -> int:
    return SOURCE_KIND_AUTHORITY.get(candidate.source_kind, 200)


def source_kind_label(kind: str) -> str:
    return SOURCE_KIND_LABELS.get(kind, kind or "unclassified source")


def infer_source_family(source: str, source_url: str | None = None) -> str:
    lowered = source.lower()
    for marker, family in (
        ("crossref", "crossref"),
        ("springer", "springer"),
        ("acl", "acl"),
        ("arxiv", "arxiv"),
        ("usenix", "usenix"),
        ("dblp", "dblp"),
        ("acm", "acm"),
        ("openlibrary", "openlibrary"),
        ("openalex", "openalex"),
        ("semantic-scholar", "semantic-scholar"),
        ("google-books", "google-books"),
        ("datacite", "datacite"),
    ):
        if marker in lowered:
            return family

    host = urlparse(source_url or "").netloc.lower().removeprefix("www.")
    for domain, family in KNOWN_FAMILY_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return family
    return host or lowered.split("-", 1)[0] or "unknown"


def page_source_kind(url: str, *, has_native_bibtex: bool = False) -> str:
    if has_native_bibtex:
        return "publisher_native_export"
    host = urlparse(url).netloc.lower()
    scholarly_markers = (
        "acm.org",
        "springer.com",
        "ieee.org",
        "usenix.org",
        "aclanthology.org",
        "sciencedirect.com",
        "elsevier.com",
        "wiley.com",
        "tandfonline.com",
        "sagepub.com",
        "mdpi.com",
        "siam.org",
        "ndss-symposium.org",
    )
    if any(marker in host for marker in scholarly_markers):
        return "publisher_page_metadata"
    return "generic_web_metadata"
