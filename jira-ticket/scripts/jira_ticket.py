#!/usr/bin/env python3
"""Create, update, transition, and comment on Jira tickets through `acli`.

Drafts are Markdown files. A ticket draft has the summary on its first line as
`# Summary`, then a blank line, then the description. A comment draft is
Markdown with no title line. This script converts the Markdown to ADF (see
md_to_adf.py), fills in the profile defaults, and calls `acli jira workitem`.

Subcommands:

    defaults     print the profile that applies, and why
    create       create a ticket from a draft
    update       replace the summary and description from a draft, change the
                 type, or both
    transition   move a ticket to a status on the configured shortlist
    comment      add a comment from a draft

Every write subcommand takes --dry-run, which prints the plan and sends nothing.

Config, in order: $JIRA_TICKET_CONFIG, then ~/.claude/jira-ticket.toml. It
lives outside the skill directory because the skill is shareable and the
site, project, and epic keys are not. See examples/jira-ticket.example.toml.

A profile carries the defaults for a kind of work: project, type, assignee,
parent epic, and labels. The profile is chosen in this order:

1. --profile on the command line
2. the profile whose `gitlab_paths` match the git remote of the repo, by
   whole path segments (the longest match wins)
3. `default_profile`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import md_to_adf

DEFAULT_CONFIG = Path.home() / ".claude" / "jira-ticket.toml"

_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_LABEL = re.compile(r"^\S+$")
_TITLE = re.compile(r"^# (?P<title>\S.*?)[ \t]*$")
# A placeholder key such as PROJ-XXXXX, for a ticket that does not exist yet.
_PLACEHOLDER = re.compile(r"\b[A-Z][A-Z0-9_]*-X{3,}\b")
# The project path in an SSH (git@host:path) or URL (scheme://host/path) remote.
_REMOTE = re.compile(
    r"^(?:[\w.+-]+@[\w.-]+:|[a-z+]+://(?:[^@/]+@)?[\w.-]+(?::\d+)?/)"
    r"(?P<path>.+?)(?:\.git)?/?$"
)
_SEPARATORS = re.compile(r"[\s_-]+")


class ConfigError(RuntimeError):
    """The config file is missing or cannot be used."""


class AcliError(RuntimeError):
    """An `acli` call exited non-zero."""


class DraftError(ValueError):
    """A draft file does not have the expected shape."""


@dataclass(frozen=True)
class Profile:
    name: str
    project: str
    type: str
    assignee: str | None
    parent: str | None
    labels: tuple[str, ...]
    gitlab_paths: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    path: Path
    site: str
    default_profile: str | None
    profiles: dict[str, Profile]
    statuses: dict[str, tuple[str, ...]]
    types: dict[str, tuple[str, ...]]


# --- config ----------------------------------------------------------------


def config_path() -> Path:
    override = os.environ.get("JIRA_TICKET_CONFIG")
    return Path(override).expanduser() if override else DEFAULT_CONFIG


def _string(
    value: object, where: str, *, pattern: re.Pattern[str] | None = None
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    if pattern and not pattern.match(value.strip()):
        raise ConfigError(f"{where} is not valid: {value!r}")
    return value.strip()


def _strings(
    value: object, where: str, *, pattern: re.Pattern[str] | None = None
) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{where} must be a list of strings")
    return tuple(_string(item, where, pattern=pattern) for item in value)


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.is_file():
        raise ConfigError(
            f"no config at {path}; copy examples/jira-ticket.example.toml there"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    site = _string(data.get("site"), f"{path}: 'site'")

    statuses: dict[str, tuple[str, ...]] = {}
    types: dict[str, tuple[str, ...]] = {}
    for key, table in (data.get("project") or {}).items():
        where = f"{path}: [project.{key}]"
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table")
        statuses[key] = _strings(table.get("statuses"), f"{where} 'statuses'")
        types[key] = _strings(table.get("types"), f"{where} 'types'")

    profiles: dict[str, Profile] = {}
    for name, table in (data.get("profile") or {}).items():
        where = f"{path}: [profile.{name}]"
        if not isinstance(table, dict):
            raise ConfigError(f"{where} must be a table")
        assignee, parent = table.get("assignee"), table.get("parent")
        profiles[name] = Profile(
            name=name,
            project=_string(table.get("project"), f"{where} 'project'"),
            type=_string(table.get("type", "Task"), f"{where} 'type'"),
            assignee=None
            if assignee is None
            else _string(assignee, f"{where} 'assignee'"),
            parent=None
            if parent is None
            else _string(parent, f"{where} 'parent'", pattern=_KEY),
            labels=_strings(table.get("labels"), f"{where} 'labels'", pattern=_LABEL),
            gitlab_paths=tuple(
                p.strip("/")
                for p in _strings(table.get("gitlab_paths"), f"{where} 'gitlab_paths'")
            ),
        )

    for profile in profiles.values():
        allowed = types.get(profile.project, ())
        if allowed and profile.type not in allowed:
            raise ConfigError(
                f"{path}: [profile.{profile.name}] type {profile.type!r}"
                f" is not in [project.{profile.project}] types"
            )

    claimed: dict[str, str] = {}
    for profile in profiles.values():
        for gitlab_path in profile.gitlab_paths:
            if gitlab_path in claimed:
                raise ConfigError(
                    f"{path}: profiles {claimed[gitlab_path]!r} and {profile.name!r}"
                    f" both claim {gitlab_path!r}"
                )
            claimed[gitlab_path] = profile.name

    default = data.get("default_profile")
    if default is not None and default not in profiles:
        raise ConfigError(
            f"{path}: default_profile {default!r} names no [profile.{default}] table"
        )

    return Config(
        path=path,
        site=site,
        default_profile=default,
        profiles=profiles,
        statuses=statuses,
        types=types,
    )


def repo_path(repo: str | None) -> str | None:
    """The GitLab project path of the origin remote, or None outside a repo."""
    proc = subprocess.run(
        ["git", "-C", repo or ".", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    m = _REMOTE.match(proc.stdout.strip())
    return m["path"] if m else None


def resolve_profile(
    config: Config, explicit: str | None, project_path: str | None
) -> tuple[Profile, str]:
    """Return the profile to use and a one-line reason for the choice."""
    if explicit:
        if explicit not in config.profiles:
            raise ConfigError(
                f"no profile {explicit!r} in {config.path};"
                f" have: {', '.join(config.profiles)}"
            )
        return config.profiles[explicit], "chosen with --profile"
    if project_path:
        matches = [
            (gitlab_path, profile)
            for profile in config.profiles.values()
            for gitlab_path in profile.gitlab_paths
            if project_path == gitlab_path or project_path.startswith(gitlab_path + "/")
        ]
        if matches:
            gitlab_path, profile = max(matches, key=lambda pair: len(pair[0]))
            return profile, f"repo {project_path} is under {gitlab_path}"
    if config.default_profile:
        if project_path:
            reason = f"default; repo {project_path} matches no gitlab_paths"
        else:
            reason = "default; no git remote here"
        return config.profiles[config.default_profile], reason
    raise ConfigError(
        f"no profile matches and {config.path} sets no default_profile; pass --profile"
    )


def canonical(names: tuple[str, ...], wanted: str) -> str | None:
    """Match ``wanted`` to one of ``names``, ignoring case and separators."""

    def norm(s: str) -> str:
        return _SEPARATORS.sub(" ", s.strip().casefold())

    return next((name for name in names if norm(name) == norm(wanted)), None)


def resolve_type(config: Config, project: str, wanted: str) -> str:
    """Return Jira's spelling of a type on the shortlist, or fail."""
    allowed = config.types.get(project, ())
    if not allowed:
        return wanted
    if found := canonical(allowed, wanted):
        return found
    raise ConfigError(
        f"type {wanted!r} is not on the {project} shortlist: {', '.join(allowed)}"
    )


