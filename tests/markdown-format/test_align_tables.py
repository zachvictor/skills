"""Tests for markdown-format/scripts/align_tables.py.

The contract this script has to keep is narrow but strict: tables get aligned,
and *nothing else in the file changes*. Most of these tests exist to pin the
second half of that.
"""

import align_tables as at


def test_pads_columns_to_the_widest_cell():
    src = "| a | bbbb |\n|---|---|\n| ccccc | d |\n"
    assert at.align(src) == ("| a     | bbbb |\n|-------|------|\n| ccccc | d    |\n")


def test_separator_width_tracks_the_content():
    # The separator is regenerated, not measured, so a too-short or too-long
    # one in the input must come out correct.
    src = "| Forge | CLI |\n|--|------------------|\n| GitHub | gh |\n"
    lines = at.align(src).split("\n")
    assert len({len(line) for line in lines if line}) == 1


def test_is_idempotent():
    src = "| a | bbbb |\n|---|---|\n| ccccc | d |\n"
    once = at.align(src)
    assert at.align(once) == once


def test_alignment_colons_are_preserved():
    src = "| a | b | c |\n|:---|---:|:---:|\n| 1 | 2 | 3 |\n"
    out = at.align(src).split("\n")
    sep = out[1]
    cells = [c for c in sep.split("|") if c]
    assert cells[0].startswith(":") and not cells[0].endswith(":")
    assert cells[1].endswith(":") and not cells[1].startswith(":")
    assert cells[2].startswith(":") and cells[2].endswith(":")
    # Colons consume dash slots rather than widening the column.
    assert len({len(line) for line in out if line}) == 1


def test_prose_is_untouched():
    src = (
        "# Heading\n"
        "\n"
        "A paragraph that is quite long and must not be reflowed by this "
        "script under any circumstances.\n"
        "\n"
        "- a list item\n"
        "- another\n"
    )
    assert at.align(src) == src


def test_document_without_tables_is_unchanged():
    src = "just text\n\nmore text\n"
    assert at.align(src) == src


def test_pipes_inside_fenced_code_are_not_treated_as_a_table():
    src = "```\n| this | is sample output |\n|--|--|\n```\n"
    assert at.align(src) == src


def test_table_after_a_fenced_block_is_still_aligned():
    src = "```sh\necho hi\n```\n\n| a | bb |\n|---|---|\n| c | d |\n"
    out = at.align(src)
    assert "```sh\necho hi\n```\n" in out
    assert "| a | bb |" in out
    assert "| c | d  |" in out


def test_indented_table_keeps_its_indent():
    # A table nested under a list item must stay nested.
    src = "- item:\n\n  | a | bb |\n  |---|---|\n  | c | d |\n"
    out = at.align(src)
    for line in out.split("\n"):
        if line.strip().startswith("|"):
            assert line.startswith("  |"), line
    assert "- item:\n" in out


def test_ragged_rows_are_padded_not_rejected():
    # A row missing its trailing cell is repaired rather than erroring.
    src = "| a | b |\n|---|---|\n| c |\n"
    out = at.align(src).split("\n")
    assert len({len(line) for line in out if line}) == 1
    assert out[2].rstrip().endswith("|")


def test_empty_cells_are_handled():
    src = "| a |  | c |\n|---|---|---|\n|  | b |  |\n"
    out = at.align(src).split("\n")
    assert len({len(line) for line in out if line}) == 1


def test_multiple_tables_are_aligned_independently():
    src = (
        "| a | bbbb |\n|---|---|\n| c | d |\n"
        "\n"
        "text between\n"
        "\n"
        "| xxxxx | y |\n|---|---|\n| z | w |\n"
    )
    out = at.align(src)
    assert "text between\n" in out
    first, second = out.split("text between")
    # Widths are computed per table, so the two must not match each other.
    assert "| a | bbbb |" in first
    assert "| xxxxx | y |" in second


def test_trailing_newline_is_preserved():
    assert at.align("| a |\n|---|\n").endswith("|\n")
    assert not at.align("| a |\n|---|").endswith("\n")


def test_indent_is_the_full_leading_whitespace_run():
    # `\s` covers Unicode whitespace, not only space and tab, and the indent
    # comes back verbatim rather than normalised.
    assert at.split_row("\t\t| a |")[0] == "\t\t"
    assert at.split_row("  \t | a |")[0] == "  \t "
    # NBSP as an escape, since a literal one in source is invisible.
    assert at.split_row("\u00a0| a |")[0] == "\u00a0"
    assert at.split_row("| a |")[0] == ""


def test_whitespace_only_line_is_not_a_table_row():
    src = "   \n| a | bb |\n|---|---|\n| c | d |\n"
    assert at.align(src).split("\n")[0] == "   "


def test_indented_fence_toggles_fence_state():
    # An indented fence still opens and closes, so the table inside it is left
    # alone rather than aligned.
    src = "  ```\n  | this | is sample output |\n  |--|--|\n  ```\n"
    assert at.align(src) == src


def test_escaped_pipe_is_content_not_a_delimiter():
    # `\|` is the only way to put a literal pipe in a GFM cell. Splitting on
    # every pipe made this row one cell wider than the rest, which corrupts the
    # table into a spurious third column.
    src = "| Helper | Change |\n|---|---|\n| `cmd [a\\|b]` | takes a or b |\n"
    out = at.align(src)
    assert "`cmd [a\\|b]`" in out
    assert [len(at.split_row(line)[1]) for line in out.split("\n") if line] == [2, 2, 2]
    assert len({len(line) for line in out.split("\n") if line}) == 1


def test_escaped_pipe_at_end_of_row_is_not_stripped_as_an_outer_pipe():
    assert at.split_row("| a | b\\|")[1] == ["a", "b\\|"]
    assert at.split_row("| a | b\\| |")[1] == ["a", "b\\|"]


def test_escaped_pipe_edge_cases():
    assert at.split_row("| \\| |")[1] == ["\\|"]
    assert at.split_row("| a\\|b\\|c |")[1] == ["a\\|b\\|c"]
    # An escape immediately followed by a real delimiter.
    assert at.split_row("| a\\|| b |")[1] == ["a\\|", "b"]


def test_escaped_pipe_widths_count_source_characters():
    # Two cells of equal *source* length line up, even though `\|` renders as
    # one character. Aligning the source is the whole point of the script.
    src = "| ab\\|c | x |\n|---|---|\n| 12345 | y |\n"
    out = at.align(src)
    assert len({len(line) for line in out.split("\n") if line}) == 1


def test_is_separator():
    assert at.is_separator(["---", ":---", "---:", ":---:"])
    assert not at.is_separator(["a", "---"])
    assert not at.is_separator([])


def test_split_row_strips_outer_pipes_and_whitespace():
    indent, cells = at.split_row("  |  a |b  |  \n".rstrip("\n"))
    assert indent == "  "
    assert cells == ["a", "b"]


def test_main_rewrites_only_files_that_change(tmp_path, capsys):
    changed = tmp_path / "changed.md"
    changed.write_text("| a | bbbb |\n|---|---|\n| ccccc | d |\n")
    already = tmp_path / "already.md"
    already.write_text("| a     | bbbb |\n|-------|------|\n| ccccc | d    |\n")
    before = already.read_text()

    assert at.main([str(changed), str(already)]) == 0

    out = capsys.readouterr().out
    assert "changed.md" in out
    assert "already.md" not in out
    assert already.read_text() == before


def test_main_without_paths_is_a_usage_error(capsys):
    assert at.main([]) == 2
    assert capsys.readouterr().err.strip()
