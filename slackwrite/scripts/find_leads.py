#!/usr/bin/env python3
"""Find what Claude Code memory and past sessions say about MR merge order.

GitLab records two kinds of merge order, stacked MRs and MR dependencies, and
slackwrite.py states both. This script looks for the rest: an order that a
past session found or agreed on, but that nobody recorded in GitLab. It prints
leads, not facts. A lead is only as current as its date.

Name each MR with a group of terms joined by commas, such as its reference and
its ticket key: `!12,ACME-1`. A lead is a passage that names two of the
MRs, or that names one MR and a merge-order phrase.

The search reads each Claude Code project directory whose name starts with the
name of the repo's main worktree, so it takes in the worktrees under it:

    <project>/memory/*.md    memory files (dated by their last change)
    <project>/*.jsonl        session transcripts (dated by each entry)

Dates are UTC. The script prints every memory lead, then the newest
transcript leads. It prints each passage once, at its newest date.

The project directories are in $CLAUDE_CONFIG_DIR/projects, else
~/.claude/projects. The search skips the current session,
$CLAUDE_CODE_SESSION_ID, because its content is already in context.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Container, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Claude Code names a project directory after its path, with each character
# other than a letter or a digit replaced by "-" (as seen in ~/.claude/projects).
_SLUG_CHAR = re.compile(r"[^A-Za-z0-9]")
# Phrases that say one MR must merge before another, or that two MRs collide.
_ORDER = re.compile(
    r"(?i)\b(?:stack(?:ed|s)?\s+on|on\s+top\s+of|blocked\s+by|blocker"
    r"|depends?\s+on|dependency|merges?\s+(?:after|before|first|second)"
    r"|merge\s+order|retarget|rebased?\s+on(?:to)?|conflict)"
)
# What tool output puts before a line of a file: the line number of a file
# read, or the marker of a diff line. The same line then reads the same.
_LINE_PREFIX = re.compile(r"^(?:\s*\d+\t|[+-](?=\S))")
# How far a passage reaches on each side of a term, within the term's line.
REACH = 240
DEFAULT_LIMIT = 30


class LeadsError(RuntimeError):
    """A step failed. The message says which step and why."""


@dataclass(frozen=True)
class Lead:
    day: str  # YYYY-MM-DD, or "" for a transcript entry without a timestamp
    source: str
    mrs: tuple[str, ...]  # the first term of each MR that the passage names
    text: str


@dataclass(frozen=True)
class Terms:
    """The search terms, each mapped to the first term of its MR's group."""

    owner: dict[str, str]
    pattern: re.Pattern[str]

    @classmethod
    def parse(cls, groups: Sequence[str]) -> Terms:
        owner: dict[str, str] = {}
        for group in groups:
            terms = [t.strip() for t in group.split(",") if t.strip()]
            if not terms:
                raise LeadsError(f"group {group!r} has no terms")
            for term in terms:
                if term in owner:
                    raise LeadsError(f"term {term!r} is in more than one group")
                owner[term] = terms[0]
        # Longest first, so that a term that starts another cannot shadow it.
        alts = "|".join(map(re.escape, sorted(owner, key=len, reverse=True)))
        return cls(owner, re.compile(rf"(?<![A-Za-z0-9])(?:{alts})(?![A-Za-z0-9])"))

    def named(self, text: str) -> list[str]:
        """Return the MRs that `text` names, in the order of their groups."""
        found = {self.owner[m.group()] for m in self.pattern.finditer(text)}
        return [mr for mr in dict.fromkeys(self.owner.values()) if mr in found]

    def in_raw(self, text: str) -> bool:
        """Tell cheaply whether `text` can hold a term at all."""
        return any(term in text for term in self.owner)


# --- search ------------------------------------------------------------------


def passages(text: str, terms: Terms) -> Iterator[tuple[list[str], str]]:
    """Yield each lead in `text`, with the MRs that it names."""
    for raw in text.splitlines():
        if not terms.in_raw(raw):
            continue
        line = _LINE_PREFIX.sub("", raw)
        spans: list[list[int]] = []
        for m in terms.pattern.finditer(line):
            lo, hi = max(0, m.start() - REACH), min(len(line), m.end() + REACH)
            window = line[lo:hi]
            if len(terms.named(window)) < 2 and not _ORDER.search(window):
                continue
            if spans and lo <= spans[-1][1]:
                spans[-1][1] = hi
            else:
                spans.append([lo, hi])
        for lo, hi in spans:
            span = " ".join(line[lo:hi].split())
            clipped = ("…" if lo else "") + span + ("…" if hi < len(line) else "")
            yield terms.named(line[lo:hi]), clipped