# --- drafts ----------------------------------------------------------------


def read_ticket_draft(path: Path) -> tuple[str, str]:
    """Return ``(summary, markdown_body)`` from a `# Summary` draft."""
    first, _, rest = path.read_text(encoding="utf-8").partition("\n")
    m = _TITLE.match(first)
    if not m:
        raise DraftError(f"{path}: the first line must be '# <summary>'")
    return m["title"], rest.strip("\n")


def placeholders(*texts: str) -> list[str]:
    return sorted({m.group() for text in texts for m in _PLACEHOLDER.finditer(text)})


def _walk(node: dict):
    yield node
    for child in node.get("content") or []:
        yield from _walk(child)


def bare_urls(doc: dict) -> list[str]:
    """Links whose text is their own URL: Jira's "URL" form, which reads badly."""
    return [
        node["text"]
        for node in _walk(doc)
        if node["type"] == "text"
        and any(
            mark["type"] == "link" and mark["attrs"]["href"] == node["text"]
            for mark in node.get("marks", [])
        )
    ]


def unconvertible(doc: dict | None) -> list[str]:
    """Node and mark types in ``doc`` that no Markdown draft can reproduce."""
    found: set[str] = set()
    for node in _walk(doc) if doc else ():
        if node["type"] not in md_to_adf.NODE_TYPES:
            found.add(node["type"])
        found.update(
            mark["type"]
            for mark in node.get("marks", [])
            if mark["type"] not in md_to_adf.MARK_TYPES
        )
    return sorted(found)


