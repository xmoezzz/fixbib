from fixbib.bib_build import build_bibtex_preserving_fields


def test_month_names_render_as_bibtex_macros():
    text = build_bibtex_preserving_fields(
        "article",
        "paper",
        {"title": "Paper", "month": "July", "year": "2025"},
        ["title", "year", "month"],
    )
    assert "month = jul," in text
    assert "month = {July}" not in text


def test_generated_values_decode_html_and_escape_tex_specials():
    text = build_bibtex_preserving_fields(
        "article",
        "paper",
        {"title": "Research &amp; Development", "abstract": "Accuracy is 50%"},
        ["title", "abstract"],
    )
    assert "Research \\& Development" in text
    assert "50\\%" in text
    assert "&amp;" not in text
