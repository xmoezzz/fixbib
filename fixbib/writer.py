from __future__ import annotations

from .bib_build import build_bibtex_preserving_fields
from .model import BibInputEntry, ParsedBibFile, ResolveResult


def write_fixed_bib(parsed: ParsedBibFile, results: list[ResolveResult]) -> str:
    # Applied entries are field-level merges, never raw remote replacements.
    replacements = {
        result.key: result.applied_bibtex
        for result in results
        if result.action == "replace"
        and result.applied_bibtex
        and not result.original.duplicate_key
    }

    chunks: list[str] = []
    for block in parsed.blocks:
        if isinstance(block, BibInputEntry) and block.key in replacements:
            chunks.append(str(replacements[block.key]).rstrip())
        elif (
            isinstance(block, BibInputEntry)
            and not block.duplicate_key
            and any(not str(value or "").strip() for value in block.fields.values())
        ):
            fields = {
                key: value
                for key, value in block.fields.items()
                if str(value or "").strip()
            }
            order = [key for key in block.field_order if key in fields]
            chunks.append(
                build_bibtex_preserving_fields(
                    entry_type=block.entry_type,
                    key=block.key,
                    fields=fields,
                    field_order=order,
                ).rstrip()
            )
        else:
            chunks.append(block.raw.rstrip())
    return "\n\n".join(chunk for chunk in chunks if chunk) + "\n"
