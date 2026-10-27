"""Tests for jira-ticket/scripts/md_to_adf.py.

The contract: every construct a ticket draft uses becomes the ADF node Jira
renders for it, and nothing that is not Markdown syntax (snake_case names,
`**kwargs` in code) is mistaken for formatting.
"""

import md_to_adf as m2a
import pytest


def blocks(md):
    return m2a.convert(md)["content"]


def texts(node):
    """The (text, mark types) pairs under ``node``, in order."""
    if node["type"] == "text":
        return [(node["text"], [m["type"] for m in node.get("marks", [])])]
    if node["type"] == "hardBreak":
        return [("\n", [])]
    return [pair for child in node.get("content", []) for pair in texts(child)]


def test_document_shape():
    assert m2a.convert("") == {"type": "doc", "version": 1, "content": []}


def test_heading_levels_and_closing_hashes():
    h2, h3 = blocks("## Problem\n### Plan ###")
    assert h2["attrs"] == {"level": 2} and texts(h2) == [("Problem", [])]
    assert h3["attrs"] == {"level": 3} and texts(h3) == [("Plan", [])]


def test_hash_without_space_is_text():
    (para,) = blocks("#hashtag")
    assert para["type"] == "paragraph"


def test_wrapped_lines_join_with_a_space():
    (para,) = blocks("one\ntwo")
    assert texts(para) == [("one two", [])]


def test_hard_breaks():
    (para,) = blocks("one  \ntwo\\\nthree")
    assert texts(para) == [
        ("one", []),
        ("\n", []),
        ("two", []),
        ("\n", []),
        ("three", []),
    ]


def test_emphasis_marks():
    (para,) = blocks("**b** *e* _u_ ~~s~~")
    assert texts(para) == [
        ("b", ["strong"]),
        (" ", []),
        ("e", ["em"]),
        (" ", []),
        ("u", ["em"]),
        (" ", []),
        ("s", ["strike"]),
    ]


def test_snake_case_is_not_emphasis():
    # Intraword underscores never open emphasis. (`__init__` alone does, as in
    # CommonMark, so drafts put dunder names in code spans.)
    (para,) = blocks("snake_case_name and a__b__c stay literal")
    assert texts(para) == [("snake_case_name and a__b__c stay literal", [])]


def test_code_span_hides_asterisks_from_emphasis():
    # Without lifting code spans first, `*args and `**` would pair up as em.
    (para,) = blocks("use *args and `**kwargs`")
    assert texts(para) == [("use *args and ", []), ("**kwargs", ["code"])]


def test_backslash_escape_is_literal():
    (para,) = blocks(r"\*not em\*")
    assert texts(para) == [("*not em*", [])]


@pytest.mark.parametrize(
    ("md", "text"),
    [
        ("[`acli`](https://e.com)", "acli"),
        ("[see `foo` here](https://e.com)", "see foo here"),
        ("[`x`][r]\n\n[r]: https://e.com", "x"),
        ("[see <https://g.com>](https://e.com)", "see https://g.com"),
    ],
)
def test_link_text_is_prose(md, text):
    # Code spans and cards inside link text become plain link text.
    (para,) = blocks(md)
    (node,) = para["content"]
    assert node == {
        "type": "text",
        "text": text,
        "marks": [{"type": "link", "attrs": {"href": "https://e.com"}}],
    }


@pytest.mark.parametrize(
    "md",
    ["`curl https://e.com/x`", "`[t](https://e.com)`", "`<https://e.com>`"],
)
def test_urls_and_links_inside_a_code_span_stay_code(md):
    (para,) = blocks(md)
    (node,) = para["content"]
    assert node["marks"] == [{"type": "code"}]


def test_urls_inside_a_code_block_stay_text():
    (code,) = blocks("```\ncurl https://e.com/x <https://e.com>\n```")
    assert code["content"] == [
        {"type": "text", "text": "curl https://e.com/x <https://e.com>"}
    ]


def test_code_drops_emphasis():
    (para,) = blocks("**`x`** and *`y`*")
    assert texts(para) == [("x", ["code"]), (" and ", []), ("y", ["code"])]