def strings(value: object) -> Iterator[str]:
    """Yield every string value in a parsed JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def transcript_leads(path: Path, source: str, terms: Terms) -> Iterator[Lead]:
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            # JSON leaves a term's characters unescaped, so a raw-line check
            # skips most entries before they are parsed.
            if not terms.in_raw(raw):
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue  # an entry that a session wrote only in part
            day = str(entry.get("timestamp", ""))[:10]
            for text in strings(entry):
                for mrs, passage in passages(text, terms):
                    yield Lead(day, source, tuple(mrs), passage)


def memory_leads(path: Path, source: str, terms: Terms) -> Iterator[Lead]:
    # UTC, like the transcript timestamps.
    day = datetime.fromtimestamp(path.stat().st_mtime, UTC).date().isoformat()
    for mrs, passage in passages(path.read_text(encoding="utf-8"), terms):
        yield Lead(day, source, tuple(mrs), passage)


def main_worktree() -> Path:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
    except FileNotFoundError as exc:
        raise LeadsError("git is not installed") from exc
    if proc.returncode != 0:
        raise LeadsError(f"not in a git repo: {proc.stderr.strip()}")
    return Path(proc.stdout.strip()).parent


def project_dirs(root: Path, projects: Path) -> list[tuple[str, Path]]:
    """Return each project directory for `root` and its worktrees, by label."""
    slug = _SLUG_CHAR.sub("-", str(root))
    dirs = sorted(p for p in projects.glob(f"{slug}*") if p.is_dir())
    return [(p.name[len(slug) :].lstrip("-") or ".", p) for p in dirs]


def newest_first(leads: Iterable[Lead], known: Container[str] = ()) -> list[Lead]:
    """Return each text not in `known` once, at its newest date, newest first."""
    newest: dict[str, Lead] = {}
    for lead in leads:
        seen = newest.get(lead.text)
        if lead.text not in known and (seen is None or lead.day > seen.day):
            newest[lead.text] = lead
    by_source = sorted(newest.values(), key=lambda lead: (lead.source, lead.text))
    return sorted(by_source, key=lambda lead: lead.day, reverse=True)


def find_leads(
    dirs: list[tuple[str, Path]], terms: Terms, skip_session: str | None
) -> tuple[list[Lead], list[Lead]]:
    """Return the memory leads and the transcript leads.

    A transcript lead whose text is also a memory lead is left out.
    """
    memory = newest_first(
        lead
        for label, d in dirs
        for md in sorted(d.glob("memory/*.md"))
        for lead in memory_leads(md, f"{label}/memory/{md.name}", terms)
    )
    transcripts = newest_first(
        (
            lead
            for label, d in dirs
            for t in sorted(d.glob("*.jsonl"))
            if t.stem != skip_session
            for lead in transcript_leads(t, f"{label}/{t.stem[:8]}", terms)
        ),
        known={lead.text for lead in memory},
    )
    return memory, transcripts


def render_leads(leads: list[Lead]) -> list[str]:
    return [
        f"{lead.day or '(no date)'}  {lead.source}  {' '.join(lead.mrs)}\n  {lead.text}"
        for lead in leads
    ]


def render(memory: list[Lead], transcripts: list[Lead], limit: int) -> str:
    """Return every memory lead, then the newest transcript leads."""
    shown = transcripts[:limit]
    return "\n\n".join(
        [
            f"## Memory leads: {len(memory)}",
            *render_leads(memory),
            f"## Transcript leads: {len(transcripts)}, the newest {len(shown)} shown",
            *render_leads(shown),
        ]
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "groups",
        nargs="+",
        metavar="GROUP",
        help="one MR's terms, joined by commas, such as '!12,ACME-1'",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="show at most this many transcript leads; memory leads are all "
        f"shown (default {DEFAULT_LIMIT})",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_dir = Path(config).expanduser() if config else Path.home() / ".claude"
    try:
        terms = Terms.parse(args.groups)
        dirs = project_dirs(main_worktree(), claude_dir / "projects")
        memory, transcripts = find_leads(
            dirs, terms, os.environ.get("CLAUDE_CODE_SESSION_ID")
        )
    except LeadsError as exc:
        print(f"find_leads: error: {exc}", file=sys.stderr)
        return 1
    print(render(memory, transcripts, args.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
