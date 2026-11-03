#!/usr/bin/env python3
"""Draft Slack messages from repo data and copy them to the clipboard.

The script gathers the data, fills in a fixed template, writes the draft to a
file, and copies it with the slackcopy script beside it. slackcopy puts rich
text on the clipboard, so Slack's composer keeps the links and the emphasis.
Nothing is sent to Slack.

Types (subcommands):

    mr    ask for review of the open MR for the current branch, or with
          --mine, of every open, non-draft MR you authored in the repo

Here "MR" means a GitLab merge request or a GitHub pull request. The message
uses the forge's own words: "MR !12" on GitLab, "PR #12" on GitHub.

The repo comes from the upstream of the current branch: the URL of its remote
gives the host and the project path. A remote on github.com is a GitHub repo,
and `gh` supplies the data and the auth. Any other host is a GitLab instance,
and `glab` supplies them.

An `mr` message states the merge order that the forge records. An MR is
stacked on an open MR whose source branch it targets, unless that branch is a
base branch (see is_base and gh_is_base). On GitLab, an MR is also blocked by
each unmerged MR that GitLab lists as its dependency. GitHub has no such list.
--after adds an order that the forge does not record, and --note adds a
sentence, such as the reason for that order. The list of MRs follows the
merge order: by MR number, with each MR moved after the listed MRs that it
waits for.

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

# The kinds of merge order, and how a sentence words each: about one of many
# MRs, and in the message about one MR.
STACKED = "stacked"
BLOCKED = "blocked"
AFTER = "after"
ORDER_VERBS = {
    STACKED: ("is stacked on", "Stacked on"),
    BLOCKED: ("is blocked by", "Blocked by"),
    AFTER: ("merges after", "Merges after"),
}
# An --after value: "13:12", "!13:!12", or "#13:#12", for "13 merges after 12".
_AFTER = re.compile(r"^[!#]?(?P<mr>\d+):[!#]?(?P<on>\d+)$")
# The MR number at the end of an MR's URL.
_URL_IID = re.compile(r"/\d+$")


# --- templates ---------------------------------------------------------------
# The trailing "  " before \n is a Markdown hard line break. Keeping it inside
# the quotes protects it from editors that strip trailing whitespace.

MR_ONE = "{repo}: [{noun} {label}]({url})  \n{title}{jira}  \n{notes}_Please review_"
MR_ONE_NOTE = "{text}  \n"
MR_MANY = "{repo} {noun}s:\n\n{lines}\n\n{notes}_Please review_"
MR_MANY_NOTE = "{text}\n\n"
MR_LINE = "- [{label}]({url}) {title}{jira}"
JIRA_REF = " ([{key}]({url}))"
MR_REF = "[{label}]({url})"
ORDER_START = "Start with {refs}."
ORDER_ONE = "{verb} {on}."
ORDER_MANY = "{mr} {verb} {on}."


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


# --- GitHub GraphQL ----------------------------------------------------------
# GitHub has no MR dependencies, so a PR records its merge order only in its
# base branch. `associatedPullRequests` of a ref lists the PRs with that ref
# as their head branch, so one query also finds each PR's stack parents.

_GH_FRAGMENTS = """
fragment pr on PullRequest {
  number url title isDraft headRefName baseRefName
  baseRef {
    branchProtectionRule { id }
    rules(first: 100) { nodes { type } }
    associatedPullRequests(states: OPEN, first: 100) {
      nodes { number url repository { nameWithOwner } }
    }
  }
}
fragment base on Repository {
  defaultBranchRef { name }
}
"""
GH_QUERY_MINE = (
    """
query($owner: String!, $name: String!, $search: String!) {
  repository(owner: $owner, name: $name) { ...base }
  search(query: $search, type: ISSUE, first: 100) { nodes { ...pr } }
}
"""
    + _GH_FRAGMENTS
)
GH_QUERY_BRANCH = (
    """