@pytest.mark.parametrize(
    ("md", "href", "tail"),
    [
        ("see https://e.com/a_b.", "https://e.com/a_b", "."),
        ("(https://e.com/x)", "https://e.com/x", ")"),
        ("https://e.com/a_(b)?", "https://e.com/a_(b)", "?"),
    ],
)
def test_bare_url_becomes_a_link_without_trailing_punctuation(md, href, tail):
    (para,) = blocks(md)
    link = next(n for n in para["content"] if n.get("marks"))
    assert link["text"] == href
    assert link["marks"] == [{"type": "link", "attrs": {"href": href}}]
    assert para["content"][-1]["text"].endswith(tail)


def test_url_in_link_text_is_not_linked_twice():
    (para,) = blocks("[https://a.com](https://b.com)")
    (node,) = para["content"]
    assert node["marks"] == [{"type": "link", "attrs": {"href": "https://b.com"}}]


def test_link():
    (para,) = blocks("[MR !27](https://g.com/m/27)")
    (node,) = para["content"]
    assert node["marks"] == [{"type": "link", "attrs": {"href": "https://g.com/m/27"}}]


def test_autolink_is_a_smart_link_card():
    (ul,) = blocks("- <https://x.atlassian.net/browse/PROJ-1>")
    (item,) = ul["content"]
    (para,) = item["content"]
    assert para["content"] == [
        {
            "type": "inlineCard",
            "attrs": {"url": "https://x.atlassian.net/browse/PROJ-1"},
        }
    ]


def test_standalone_autolink_is_a_card():
    card, para = blocks("<https://e.com/a>\n\ntext <https://e.com/b>")
    assert card == {"type": "blockCard", "attrs": {"url": "https://e.com/a"}}
    assert para["type"] == "paragraph"
    assert para["content"][1]["type"] == "inlineCard"


@pytest.mark.parametrize(
    "md",
    ["- <https://e.com>", "> <https://e.com>", "| a |\n|---|\n| <https://e.com> |"],
)
def test_autolink_stays_inline_where_adf_forbids_a_card(md):
    nodes = []

    def walk(n):
        nodes.append(n["type"])
        for c in n.get("content", []):
            walk(c)

    for block in blocks(md):
        walk(block)
    assert "inlineCard" in nodes and "blockCard" not in nodes


@pytest.mark.parametrize(
    ("md", "level", "text"),
    [("Title\n=====", 1, "Title"), ("Two\nlines\n---", 2, "Two lines")],
)
def test_setext_headings(md, level, text):
    (heading,) = blocks(md)
    assert heading["type"] == "heading" and heading["attrs"] == {"level": level}
    assert texts(heading) == [(text, [])]


def test_dashes_after_a_blank_line_are_a_rule():
    para, rule = blocks("text\n\n---")
    assert para["type"] == "paragraph" and rule == {"type": "rule"}


def test_lone_hyphen_under_text_is_not_a_heading():
    para, ul = blocks("text\n-")
    assert para["type"] == "paragraph" and ul["type"] == "bulletList"


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        ("R&amp;D &rarr; ship", [("R&D \u2192 ship", [])]),
        ("&#42;not em&#42;", [("*not em*", [])]),
        ("&nosuch; stays", [("&nosuch; stays", [])]),
        ("`&amp;`", [("&amp;", ["code"])]),
    ],
)
def test_html_entities(md, expected):
    (para,) = blocks(md)
    assert texts(para) == expected


REFS = '\n\n[Spec]: https://e.com/spec\n[r2]: <https://e.com/two> "Title"\n'


@pytest.mark.parametrize(
    ("md", "text", "href"),
    [
        ("[the spec][spec]", "the spec", "https://e.com/spec"),
        ("[Spec][]", "Spec", "https://e.com/spec"),
        ("[SPEC]", "SPEC", "https://e.com/spec"),
        ("[two][R2]", "two", "https://e.com/two"),
    ],
)
def test_reference_links(md, text, href):
    content = blocks(md + REFS)
    (para,) = content  # the definitions produce no blocks
    (node,) = para["content"]
    assert node["text"] == text
    assert node["marks"] == [{"type": "link", "attrs": {"href": href}}]


def test_undefined_reference_is_literal_with_inline_parsing():
    (para,) = blocks("[*x*] and [ ]")
    assert texts(para) == [("[", []), ("x", ["em"]), ("] and [ ]", [])]


