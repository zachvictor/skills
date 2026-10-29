#!/usr/bin/env python3
"""Find what Claude Code memory and past sessions say about MR merge order.

GitLab records two kinds of merge order, stacked MRs and MR dependencies, and
slackwrite.py states both. This script looks for the rest: an order that a
past session found or agreed on, but that nobody recorded in GitLab. It prints
leads, not facts. A lead is only as current as its date.

Name each MR with a group of terms joined by commas, such as its reference and
its ticket key: `!12,ACME-1`. A term `!N` also matches `<repo>!N` (the
form GitLab uses for an MR of a project in the same group) and the MR's URL.
A lead is a passage that names two of the MRs, or that names one MR and a
merge-order phrase.

The repo is the one slackwrite.py uses: the upstream of the current branch
gives its GitLab host and project path. The search reads each Claude Code
project directory for the repo:

- the directory of the main worktree, and of each worktree whose path starts
  with the path of the main worktree
- the directory of each clone whose path ends in the GitLab project path, and
  of the worktrees of that clone

In each one it reads:

    <project>/memory/*.md    memory files (dated by their last change)
    <project>/*.jsonl        session transcripts (dated by each entry)

Dates are UTC. The script prints every memory lead, then the newest
transcript leads, at most --per-session of them from one session. It prints
each passage once, at its newest date.

A transcript also records this skill's own work: the load of the skill, and
each tool call that runs, reads, or edits one of its scripts, with the result
of the call. The search skips these, because they repeat what the skill
printed. It also skips the current session, $CLAUDE_CODE_SESSION_ID, because
its content is already in context.

The project directories are in $CLAUDE_CONFIG_DIR/projects, else
~/.claude/projects.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Container, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import slackwrite

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
# A term that names an MR of the repo by its number.
_MR_TERM = re.compile(r"^!(?P<iid>\d+)$")
# The left edge of a term: no letter or digit before it.
_EDGE = r"(?<![A-Za-z0-9])"
# The left edge of a project name or host, which can hold "-", "_", and ".".
_NAME_EDGE = r"(?<![\w.-])"
# This skill's scripts, and one of them in the input of a tool call.
OWN_SCRIPTS = ("slackwrite.py", "find_leads.py")
_OWN_SCRIPT = re.compile(rf"(?<![\w.-])(?:{'|'.join(map(re.escape, OWN_SCRIPTS))})\b")
# The first line of a load of this skill, as Claude Code records it.
_OWN_LOAD = re.compile(r"^Base directory for this skill: [^\n]*/slackwrite(?:\n|$)")
# How far a passage reaches on each side of a term, within the term's line.
REACH = 240
DEFAULT_LIMIT = 30
DEFAULT_PER_SESSION = 5


class LeadsError(RuntimeError):
    """A step failed. The message says which step and why."""


@dataclass(frozen=True)
class Lead:
    day: str  # YYYY-MM-DD, or "" for a transcript entry without a timestamp
    source: str
    mrs: tuple[str, ...]  # the first term of each MR that the passage names
    text: str


def spellings(term: str, repo: slackwrite.Repo | None) -> list[tuple[str, str]]:
    """Return each way to write `term`, with the left edge that it needs."""
    m = _MR_TERM.match(term)
    if m is None or repo is None:
        return [(term, _EDGE)]
    return [
        (term, _EDGE),
        (f"{repo.name}{term}", _NAME_EDGE),
        (f"{repo.host}/{repo.path}/-/merge_requests/{m['iid']}", _NAME_EDGE),
    ]


@dataclass(frozen=True)
class Terms:
    """The search terms, each mapped to the first term of its MR's group."""

    owner: dict[str, str]
    pattern: re.Pattern[str]
    # The terms that hold no other term. A text that holds a term holds one.
    probes: tuple[str, ...]

    @classmethod
    def parse(cls, groups: Sequence[str], repo: slackwrite.Repo | None = None) -> Terms:
        """Parse the groups. With `repo`, a term `!N` gets its other spellings."""
        owner: dict[str, str] = {}
        edges: dict[str, str] = {}
        for group in groups:
            terms = [t.strip() for t in group.split(",") if t.strip()]
            if not terms:
                raise LeadsError(f"group {group!r} has no terms")
            for term in terms:
                for text, edge in spellings(term, repo):
                    if text in owner:
                        raise LeadsError(f"term {text!r} is in more than one group")
                    owner[text] = terms[0]
                    edges[text] = edge
        # Longest first, so that a term that starts another cannot shadow it.
        alts = "|".join(
            edges[t] + re.escape(t) for t in sorted(owner, key=len, reverse=True)
        )
        probes = tuple(t for t in owner if not any(o != t and o in t for o in owner))
        return cls(owner, re.compile(rf"(?:{alts})(?![A-Za-z0-9])"), probes)

    def named(self, text: str) -> list[str]:
        """Return the MRs that `text` names, in the order of their groups."""
        found = {self.owner[m.group()] for m in self.pattern.finditer(text)}
        return [mr for mr in dict.fromkeys(self.owner.values()) if mr in found]

    def in_raw(self, text: str) -> bool:
        """Tell cheaply whether `text` can hold a term at all."""
        return any(probe in text for probe in self.probes)


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


