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

An `mr` message states the merge order that GitLab records. An MR is stacked
on an open MR whose source branch it targets, unless that branch is the
default branch or a protected branch. An MR is blocked by each unmerged MR
that GitLab lists as its dependency. --note adds a sentence for an order that
GitLab does not record.

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
from collections.abc import Sequence
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

# The kinds of merge order, as the message words them.
STACKED = "stacked on"
BLOCKED = "blocked by"


# --- templates ---------------------------------------------------------------
# The trailing "  " before \n is a Markdown hard line break. Keeping it inside
# the quotes protects it from editors that strip trailing whitespace.

MR_ONE = "{repo}: [MR !{iid}]({url})  \n{title}{jira}  \n{notes}_Please review_"
MR_ONE_NOTE = "{text}  \n"
MR_MANY = "{repo} MRs:\n\n{lines}\n\n{notes}_Please review_"
MR_MANY_NOTE = "{text}\n\n"
MR_LINE = "- [!{iid}]({url}) {title}{jira}"
JIRA_REF = " ([{key}]({url}))"
MR_REF = "[{label}]({url})"
ORDER_START = "Start with {refs}."
ORDER_ONE = "{kind} {on}."
ORDER_MANY = "{mr} is {kind} {on}."


# --- GitLab GraphQL ----------------------------------------------------------
# `blockingMergeRequests` holds GitLab's MR dependencies. Its `hiddenCount`
# counts the blockers that the user cannot see.

_FRAGMENTS = """
fragment mr on MergeRequest {
  iid webUrl title draft sourceBranch targetBranch
  blockingMergeRequests {
    hiddenCount
    visibleMergeRequests { iid webUrl state project { fullPath } }
  }
}
fragment base on Project {
  repository { rootRef }
  branchRules { nodes { name isProtected } }
}
"""
# The project path goes in twice because the two fields type it differently.
QUERY_MINE = (
    """
query($path: ID!, $projectPath: String!) {
  project(fullPath: $path) { ...base }
  currentUser {
    authoredMergeRequests(
      projectPath: $projectPath, state: opened, draft: false, first: 100
    ) { nodes { ...mr } }
  }
}
"""
    + _FRAGMENTS
)
QUERY_BRANCH = (
    """
query($path: ID!, $branch: String!) {
  project(fullPath: $path) {
    ...base
    mergeRequests(state: opened, sourceBranches: [$branch], first: 100) {
      nodes { ...mr }
    }
  }
}
"""
    + _FRAGMENTS
)
QUERY_PARENTS = """
query($path: ID!, $branches: [String!]) {
  project(fullPath: $path) {
    mergeRequests(state: opened, sourceBranches: $branches, first: 100) {
      nodes { iid webUrl sourceBranch }
    }
  }
}
"""


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
class Ref:
    label: str  # "!12", or "group/project!12" for an MR of another project
    url: str

    def link(self) -> str:
        return MR_REF.format(label=self.label, url=self.url)


@dataclass(frozen=True)
class MR:
    iid: int
    url: str
    title: str  # without the ticket key
    key: str | None
    draft: bool
    source: str  # the source branch
    target: str  # the target branch
    blockers: tuple[Ref, ...] = ()  # the unmerged MRs that GitLab says block it

    @property
    def ref(self) -> Ref:
        return Ref(f"!{self.iid}", self.url)


@dataclass(frozen=True)
class Dep:
    """`mr` waits for `on` to merge."""

    mr: Ref
    kind: str  # STACKED or BLOCKED
    on: Ref


# --- gathering ---------------------------------------------------------------


