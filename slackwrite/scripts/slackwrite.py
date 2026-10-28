#!/usr/bin/env python3
"""Draft Slack messages from repo data and copy them to the clipboard.

The script gathers the data, fills in a fixed template, writes the draft to a
file, and copies it with the slackcopy script beside it. slackcopy puts rich
text on the clipboard, so Slack's composer keeps the links and the emphasis.
Nothing is sent to Slack.

Types (subcommands):

    mr    ask for review of the open MR for the current branch, or with
          --mine, of every open, non-draft MR you authored in the repo

The repo comes from the upstream of the current branch: the URL of its remote
gives the GitLab host and project path. `glab` supplies the MR data and auth.

The draft goes to tmp/<title>_<KEY>/slack-<type>.md when the message is about
one ticket and that ticket directory exists, else to
tmp/slackwrite/slack-<type>.md. Each run replaces the file.

Jira links use `site` from the jira-ticket config: $JIRA_TICKET_CONFIG, then
~/.claude/jira-ticket.toml.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

SLACKCOPY = Path(__file__).resolve().parent / "slackcopy"

# The same lookup as config_path() in jira-ticket/scripts/jira_ticket.py. This
# script reads only `site` from that file.
JIRA_CONFIG_ENV = "JIRA_TICKET_CONFIG"
DEFAULT_JIRA_CONFIG = Path.home() / ".claude" / "jira-ticket.toml"

# The host and project path in an SSH (git@host:path) or URL
# (scheme://host/path) remote.
_REMOTE = re.compile(
    r"^(?:[\w.+-]+@(?P<scp_host>[\w.-]+):"
    r"|[a-z+]+://(?:[^@/]+@)?(?P<url_host>[\w.-]+)(?::\d+)?/)"
    r"(?P<path>.+?)(?:\.git)?/?$"
)
# A ticket key in parentheses at the end of an MR title: "feat(x): y (ACME-1)".
_TITLE_KEY = re.compile(r"^(?P<title>.*?)\s*\((?P<key>[A-Z][A-Z0-9_]*-\d+)\)\s*$")


# --- templates ---------------------------------------------------------------
# The trailing "  " before \n is a Markdown hard line break. Keeping it inside
# the quotes protects it from editors that strip trailing whitespace.

MR_ONE = "{repo}: [MR !{iid}]({url})  \n{title}{jira}  \n_Please review_"
MR_MANY = "{repo} MRs:\n\n{lines}\n\n_Please review_"
MR_LINE = "- [!{iid}]({url}) {title}{jira}"
JIRA_REF = " ([{key}]({url}))"


class SlackwriteError(RuntimeError):
    """A step failed. The message says which step and why."""


@dataclass(frozen=True)
class Repo:
    root: Path  # the top of the working tree
    host: str
    path: str  # the GitLab project path: group/subgroup/name
    branch: str  # the upstream branch, as the remote names it

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class MR:
    iid: int
    url: str
    title: str  # without the ticket key
    key: str | None
    draft: bool


# --- gathering ---------------------------------------------------------------


def run(*cmd: str) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise SlackwriteError(f"{cmd[0]} is not installed") from exc
    if proc.returncode != 0:
        raise SlackwriteError(f"`{' '.join(cmd)}` failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def parse_remote(url: str) -> tuple[str, str]:
    """Return the host and project path of a git remote URL."""
    m = _REMOTE.match(url)
    if not m:
        raise SlackwriteError(f"cannot read a host and project path from {url!r}")
    return m["scp_host"] or m["url_host"], m["path"]


def current_repo() -> Repo:
    root = Path(run("git", "rev-parse", "--show-toplevel"))
    local = run("git", "symbolic-ref", "--short", "HEAD")
    try:
        remote = run("git", "config", "--get", f"branch.{local}.remote")
        merge = run("git", "config", "--get", f"branch.{local}.merge")
    except SlackwriteError as exc:
        raise SlackwriteError(
            f"branch {local!r} has no upstream; push it with `git push -u`"
        ) from exc
    host, path = parse_remote(run("git", "remote", "get-url", remote))
    return Repo(root, host, path, merge.removeprefix("refs/heads/"))


def split_title(title: str) -> tuple[str, str | None]:
    """Split the ticket key off the end of an MR title, if the title has one."""
    m = _TITLE_KEY.match(title)
    return (m["title"], m["key"]) if m else (title, None)


def list_mrs(repo: Repo, *filters: str) -> list[MR]:
    """Return the repo's open MRs that pass the glab filters, by MR number."""
    # `glab mr list` returns only open MRs unless a state flag says otherwise.
    out = run(
        "glab", "mr", "list",
        "--repo", f"{repo.host}/{repo.path}",
        "--per-page", "100",
        "--output", "json",
        *filters,
    )  # fmt: skip
    mrs = []
    for data in json.loads(out):
        title, key = split_title(data["title"])
        mrs.append(MR(data["iid"], data["web_url"], title, key, data["draft"]))
    return sorted(mrs, key=lambda mr: mr.iid)


