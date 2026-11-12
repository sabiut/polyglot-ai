"""SQL colouring in the Database panel's query editor."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtGui import QTextDocument  # noqa: E402
from PyQt6.QtWidgets import QPlainTextEdit  # noqa: E402

from polyglot_ai.ui.widgets.sql_highlighter import (  # noqa: E402
    IN_COMMENT,
    IN_STRING,
    NORMAL,
    SqlHighlighter,
    scan_line,
)


def _kinds(text: str, state: int = NORMAL, **kwargs) -> list[tuple[str, str]]:
    spans, _state = scan_line(text, state, **kwargs)
    return [(text[start : start + length], kind) for start, length, kind in spans]


def test_keywords_functions_strings_and_numbers():
    assert _kinds("SELECT count(*) FROM cases WHERE status = 'Open' AND id > 10.5") == [
        ("SELECT", "keyword"),
        ("count", "builtin"),
        ("FROM", "keyword"),
        ("WHERE", "keyword"),
        ("'Open'", "string"),
        ("AND", "keyword"),
        ("10.5", "number"),
    ]


def test_keywords_are_matched_whole_and_in_any_case():
    assert _kinds("select selection, from_date, t1.orders2 from t1") == [
        ("select", "keyword"),
        ("from", "keyword"),
    ]


def test_comment_runs_to_the_end_of_the_line_and_hides_what_is_in_it():
    assert _kinds("SELECT 1 -- not 'a string', not SELECT") == [
        ("SELECT", "keyword"),
        ("1", "number"),
        ("-- not 'a string', not SELECT", "comment"),
    ]
    # ...but a "--" inside a string is part of the string.
    assert _kinds("SELECT '--x' FROM t")[1] == ("'--x'", "string")


def test_block_comment_spans_lines():
    spans, state = scan_line("SELECT 1 /* start")
    assert state == IN_COMMENT and spans[-1][2] == "comment"
    assert scan_line("still in it", state) == ([(0, 11, "comment")], IN_COMMENT)
    assert _kinds("done */ FROM t", IN_COMMENT) == [("done */", "comment"), ("FROM", "keyword")]
    assert scan_line("done */ FROM t", IN_COMMENT)[1] == NORMAL


def test_string_with_doubled_quote_and_one_that_spans_lines():
    assert _kinds("WHERE name = 'O''Brien' OR 1") == [
        ("WHERE", "keyword"),
        ("'O''Brien'", "string"),
        ("OR", "keyword"),
        ("1", "number"),
    ]
    spans, state = scan_line("VALUES ('first line")
    assert state == IN_STRING
    assert _kinds("second line') -- done", state) == [
        ("second line'", "string"),
        ("-- done", "comment"),
    ]


def test_quoted_names_are_not_keywords():
    assert _kinds('SELECT "order", `select` FROM t') == [
        ("SELECT", "keyword"),
        ('"order"', "identifier"),
        ("`select`", "identifier"),
        ("FROM", "keyword"),
    ]


def test_square_brackets_are_names_only_where_the_engine_says_so():
    sql = "SELECT TOP 100 * FROM [order]"
    assert ("[order]", "identifier") in _kinds(sql, brackets=True)
    # PostgreSQL: tags[1] is an array subscript, and "order" inside is a keyword.
    assert ("[order]", "identifier") not in _kinds(sql)
    assert _kinds("SELECT tags[1] FROM t") == [
        ("SELECT", "keyword"),
        ("1", "number"),
        ("FROM", "keyword"),
    ]


def test_variables_and_bind_names_are_left_alone():
    assert _kinds("WHERE id = @select AND x = :from AND y = #temp") == [
        ("WHERE", "keyword"),
        ("AND", "keyword"),
        ("AND", "keyword"),
    ]


def _colours(document: QTextDocument) -> list[tuple[str, str]]:
    """(text, colour) of every coloured run in the document."""
    found = []
    block = document.begin()
    while block.isValid():
        for fmt_range in block.layout().formats():
            text = block.text()[fmt_range.start : fmt_range.start + fmt_range.length]
            found.append((text, fmt_range.format.foreground().color().name()))
        block = block.next()
    return found


def test_highlighter_colours_a_document(qtbot):
    from polyglot_ai.ui import theme_colors as tc

    editor = QPlainTextEdit()
    qtbot.addWidget(editor)
    document = editor.document()
    highlighter = SqlHighlighter(document, brackets=True)
    editor.setPlainText("SELECT * FROM [cases] /* open\nonly */ WHERE status = 'Open'")

    colours = dict(_colours(document))
    assert colours["SELECT"] == tc.get("syn_keyword")
    assert colours["[cases]"] == tc.get("syn_identifier")
    assert colours["/* open"] == colours["only */"] == tc.get("syn_comment")
    assert colours["'Open'"] == tc.get("syn_string")

    highlighter.set_brackets(False)
    assert "[cases]" not in dict(_colours(document))


def test_offsets_survive_a_character_outside_the_basic_plane(qtbot):
    """Qt counts UTF-16 units: the emoji is two of them, one Python character."""
    from polyglot_ai.ui import theme_colors as tc

    editor = QPlainTextEdit()
    qtbot.addWidget(editor)
    highlighter = SqlHighlighter(editor.document())
    editor.setPlainText("SELECT '😀' FROM t")
    assert highlighter.document() is editor.document()
    block = editor.document().begin()
    utf16 = block.text().encode("utf-16-le")
    runs = {
        utf16[2 * r.start : 2 * (r.start + r.length)].decode("utf-16-le"): r.format.foreground()
        .color()
        .name()
        for r in block.layout().formats()
    }
    assert runs == {
        "SELECT": tc.get("syn_keyword"),
        "'😀'": tc.get("syn_string"),
        "FROM": tc.get("syn_keyword"),
    }