query($owner: String!, $name: String!, $branch: String!) {
  repository(owner: $owner, name: $name) {
    ...base
    pullRequests(states: OPEN, headRefName: $branch, first: 100) {
      nodes { ...pr }
    }
  }
}
"""
    + _GH_FRAGMENTS
)
# A GitHub search for your open, non-draft PRs in one repo.
GH_SEARCH_MINE = "repo:{path} is:pr is:open draft:false author:@me"
# The ruleset rule that makes a branch take changes only through a PR.
GH_PR_RULE = "PULL_REQUEST"


class SlackwriteError(RuntimeError):
    """A step failed. The message says which step and why."""


@dataclass(frozen=True)
class Forge:
    """A code host, and the words that its MRs go by."""

    cli: str  # the CLI that supplies the data and the auth
    noun: str  # what the forge calls an MR
    sigil: str  # what goes before an MR number in a reference
    mr_path: str  # what goes between the project URL and an MR number


GITLAB = Forge("glab", "MR", "!", "-/merge_requests/")
GITHUB = Forge("gh", "PR", "#", "pull/")
# The host of a GitHub remote. A remote on any other host is a GitLab instance.
GITHUB_HOST = "github.com"


@dataclass(frozen=True)
class Repo:
    root: Path  # the top of the working tree
    host: str
    path: str  # the project path: group/subgroup/name, or owner/name on GitHub
    branch: str  # the upstream branch, as the remote names it

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def forge(self) -> Forge:
        return GITHUB if self.host == GITHUB_HOST else GITLAB


@dataclass(frozen=True)
class Ref:
    label: str  # "!12" or "#12", or "group/project!12" for another project's MR
    url: str

    def link(self) -> str:
        return MR_REF.format(label=self.label, url=self.url)


@dataclass(frozen=True)
class MR:
    forge: Forge
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
        return Ref(f"{self.forge.sigil}{self.iid}", self.url)


@dataclass(frozen=True)
class Dep:
    """`mr` waits for `on` to merge."""

    mr: Ref
    kind: str  # STACKED, BLOCKED, or AFTER
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
    """Run a query through the CLI of the repo's forge, and return its data."""
    # glab and gh exit non-zero when the response has errors.
    out = run(
        repo.forge.cli, "api", "graphql", "--hostname", repo.host, "--input", "-",
        stdin=json.dumps({"query": query, "variables": variables}),
    )  # fmt: skip
    return json.loads(out)["data"]


def split_title(title: str) -> tuple[str, str | None]:
    """Split the ticket key off the end of an MR title, if the title has one."""
    m = _TITLE_KEY.match(title)
    return (m["title"], m["key"]) if m else (title, None)


def found(repo: Repo, mrs: list[MR], mine: bool) -> list[MR]:
    """Return `mrs` by MR number, if they suit the request; else raise."""
    mrs = sorted(mrs, key=lambda mr: mr.iid)
    noun = repo.forge.noun
    if mine and not mrs:
        raise SlackwriteError(f"you have no open, non-draft {noun}s in {repo.path}")
    if not mrs:
        raise SlackwriteError(
            f"no open {noun} for branch {repo.branch!r} in {repo.path}"
        )
    if not mine and len(mrs) > 1:
        refs = ", ".join(mr.ref.label for mr in mrs)
        raise SlackwriteError(
            f"branch {repo.branch!r} has more than one open {noun}: {refs}"
        )
    return mrs


# --- GitLab ------------------------------------------------------------------


def gitlab_graphql(repo: Repo, query: str, **variables: object) -> dict:
    """Run a query that reads the repo's GitLab project, and return its data."""
    data = graphql(repo, query, **variables)
    # GitLab answers null, not an error, for a project that you cannot see.
    if data["project"] is None:
        raise SlackwriteError(f"no project {repo.path} on {repo.host} that you can see")
    return data


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
        GITLAB, int(node["iid"]), node["webUrl"], title, key, node["draft"],
        node["sourceBranch"], node["targetBranch"], tuple(blockers),
    )  # fmt: skip


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


def gitlab_mrs(repo: Repo, mine: bool) -> tuple[list[MR], dict[str, list[Ref]]]:
    """Return the MRs by MR number, and their stack parents by source branch.

    The MRs are the open MR for the upstream branch, or with `mine`, your
    open, non-draft MRs.
    """
    if mine:
        data = gitlab_graphql(repo, QUERY_MINE, path=repo.path, projectPath=repo.path)
        nodes = data["currentUser"]["authoredMergeRequests"]["nodes"]
    else:
        data = gitlab_graphql(repo, QUERY_BRANCH, path=repo.path, branch=repo.branch)
        nodes = data["project"]["mergeRequests"]["nodes"]
    mrs = found(repo, [parse_mr(node, repo) for node in nodes], mine)
    return mrs, stack_parents(repo, mrs, base_branches(data["project"]))


