from __future__ import annotations

import base64
import binascii
import html
import json
import re
from urllib.parse import unquote_to_bytes

from bs4 import BeautifulSoup

from .bib_build import build_bibtex_from_fields
from .model import BibDiagnostic, BibInputEntry, CandidateBibParse
from .parser import parse_bib_text

MAX_CANDIDATE_CHARS = 5_000_000
BIB_START_RE = re.compile(
    r"@(article|inproceedings|proceedings|misc|book|incollection|inbook|"
    r"phdthesis|mastersthesis|techreport|unpublished|manual)\s*[({]",
    re.I,
)
DATA_BIB_MIME_TYPES = {
    "application/x-bibtex",
    "text/x-bibtex",
    "application/bibtex",
    "text/bibtex",
}


def parse_candidate_bibtex(text: str) -> CandidateBibParse:
    """Parse untrusted publisher BibTeX without leaking parser warnings.

    Publisher exports may contain multiple records, malformed records, duplicate
    keys, HTML wrappers, JSON wrappers, URL encoding, NUL bytes, or Markdown
    fences. Every representation is treated as untrusted. Valid entries are
    retained while malformed blocks become structured diagnostics.
    """

    diagnostics: list[BibDiagnostic] = []
    transformations: list[str] = []
    truncated = False
    source = str(text or "")
    if len(source) > MAX_CANDIDATE_CHARS:
        source = source[:MAX_CANDIDATE_CHARS]
        truncated = True
        diagnostics.append(
            BibDiagnostic(
                kind="candidate_too_large",
                message=f"candidate export truncated at {MAX_CANDIDATE_CHARS} characters",
            )
        )

    source = source.lstrip("\ufeff").replace("\x00", "")
    payloads = _candidate_payload_variants(source, transformations)
    entries: list[BibInputEntry] = []
    seen_entries: set[tuple[object, ...]] = set()
    seen_diagnostics: set[tuple[str, str, int | None]] = set()

    for payload in payloads:
        parsed = parse_bib_text(payload, path="<remote-candidate>")
        for diagnostic in parsed.diagnostics:
            signature = (diagnostic.kind, diagnostic.message, diagnostic.start_line)
            if signature not in seen_diagnostics:
                seen_diagnostics.add(signature)
                diagnostics.append(diagnostic)
        for entry in parsed.entries:
            signature = (
                entry.entry_type,
                entry.key,
                tuple(sorted(entry.fields.items())),
            )
            if signature in seen_entries:
                continue
            seen_entries.add(signature)
            entries.append(entry)

    if not entries and source.strip():
        diagnostics.append(
            BibDiagnostic(
                kind="candidate_no_bibtex_entry",
                message="remote export contained no parseable BibTeX entry",
            )
        )

    return CandidateBibParse(
        entries=entries,
        diagnostics=diagnostics,
        transformations=transformations,
        truncated=truncated,
    )


def parse_bibtex_entries(text: str) -> list[BibInputEntry]:
    return parse_candidate_bibtex(text).entries


def parse_single_bibtex_entry(text: str) -> BibInputEntry | None:
    entries = parse_bibtex_entries(text)
    if len(entries) != 1:
        return None
    return entries[0]


def rekey_parsed_entry(parsed: BibInputEntry, original_key: str) -> tuple[str, BibInputEntry]:
    rebuilt = build_bibtex_from_fields(parsed.entry_type, original_key, parsed.fields)
    reparsed = BibInputEntry(
        kind="entry",
        raw=rebuilt,
        start_line=None,
        entry_type=parsed.entry_type,
        key=original_key,
        fields=parsed.fields,
        field_order=parsed.field_order,
        duplicate_fields=parsed.duplicate_fields,
        duplicate_key=parsed.duplicate_key,
    )
    return rebuilt, reparsed


def rekey_candidate_bibtex_entries(
    text: str,
    original_key: str,
) -> tuple[list[tuple[str, BibInputEntry, str]], CandidateBibParse]:
    parsed_bundle = parse_candidate_bibtex(text)
    result: list[tuple[str, BibInputEntry, str]] = []
    for parsed in parsed_bundle.entries:
        rebuilt, reparsed = rekey_parsed_entry(parsed, original_key)
        result.append((rebuilt, reparsed, parsed.key))
    return result, parsed_bundle


