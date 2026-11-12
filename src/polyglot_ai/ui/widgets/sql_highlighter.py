"""SQL syntax colouring for the Database panel's query editor.

:func:`scan_line` is the tokenizer — plain Python, one line at a time,
carrying a state across lines for the two things that span them (block
comments and string literals). :class:`SqlHighlighter` applies its
result to a ``QTextDocument``.

It colours what is the same in every engine the panel talks to; it does
not try to know each dialect's full grammar. The one dialect switch is
``brackets``: ``[name]`` is a quoted identifier in SQL Server and
SQLite, but an array subscript in PostgreSQL.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from polyglot_ai.ui import theme_colors as tc

#: States carried from one line to the next.
NORMAL, IN_COMMENT, IN_STRING = 0, 1, 2

KEYWORDS = frozenset(
    """
    add all alter and any as asc authorization backup begin between break by
    cascade case check close column commit constraint create cross current
    cursor database declare default delete desc distinct drop else end escape
    except exec execute exists explain fetch first following for foreign from
    full function go grant group having if ilike in index inner insert
    intersect into is join key left like limit merge minus natural next no not
    null nulls of offset on only open or order outer over partition preceding
    primary procedure range recursive references rename replace returning
    revoke right rollback row rownum rows savepoint schema select set show
    some start table temp temporary then ties to top transaction trigger
    truncate unbounded union unique update use using values view when where
    while window with within
    """.split()
)

#: Data types and the functions found in every engine: coloured apart
#: from keywords so ``count(*)`` and ``varchar(20)`` stand out.
BUILTINS = frozenset(
    """
    abs avg bigint binary bit blob bool boolean cast ceil ceiling char
    character clob coalesce concat convert count cume_dist current_date
    current_time current_timestamp current_user date dateadd datediff datetime
    datetime2 decimal dense_rank double extract false first_value float floor
    getdate greatest ifnull int integer interval isnull json jsonb lag
    last_value lead least len length lower ltrim max min mod money nchar now
    ntile nullif number numeric nvarchar nvarchar2 nvl percent_rank power rank
    real round row_number rtrim serial smallint substr substring sum
    sysdate text time timestamp timestamptz tinyint to_char to_date trim true
    upper uuid varbinary varchar varchar2 xml year month day
    """.split()
)


def _word_end(text: str, start: int) -> int:
    end = start + 1
    while end < len(text) and (text[end].isalnum() or text[end] in "_$#"):
        end += 1
    return end


def _number_end(text: str, start: int) -> int:
    end = start
    while end < len(text) and text[end].isdigit():
        end += 1
    if end + 1 < len(text) and text[end] == "." and text[end + 1].isdigit():
        end += 1
        while end < len(text) and text[end].isdigit():
            end += 1
    return end


def _string_end(text: str, start: int) -> tuple[int, bool]:
    """End of a ``'…'`` literal whose content starts at ``start``.

    Returns ``(index after it, closed?)``. A doubled quote is an escaped
    quote, not the end.
    """
    i = start
    while i < len(text):
        if text[i] == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            return i + 1, True
        i += 1
    return len(text), False


def scan_line(
    text: str, state: int = NORMAL, *, brackets: bool = False
) -> tuple[list[tuple[int, int, str]], int]:
    """Colour one line: ``([(start, length, kind)], state for the next line)``.

    Kinds: ``keyword``, ``builtin``, ``string``, ``identifier`` (a quoted
    name), ``number``, ``comment``.
    """
    spans: list[tuple[int, int, str]] = []
    i = 0
    if state == IN_COMMENT:
        end = text.find("*/")
        if end < 0:
            return ([(0, len(text), "comment")] if text else []), IN_COMMENT
        i = end + 2
        spans.append((0, i, "comment"))
    elif state == IN_STRING:
        i, closed = _string_end(text, 0)
        if i:
            spans.append((0, i, "string"))
        if not closed:
            return spans, IN_STRING

    while i < len(text):
        ch = text[i]
        if ch == "-" and text.startswith("--", i):
            spans.append((i, len(text) - i, "comment"))
            break
        if ch == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                spans.append((i, len(text) - i, "comment"))
                return spans, IN_COMMENT
            spans.append((i, end + 2 - i, "comment"))
            i = end + 2
        elif ch == "'":
            end, closed = _string_end(text, i + 1)
            spans.append((i, end - i, "string"))
            if not closed:
                return spans, IN_STRING
            i = end
        elif ch in '"`' or (ch == "[" and brackets):
            closing = "]" if ch == "[" else ch
            end = text.find(closing, i + 1)
            if end < 0:
                i += 1  # never closed on this line: leave it plain
                continue
            spans.append((i, end + 1 - i, "identifier"))
            i = end + 1
        elif ch.isdigit():
            end = _number_end(text, i)
            spans.append((i, end - i, "number"))
            i = end
        elif ch.isalpha() or ch == "_":
            end = _word_end(text, i)
            word = text[i:end].lower()
            if word in KEYWORDS:
                spans.append((i, end - i, "keyword"))
            elif word in BUILTINS:
                spans.append((i, end - i, "builtin"))
            i = end
        elif ch in "@:#$" and i + 1 < len(text) and (text[i + 1].isalnum() or text[i + 1] == "_"):
            # @variable, :bind, #temp_table — a name, whatever it spells.
            i = _word_end(text, i + 1)
        else:
            i += 1
    return spans, NORMAL


_COLOURS = {
    "keyword": "syn_keyword",
    "builtin": "syn_builtin",
    "string": "syn_string",
    "identifier": "syn_identifier",
    "number": "syn_number",
    "comment": "syn_comment",
}


class SqlHighlighter(QSyntaxHighlighter):
    """Colours SQL in a document as it is typed."""

    def __init__(self, document: QTextDocument, *, brackets: bool = False) -> None:
        super().__init__(document)
        self._brackets = brackets
        self._formats: dict[str, QTextCharFormat] = {}
        for kind, colour in _COLOURS.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(tc.get(colour)))
            if kind == "keyword":
                fmt.setFontWeight(QFont.Weight.DemiBold)
            elif kind == "comment":
                fmt.setFontItalic(True)
            self._formats[kind] = fmt

    def set_brackets(self, brackets: bool) -> None:
        """Whether ``[name]`` is a quoted identifier (SQL Server, SQLite)."""
        if brackets != self._brackets:
            self._brackets = brackets
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 — Qt override
        state = max(self.previousBlockState(), NORMAL)
        spans, state = scan_line(text, state, brackets=self._brackets)
        self.setCurrentBlockState(state)
        if not spans:
            return
        # Qt counts in UTF-16 units; Python in code points. They differ
        # only past a character outside the Basic Multilingual Plane.
        wide = [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]
        for start, length, kind in spans:
            if wide:
                before = sum(1 for i in wide if i < start)
                inside = sum(1 for i in wide if start <= i < start + length)
                start, length = start + before, length + inside
            self.setFormat(start, length, self._formats[kind])