def stack_parents(
    repo: Repo, mrs: list[MR], base: Sequence[str]
) -> dict[str, list[Ref]]:
    """Return, by source branch, the open MRs whose source branch an MR targets."""
    branches = sorted({mr.target for mr in mrs if not is_base(mr.target, base)})
    if not branches:
        return {}
    data = gitlab_graphql(repo, QUERY_PARENTS, path=repo.path, branches=branches)
    parents: dict[str, list[Ref]] = {}
    for node in data["project"]["mergeRequests"]["nodes"]:
        ref = Ref(f"!{node['iid']}", node["webUrl"])
        parents.setdefault(node["sourceBranch"], []).append(ref)
    return parents


# --- GitHub ------------------------------------------------------------------


def parse_pr(node: dict) -> MR:
    """Return the PR in a GraphQL `pr` fragment."""
    title, key = split_title(node["title"])
    return MR(
        GITHUB, node["number"], node["url"], title, key, node["isDraft"],
        node["headRefName"], node["baseRefName"],
    )  # fmt: skip


def gh_is_base(branch: str, ref: dict, default: str) -> bool:
    """Tell whether a PR's base branch is a base branch.

    A base branch is the default branch, a branch with a classic protection
    rule, or a branch where a ruleset requires a PR. Other ruleset rules do not
    count, because a ruleset such as "require signed commits" can apply to
    every branch.
    """
    return (
        branch == default
        or ref["branchProtectionRule"] is not None
        or any(rule["type"] == GH_PR_RULE for rule in ref["rules"]["nodes"])
    )


def gh_parents(node: dict, repo: Repo, default: str) -> list[Ref]:
    """Return the open PRs whose head branch is the base branch of a PR."""
    ref = node["baseRef"]
    # A PR whose base branch was deleted has no base ref.
    if ref is None or gh_is_base(node["baseRefName"], ref, default):
        return []
    parents = []
    for p in ref["associatedPullRequests"]["nodes"]:
        path = p["repository"]["nameWithOwner"]
        label = f"#{p['number']}" if path == repo.path else f"{path}#{p['number']}"
        parents.append(Ref(label, p["url"]))
    return parents


def github_prs(repo: Repo, mine: bool) -> tuple[list[MR], dict[str, list[Ref]]]:
    """Return the PRs by PR number, and their stack parents by head branch.

    The PRs are the open PR for the upstream branch, or with `mine`, your
    open, non-draft PRs.
    """
    owner, _, name = repo.path.partition("/")
    if mine:
        search = GH_SEARCH_MINE.format(path=repo.path)
        data = graphql(repo, GH_QUERY_MINE, owner=owner, name=name, search=search)
        nodes = data["search"]["nodes"]
    else:
        data = graphql(
            repo, GH_QUERY_BRANCH, owner=owner, name=name, branch=repo.branch
        )
        nodes = data["repository"]["pullRequests"]["nodes"]
    mrs = found(repo, [parse_pr(node) for node in nodes], mine)
    default = data["repository"]["defaultBranchRef"]["name"]
    parents = {}
    for node in nodes:
        if refs := gh_parents(node, repo, default):
            parents[node["baseRefName"]] = refs
    return mrs, parents


# --- merge order -------------------------------------------------------------


def dependencies(mrs: list[MR], parents: dict[str, list[Ref]]) -> list[Dep]:
    """Return the merge order that the forge records for `mrs`, in their order."""
    deps = []
    for mr in mrs:
        stacked = parents.get(mr.target, [])
        deps += [Dep(mr.ref, STACKED, parent) for parent in stacked]
        # A blocker that is also the stack parent adds nothing.
        deps += [Dep(mr.ref, BLOCKED, b) for b in mr.blockers if b not in stacked]
    return deps


def after_deps(
    mrs: list[MR], deps: list[Dep], pairs: Sequence[tuple[int, int]]
) -> list[Dep]:
    """Return a Dep for each --after pair that `deps` does not already state."""
    listed = {mr.iid: mr.ref for mr in mrs}
    sigil = mrs[0].forge.sigil
    stated = {(d.mr, d.on) for d in deps}
    added = []
    for iid, on_iid in pairs:
        if iid not in listed:
            raise SlackwriteError(f"--after {iid}:{on_iid}: {sigil}{iid} is not listed")
        # An MR outside the message gets a URL in the shape of a listed MR's.
        on = listed.get(on_iid) or Ref(
            f"{sigil}{on_iid}", _URL_IID.sub(f"/{on_iid}", mrs[0].url)
        )
        if (listed[iid], on) in stated:
            warn(f"--after {iid}:{on_iid}: the message already states this order")
            continue
        stated.add((listed[iid], on))
        added.append(Dep(listed[iid], AFTER, on))
    return added