def branch_mr(repo: Repo) -> MR:
    mrs = list_mrs(repo, "--source-branch", repo.branch)
    if not mrs:
        raise SlackwriteError(f"no open MR for branch {repo.branch!r} in {repo.path}")
    if len(mrs) > 1:
        iids = ", ".join(f"!{mr.iid}" for mr in mrs)
        raise SlackwriteError(
            f"branch {repo.branch!r} has more than one open MR: {iids}"
        )
    return mrs[0]


def my_mrs(repo: Repo) -> list[MR]:
    mrs = list_mrs(repo, "--author", "@me", "--not-draft")
    if not mrs:
        raise SlackwriteError(f"you have no open, non-draft MRs in {repo.path}")
    return mrs


def jira_site() -> str:
    override = os.environ.get(JIRA_CONFIG_ENV)
    path = Path(override).expanduser() if override else DEFAULT_JIRA_CONFIG
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise SlackwriteError(f"cannot read the Jira site from {path}: {exc}") from exc
    site = data.get("site")
    if not isinstance(site, str) or not site.strip():
        raise SlackwriteError(f"{path}: 'site' must be a non-empty string")
    return site.strip()


# --- rendering ---------------------------------------------------------------


def render_mr(repo_name: str, mrs: list[MR], site: str) -> str:
    """Return the review request for one or more MRs of one repo."""

    def fields(mr: MR) -> dict[str, object]:
        jira = ""
        if mr.key:
            jira = JIRA_REF.format(key=mr.key, url=f"https://{site}/browse/{mr.key}")
        return {"iid": mr.iid, "url": mr.url, "title": mr.title, "jira": jira}

    if len(mrs) == 1:
        return MR_ONE.format(repo=repo_name, **fields(mrs[0]))
    lines = "\n".join(MR_LINE.format(**fields(mr)) for mr in mrs)
    return MR_MANY.format(repo=repo_name, lines=lines)


def draft_path(root: Path, kind: str, key: str | None) -> Path:
    """Return the draft file: in the ticket directory for `key`, if one exists."""
    name = f"slack-{kind}.md"
    if key:
        dirs = sorted(p for p in (root / "tmp").glob(f"*_{key}") if p.is_dir())
        if len(dirs) > 1:
            names = ", ".join(str(d) for d in dirs)
            raise SlackwriteError(f"more than one ticket directory for {key}: {names}")
        if dirs:
            return dirs[0] / name
    return root / "tmp" / "slackwrite" / name


def deliver(text: str, path: Path) -> None:
    """Write the draft, print it, and copy it to the clipboard."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nslackwrite: wrote {path}")
    if subprocess.run([str(SLACKCOPY), str(path)], check=False).returncode != 0:
        raise SlackwriteError(
            "slackcopy failed; the draft is written, the clipboard is not"
        )


# --- commands ----------------------------------------------------------------


def cmd_mr(args: argparse.Namespace) -> int:
    repo = current_repo()
    mrs = my_mrs(repo) if args.mine else [branch_mr(repo)]
    for mr in mrs:
        if mr.draft:
            print(f"slackwrite: warning: !{mr.iid} is a draft", file=sys.stderr)
    text = render_mr(repo.name, mrs, jira_site())
    key = mrs[0].key if len(mrs) == 1 else None
    deliver(text, draft_path(repo.root, "mr", key))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="type", required=True, metavar="TYPE")

    sp_mr = sub.add_parser("mr", help="ask for review of open MRs")
    sp_mr.add_argument(
        "--mine",
        action="store_true",
        help="every open, non-draft MR you authored in the repo, "
        "not only the MR for the current branch",
    )
    sp_mr.set_defaults(func=cmd_mr)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except SlackwriteError as exc:
        print(f"slackwrite: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
