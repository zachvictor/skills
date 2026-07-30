#!/usr/bin/env python3
r"""Align Markdown table columns, leaving everything else exactly as it was.

Padding table cells by hand is tedious and easy to get wrong by one character.
A full Markdown formatter fixes that but also reflows prose onto single lines,
which makes the source and its diffs harder to read. This does tables only.

Rows inside fenced code blocks are left alone, so sample output containing
pipes is safe. Alignment colons (`:---`, `---:`, `:---:`) are preserved.
An escaped pipe (`\|`) is cell content, not a delimiter — in GFM that is the
only way to put a literal pipe in a cell, so it is the whole escape grammar.

Usage:
    python scripts/align-md-tables.py README.md docs/*.md
"""

import re
import sys
from pathlib import Path

# The whole grammar this script recognizes, in one place. Every pattern is
# used with `.match()`, which anchors at position 0 on its own; the leading `^`
# is kept so each pattern states its own anchoring rather than relying on the
# caller's choice of method.

# A separator cell is dashes with optional leading/trailing alignment colons.
_SEP_CELL = re.compile(r"^:?-+:?$")

# A cell delimiter is a pipe that isn't escaped. `str.split("|")` can't express
# "not preceded by a backslash", which is why this one is a regex: splitting on
# every pipe turns `` `cmd [a\|b]` `` into two cells and silently corrupts the
# row into a table one column wider than the rest.
_DELIM = re.compile(r"(?<!\\)\|")

# Line classifiers, both indifferent to indentation.
_FENCE = re.compile(r"^\s*```")
_ROW = re.compile(r"^\s*\|")

# The leading whitespace run. `\s*` can match empty, so this always matches and
# `.group()` needs no None guard.
_INDENT = re.compile(r"^\s*")


def split_row(line: str) -> tuple[str, list[str]]:
    """Split one table row into its indent and its stripped cells."""
    indent = _INDENT.match(line).group()
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return indent, [cell.strip() for cell in _DELIM.split(body)]


def is_separator(cells: list[str]) -> bool:
    """True for the `|---|---|` row under a table header."""
    return bool(cells) and all(_SEP_CELL.match(cell) for cell in cells)


def render(indent: str, rows: list[list[str]]) -> list[str]:
    """Re-emit one table with every column padded to its widest cell.

    Separator rows are excluded from the width calculation, since their
    dashes are generated to fit rather than measured.
    """
    columns = max(len(row) for row in rows)
    rows = [row + [""] * (columns - len(row)) for row in rows]
    body = [row for row in rows if not is_separator(row)]
    widths = [max(len(row[i]) for row in body) for i in range(columns)]

    out: list[str] = []
    for row in rows:
        if is_separator(row):
            cells = []
            for i, cell in enumerate(row):
                left = cell.startswith(":")
                right = cell.endswith(":") and len(cell) > 1
                dashes = "-" * (widths[i] + 2 - left - right)
                cells.append(f"{':' if left else ''}{dashes}{':' if right else ''}")
        else:
            # Widths are source lengths, so `\|` counts as the two characters
            # it occupies in the file. That is the point: this aligns the
            # source, not the rendered table.
            cells = [f" {cell.ljust(widths[i])} " for i, cell in enumerate(row)]
        out.append(indent + "|" + "|".join(cells) + "|")
    return out


def align(text: str) -> str:
    """Return ``text`` with every Markdown table aligned."""
    out: list[str] = []
    block: list[list[str]] = []
    indent = ""
    in_fence = False

    def flush() -> None:
        nonlocal block
        if block:
            out.extend(render(indent, block))
            block = []

    for line in text.split("\n"):
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and _ROW.match(line):
            row_indent, cells = split_row(line)
            if not block:
                indent = row_indent
            block.append(cells)
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__, file=sys.stderr)
        return 2
    for name in paths:
        path = Path(name)
        original = path.read_text()
        aligned = align(original)
        if aligned != original:
            path.write_text(aligned)
            print(f"aligned {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
