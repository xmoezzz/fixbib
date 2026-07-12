from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from collections import Counter
from pathlib import Path
from typing import Any

import bibtexparser
from bibtexparser.model import (
    DuplicateBlockKeyBlock,
    DuplicateFieldKeyBlock,
    Entry,
    ExplicitComment,
    ImplicitComment,
    MiddlewareErrorBlock,
    ParsingFailedBlock,
    Preamble,
    String,
)

from .model import (
    BibBlock,
    BibDiagnostic,
    BibInputEntry,
    BibPreamble,
    BibString,
    ParsedBibFile,
)

MONTH_MACROS = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}


def read_text_lossless(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def parse_bib_file(path: Path | str) -> ParsedBibFile:
    p = Path(path)
    return parse_bib_text(read_text_lossless(p), path=str(p))


def parse_bib_text(text: str, path: str = "<memory>") -> ParsedBibFile:
    """Parse BibTeX using bibtexparser v2 and retain raw top-level blocks.

    bibtexparser is responsible for syntax, nested braces, comments, strings,
    preambles, quoted values, duplicate-key error blocks, and line metadata.
    We only convert its model into FixBib's stable internal model.
    """
    normalized_text, delimiter_rewrites = _normalize_entry_delimiters(text)
    try:
        with _quiet_bibtexparser_logs():
            # We intentionally disable bibtexparser's default middleware. FixBib
            # resolves strings and removes enclosing braces itself, while the
            # default middleware logs warnings for recoverable error blocks.
            library = bibtexparser.parse_string(normalized_text, parse_stack=[])
    except Exception as exc:
        # Arbitrary user files and hostile remote exports must not crash the CLI
        # even if the third-party parser reaches an unexpected internal state.
        diagnostic = BibDiagnostic(
            kind="parser_exception",
            message=f"{type(exc).__name__}: {exc}",
            start_line=None,
            raw=text,
        )
        return ParsedBibFile(
            path=path,
            blocks=[BibBlock(kind="parse_failed", raw=text, start_line=1)],
            entries=[],
            diagnostics=[diagnostic],
        )
    blocks: list[BibBlock] = []
    entries: list[BibInputEntry] = []
    diagnostics: list[BibDiagnostic] = []
    string_macros: dict[str, str] = dict(MONTH_MACROS)
    seen_keys: Counter[str] = Counter()

    # First collect @string definitions so references can be normalized even if
    # the definition appears after an entry.
    for block in library.blocks:
        if isinstance(block, String):
            string_macros[str(block.key).lower()] = _clean_value(str(block.value), {})

    for block in library.blocks:
        raw = getattr(block, "raw", "") or ""
        raw = _restore_original_raw(raw, normalized_text, text)
        start_line = _line_number(getattr(block, "start_line", None))

        if isinstance(block, Entry):
            converted = _convert_entry(block, raw, start_line, string_macros)
            seen_keys[converted.key] += 1
            converted.duplicate_key = seen_keys[converted.key] > 1
            entries.append(converted)
            blocks.append(converted)
            continue

        if isinstance(block, DuplicateFieldKeyBlock):
            failed_entry = block.ignore_error_block
            converted = _convert_entry(
                failed_entry,
                raw,
                start_line,
                string_macros,
                duplicate_fields=sorted(str(x).lower() for x in block.duplicate_keys),
            )
            seen_keys[converted.key] += 1
            converted.duplicate_key = seen_keys[converted.key] > 1
            entries.append(converted)
            blocks.append(converted)
            diagnostics.append(
                BibDiagnostic(
                    kind="duplicate_fields",
                    message=f"{converted.key}: duplicate fields: {', '.join(converted.duplicate_fields)}",
                    start_line=start_line,
                    raw=raw,
                )
            )
            continue

        if isinstance(block, DuplicateBlockKeyBlock):
            failed_entry = block.ignore_error_block
            converted = _convert_entry(failed_entry, raw, start_line, string_macros)
            converted.duplicate_key = True
            seen_keys[converted.key] += 1
            entries.append(converted)
            blocks.append(converted)
            diagnostics.append(
                BibDiagnostic(
                    kind="duplicate_entry_key",
                    message=f"duplicate entry key: {converted.key}",
                    start_line=start_line,
                    raw=raw,
                )
            )
            continue

        if isinstance(block, String):
            blocks.append(
                BibString(
                    kind="string",
                    raw=raw,
                    start_line=start_line,
                    name=str(block.key),
                    value=_clean_value(str(block.value), string_macros),
                )
            )
            continue

        if isinstance(block, Preamble):
            blocks.append(
                BibPreamble(
                    kind="preamble",
                    raw=raw,
                    start_line=start_line,
                    value=str(block.value),
                )
            )
            continue

        if isinstance(block, (ExplicitComment, ImplicitComment)):
            blocks.append(BibBlock(kind="comment", raw=raw, start_line=start_line))
            continue

        if isinstance(block, (ParsingFailedBlock, MiddlewareErrorBlock)):
            blocks.append(BibBlock(kind="parse_failed", raw=raw, start_line=start_line))
            diagnostics.append(
                BibDiagnostic(
                    kind="parse_error",
                    message=str(block.error),
                    start_line=start_line,
                    raw=raw,
                )
            )
            continue

        blocks.append(BibBlock(kind="unknown", raw=raw, start_line=start_line))
        diagnostics.append(
            BibDiagnostic(
                kind="unknown_block",
                message=f"unsupported bibtexparser block: {type(block).__name__}",
                start_line=start_line,
                raw=raw,
            )
        )

    # bibtexparser suppresses later duplicate entries into error blocks. Mark the
    # first occurrence too, so no duplicate-key entry is auto-rewritten.
    duplicate_keys = {key for key, count in seen_keys.items() if count > 1}
    for entry in entries:
        if entry.key in duplicate_keys:
            entry.duplicate_key = True

    if delimiter_rewrites:
        diagnostics.append(
            BibDiagnostic(
                kind="normalized_entry_delimiter",
                message=(
                    f"normalized {delimiter_rewrites} parenthesized BibTeX "
                    "entry delimiter(s) for parsing"
                ),
            )
        )

    return ParsedBibFile(path=path, blocks=blocks, entries=entries, diagnostics=diagnostics)


def _convert_entry(
    entry: Entry,
    raw: str,
    start_line: int | None,
    string_macros: dict[str, str],
    duplicate_fields: list[str] | None = None,
) -> BibInputEntry:
    fields: dict[str, str] = {}
    field_order: list[str] = []
    counts: Counter[str] = Counter()

    for field in entry.fields:
        key = str(field.key).strip().lower()
        counts[key] += 1
        if key in fields:
            continue
        fields[key] = _clean_value(str(field.value), string_macros)
        field_order.append(key)

    dups = duplicate_fields or sorted(key for key, count in counts.items() if count > 1)
    return BibInputEntry(
        kind="entry",
        raw=raw,
        start_line=start_line,
        entry_type=str(entry.entry_type),
        key=str(entry.key),
        fields=fields,
        field_order=field_order,
        duplicate_fields=dups,
    )


def _line_number(value: Any) -> int | None:
    if value is None:
        return None
    # bibtexparser v2 uses zero-based line numbers.
    return int(value) + 1


def _clean_value(value: str, string_macros: dict[str, str]) -> str:
    value = value.strip()
    value = _strip_outer(value)

    if "#" in value:
        parts = _split_concat(value)
        cleaned: list[str] = []
        for part in parts:
            token = _strip_outer(part.strip())
            cleaned.append(string_macros.get(token.lower(), token))
        value = "".join(cleaned)
    elif value.lower() in string_macros:
        value = string_macros[value.lower()]

    return value.strip()


def _strip_outer(value: str) -> str:
    if len(value) < 2:
        return value
    if value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    if value[0] == "{" and value[-1] == "}" and _outer_braces_cover_all(value):
        return value[1:-1]
    return value


def _outer_braces_cover_all(value: str) -> bool:
    depth = 0
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return False
    return depth == 0


def _split_concat(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    brace_depth = 0
    in_quote = False
    escaped = False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if char == '"' and brace_depth == 0:
            in_quote = not in_quote
            current.append(char)
            continue
        if not in_quote:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif char == "#" and brace_depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(char)
    parts.append("".join(current))
    return parts


def entries_to_jsonable(entries: list[BibInputEntry]) -> list[dict[str, object]]:
    return [
        {
            "key": entry.key,
            "entry_type": entry.entry_type,
            "start_line": entry.start_line,
            "fields": entry.fields,
            "field_order": entry.field_order,
            "duplicate_fields": entry.duplicate_fields,
            "duplicate_key": entry.duplicate_key,
            "raw": entry.raw,
        }
        for entry in entries
    ]


@contextmanager
def _quiet_bibtexparser_logs():
    """Prevent recoverable parser diagnostics from leaking to stderr."""

    names = (
        "bibtexparser.splitter",
        "bibtexparser.middlewares.middleware",
    )
    states: list[tuple[logging.Logger, bool, int]] = []
    for name in names:
        logger = logging.getLogger(name)
        states.append((logger, logger.disabled, logger.level))
        logger.disabled = True
    try:
        yield
    finally:
        for logger, disabled, level in states:
            logger.disabled = disabled
            logger.setLevel(level)


def _normalize_entry_delimiters(text: str) -> tuple[str, int]:
    """Normalize standard ``@type(...)`` blocks for bibtexparser v2."""

    chars = list(text)
    rewrites = 0
    pattern = re.compile(r"@[A-Za-z][A-Za-z0-9_:-]*\s*\(")
    pos = 0
    while True:
        match = pattern.search(text, pos)
        if not match:
            break
        open_index = match.end() - 1
        close_index = _find_matching_parenthesis(text, open_index)
        if close_index is None:
            pos = match.end()
            continue
        chars[open_index] = "{"
        chars[close_index] = "}"
        rewrites += 1
        pos = close_index + 1
    return "".join(chars), rewrites


def _find_matching_parenthesis(text: str, open_index: int) -> int | None:
    paren_depth = 1
    brace_depth = 0
    quoted = False
    escaped = False
    for index in range(open_index + 1, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"' and brace_depth == 0:
            quoted = not quoted
            continue
        if quoted:
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth > 0:
            brace_depth -= 1
        elif brace_depth == 0:
            if char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    return index
    return None


def _restore_original_raw(raw: str, normalized: str, original: str) -> str:
    """Restore an original block slice after same-length delimiter rewriting."""

    if not raw or normalized == original:
        return raw
    index = normalized.find(raw)
    if index < 0:
        return raw
    return original[index : index + len(raw)]