def test_first_definition_wins():
    (para,) = blocks("[a]\n\n[a]: https://one.com\n[a]: https://two.com")
    assert para["content"][0]["marks"][0]["attrs"]["href"] == "https://one.com"


def test_definition_cannot_interrupt_a_paragraph():
    (para,) = blocks("text\n[a]: https://e.com")
    assert texts(para) == [("text [a]: ", []), ("https://e.com", ["link"])]


def test_definition_inside_a_code_block_is_code():
    code, para = blocks("```\n[a]: https://e.com\n```\n\n[a]")
    assert code["content"][0]["text"] == "[a]: https://e.com"
    assert texts(para) == [("[a]", [])]


def test_nested_lists_and_ordered_start():
    (ol,) = blocks("3. a\n4. b\n   - c\n   - d")
    assert ol["type"] == "orderedList" and ol["attrs"] == {"order": 3}
    first, second = ol["content"]
    assert texts(first) == [("a", [])]
    _, nested = second["content"]
    assert nested["type"] == "bulletList" and len(nested["content"]) == 2


def test_loose_item_keeps_its_second_paragraph():
    (ul,) = blocks("- one\n\n  more\n- two")
    first, _ = ul["content"]
    assert [b["type"] for b in first["content"]] == ["paragraph", "paragraph"]


def test_list_ends_at_unindented_paragraph():
    ul, para = blocks("- one\n\nafter")
    assert ul["type"] == "bulletList" and para["type"] == "paragraph"


def test_lazy_continuation_joins_the_item():
    (ul,) = blocks("- one\ncontinued")
    assert texts(ul) == [("one continued", [])]


def test_empty_item_gets_an_empty_paragraph():
    (ul,) = blocks("-\n- b")
    assert ul["content"][0]["content"] == [{"type": "paragraph", "content": []}]


def test_item_may_open_with_a_nested_list():
    (outer,) = blocks("- - inner")
    (item,) = outer["content"]
    assert [b["type"] for b in item["content"]] == ["bulletList"]


def test_spaced_asterisks_are_a_rule_not_a_list():
    assert blocks("* * *") == [{"type": "rule"}]


def test_table_cells_escaped_pipe_and_ragged_row():
    (table,) = blocks("| a | b |\n|---|:-:|\n| `x \\| y` | 1 |\n| only |")
    header, row, short = table["content"]
    assert [c["type"] for c in header["content"]] == ["tableHeader"] * 2
    assert texts(row["content"][0]) == [("x | y", ["code"])]
    assert len(short["content"]) == 2 and short["content"][1]["content"] == [
        {"type": "paragraph", "content": []}
    ]


def test_code_block_language_and_tabs():
    (code,) = blocks("```go\nfunc f() {\n\treturn\n}\n```")
    assert code["attrs"] == {"language": "go", "wrap": True}
    assert code["content"][0]["text"] == "func f() {\n\treturn\n}"


def test_code_block_contents_are_not_parsed():
    (code,) = blocks("```\n**x** | y\n- z\n```")
    assert code["attrs"] == {"wrap": True}
    assert code["content"][0]["text"] == "**x** | y\n- z"


def test_blockquote():
    (quote,) = blocks("> a *b*\n> c")
    assert quote["type"] == "blockquote"
    assert texts(quote) == [("a ", []), ("b", ["em"]), (" c", [])]


def test_table_in_list_item_is_rejected():
    with pytest.raises(m2a.ConversionError, match="table inside a list item"):
        blocks("- item\n\n  | a |\n  |---|\n  | 1 |")


def test_declared_types_are_exactly_what_convert_emits():
    md = "\n\n".join(
        [
            "# h",
            "p **s** *e* ~~x~~ `c` [l](https://e.com)  \nbreak",
            "> q",
            "---",
            "- a\n\n1. b",
            "```\ncode\n```",
            "| a |\n|---|\n| b |",
            "- <https://e.com>",
            "<https://e.com>",
        ]
    )
    nodes, marks = set(), set()

    def walk(n):
        nodes.add(n["type"])
        marks.update(m["type"] for m in n.get("marks", []))
        for c in n.get("content", []):
            walk(c)

    walk(m2a.convert(md))
    assert nodes == m2a.NODE_TYPES
    assert marks == m2a.MARK_TYPES