def run(*cmd: str, stdin: str | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise SlackwriteError(f"{cmd[0]} is not installed") from exc
    if proc.returncode != 0:
        raise SlackwriteError(f"`{' '.join(cmd)}` failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def warn(message: str) -> None:
    print(f"slackwrite: warning: {message}", file=sys.stderr)


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


def graphql(repo: Repo, query: str, **variables: object) -> dict:
    """Run a query that reads the repo's project, and return its data."""
    # glab exits non-zero when the response has errors.
    out = run(
        "glab", "api", "graphql", "--hostname", repo.host, "--input", "-",
        stdin=json.dumps({"query": query, "variables": variables}),
    )  # fmt: skip
    data = json.loads(out)["data"]
    if data["project"] is None:
        raise SlackwriteError(f"no project {repo.path} on {repo.host} that you can see")
    return data


def split_title(title: str) -> tuple[str, str | None]:
    """Split the ticket key off the end of an MR title, if the title has one."""
    m = _TITLE_KEY.match(title)
    return (m["title"], m["key"]) if m else (title, None)


def parse_mr(node: dict, repo: Repo) -> MR:
    """Return the MR in a GraphQL `mr` fragment."""
    title, key = split_title(node["title"])
    blocking = node["blockingMergeRequests"]
    if blocking["hiddenCount"]:
        warn(
            f"!{node['iid']} has {blocking['hiddenCount']} blocking MRs "
            "that you cannot see"
        )
    blockers = []
    for b in blocking["visibleMergeRequests"]:
        if b["state"] == "merged":
            continue
        path = b["project"]["fullPath"]
        label = f"!{b['iid']}" if path == repo.path else f"{path}!{b['iid']}"
        blockers.append(Ref(label, b["webUrl"]))
    return MR(
        int(node["iid"]), node["webUrl"], title, key, node["draft"],
        node["sourceBranch"], node["targetBranch"], tuple(blockers),
    )  # fmt: skip


def by_number(nodes: list[dict], repo: Repo) -> list[MR]:
    return sorted((parse_mr(node, repo) for node in nodes), key=lambda mr: mr.iid)


def base_branches(project: dict) -> list[str]:
    """Return the default branch and the protected-branch patterns."""
    rules = project["branchRules"]["nodes"]
    return [
        project["repository"]["rootRef"],
        *(rule["name"] for rule in rules if rule["isProtected"]),
    ]


def is_base(branch: str, patterns: Sequence[str]) -> bool:
    """Tell whether `branch` is the default branch or a protected branch."""
    # GitLab's only wildcard is `*`, which matches any characters, `/` too.
    return any(
        re.fullmatch(re.escape(p).replace(r"\*", ".*"), branch) for p in patterns
    )


def branch_mr(repo: Repo) -> tuple[list[MR], list[str]]:
    """Return the open MR for the upstream branch, and the base branches."""
    project = graphql(repo, QUERY_BRANCH, path=repo.path, branch=repo.branch)["project"]
    mrs = by_number(project["mergeRequests"]["nodes"], repo)
    if not mrs:
        raise SlackwriteError(f"no open MR for branch {repo.branch!r} in {repo.path}")
    if len(mrs) > 1:
        iids = ", ".join(f"!{mr.iid}" for mr in mrs)
        raise SlackwriteError(
            f"branch {repo.branch!r} has more than one open MR: {iids}"
        )
    return mrs, base_branches(project)


def my_mrs(repo: Repo) -> tuple[list[MR], list[str]]:
    """Return your open, non-draft MRs by MR number, and the base branches."""
    data = graphql(repo, QUERY_MINE, path=repo.path, projectPath=repo.path)
    mrs = by_number(data["currentUser"]["authoredMergeRequests"]["nodes"], repo)
    if not mrs:
        raise SlackwriteError(f"you have no open, non-draft MRs in {repo.path}")
    return mrs, base_branches(data["project"])


def stack_parents(
    repo: Repo, mrs: list[MR], base: Sequence[str]
) -> dict[str, list[Ref]]:
    """Return, by source branch, the open MRs whose source branch an MR targets."""
    branches = sorted({mr.target for mr in mrs if not is_base(mr.target, base)})
    if not branches:
        return {}
    data = graphql(repo, QUERY_PARENTS, path=repo.path, branches=branches)
    parents: dict[str, list[Ref]] = {}
    for node in data["project"]["mergeRequests"]["nodes"]:
        ref = Ref(f"!{node['iid']}", node["webUrl"])
        parents.setdefault(node["sourceBranch"], []).append(ref)
    return parents


def dependencies(mrs: list[MR], parents: dict[str, list[Ref]]) -> list[Dep]:
    """Return the merge order that GitLab records for `mrs`, in their order."""
    deps = []
    for mr in mrs:
        stacked = parents.get(mr.target, [])
        deps += [Dep(mr.ref, STACKED, parent) for parent in stacked]
        # A blocker that is also the stack parent adds nothing.
        deps += [Dep(mr.ref, BLOCKED, b) for b in mr.blockers if b not in stacked]
    return deps


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


def render_order(mrs: list[MR], deps: list[Dep]) -> str:
    """Return the sentences that state `deps`, or "" when there are none."""
    if len(mrs) == 1:
        return " ".join(
            ORDER_ONE.format(kind=d.kind.capitalize(), on=d.on.link()) for d in deps
        )
    waiting = {d.mr for d in deps}
    waited_on = {d.on for d in deps}
    starts = [mr.ref for mr in mrs if mr.ref in waited_on and mr.ref not in waiting]
    sentences = []
    if starts:
        sentences.append(ORDER_START.format(refs=", ".join(r.link() for r in starts)))
    sentences += [
        ORDER_MANY.format(mr=d.mr.link(), kind=d.kind, on=d.on.link()) for d in deps
    ]
    return " ".join(sentences)


def render_mr(
    repo_name: str, mrs: list[MR], site: str, notes: Sequence[str] = ()
) -> str:
    """Return the review request for one or more MRs of one repo.

    Each note goes above the closing request: on its own line for one MR, as
    its own paragraph for more.
    """

    def fields(mr: MR) -> dict[str, object]:
        jira = ""
        if mr.key:
            jira = JIRA_REF.format(key=mr.key, url=f"https://{site}/browse/{mr.key}")
        return {"iid": mr.iid, "url": mr.url, "title": mr.title, "jira": jira}

    if len(mrs) == 1:
        text = "".join(MR_ONE_NOTE.format(text=n) for n in notes)
        return MR_ONE.format(repo=repo_name, notes=text, **fields(mrs[0]))
    lines = "\n".join(MR_LINE.format(**fields(mr)) for mr in mrs)
    text = "".join(MR_MANY_NOTE.format(text=n) for n in notes)
    return MR_MANY.format(repo=repo_name, lines=lines, notes=text)


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
    mrs, base = my_mrs(repo) if args.mine else branch_mr(repo)
    for mr in mrs:
        if mr.draft:
            warn(f"!{mr.iid} is a draft")
    deps = dependencies(mrs, stack_parents(repo, mrs, base))
    notes = [n for n in (render_order(mrs, deps), (args.note or "").strip()) if n]
    text = render_mr(repo.name, mrs, jira_site(), notes)
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
    sp_mr.add_argument(
        "--note",
        metavar="TEXT",
        help="a sentence to add above the closing request, such as a merge "
        "order that GitLab does not record",
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
