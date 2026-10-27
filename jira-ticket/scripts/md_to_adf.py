#!/usr/bin/env python3
r"""Convert Markdown to Atlassian Document Format (ADF).

Jira Cloud stores rich text as ADF, a JSON tree. `acli` takes a description or
a comment as plain text or as ADF, but not as Markdown, so a Markdown draft
sent as-is shows its `**` and `|` literally. This converts the subset of
Markdown that ticket drafts use:

- ATX (`#`) and setext (`===`, `---` underline) headings, paragraphs, and
  hard breaks (two trailing spaces or a backslash)
- bullet and ordered lists, nested by indentation
- fenced code blocks with an optional language
- block quotes and thematic breaks (`---`)
- GFM pipe tables
- inline: `code`, **strong**, *em*, ~~strike~~, [links](url), bare http(s)
  URLs, backslash escapes, and HTML entities (`&amp;`, `&#8594;`)
- <url> autolinks, as smart links. Jira shows a ticket's key, summary, and
  status. A <url> that is a whole top-level paragraph becomes a Card link
  (`blockCard`), which also shows the assignee, priority, and the start of the
  description. Elsewhere, such as in a list item or a table cell, it is an
  Inline link (`inlineCard`). ADF allows a Card only at the top level.
- reference links: [text][label], [label][], and [label], with `[label]: url`
  definitions anywhere outside code

The output keeps to what the Jira editor can represent, not only to what the
ADF JSON schema accepts. The editor's `code` mark excludes every other mark
this converter emits: code inside bold loses the bold. Link text is prose, so
inside it a code span or a <url> card becomes plain link text.

Other syntax passes through as literal text. ADF forbids some nestings that
Markdown allows, such as a table inside a list item. Those raise
ConversionError, because Jira would reject the document anyway and the error
here names the cause.

Usage:
    python3 md_to_adf.py draft.md > draft.adf.json
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path


class ConversionError(ValueError):
    """Raised for Markdown that has no valid ADF equivalent."""


# Block grammar. Every pattern is used with `.match()`, and each keeps its `^`
# so it states its own anchoring.

_BLANK = re.compile(r"^[ \t]*$")
_FENCE = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})[ \t]*(?P<lang>[^\s`]*)[^`]*$"
)
# The lazy text gives way to an optional closing run of hashes ("## Title ##").
_HEADING = re.compile(
    r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<text>.*?))?(?:[ \t]+#+)?[ \t]*$"
)
_RULE = re.compile(r"^ {0,3}(?P<char>[-*_])(?:[ \t]*(?P=char)){2,}[ \t]*$")
_QUOTE = re.compile(r"^ {0,3}> ?(?P<rest>.*)$")
_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|(?P<num>\d{1,9})[.)])"
    r"(?P<gap>[ \t]+|$)(?P<text>.*)$"
)
# A delimiter row has at least one pipe, so a bare `---` stays a thematic break.
_TABLE_SEP = re.compile(
    r"^(?=.*\|)[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$"
)
# A cell delimiter is a pipe that is not escaped.
_CELL_DELIM = re.compile(r"(?<!\\)\|")
_EDGE_PIPES = re.compile(r"^\||(?<!\\)\|$")
_HARD_BREAK = re.compile(r"(?: {2,}|\\)$")
# A setext underline needs two hyphens, so a lone "-" stays an empty list item.
_SETEXT = re.compile(r"^ {0,3}(?P<char>=+|-{2,})[ \t]*$")
_DEFINITION = re.compile(
    r"^ {0,3}\[(?P<label>[^\]]+)\]:[ \t]*<?(?P<url>[^\s<>]+)>?"
    r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*$"
)

# Inline grammar. Code spans and backslash escapes are lifted out first and
# replaced by placeholders, so that a `*` inside `**kwargs` or after a
# backslash can never open or close emphasis. The placeholders use private-use
# code points, which a draft has no reason to contain.

_CODE_SPAN = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)(?P<text>.+?)(?<!`)(?P=ticks)(?!`)", re.DOTALL
)
_ESCAPE = re.compile(r"\\(?P<char>[!-/:-@\[-`{-~])")
_ENTITY = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
_TOKEN = re.compile(r"\uE000(?P<index>\d+)\uE001")
# A bare URL may not swallow a placeholder, so a code span or an escape right
# after it stays out of the href.
_INLINE = re.compile(
    r"(?P<autolink><(?P<auto_href>https?://[^\s<>]+)>)"
    r"|(?P<bare>(?<![\w/])https?://[^\s<>\uE000\uE001]+)"
    r"|(?P<link>\[(?P<link_text>[^\]]+)\]\((?P<href>[^)\s]+)(?:[ \t]+\"[^\"]*\")?\))"
    r"|(?P<ref>\[(?P<ref_text>[^\]]+)\](?:\[(?P<ref_label>[^\]]*)\])?)"
    r"|(?P<strong>\*\*(?=\S)(?P<strong_a>.+?)(?<=\S)\*\*|(?<!\w)__(?=\S)(?P<strong_u>.+?)(?<=\S)__(?!\w))"
    r"|(?P<strike>~~(?=\S)(?P<strike_text>.+?)(?<=\S)~~)"
    r"|(?P<em>\*(?=\S)(?P<em_a>.+?)(?<=\S)\*|(?<!\w)_(?=\S)(?P<em_u>.+?)(?<=\S)_(?!\w))",
    re.DOTALL,
)

# The block types ADF accepts inside each container.
_LIST_ITEM_CHILDREN = {"paragraph", "bulletList", "orderedList", "codeBlock"}
_QUOTE_CHILDREN = {"paragraph", "bulletList", "orderedList", "codeBlock"}

# Everything `convert` can emit. A Jira description with other types (a Card or
# an Embed link, a panel, a mention, colored text) cannot round-trip through
# Markdown, so a caller can warn before it replaces one.
NODE_TYPES = frozenset(
    {
        "doc", "paragraph", "text", "hardBreak", "heading", "rule", "blockquote",
        "bulletList", "orderedList", "listItem", "codeBlock",
        "table", "tableRow", "tableHeader", "tableCell", "inlineCard", "blockCard",
    }
)  # fmt: skip
MARK_TYPES = frozenset({"strong", "em", "strike", "code", "link"})


def convert(markdown: str) -> dict:
    """Return the ADF document for ``markdown``."""
    lines, refs = _take_definitions(markdown.splitlines())
    content = [_card_or_block(block) for block in _blocks(lines, refs)]
    return {"type": "doc", "version": 1, "content": content}


def _card_or_block(block: dict) -> dict:
    """A top-level paragraph that is only a <url> becomes a Card link."""
    content = block.get("content", [])
    if (
        block["type"] == "paragraph"
        and len(content) == 1
        and content[0]["type"] == "inlineCard"
    ):
        return {"type": "blockCard", "attrs": content[0]["attrs"]}
    return block


def _take_definitions(lines: list[str]) -> tuple[list[str], dict[str, str]]:
    """Remove link reference definitions; return the other lines and the map.

    A definition cannot interrupt a paragraph, and a line inside a fenced code
    block is code. The first definition of a label wins, as in CommonMark.
    """
    kept: list[str] = []
    refs: dict[str, str] = {}
    close: re.Pattern[str] | None = None
    for line in lines:
        if close:
            if close.match(line):
                close = None
        elif m := _FENCE.match(line):
            close = _closing_fence(m)
        elif (m := _DEFINITION.match(line)) and (not kept or _BLANK.match(kept[-1])):
            refs.setdefault(_label(m["label"]), m["url"])
            continue
        kept.append(line)
    return kept, refs


def _label(text: str) -> str:
    """Normalize a reference label: case and runs of whitespace do not count."""
    return " ".join(text.split()).casefold()


def _closing_fence(m: re.Match[str]) -> re.Pattern[str]:
    fence = m["fence"]
    return re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")


# --- blocks ----------------------------------------------------------------


def _indent(line: str) -> int:
    """The column width of the leading whitespace, with tabs stopping at 4."""
    lead = line[: len(line) - len(line.lstrip(" \t"))]
    return len(lead.expandtabs(4))


def _dedent(line: str, columns: int) -> str:
    """Remove up to ``columns`` columns of leading whitespace."""
    if columns == 0:
        return line
    stripped = line.lstrip(" \t")
    lead = line[: len(line) - len(stripped)].expandtabs(4)
    return lead[min(columns, len(lead)) :] + stripped


def _starts_block(line: str) -> bool:
    """Whether ``line`` opens a block that interrupts a paragraph."""
    return any(p.match(line) for p in (_FENCE, _HEADING, _RULE, _QUOTE, _ITEM))


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i]
        and i + 1 < len(lines)
        and _TABLE_SEP.match(lines[i + 1]) is not None
    )


def _blocks(lines: list[str], refs: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _BLANK.match(line):
            i += 1
            continue
        if m := _FENCE.match(line):
            node, i = _code_block(lines, i, m)
        elif m := _HEADING.match(line):
            node = {
                "type": "heading",
                "attrs": {"level": len(m["hashes"])},
                "content": _inline(m["text"] or "", refs),
            }
            i += 1
        elif _RULE.match(line):
            # Checked before lists, so `* * *` is a break and not a nested bullet.
            node = {"type": "rule"}
            i += 1
        elif _is_table_start(lines, i):
            node, i = _table(lines, i, refs)
        elif _QUOTE.match(line):
            node, i = _blockquote(lines, i, refs)
        elif _ITEM.match(line):
            node, i = _list(lines, i, refs)
        else:
            node, i = _paragraph(lines, i, refs)
        out.append(node)
    return out


def _code_block(lines: list[str], i: int, m: re.Match[str]) -> tuple[dict, int]:
    close = _closing_fence(m)
    body: list[str] = []
    i += 1
    while i < len(lines) and not close.match(lines[i]):
        body.append(_dedent(lines[i], len(m["indent"])))
        i += 1
    # Skip the closing fence. An unclosed fence runs to the end, as in CommonMark.
    i += 1
    # Wrap long lines, as the editor does for a code block pasted from outside.
    attrs = {"language": m["lang"], "wrap": True} if m["lang"] else {"wrap": True}
    node: dict = {"type": "codeBlock", "attrs": attrs, "content": []}
    if text := "\n".join(body):
        node["content"] = [{"type": "text", "text": text}]
    return node, i


def _paragraph(lines: list[str], i: int, refs: dict[str, str]) -> tuple[dict, int]:
    buf = [lines[i]]
    i += 1
    while i < len(lines) and not _BLANK.match(lines[i]):
        # Checked before blocks: under a paragraph, `---` underlines a heading.
        if m := _SETEXT.match(lines[i]):
            level = 1 if m["char"][0] == "=" else 2
            content = _inline_lines(buf, refs)
            return {
                "type": "heading",
                "attrs": {"level": level},
                "content": content,
            }, i + 1
        if _starts_block(lines[i]) or _is_table_start(lines, i):
            break
        buf.append(lines[i])
        i += 1
    return {"type": "paragraph", "content": _inline_lines(buf, refs)}, i


def _inline_lines(buf: list[str], refs: dict[str, str]) -> list[dict]:
    """Join paragraph lines, keeping hard breaks as newlines for `_inline`."""
    parts: list[str] = []
    for k, line in enumerate(buf):
        last = k == len(buf) - 1
        if not last and _HARD_BREAK.search(line):
            parts.append(line.rstrip(" ").removesuffix("\\").strip() + "\n")
        else:
            parts.append(line.strip() + ("" if last else " "))
    return _inline("".join(parts), refs)


def _table(lines: list[str], i: int, refs: dict[str, str]) -> tuple[dict, int]:
    header = _cells(lines[i])
    rows = [_row(header, "tableHeader", len(header), refs)]
    i += 2  # header and delimiter row
    while i < len(lines) and not _BLANK.match(lines[i]) and "|" in lines[i]:
        rows.append(_row(_cells(lines[i]), "tableCell", len(header), refs))
        i += 1
    return {"type": "table", "content": rows}, i


def _cells(line: str) -> list[str]:
    # GFM unescapes `\|` before inline parsing, even inside code spans.
    return [
        c.strip().replace("\\|", "|")
        for c in _CELL_DELIM.split(_EDGE_PIPES.sub("", line.strip()))
    ]


def _row(cells: list[str], cell_type: str, width: int, refs: dict[str, str]) -> dict:
    # GFM pads a short row and drops cells past the header's width.
    cells = (cells + [""] * width)[:width]
    return {
        "type": "tableRow",
        "content": [
            {
                "type": cell_type,
                "content": [{"type": "paragraph", "content": _inline(c, refs)}],
            }
            for c in cells
        ],
    }


def _blockquote(lines: list[str], i: int, refs: dict[str, str]) -> tuple[dict, int]:
    inner: list[str] = []
    while i < len(lines) and (m := _QUOTE.match(lines[i])):
        inner.append(m["rest"])
        i += 1
    content = _blocks(inner, refs)
    _require(content, _QUOTE_CHILDREN, "a block quote")
    return {"type": "blockquote", "content": content}, i


def _list(lines: list[str], i: int, refs: dict[str, str]) -> tuple[dict, int]:
    m = _ITEM.match(lines[i])
    assert m is not None  # the caller matched this line
    ordered = m["num"] is not None
    items: list[dict] = []
    first_number = int(m["num"]) if ordered else 0

    while True:
        gap = len(m["gap"].expandtabs(4))
        # A gap of 5+ columns would start indented code in CommonMark; treat
        # it as one space plus indented text instead.
        offset = _indent(lines[i]) + len(m["marker"]) + (gap if 1 <= gap <= 4 else 1)
        body = [m["text"]]
        i += 1
        while i < len(lines):
            line = lines[i]
            if _BLANK.match(line):
                nxt = _next_nonblank(lines, i)
                if nxt is None or _indent(lines[nxt]) < offset:
                    break
                body.append("")
            elif _indent(line) >= offset:
                body.append(_dedent(line, offset))
            elif body[-1].strip() and not _starts_block(line):
                body.append(line.strip())  # lazy paragraph continuation
            else:
                break
            i += 1
        items.append(_list_item(_blocks(body, refs)))

        nxt = _next_nonblank(lines, i)
        sibling = _ITEM.match(lines[nxt]) if nxt is not None else None
        if (
            sibling is None
            or (sibling["num"] is not None) != ordered
            or _RULE.match(lines[nxt])
        ):
            break
        i, m = nxt, sibling

    if ordered:
        return {
            "type": "orderedList",
            "attrs": {"order": first_number},
            "content": items,
        }, i
    return {"type": "bulletList", "content": items}, i


def _list_item(content: list[dict]) -> dict:
    if not content:
        # ADF needs at least one child; a nested list may come first.
        content = [{"type": "paragraph", "content": []}]
    _require(content, _LIST_ITEM_CHILDREN, "a list item")
    return {"type": "listItem", "content": content}


def _next_nonblank(lines: list[str], i: int) -> int | None:
    while i < len(lines):
        if not _BLANK.match(lines[i]):
            return i
        i += 1
    return None


def _require(content: list[dict], allowed: set[str], where: str) -> None:
    for node in content:
        if node["type"] not in allowed:
            raise ConversionError(f"ADF does not allow a {node['type']} inside {where}")


# --- inline ----------------------------------------------------------------


def _inline(text: str, refs: dict[str, str]) -> list[dict]:
    """Convert inline Markdown. A newline in ``text`` is a hard break."""
    tokens: list[tuple[str, str]] = []

    def lift(kind: str, value: str) -> str:
        tokens.append((kind, value))
        return f"\ue000{len(tokens) - 1}\ue001"

    def code(m: re.Match[str]) -> str:
        body = m["text"].replace("\n", " ")
        # CommonMark strips one space from each side of `` ` x ` ``.
        if (
            len(body) > 2
            and body.startswith(" ")
            and body.endswith(" ")
            and body.strip()
        ):
            body = body[1:-1]
        return lift("code", body)

    text = _CODE_SPAN.sub(code, text)
    text = _ESCAPE.sub(lambda m: lift("text", m["char"]), text)
    # Lifted, so that a decoded `&#42;` is a literal asterisk, not emphasis.
    text = _ENTITY.sub(lambda m: lift("text", html.unescape(m.group())), text)
    return _merge(_runs(text, (), tokens, refs))


def _runs(
    text: str,
    marks: tuple[dict, ...],
    tokens: list[tuple[str, str]],
    refs: dict[str, str],
) -> list[dict]:
    out: list[dict] = []
    pos = 0
    while m := _INLINE.search(text, pos):
        out += _text_nodes(text[pos : m.start()], marks, tokens)
        if m["bare"]:
            url = _trim_url(m["bare"])
            href = _restore(url, tokens)
            out += _text_nodes(
                url, _add(marks, {"type": "link", "attrs": {"href": href}}), tokens
            )
            # Trailing punctuation goes back to the text.
            pos = m.start() + len(url)
            continue
        if m["autolink"]:
            if any(mk["type"] == "link" for mk in marks):
                # Link text is prose: the card becomes its URL as link text.
                out += _text_nodes(m["auto_href"], marks, tokens)
            else:
                # A smart link: Jira shows a ticket's key, summary, and status.
                # It is a node, so it carries no marks.
                out.append({"type": "inlineCard", "attrs": {"url": m["auto_href"]}})
        elif m["link"]:
            href = _restore(m["href"], tokens)
            link = {"type": "link", "attrs": {"href": href}}
            out += _runs(m["link_text"], _add(marks, link), tokens, refs)
        elif m["ref"]:
            # [text][label], [label][], or [label]. An empty label means the
            # text is the label.
            label = _label(_restore(m["ref_label"] or m["ref_text"], tokens))
            if label not in refs:
                # Not a reference: keep the bracket and read on after it.
                out += _text_nodes("[", marks, tokens)
                pos = m.start() + 1
                continue
            link = {"type": "link", "attrs": {"href": _restore(refs[label], tokens)}}
            out += _runs(m["ref_text"], _add(marks, link), tokens, refs)
        elif m["strong"]:
            strong = m["strong_a"] or m["strong_u"]
            out += _runs(strong, _add(marks, {"type": "strong"}), tokens, refs)
        elif m["strike"]:
            strike = m["strike_text"]
            out += _runs(strike, _add(marks, {"type": "strike"}), tokens, refs)
        else:
            em = m["em_a"] or m["em_u"]
            out += _runs(em, _add(marks, {"type": "em"}), tokens, refs)
        pos = m.end()
    out += _text_nodes(text[pos:], marks, tokens)
    return out


def _trim_url(url: str) -> str:
    """Drop trailing punctuation from a bare URL, as GFM autolinks do.

    A closing parenthesis stays only while it balances one inside the URL, so
    "(see https://e.com/a_(b))." keeps "https://e.com/a_(b)".
    """
    while url[-1] in "?!.,:;*_~'\"" or (
        url[-1] == ")" and url.count(")") > url.count("(")
    ):
        url = url[:-1]
    return url


def _add(marks: tuple[dict, ...], mark: dict) -> tuple[dict, ...]:
    if any(m["type"] == mark["type"] for m in marks):
        return marks
    return (*marks, mark)


def _restore(text: str, tokens: list[tuple[str, str]]) -> str:
    return _TOKEN.sub(lambda m: tokens[int(m["index"])][1], text)


def _text_nodes(
    text: str, marks: tuple[dict, ...], tokens: list[tuple[str, str]]
) -> list[dict]:
    out: list[dict] = []
    for piece in re.split(r"(\uE000\d+\uE001|\n)", text):
        if not piece:
            continue
        if piece == "\n":
            out.append({"type": "hardBreak"})
        elif m := _TOKEN.fullmatch(piece):
            kind, value = tokens[int(m["index"])]
            if kind != "code" or any(mk["type"] == "link" for mk in marks):
                # Escaped text, or a code span in link text. The editor's code
                # mark excludes link, and link text is prose: strip the code.
                out.append(_text(value, marks))
            else:
                # The editor's code mark excludes strong, em, and strike.
                out.append(_text(value, ({"type": "code"},)))
        else:
            out.append(_text(piece, marks))
    return out


def _text(value: str, marks: tuple[dict, ...]) -> dict:
    node: dict = {"type": "text", "text": value}
    if marks:
        node["marks"] = list(marks)
    return node


def _merge(nodes: list[dict]) -> list[dict]:
    """Join adjacent text nodes that carry the same marks."""
    out: list[dict] = []
    for node in nodes:
        prev = out[-1] if out else None
        if (
            prev
            and prev["type"] == node["type"] == "text"
            and prev.get("marks") == node.get("marks")
        ):
            prev["text"] += node["text"]
        else:
            out.append(node)
    return out


def main(argv: list[str]) -> int:
    source = (
        Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 else sys.stdin.read()
    )
    try:
        doc = convert(source)
    except ConversionError as exc:
        print(f"md_to_adf: {exc}", file=sys.stderr)
        return 1
    json.dump(doc, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