def drop_own(entry: dict, own: set[str]) -> dict:
    """Return `entry` without this skill's own blocks.

    Add the id of each own tool call to `own`, so that its result, which comes
    later in the transcript, is dropped too.
    """
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return entry
    kept = []
    dropped_result = False
    for block in content:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "tool_use" and any(
            _OWN_SCRIPT.search(s) for s in strings(block.get("input"))
        ):
            if isinstance(block.get("id"), str):
                own.add(block["id"])
        elif kind == "tool_result" and block.get("tool_use_id") in own:
            dropped_result = True
        elif kind == "text" and _OWN_LOAD.match(str(block.get("text", ""))):
            pass
        else:
            kept.append(block)
    if len(kept) == len(content):
        return entry
    entry = {**entry, "message": {**message, "content": kept}}
    if dropped_result:
        # The result of a tool call, as the tool gave it.
        entry.pop("toolUseResult", None)
    return entry


def transcript_leads(path: Path, source: str, terms: Terms) -> Iterator[Lead]:
    own: set[str] = set()
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            # JSON leaves a term's characters unescaped, so a raw-line check
            # skips most entries before they are parsed. An entry without a
            # term is still read for this skill's own tool calls.
            named = terms.in_raw(raw)
            if not named and not any(name in raw for name in OWN_SCRIPTS):
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue  # an entry that a session wrote only in part
            entry = drop_own(entry, own)
            if not named:
                continue
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


def project_dirs(root: Path, repo: slackwrite.Repo, projects: Path) -> list[Path]:
    """Return the project directory of each clone and worktree of the repo.

    A directory counts if its path starts with `root`, or ends in the GitLab
    project path, in either case followed by nothing or by a further part.
    """
    root_slug = re.escape(_SLUG_CHAR.sub("-", str(root)))
    path_slug = re.escape(_SLUG_CHAR.sub("-", repo.path))
    match = re.compile(rf"^(?:{root_slug}|.*-{path_slug})(?:-.*)?$")
    return sorted(p for p in projects.glob("*") if p.is_dir() and match.match(p.name))


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
    dirs: list[Path], terms: Terms, skip_session: str | None
) -> tuple[list[Lead], list[Lead]]:
    """Return the memory leads and the transcript leads.

    A transcript lead whose text is also a memory lead is left out.
    """
    memory = newest_first(
        lead
        for d in dirs
        for md in sorted(d.glob("memory/*.md"))
        for lead in memory_leads(md, f"{d.name}/memory/{md.name}", terms)
    )
    transcripts = newest_first(
        (
            lead
            for d in dirs
            for t in sorted(d.glob("*.jsonl"))
            if t.stem != skip_session
            for lead in transcript_leads(t, f"{d.name}/{t.stem[:8]}", terms)
        ),
        known={lead.text for lead in memory},
    )
    return memory, transcripts


def per_source(leads: Iterable[Lead], cap: int) -> list[Lead]:
    """Return `leads` in their order, with at most `cap` from one source."""
    counts: Counter[str] = Counter()
    kept = []
    for lead in leads:
        counts[lead.source] += 1
        if counts[lead.source] <= cap:
            kept.append(lead)
    return kept


def render_leads(leads: list[Lead]) -> list[str]:
    return [
        f"{lead.day or '(no date)'}  {lead.source}  {' '.join(lead.mrs)}\n  {lead.text}"
        for lead in leads
    ]


def render(
    memory: list[Lead], transcripts: list[Lead], limit: int, per_session: int
) -> str:
    """Return every memory lead, then the newest transcript leads."""
    shown = per_source(transcripts, per_session)[:limit]
    sessions = len({lead.source for lead in transcripts})
    return "\n\n".join(
        [
            f"## Memory leads: {len(memory)}",
            *render_leads(memory),
            f"## Transcript leads: {len(transcripts)} in {sessions} sessions, "
            f"{len(shown)} shown (newest first, at most {per_session} a session)",
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
    p.add_argument(
        "--per-session",
        type=int,
        default=DEFAULT_PER_SESSION,
        help="show at most this many transcript leads from one session "
        f"(default {DEFAULT_PER_SESSION})",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_dir = Path(config).expanduser() if config else Path.home() / ".claude"
    try:
        repo = slackwrite.current_repo()
        terms = Terms.parse(args.groups, repo)
        dirs = project_dirs(main_worktree(), repo, claude_dir / "projects")
        memory, transcripts = find_leads(
            dirs, terms, os.environ.get("CLAUDE_CODE_SESSION_ID")
        )
    except (LeadsError, slackwrite.SlackwriteError) as exc:
        print(f"find_leads: error: {exc}", file=sys.stderr)
        return 1
    print(render(memory, transcripts, args.limit, args.per_session))
    return 0


if __name__ == "__main__":
    sys.exit(main())
