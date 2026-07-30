---
name: markdown-format
description: Formats Markdown source for readability without reflowing prose. Currently aligns table columns; run it after editing any Markdown table, or when asked to "format the markdown", "fix the tables", or "align the table".
---

# Steps

1. Identify the Markdown files to format. If the user named files, use those.
   Otherwise use the ones you just edited.
2. Run the aligner:

   ```sh
   python3 ~/.claude/skills/markdown-format/scripts/align_tables.py <file>...
   ```

   It prints one line per file it changed and stays silent for files that were
   already correct, so a second run producing no output confirms convergence.
3. Report which files changed. Do not describe the diff cell by cell.

# What it does

Pads every Markdown table cell so the columns line up in the source, and
regenerates the `|---|---|` separator to match. Nothing else is touched.

Specifically preserved:

- **Prose.** Paragraphs, wrapping, and blank lines are left exactly as they
  were.
- **Fenced code blocks.** Sample output containing `|` characters is not
  mistaken for a table.
- **Alignment colons.** `:---`, `---:`, and `:---:` keep their meaning, and the
  dash count adjusts around them.
- **Indentation.** A table nested inside a list item keeps its indent.
- **Escaped pipes.** `\|` inside a cell is content, not a column delimiter — in
  GFM that is the only way to put a literal pipe in a cell, so it is the whole
  escape grammar. Widths count `\|` as the two characters it occupies in the
  file, since the goal is aligning the source rather than the rendered table.

Ragged rows are padded to the widest row rather than rejected, so a table with
a missing trailing cell is repaired instead of erroring.

# When to use it

Use it whenever you have edited a Markdown table, since hand-padding cells is
tedious and wrong by one character often enough to matter.

Prefer it over a general Markdown formatter. IDE and CLI formatters do align
tables, but they also reflow each paragraph onto a single long line, which
makes the source and its diffs harder to read. This skill exists because that
tradeoff is not worth making just to fix a table.

Note for JetBrains IDEs specifically: `Code > Reformat` scoped to a selected
table works well by hand, but the MCP `reformat_file` tool is whole-file only
and will reflow the prose. Use this skill instead of that tool.

# Extending

The skill is named for Markdown formatting generally; table alignment is only
its first capability. Add a new script under `scripts/` and a section here when
another safe, narrowly scoped transform is worth having. The design rule that
earns this skill its place is that every transform must be **surgical**: it
changes the one construct it targets and provably leaves everything else byte
identical.

A useful check when adding one:

```sh
diff <(grep -v '^\s*|' before.md) <(grep -v '^\s*|' after.md)
```

Adapt the filter to whatever the new transform targets. It should report no
differences.