def describe(doc: dict) -> str:
    """Summarize the top-level blocks of an ADF document for a plan."""
    counts = Counter(node["type"] for node in doc["content"])
    parts = ", ".join(f"{n} {t}" for t, n in sorted(counts.items()))
    return f"{len(doc['content'])} blocks ({parts})" if counts else "empty"


# --- acli ------------------------------------------------------------------


def run_acli(args: list[str]) -> str:
    proc = subprocess.run(["acli", "jira", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise AcliError(
            f"acli jira {' '.join(args[:2])} failed ({proc.returncode}): {detail}"
        )
    return proc.stdout


def fetch(
    key: str, fields: str = "summary,status,issuetype,parent,labels,assignee"
) -> dict:
    return json.loads(
        run_acli(["workitem", "view", key, "--json", "--fields", fields])
    )["fields"]


def with_adf_file(doc: dict, flag: str, args: list[str]) -> tuple[list[str], Path]:
    """Write ``doc`` to a temp file; return ``args`` plus ``flag <file>``."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".adf.json", encoding="utf-8", delete=False
    ) as fh:
        json.dump(doc, fh, ensure_ascii=False)
    return [*args, flag, fh.name], Path(fh.name)


def created_key(stdout: str) -> str | None:
    """The new key from `acli ... create --json`, which prints the created issue.

    None when the output is not that shape, so that the caller can still show
    the output: the ticket exists either way.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    key = data.get("key") if isinstance(data, dict) else None
    return key if isinstance(key, str) else None


def browse_url(config: Config, key: str) -> str:
    return f"https://{config.site}/browse/{key}"


def summary_of(fields: dict) -> str:
    status = (fields.get("status") or {}).get("name", "?")
    return f"{fields.get('summary', '?')!r} ({status})"


# --- subcommands -----------------------------------------------------------


def _print_plan(rows: list[tuple[str, str]]) -> None:
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label + ':':<{width + 1}} {value}")


def _warn_placeholders(*texts: str) -> None:
    if found := placeholders(*texts):
        print(f"WARNING: the draft still contains {', '.join(found)}")


def _warn_bare_urls(doc: dict) -> None:
    if found := bare_urls(doc):
        print(f"WARNING: bare URL shown as its own text: {', '.join(found)}")
        print("  Use [label](url) in prose, or <url> for a card on its own line.")


def cmd_defaults(args: argparse.Namespace, config: Config) -> int:
    profile, reason = resolve_profile(config, args.profile, repo_path(args.repo))
    rows = [
        ("Profile", f"{profile.name} ({reason})"),
        ("Project", profile.project),
        ("Type", profile.type),
        ("Assignee", profile.assignee or "(none)"),
        (
            "Parent",
            f"{profile.parent} {summary_of(fetch(profile.parent))}"
            if profile.parent
            else "(none)",
        ),
        ("Labels", ", ".join(profile.labels) or "(none)"),
        ("Types", ", ".join(config.types.get(profile.project, ())) or "(any)"),
        (
            "Statuses",
            ", ".join(config.statuses.get(profile.project, ())) or "(no shortlist)",
        ),
    ]
    _print_plan(rows)
    return 0


def cmd_create(args: argparse.Namespace, config: Config) -> int:
    summary, body = read_ticket_draft(args.file)
    profile, reason = resolve_profile(config, args.profile, repo_path(args.repo))
    doc = md_to_adf.convert(body)
    issue_type = (
        resolve_type(config, profile.project, args.type) if args.type else profile.type
    )
    labels = tuple(dict.fromkeys([*profile.labels, *args.label]))

    acli_args = [
        "workitem",
        "create",
        "--json",
        "--project",
        profile.project,
        "--type",
        issue_type,
        "--summary",
        summary,
    ]
    if profile.assignee:
        acli_args += ["--assignee", profile.assignee]
    if profile.parent:
        acli_args += ["--parent", profile.parent]
    if labels:
        acli_args += ["--label", ",".join(labels)]

    _print_plan(
        [
            ("Profile", f"{profile.name} ({reason})"),
            ("Project", profile.project),
            ("Type", issue_type),
            ("Summary", summary),
            ("Assignee", profile.assignee or "(none)"),
            # Fetching the parent also proves it exists before anything is sent.
            (
                "Parent",
                f"{profile.parent} {summary_of(fetch(profile.parent))}"
                if profile.parent
                else "(none)",
            ),
            ("Labels", ", ".join(labels) or "(none)"),
            ("Description", f"{describe(doc)} from {args.file}"),
        ]
    )
    _warn_placeholders(summary, body)
    _warn_bare_urls(doc)
    if args.show_adf:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    if args.dry_run:
        print("Dry run: nothing sent.")
        return 0

    if doc["content"]:
        acli_args, adf_file = with_adf_file(doc, "--description-file", acli_args)
    else:
        adf_file = None
    try:
        stdout = run_acli(acli_args)
    finally:
        if adf_file:
            adf_file.unlink()
    key = created_key(stdout)
    if key is None:
        print("Created, but no key was found in the acli output:")
        print(stdout)
        return 0
    print(f"Created {key}: {browse_url(config, key)}")
    return 0


def cmd_update(args: argparse.Namespace, config: Config) -> int:
    if args.file is None and args.type is None:
        print("jira_ticket: update needs --file, --type, or both", file=sys.stderr)
        return 2
    current = fetch(args.key, "summary,status,issuetype,description")
    rows = [("Ticket", f"{args.key} {summary_of(current)}")]
    acli_args = ["workitem", "edit", "--key", args.key, "--yes", "--json"]

    if args.type:
        issue_type = resolve_type(config, args.key.rpartition("-")[0], args.type)
        current_type = (current.get("issuetype") or {}).get("name", "?")
        change = f"{current_type} -> {issue_type}"
        rows.append(("Type", "(unchanged)" if current_type == issue_type else change))
        acli_args += ["--type", issue_type]

    doc = None
    if args.file:
        summary, body = read_ticket_draft(args.file)
        doc = md_to_adf.convert(body)
        unchanged = current.get("summary") == summary
        rows.append(("Summary", "(unchanged)" if unchanged else f"-> {summary!r}"))
        rows.append(("Description", f"replace with {describe(doc)} from {args.file}"))
        acli_args += ["--summary", summary]

    _print_plan(rows)
    if args.file:
        _warn_placeholders(summary, body)
        _warn_bare_urls(doc)
        if lost := unconvertible(current.get("description")):
            print(
                f"WARNING: the description in Jira has {', '.join(lost)}, which no"
                " draft can express. Sending replaces them."
            )
    if args.show_adf and doc is not None:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    if args.dry_run:
        print("Dry run: nothing sent.")
        return 0

    adf_file = None
    if doc is not None:
        acli_args, adf_file = with_adf_file(doc, "--description-file", acli_args)
    try:
        run_acli(acli_args)
    finally:
        if adf_file:
            adf_file.unlink()
    print(f"Updated {args.key}: {browse_url(config, args.key)}")
    return 0


def cmd_transition(args: argparse.Namespace, config: Config) -> int:
    project = args.key.rpartition("-")[0]
    status = canonical(config.statuses.get(project, ()), args.status)
    if status is None:
        if not args.any:
            allowed = ", ".join(config.statuses.get(project, ())) or "(none)"
            print(
                f"{args.status!r} is not on the {project} shortlist: {allowed}",
                file=sys.stderr,
            )
            print("Pass --any to send it to Jira as written.", file=sys.stderr)
            return 1
        status = args.status
    current = fetch(args.key, "summary,status")
    current_status = (current.get("status") or {}).get("name", "?")

    _print_plan(
        [
            ("Ticket", f"{args.key} {current.get('summary', '?')!r}"),
            ("Status", f"{current_status} -> {status}"),
        ]
    )
    if current_status.casefold() == status.casefold():
        print("Already there: nothing to do.")
        return 0
    if args.dry_run:
        print("Dry run: nothing sent.")
        return 0

    run_acli(
        [
            "workitem",
            "transition",
            "--key",
            args.key,
            "--status",
            status,
            "--yes",
            "--json",
        ]
    )
    print(f"Moved {args.key} to {status}: {browse_url(config, args.key)}")
    return 0


def cmd_comment(args: argparse.Namespace, config: Config) -> int:
    body = args.file.read_text(encoding="utf-8").strip("\n")
    if not body.strip():
        raise DraftError(f"{args.file} is empty")
    doc = md_to_adf.convert(body)
    current = fetch(args.key, "summary,status")

    _print_plan(
        [
            ("Ticket", f"{args.key} {summary_of(current)}"),
            ("Comment", f"{describe(doc)} from {args.file}"),
        ]
    )
    _warn_placeholders(body)
    _warn_bare_urls(doc)
    if args.show_adf:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    if args.dry_run:
        print("Dry run: nothing sent.")
        return 0

    acli_args, adf_file = with_adf_file(
        doc,
        "--body-file",
        ["workitem", "comment", "create", "--key", args.key, "--json"],
    )
    try:
        run_acli(acli_args)
    finally:
        adf_file.unlink()
    print(f"Commented on {args.key}: {browse_url(config, args.key)}")
    return 0


# --- CLI -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add_profile_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--profile",
            help="profile name; overrides the repo match and default_profile",
        )
        p.add_argument(
            "--repo", help="repo whose origin remote selects the profile (default: cwd)"
        )

    def add_write_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--dry-run", action="store_true", help="print the plan and send nothing"
        )
        p.add_argument(
            "--show-adf", action="store_true", help="also print the converted ADF"
        )

    p = sub.add_parser("defaults", help="print the profile that applies, and why")
    add_profile_args(p)
    p.set_defaults(func=cmd_defaults)

    p = sub.add_parser("create", help="create a ticket from a '# Summary' draft")
    p.add_argument("--file", type=Path, required=True)
    p.add_argument(
        "--type", help="work item type, e.g. Bug (default: the profile's type)"
    )
    p.add_argument(
        "--label",
        action="append",
        default=[],
        help="a label to add to the profile's labels",
    )
    add_profile_args(p)
    add_write_args(p)
    p.set_defaults(func=cmd_create)

    p = sub.add_parser(
        "update", help="replace the summary and description, change the type, or both"
    )
    p.add_argument("key")
    p.add_argument("--file", type=Path, help="a '# Summary' draft")
    p.add_argument("--type", help="a work item type on the shortlist, e.g. Bug")
    add_write_args(p)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("transition", help="move a ticket to a shortlisted status")
    p.add_argument("key")
    p.add_argument("status")
    p.add_argument(
        "--any", action="store_true", help="allow a status that is not on the shortlist"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan and send nothing"
    )
    p.set_defaults(func=cmd_transition)

    p = sub.add_parser("comment", help="add a comment from a Markdown file")
    p.add_argument("key")
    p.add_argument("--file", type=Path, required=True)
    add_write_args(p)
    p.set_defaults(func=cmd_comment)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "key", None) and not _KEY.match(args.key):
        print(f"jira_ticket: {args.key!r} is not a ticket key", file=sys.stderr)
        return 2
    try:
        return args.func(args, load_config())
    # OSError covers an unreadable draft and a missing `acli` or `git`.
    except (
        ConfigError,
        AcliError,
        DraftError,
        md_to_adf.ConversionError,
        OSError,
    ) as exc:
        print(f"jira_ticket: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
