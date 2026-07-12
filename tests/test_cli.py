from pathlib import Path

from fixbib.cli import _default_output_path, _default_report_path, _unique_backup_path, build_parser


def test_default_output_and_report_paths():
    source = Path('/tmp/references.bib')
    assert _default_output_path(source) == Path('/tmp/references.fixed.bib')
    assert _default_report_path(source) == Path('/tmp/references.fixbib.json')


def test_cli_primary_form_is_fixbib_file_without_subcommand():
    args = build_parser().parse_args(['references.bib'])
    assert args.bib == Path('references.bib')
    assert args.auto == 'verified'
    assert args.output is None
    assert args.in_place is False


def test_cli_has_no_mailto_option():
    help_text = build_parser().format_help()
    assert '--mailto' not in help_text


def test_backup_path_does_not_overwrite_existing_backups(tmp_path):
    bib = tmp_path / 'refs.bib'
    bib.write_text('@misc{x, title={x}}\n', encoding='utf-8')
    (tmp_path / 'refs.bib.bak').write_text('old', encoding='utf-8')
    assert _unique_backup_path(bib, '.bak') == tmp_path / 'refs.bib.bak.1'


def test_cli_default_auto_policy_is_verified():
    args = build_parser().parse_args(['references.bib'])
    assert args.auto == 'verified'


def test_cli_cache_is_enabled_by_default_and_no_cache_can_disable_it():
    default_args = build_parser().parse_args(["references.bib"])
    assert default_args.no_cache is False
    bypass_args = build_parser().parse_args(["references.bib", "--no-cache"])
    assert bypass_args.no_cache is True
