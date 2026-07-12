from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable

from .model import BibInputEntry
from .normalizer import find_entry_arxiv_id, find_entry_doi

URL_RE = re.compile(r"https?://[^\s{}<>\\]+", re.I)
LATEX_URL_RE = re.compile(r"\\url\s*\{([^{}]+)\}", re.I)
ISBN_RE = re.compile(r"(?<!\d)(?:97[89][\s-]?)?\d(?:[\d\s-]{7,15})[\dXx](?!\d)")


@dataclass(frozen=True)
class EntryInventory:
    doi: str | None
    urls: tuple[str, ...]
    isbn: str | None
    arxiv_id: str | None

    @property
    def has_doi(self) -> bool:
        return bool(self.doi)

    @property
    def has_url(self) -> bool:
        return bool(self.urls)

    @property
    def has_doi_or_url(self) -> bool:
        return self.has_doi or self.has_url

    @property
    def has_any_locator(self) -> bool:
        return bool(self.doi or self.urls or self.isbn or self.arxiv_id)

    @property
    def locator_label(self) -> str:
        labels: list[str] = []
        if self.doi:
            labels.append("DOI")
        if self.urls:
            labels.append("URL")
        if self.isbn:
            labels.append("ISBN")
        if self.arxiv_id:
            labels.append("arXiv")
        return "+".join(labels) if labels else "none"

    def to_jsonable(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            {
                "has_doi": self.has_doi,
                "has_url": self.has_url,
                "has_doi_or_url": self.has_doi_or_url,
                "has_any_locator": self.has_any_locator,
                "locator_label": self.locator_label,
            }
        )
        return payload


def inspect_entry(entry: BibInputEntry) -> EntryInventory:
    return EntryInventory(
        doi=find_entry_doi(entry.fields),
        urls=tuple(extract_entry_urls(entry.fields)),
        isbn=find_entry_isbn(entry.fields),
        arxiv_id=find_entry_arxiv_id(entry.fields) or None,
    )


def extract_entry_urls(fields: dict[str, str]) -> list[str]:
    urls: list[str] = []
    # URL-bearing fields first, followed by all fields as a conservative fallback.
    ordered_keys = [
        "url",
        "howpublished",
        "note",
        "biburl",
        "ee",
        "pdf",
    ]
    seen_keys = set(ordered_keys)
    values: list[str] = [str(fields.get(key) or "") for key in ordered_keys]
    values.extend(str(value or "") for key, value in fields.items() if key not in seen_keys)

    for value in values:
        if not value:
            continue
        for match in LATEX_URL_RE.finditer(value):
            _append_unique(urls, _clean_url(match.group(1)))
        for match in URL_RE.finditer(value):
            _append_unique(urls, _clean_url(match.group(0)))
    return urls


def find_entry_isbn(fields: dict[str, str]) -> str | None:
    explicit = str(fields.get("isbn") or "")
    candidates: Iterable[str] = (
        [explicit]
        if explicit
        else [str(fields.get(key) or "") for key in ("url", "howpublished", "note")]
    )
    for value in candidates:
        for match in ISBN_RE.finditer(str(value or "")):
            normalized = normalize_isbn(match.group(0))
            if len(normalized) in {10, 13}:
                return normalized
    return None


def normalize_isbn(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", value).upper()


def _clean_url(value: str) -> str:
    return value.strip().rstrip(".,;:)]")


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)
