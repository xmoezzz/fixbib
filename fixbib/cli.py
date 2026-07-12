from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from . import __version__
from .inventory import EntryInventory, inspect_entry
from .parser import entries_to_jsonable, parse_bib_file
from .provenance import source_authority, source_kind_label
from .resolve import ResolverPipeline
from .writer import write_fixed_bib

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fixbib",
        description="Verify and repair a BibTeX file while preserving every citation key.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("bib", type=Path, help="Input .bib file")
    parser.add_argument("--version", action="version", version=f"FixBib {__version__}")

    output = parser.add_argument_group("output")
    exclusive = output.add_mutually_exclusive_group()
    exclusive.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .bib path; defaults to <input>.fixed.bib",
    )
    exclusive.add_argument(
        "--in-place",
        action="store_true",
        help="Replace the input file after creating a backup",
    )
    output.add_argument(
        "--backup-suffix",
        default=".bak",
        help="Backup suffix used with --in-place",
    )
    output.add_argument(
        "--report",
        type=Path,
        help="JSON audit path; defaults to <input>.fixbib.json",
    )
    output.add_argument("--no-report", action="store_true", help="Do not write the JSON audit report")
    output.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and display results without writing a BibTeX file",
    )
    output.add_argument(
        "--inspect",
        action="store_true",
        help="Parse and inventory the file only; do not access the network",
    )

    resolution = parser.add_argument_group("resolution")
    resolution.add_argument(
        "--auto",
        choices=["none", "exact", "verified", "high"],
        default="verified",
        help=(
            "Automatic update policy: exact identifiers only; verified also accepts "
            "a unique authoritative/corroborated high match; high accepts any high match"
        ),
    )
    resolution.add_argument(
        "--thorough",
        action="store_true",
        help="Query all applicable sources for cross-checking instead of stopping after strong evidence",
    )
    resolution.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout per request in seconds")
    resolution.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass ~/.bibfix_cache completely and query every remote source again",
    )
    resolution.add_argument(
        "--semantic-scholar",
        action="store_true",
        help="Enable Semantic Scholar as an optional title-search source",
    )
    resolution.add_argument("--no-page-fallback", action="store_true", help="Disable HTML metadata/export fallback")
    resolution.add_argument("--no-doi-landing", action="store_true", help="Do not open DOI landing pages")
    resolution.add_argument("--no-acm-fallback", action="store_true", help="Disable ACM native export and ACM page fallback")
    resolution.add_argument(
        "--no-discovered-page-fallback",
        action="store_true",
        help="Do not inspect publication pages discovered by bibliographic indexes",
    )

    display = parser.add_argument_group("display")
    display.add_argument(
        "--table",
        choices=["all", "problems", "none"],
        default="problems",
        help="Which entries to show in the final table",
    )
    display.add_argument("--max-table-rows", type=int, default=80)

    # Optional source credentials are read from environment variables by default.
    resolution.add_argument("--google-books-api-key", default=os.getenv("GOOGLE_BOOKS_API_KEY"), help=argparse.SUPPRESS)
    resolution.add_argument("--openalex-api-key", default=os.getenv("OPENALEX_API_KEY"), help=argparse.SUPPRESS)
    resolution.add_argument("--semantic-scholar-api-key", default=os.getenv("SEMANTIC_SCHOLAR_API_KEY"), help=argparse.SUPPRESS)

    # Endpoint overrides are kept for tests, mirrors, and advanced deployments.
    for flag, default in (
        ("--doi-base-url", "https://doi.org"),
        ("--crossref-api-base", "https://api.crossref.org"),
        ("--dblp-api-base", "https://dblp.org/search/publ/api"),
        ("--springer-base-url", "https://citation-needed.springer.com/v2/references"),
        ("--acl-base-url", "https://aclanthology.org"),
        ("--arxiv-bibtex-base", "https://arxiv.org/bibtex"),
        ("--arxiv-api-base", "https://export.arxiv.org/api/query"),
        ("--acm-base-url", "https://dl.acm.org/doi"),
        ("--acm-export-url", "https://dl.acm.org/action/exportCiteProcCitation"),
        ("--ieee-base-url", "https://ieeexplore.ieee.org"),
        ("--openlibrary-api-base", "https://openlibrary.org"),
        ("--google-books-api-base", "https://www.googleapis.com/books/v1/volumes"),
        ("--openalex-api-base", "https://api.openalex.org/works"),
        ("--semantic-scholar-api-base", "https://api.semanticscholar.org/graph/v1/paper/search"),
    ):
        resolution.add_argument(flag, default=default, help=argparse.SUPPRESS)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.in_place and args.dry_run:
        parser.error("--in-place and --dry-run cannot be used together")
    if args.no_report and args.report:
        parser.error("--report and --no-report cannot be used together")

    try:
        _validate_input(args.bib)
        if args.inspect:
            cmd_inspect(args.bib)
            return
        cmd_fix(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise SystemExit(130)
    except (OSError, ValueError) as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise SystemExit(2)


def _validate_input(path: Path) -> None:
    if not path.exists():
        raise ValueError(f"input file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"input path is not a file: {path}")
    if path.suffix.lower() != ".bib":
        raise ValueError(f"input file must use the .bib extension: {path}")


def _derived_path(bib: Path, marker: str, suffix: str) -> Path:
    return bib.with_name(f"{bib.stem}{marker}{suffix}")


def _default_output_path(bib: Path) -> Path:
    return _derived_path(bib, ".fixed", ".bib")


def _default_report_path(bib: Path) -> Path:
    return _derived_path(bib, ".fixbib", ".json")


def cmd_inspect(bib: Path) -> None:
    parsed = parse_bib_file(bib)
    table = Table(title="BibTeX inventory", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Key", style="bold")
    table.add_column("Type")
    table.add_column("Locator")
    table.add_column("Line", justify="right")
    table.add_column("Title", overflow="fold")
    for entry in parsed.entries[:80]:
        inventory = inspect_entry(entry)
        table.add_row(
            entry.key,
            entry.entry_type,
            inventory.locator_label,
            str(entry.start_line or ""),
            entry.fields.get("title", ""),
        )
    console.print(
        Panel.fit(
            f"[bold]{parsed.path}[/bold]\nEntries: {len(parsed.entries)}  Diagnostics: {len(parsed.diagnostics)}",
            title="FixBib inspection",
        )
    )
    console.print(table)
    if len(parsed.entries) > 80:
        console.print(f"[dim]Showing 80 of {len(parsed.entries)} entries.[/dim]")
    if parsed.diagnostics:
        diagnostics = Table(title="Parser diagnostics", box=box.MINIMAL_DOUBLE_HEAD)
        diagnostics.add_column("Kind")
        diagnostics.add_column("Line", justify="right")
        diagnostics.add_column("Message")
        for diagnostic in parsed.diagnostics[:80]:
            diagnostics.add_row(diagnostic.kind, str(diagnostic.start_line or ""), diagnostic.message)
        console.print(diagnostics)


def cmd_fix(args: argparse.Namespace) -> None:
    parsed = parse_bib_file(args.bib)
    if not parsed.entries:
        raise ValueError("the input file contains no BibTeX entries")

    output_path: Path | None
    if args.dry_run:
        output_path = None
    elif args.in_place:
        output_path = args.bib
    else:
        output_path = args.output or _default_output_path(args.bib)
        if output_path.resolve() == args.bib.resolve():
            raise ValueError("refusing to overwrite the input; use --in-place to do that safely")

    report_path = None if args.no_report else (args.report or _default_report_path(args.bib))

    pipeline = ResolverPipeline(
        doi_base_url=args.doi_base_url,
        crossref_api_base=args.crossref_api_base,
        dblp_api_base=args.dblp_api_base,
        springer_base_url=args.springer_base_url,
        acl_base_url=args.acl_base_url,
        arxiv_bibtex_base=args.arxiv_bibtex_base,
        arxiv_api_base=args.arxiv_api_base,
        acm_base_url=args.acm_base_url,
        acm_export_url=args.acm_export_url,
        ieee_base_url=args.ieee_base_url,
        openlibrary_api_base=args.openlibrary_api_base,
        google_books_api_key=args.google_books_api_key,
        google_books_api_base=args.google_books_api_base,
        openalex_api_key=args.openalex_api_key,
        openalex_api_base=args.openalex_api_base,
        semantic_scholar_enabled=args.semantic_scholar,
        semantic_scholar_api_key=args.semantic_scholar_api_key,
        semantic_scholar_api_base=args.semantic_scholar_api_base,
        timeout=args.timeout,
        thorough=args.thorough,
        page_fallback=not args.no_page_fallback,
        doi_landing=not args.no_doi_landing,
        acm_fallback=not args.no_acm_fallback,
        discovered_page_fallback=not args.no_discovered_page_fallback,
        cache_enabled=not args.no_cache,
    )

    results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Resolving bibliography", total=len(parsed.entries))
        for entry in parsed.entries:
            progress.update(task, description=f"Resolving [bold]{entry.key}[/bold]")
            results.append(pipeline.resolve_one(entry, auto=args.auto))
            progress.advance(task)

    fixed = write_fixed_bib(parsed, results)
    backup_path: Path | None = None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if args.in_place:
            backup_path = _unique_backup_path(args.bib, args.backup_suffix)
            shutil.copy2(args.bib, backup_path)
            temporary = args.bib.with_name(f".{args.bib.name}.fixbib.tmp")
            temporary.write_text(fixed, encoding="utf-8")
            os.replace(temporary, args.bib)
        else:
            output_path.write_text(fixed, encoding="utf-8")

    inventories = {id(entry): inspect_entry(entry) for entry in parsed.entries}
    summary = _summary(results, list(inventories.values()))
    report: dict[str, Any] = {
        "tool": "fixbib",
        "version": __version__,
        "input": str(args.bib),
        "output": str(output_path) if output_path else None,
        "backup": str(backup_path) if backup_path else None,
        "auto": args.auto,
        "verification_policy": (
            "local BibTeX metadata is untrusted; every entry is verified against live "
            "remote sources or DOI-keyed cached source snapshots; cached candidates "
            "are re-scored against the current entry; registry-generated BibTeX is "
            "evidence-only; a conflicting input DOI is corrected only after independent "
            "title/year/author verification"
        ),
        "cache": pipeline.cache.summary(),
        "source_hierarchy": [
            "publisher_native_export",
            "repository_native_export",
            "publisher_page_metadata",
            "bibliographic_index_export",
            "registry_metadata",
            "bibliographic_index",
            "registry_transform (evidence-only; never written)",
        ],
        "thorough": args.thorough,
        "pipeline": [
            "doi_landing",
            "exact_identifier",
            "publisher_native_export",
            "entry_url",
            "book_lookup",
            "bibliographic_search",
            "candidate_identifier_enrichment",
            "discovered_publication_page",
            "publisher_fallback",
            "preprint_search",
        ],
        "fallback_policy": {
            "google_scholar": "not_used",
            "doi_landing": not args.no_doi_landing,
            "acm_dl": "native_export_then_page_fallback" if not args.no_acm_fallback else "disabled",
            "ieee_xplore": "native_bibtex_with_abstract",
            "arxiv": "exact ID, or title search only for entries already identified as preprints",
            "books": [
                "openlibrary",
                "google_books" if args.google_books_api_key else None,
                "crossref",
            ],
            "default_search": ["dblp", "crossref"],
            "optional_search": [
                "openalex" if args.openalex_api_key else None,
                "semantic_scholar" if args.semantic_scholar else None,
            ],
        },
        "parser_diagnostics": [asdict(diagnostic) for diagnostic in parsed.diagnostics],
        "results": [result_to_jsonable(result, inventories[id(result.original)]) for result in results],
        "summary": summary,
    }
    for key in ("books", "optional_search"):
        report["fallback_policy"][key] = [item for item in report["fallback_policy"][key] if item]
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _render_final_report(
        results,
        inventories,
        summary,
        table_mode=args.table,
        max_rows=max(1, args.max_table_rows),
        out=output_path,
        report=report_path,
        cache=pipeline.cache.summary(),
    )
    if backup_path is not None:
        console.print(f"[green]Backup:[/green] {backup_path}")


def _unique_backup_path(path: Path, suffix: str) -> Path:
    candidate = path.with_name(path.name + suffix)
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = path.with_name(path.name + suffix + f".{index}")
        if not candidate.exists():
            return candidate
        index += 1


def result_to_jsonable(result: Any, inventory: EntryInventory) -> dict[str, Any]:
    return {
        "key": result.key,
        "entry_type": result.original.entry_type,
        "title": result.original.fields.get("title", ""),
        "inventory": inventory.to_jsonable(),
        "status": _status_name(result, inventory),
        "action": result.action,
        "selected": candidate_to_jsonable(result.selected),
        "verification": _verification_summary(result),
        "applied": result.merge_report,
        "candidates": [candidate_to_jsonable(candidate) for candidate in result.candidates],
        "diagnostics": result.diagnostics,
    }


def candidate_to_jsonable(candidate: Any) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {
        "source": candidate.source,
        "stage": candidate.stage,
        "source_url": candidate.source_url,
        "canonical_id": candidate.canonical_id,
        "source_priority": candidate.source_priority,
        "source_kind": candidate.source_kind,
        "source_kind_label": source_kind_label(candidate.source_kind),
        "source_family": candidate.source_family,
        "source_authority": source_authority(candidate),
        "confidence": candidate.confidence,
        "score": candidate.score,
        "fields": candidate.fields,
        "evidence": candidate.evidence,
        "bibtex": candidate.bibtex,
    }


def _verification_summary(result: Any) -> dict[str, Any]:
    selected = result.selected
    if selected is None:
        return {
            "identity": "unresolved",
            "selected_source_kind": None,
            "publisher_native_export_available": False,
            "publisher_page_checks": {},
        }

    evidence = set(selected.evidence)
    if "doi_exact_match" in evidence:
        identity = "exact_doi"
    elif "isbn_exact_match" in evidence:
        identity = "exact_isbn"
    elif "year_match" in evidence and "first_author_match" in evidence:
        identity = "bibliographic_match"
    else:
        identity = selected.confidence

    checks: dict[str, str] = {}
    for candidate in result.candidates:
        if candidate.source_kind != "probe":
            continue
        family = candidate.source_family or "unknown"
        if "blocked_or_challenge_page" in candidate.evidence:
            checks[family] = "blocked"
            continue
        status = next(
            (item.split("=", 1)[1] for item in candidate.evidence if item.startswith("http_status=")),
            "",
        )
        if status:
            checks.setdefault(family, f"http_{status}")

    return {
        "identity": identity,
        "selected_source": selected.source,
        "selected_source_kind": selected.source_kind,
        "selected_source_family": selected.source_family,
        "selected_source_authority": source_authority(selected),
        "publisher_native_export_available": any(
            candidate.bibtex and candidate.source_kind == "publisher_native_export"
            for candidate in result.candidates
        ),
        "registry_transform_available": any(
            candidate.bibtex and candidate.source_kind == "registry_transform"
            for candidate in result.candidates
        ),
        "registry_transform_selected": selected.source_kind == "registry_transform",
        "publisher_page_checks": checks,
    }


def _summary(results: list[Any], inventories: list[EntryInventory]) -> dict[str, int]:
    return {
        "entries": len(results),
        "updated": sum(1 for result in results if result.action == "replace"),
        "replaced": sum(1 for result in results if result.action == "replace"),
        "verified_no_change": sum(1 for result in results if result.action == "verified_no_change"),
        "fields_added": sum(
            len((result.merge_report or {}).get("fields_added", [])) for result in results
        ),
        "fields_updated": sum(
            len((result.merge_report or {}).get("fields_updated", [])) for result in results
        ),
        "verified_exact": sum(1 for result in results if result.selected and result.selected.confidence == "exact"),
        "matched_high": sum(1 for result in results if result.selected and result.selected.confidence == "high"),
        "matched_low": sum(1 for result in results if result.selected and result.selected.confidence == "low"),
        "unresolved": sum(1 for result in results if result.selected is None),
        "conflicts": sum(1 for result in results if result.action == "report_only_conflict"),
        "ambiguous": sum(1 for result in results if result.action == "report_only_ambiguous"),
        "without_doi_or_url": sum(1 for inventory in inventories if not inventory.has_doi_or_url),
        "without_any_locator": sum(1 for inventory in inventories if not inventory.has_any_locator),
    }


def _render_final_report(
    results: list[Any],
    inventories: dict[int, EntryInventory],
    summary: dict[str, int],
    *,
    table_mode: str,
    max_rows: int,
    out: Path | None,
    report: Path | None,
    cache: dict[str, object] | None = None,
) -> None:
    summary_grid = Table.grid(padding=(0, 2))
    summary_grid.add_column(style="bold")
    summary_grid.add_column(justify="right")
    for label, key in (
        ("Entries", "entries"),
        ("Entries updated", "updated"),
        ("Verified, unchanged", "verified_no_change"),
        ("Fields added", "fields_added"),
        ("Fields corrected", "fields_updated"),
        ("Exact matches", "verified_exact"),
        ("High matches", "matched_high"),
        ("Unresolved", "unresolved"),
        ("Conflicts", "conflicts"),
        ("Ambiguous exports", "ambiguous"),
        ("No DOI or URL", "without_doi_or_url"),
        ("No stable locator", "without_any_locator"),
    ):
        summary_grid.add_row(label, str(summary[key]))
    console.print(Panel(summary_grid, title="FixBib resolution summary", border_style="cyan"))
    if cache is not None:
        cache_grid = Table.grid(padding=(0, 2))
        cache_grid.add_column(style="bold")
        cache_grid.add_column()
        cache_grid.add_row("Enabled", str(bool(cache.get("enabled"))))
        cache_grid.add_row("Directory", str(cache.get("directory", "")))
        cache_grid.add_row("Hits", str(cache.get("hits", 0)))
        cache_grid.add_row("Misses", str(cache.get("misses", 0)))
        cache_grid.add_row("Writes", str(cache.get("writes", 0)))
        console.print(Panel(cache_grid, title="DOI cache", border_style="blue"))

    if table_mode != "none":
        selected_results = results if table_mode == "all" else [
            result
            for result in results
            if _is_problem(result, inventories[id(result.original)])
        ]
        _print_result_table(selected_results[:max_rows], inventories, table_mode)
        if len(selected_results) > max_rows:
            console.print(
                f"[dim]Showing {max_rows} of {len(selected_results)} {table_mode} rows; full details are in {report}.[/dim]"
            )

    no_link = [result for result in results if not inventories[id(result.original)].has_doi_or_url]
    if no_link:
        table = Table(
            title="Entries with neither DOI nor URL",
            box=box.ROUNDED,
            border_style="yellow",
            show_lines=False,
        )
        table.add_column("Key", style="bold yellow")
        table.add_column("Type")
        table.add_column("Other locator")
        table.add_column("Year")
        table.add_column("Title", overflow="fold")
        for result in no_link[:max_rows]:
            inventory = inventories[id(result.original)]
            other = []
            if inventory.isbn:
                other.append(f"ISBN {inventory.isbn}")
            if inventory.arxiv_id:
                other.append(f"arXiv {inventory.arxiv_id}")
            table.add_row(
                result.key,
                result.original.entry_type,
                ", ".join(other) or "none",
                result.original.fields.get("year", ""),
                result.original.fields.get("title", ""),
            )
        console.print(table)
        if len(no_link) > max_rows:
            console.print(f"[dim]Showing {max_rows} of {len(no_link)} entries without DOI/URL.[/dim]")

    if out is not None:
        console.print(f"[green]BibTeX output:[/green] {out}")
    else:
        console.print("[yellow]BibTeX output:[/yellow] not written (--dry-run)")
    if report is not None:
        console.print(f"[green]Audit report:[/green] {report}")


def _print_result_table(results: list[Any], inventories: dict[int, EntryInventory], mode: str) -> None:
    title = "Resolution results" if mode == "all" else "Entries updated or requiring attention"
    compact = console.width < 105
    table = Table(
        title=title,
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Status", width=11, no_wrap=True)
    table.add_column("Key", min_width=12, max_width=22, no_wrap=True, overflow="ellipsis", style="bold")
    if not compact:
        table.add_column("Type", width=14, no_wrap=True, overflow="ellipsis")
    table.add_column("Locator", width=10, no_wrap=True)
    table.add_column("Match / source", min_width=15, max_width=24, overflow="ellipsis")
    table.add_column("Changes", min_width=10, max_width=22, overflow="fold")
    table.add_column("Title", ratio=2, min_width=18, overflow="fold")
    for result in results:
        inventory = inventories[id(result.original)]
        selected = result.selected
        match = (
            f"{selected.confidence} · {_short_source(selected.source)}"
            if selected
            else "—"
        )
        row = [
            _status_text(result, inventory),
            result.key,
        ]
        if not compact:
            row.append(result.original.entry_type)
        row.extend(
            [
                inventory.locator_label,
                match,
                _change_summary(result),
                result.original.fields.get("title", ""),
            ]
        )
        table.add_row(*row)
    console.print(table)


def _change_summary(result: Any) -> str:
    report = result.merge_report or {}
    added = list(report.get("fields_added", []))
    updated = list(report.get("fields_updated", []))
    parts: list[str] = []
    if updated:
        parts.append("update: " + ", ".join(updated))
    if added:
        parts.append("add: " + ", ".join(added))
    if result.action == "verified_no_change":
        return "checked; no differences"
    if result.action == "report_only_registry_transform":
        return "identity checked; transform not applied"
    if result.action.startswith("report_only") and result.selected is not None:
        return "checked; not applied"
    return "; ".join(parts) or "—"


def _short_source(source: str) -> str:
    replacements = {
        "generic-page-metadata-bibtex-link": "page BibTeX",
        "generic-page-metadata-inline-bibtex": "inline BibTeX",
        "generic-page-metadata": "page metadata",
        "doi-content-negotiation": "DOI transform",
        "crossref-transform": "Crossref transform",
        "datacite-transform": "DataCite transform",
        "doi-landing-page": "publisher page",
        "crossref-search": "Crossref",
        "crossref-doi": "Crossref registry",
        "dblp-bibtex": "DBLP BibTeX",
        "openlibrary-isbn": "Open Library ISBN",
        "openlibrary-title-search": "Open Library",
        "google-books-search": "Google Books",
        "acm-native-export": "ACM native export",
        "acm-dl-fallback": "ACM page",
        "ieee-xplore-bibtex": "IEEE native BibTeX",
    }
    return replacements.get(source, source)


def _status_name(result: Any, inventory: EntryInventory) -> str:
    if result.action == "report_only_conflict":
        return "conflict"
    if result.action == "report_only_ambiguous":
        return "ambiguous"
    if result.original.duplicate_key:
        return "duplicate_key"
    if result.selected is None:
        return "unresolved_no_locator" if not inventory.has_doi_or_url else "unresolved"
    if result.action == "replace":
        return "updated"
    if result.action == "verified_no_change":
        return "verified"
    if result.selected.confidence == "exact":
        return "verified"
    if result.selected.confidence == "high":
        return "high_match"
    return "low_match"


def _status_text(result: Any, inventory: EntryInventory) -> Text:
    status = _status_name(result, inventory)
    labels = {
        "conflict": ("CONFLICT", "bold red"),
        "ambiguous": ("AMBIGUOUS", "bold bright_magenta"),
        "duplicate_key": ("DUPLICATE", "bold red"),
        "unresolved_no_locator": ("NO LINK", "bold yellow"),
        "unresolved": ("UNRESOLVED", "yellow"),
        "updated": ("UPDATED", "bold green"),
        "verified": ("VERIFIED", "green"),
        "high_match": ("HIGH", "cyan"),
        "low_match": ("LOW", "magenta"),
    }
    label, style = labels[status]
    return Text(label, style=style)


def _is_problem(result: Any, inventory: EntryInventory) -> bool:
    return bool(
        not inventory.has_doi_or_url
        or result.selected is None
        or result.action in {"replace", "report_only_conflict", "report_only_ambiguous"}
        or result.original.duplicate_key
        or (result.selected and result.selected.confidence == "low")
    )


if __name__ == "__main__":
    main()