def merge_order(mrs: list[MR], deps: list[Dep]) -> list[MR]:
    """Return `mrs` by MR number, each moved after the listed MRs it waits for."""
    by_ref = {mr.ref: mr for mr in mrs}
    waits: dict[Ref, list[Ref]] = {mr.ref: [] for mr in mrs}
    for d in deps:
        if d.on in by_ref:
            waits[d.mr].append(d.on)
    ordered: list[MR] = []
    visiting: set[Ref] = set()

    def place(ref: Ref) -> None:
        visiting.add(ref)
        for on in sorted(waits[ref], key=lambda r: by_ref[r].iid):
            if on in visiting:
                warn(
                    f"the merge order has a cycle at {ref.label} -> {on.label}; "
                    "the list ignores that step"
                )
            elif by_ref[on] not in ordered:
                place(on)
        visiting.discard(ref)
        ordered.append(by_ref[ref])

    for mr in mrs:
        if mr not in ordered:
            place(mr.ref)
    return ordered


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
    """Return the sentences that state `deps`, in the order of `mrs`.

    Return "" when there are no deps.
    """
    if len(mrs) == 1:
        return " ".join(
            ORDER_ONE.format(verb=ORDER_VERBS[d.kind][1], on=d.on.link()) for d in deps
        )
    position = {mr.ref: i for i, mr in enumerate(mrs)}
    waiting = {d.mr for d in deps}
    waited_on = {d.on for d in deps}
    starts = [mr.ref for mr in mrs if mr.ref in waited_on and mr.ref not in waiting]
    sentences = []
    if starts:
        sentences.append(ORDER_START.format(refs=", ".join(r.link() for r in starts)))
    sentences += [
        ORDER_MANY.format(mr=d.mr.link(), verb=ORDER_VERBS[d.kind][0], on=d.on.link())
        for d in sorted(deps, key=lambda d: position[d.mr])
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
        return {"label": mr.ref.label, "url": mr.url, "title": mr.title, "jira": jira}

    noun = mrs[0].forge.noun
    if len(mrs) == 1:
        text = "".join(MR_ONE_NOTE.format(text=n) for n in notes)
        return MR_ONE.format(repo=repo_name, noun=noun, notes=text, **fields(mrs[0]))
    lines = "\n".join(MR_LINE.format(**fields(mr)) for mr in mrs)
    text = "".join(MR_MANY_NOTE.format(text=n) for n in notes)
    return MR_MANY.format(repo=repo_name, noun=noun, lines=lines, notes=text)


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
    gather = github_prs if repo.forge is GITHUB else gitlab_mrs
    mrs, parents = gather(repo, args.mine)
    for mr in mrs:
        if mr.draft:
            warn(f"{mr.ref.label} is a draft")
    deps = dependencies(mrs, parents)
    deps += after_deps(mrs, deps, args.after)
    mrs = merge_order(mrs, deps)
    notes = [n for n in (render_order(mrs, deps), (args.note or "").strip()) if n]
    text = render_mr(repo.name, mrs, jira_site(), notes)
    key = mrs[0].key if len(mrs) == 1 else None
    deliver(text, draft_path(repo.root, "mr", key))
    return 0


def after_pair(text: str) -> tuple[int, int]:
    """Parse an --after value into the MR that waits and the MR it waits for."""
    m = _AFTER.match(text)
    pair = (int(m["mr"]), int(m["on"])) if m else None
    if pair is None or pair[0] == pair[1]:
        raise argparse.ArgumentTypeError(
            f"expected A:B with two different MR numbers, such as 13:12; got {text!r}"
        )
    return pair


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
        "--after",
        action="append",
        default=[],
        type=after_pair,
        metavar="A:B",
        help="MR A merges after MR B, an order that the forge does not record; "
        "A must be in the message (repeatable)",
    )
    sp_mr.add_argument(
        "--note",
        metavar="TEXT",
        help="a sentence to add above the closing request, such as the reason "
        "for an --after order",
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