def rekey_bibtex_entries(text: str, original_key: str) -> list[tuple[str, BibInputEntry, str]]:
    result, _ = rekey_candidate_bibtex_entries(text, original_key)
    return result


def rekey_bibtex(text: str, original_key: str) -> tuple[str, BibInputEntry] | None:
    entries = rekey_bibtex_entries(text, original_key)
    if len(entries) != 1:
        return None
    bibtex, parsed, _ = entries[0]
    return bibtex, parsed


def decode_bibtex_data_uri(value: str) -> tuple[str | None, str | None]:
    """Decode BibTeX embedded in a data URI or a known malformed equivalent."""

    raw = html.unescape(str(value or "").strip())
    lowered = raw.lower()
    if lowered.startswith("data:"):
        payload = raw[5:]
    elif lowered.startswith(("/application/x-bibtex", "application/x-bibtex")):
        # Some sites or HTML processors drop the ``data:`` prefix while keeping
        # the media type and encoded payload. Treat this as recoverable input.
        payload = raw.lstrip("/")
    else:
        return None, "not_a_bibtex_data_uri"

    if "," not in payload:
        return None, "data_uri_missing_comma"
    metadata, encoded = payload.split(",", 1)
    parts = [part.strip() for part in metadata.split(";") if part.strip()]
    media_type = (parts[0] if parts else "text/plain").lower()
    if media_type not in DATA_BIB_MIME_TYPES:
        return None, f"unsupported_data_uri_media_type={media_type}"
    is_base64 = any(part.lower() == "base64" for part in parts[1:])
    try:
        raw_bytes = (
            base64.b64decode(encoded, validate=True)
            if is_base64
            else unquote_to_bytes(encoded)
        )
    except (binascii.Error, ValueError) as exc:
        return None, f"invalid_data_uri={type(exc).__name__}"

    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding), None
        except UnicodeDecodeError:
            continue
    return None, "data_uri_decode_failed"


def _candidate_payload_variants(source: str, transformations: list[str]) -> list[str]:
    variants: list[str] = []

    def add(value: str, marker: str | None = None) -> None:
        value = value.strip()
        if not value or value in variants:
            return
        variants.append(value)
        if marker and marker not in transformations:
            transformations.append(marker)

    decoded_data, _ = decode_bibtex_data_uri(source)
    if decoded_data is not None:
        add(decoded_data, "decoded_data_uri")
    add(_strip_markdown_fence(source))

    unescaped = html.unescape(source)
    if unescaped != source:
        add(_strip_markdown_fence(unescaped), "html_unescaped")

    if re.search(r"%(?:40|7[bB]|7[dD]|2[cC]|3[dD])", source):
        try:
            percent = unquote_to_bytes(source).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            percent = ""
        if percent:
            add(_strip_markdown_fence(percent), "percent_decoded")

    for extracted, marker in _extract_wrapped_payloads(source):
        add(extracted, marker)

    return [variant for variant in variants if BIB_START_RE.search(variant)] or variants


def _strip_markdown_fence(value: str) -> str:
    stripped = value.strip()
    match = re.fullmatch(r"```(?:bibtex|bib)?\s*(.*?)\s*```", stripped, re.I | re.S)
    return match.group(1) if match else stripped


def _extract_wrapped_payloads(source: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    stripped = source.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            payload = json.loads(source)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            for value in _walk_json_strings(payload):
                if BIB_START_RE.search(value) or value.lower().startswith("data:"):
                    decoded, _ = decode_bibtex_data_uri(value)
                    result.append((decoded or value, "json_wrapped_bibtex"))

    if "<" in source and ">" in source:
        soup = BeautifulSoup(source, "html.parser")
        for node in soup.find_all(["pre", "code", "textarea"]):
            value = node.get_text("\n", strip=True)
            if BIB_START_RE.search(value):
                result.append((value, "html_wrapped_bibtex"))
    return result


def _walk_json_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_json_strings(item)
