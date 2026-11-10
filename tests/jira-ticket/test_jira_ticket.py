"""Tests for jira-ticket/scripts/jira_ticket.py.

`acli` is never run: each test that reaches Jira patches `run_acli` with a
fake that records the call and answers `workitem view` with canned fields.
"""

import json
import os
import subprocess
from pathlib import Path

import jira_ticket as jt
import md_to_adf
import pytest

EXAMPLE = (
    Path(__file__).parents[2] / "jira-ticket" / "examples" / "jira-ticket.example.toml"
)

CONFIG = """
site = "acme.atlassian.net"
default_profile = "general"

[project.ACME]
statuses = ["To Do", "on integration", "Done"]
types = ["Task", "Bug"]

[profile.general]
project = "ACME"

[profile.data]
project = "ACME"
assignee = "@me"
parent = "ACME-100"
labels = ["data"]
repo_paths = ["acme/data"]

[profile.etl]
project = "ACME"
repo_paths = ["acme/data/etl"]
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "jira-ticket.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return jt.load_config(path)


@pytest.fixture
def acli(monkeypatch):
    """Record every acli call; answer `view` with fixed fields.

    A call that sends an ADF file is followed in the record by the file's ADF.
    """
    calls = []

    def fake(args):
        calls.append(args)
        if args[:2] == ["workitem", "view"]:
            fields = {"summary": "Old summary", "status": {"name": "To Do"}}
            return json.dumps({"key": args[2], "fields": fields})
        for flag in ("--description-file", "--body-file"):
            if flag in args:
                # Capture the ADF file while it still exists.
                adf = Path(args[args.index(flag) + 1]).read_text(encoding="utf-8")
                calls.append(json.loads(adf))
        if args[:2] == ["workitem", "create"]:
            return json.dumps({"key": "ACME-123"})
        return "{}"

    monkeypatch.setattr(jt, "run_acli", fake)
    monkeypatch.setattr(jt, "repo_path", lambda repo: "acme/data/pipeline")
    return calls


def draft(tmp_path, text):
    path = tmp_path / "jira.md"
    path.write_text(text, encoding="utf-8")
    return path


# --- config ----------------------------------------------------------------


def test_example_config_loads():
    config = jt.load_config(EXAMPLE)
    assert config.default_profile in config.profiles


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('default_profile = "nope"\n', "names no"),
        ('[profile.x]\nproject = "A"\nparent = "not-a-key"\n', "not valid"),
        ('[profile.x]\nproject = "A"\nlabels = ["two words"]\n', "not valid"),
        ('[profile.x]\nproject = "A"\nrepo_paths = ["acme/data/"]\n', "both claim"),
        ('[profile.x]\nproject = "A"\ngitlab_paths = ["acme/data"]\n', "both claim"),
        (
            '[profile.x]\nproject = "A"\n'
            'repo_paths = ["a/b"]\ngitlab_paths = ["a/c"]\n',
            "both 'repo_paths' and 'gitlab_paths'",
        ),
    ],
)
def test_bad_config_fails_loudly(tmp_path, extra, message):
    path = tmp_path / "c.toml"
    # Prepend top-level keys so they stay outside the last table.
    body = extra + CONFIG if extra.startswith("default") else CONFIG + extra
    path.write_text(
        body.replace('default_profile = "general"\n', "", 1), encoding="utf-8"
    )
    with pytest.raises(jt.ConfigError, match=message):
        jt.load_config(path)


def test_gitlab_paths_is_the_old_name_of_repo_paths(tmp_path):
    path = tmp_path / "c.toml"
    old = CONFIG.replace(
        'repo_paths = ["acme/data/etl"]', 'gitlab_paths = ["acme/data/etl"]'
    )
    path.write_text(old, encoding="utf-8")
    config = jt.load_config(path)
    assert config.profiles["etl"].repo_paths == ("acme/data/etl",)
    assert jt.resolve_profile(config, None, "acme/data/etl/jobs")[0].name == "etl"


def test_missing_config_names_the_example(tmp_path):
    with pytest.raises(jt.ConfigError, match="example"):
        jt.load_config(tmp_path / "absent.toml")


# --- includes --------------------------------------------------------------

INCLUDE_EXAMPLE = EXAMPLE.with_name("include.example.toml")
TEAM_A = "~/team origin/main:a.toml"
TEAM_B = "~/team origin/main:b.toml"


@pytest.fixture
def team(monkeypatch):
    """Serve team files by their include path instead of running git."""
    files: dict[str, str] = {}
    monkeypatch.setattr(jt, "read_include", lambda include: files[include.path])
    return files


def personal(tmp_path, *team_paths, head=""):
    """A personal config: ``head``, then one [[include]] per team path."""
    includes = "".join(
        f'\n[[include]]\nrepo = "~/team"\nref = "origin/main"\npath = "{p}"\n'
        for p in team_paths
    )
    path = tmp_path / "personal.toml"
    path.write_text(head + includes, encoding="utf-8")
    return path


def git(repo, *args):
    """Run git in ``repo`` without the user's config, which may sign commits."""
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=env
    )


def test_include_example_loads(team):
    team["docs/jira-ticket.toml"] = EXAMPLE.read_text(encoding="utf-8")
    config = jt.load_config(INCLUDE_EXAMPLE)
    assert config.site == "acme.atlassian.net"
    assert config.default_profile == "acme"  # the personal key wins
    assert config.profiles["platform-data"].source == (
        "~/src/acme/platform/data origin/main:docs/jira-ticket.toml"
    )


def test_a_team_file_supplies_the_config(tmp_path, team):
    team["a.toml"] = CONFIG
    config = jt.load_config(personal(tmp_path, "a.toml"))
    profile, _ = jt.resolve_profile(config, None, "acme/data/etl/jobs")
    assert (profile.name, profile.source, profile.replaces) == ("etl", TEAM_A, None)
    assert config.statuses["ACME"] == ("To Do", "on integration", "Done")


def test_the_personal_config_replaces_a_team_profile(
    tmp_path, team, acli, monkeypatch, capsys
):
    team["a.toml"] = CONFIG
    head = '[profile.data]\nproject = "ACME"\nparent = "ACME-200"\n'
    head += 'repo_paths = ["acme/data"]\n'
    path = personal(tmp_path, "a.toml", head=head)
    config = jt.load_config(path)
    assert config.profiles["data"].parent == "ACME-200"
    assert run(config, monkeypatch, ["defaults"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert f"Source:   {path}, which replaces the profile in {TEAM_A}" in out


def test_an_identical_personal_copy_replaces_nothing(tmp_path, team):
    team["a.toml"] = CONFIG
    head = '[profile.etl]\nproject = "ACME"\nrepo_paths = ["acme/data/etl"]\n'
    config = jt.load_config(personal(tmp_path, "a.toml", head=head))
    assert config.profiles["etl"].replaces is None


@pytest.mark.parametrize(
    ("second", "shown"),
    [
        ('site = "other.atlassian.net"\n', "'site'"),
        ('[project.ACME]\nstatuses = ["Done"]\n', "[project.ACME]"),
        ('[profile.data]\nproject = "ACME"\n', "[profile.data]"),
    ],
)
def test_team_files_that_disagree_fail_and_name_both(tmp_path, team, second, shown):
    team["a.toml"], team["b.toml"] = CONFIG, second
    with pytest.raises(jt.ConfigError) as exc:
        jt.load_config(personal(tmp_path, "a.toml", "b.toml"))
    assert str(exc.value).startswith(f"{TEAM_A} and {TEAM_B} disagree on {shown};")


def test_team_files_may_repeat_a_definition(tmp_path, team):
    # The old key name and a trailing slash do not change the definition.
    team["a.toml"] = CONFIG
    team["b.toml"] = (
        'site = "acme.atlassian.net"\n'
        '[profile.etl]\nproject = "ACME"\ngitlab_paths = ["acme/data/etl/"]\n'
    )
    config = jt.load_config(personal(tmp_path, "a.toml", "b.toml"))
    assert config.profiles["etl"].source == TEAM_A


def test_one_path_claimed_in_two_files_names_both(tmp_path, team):
    team["a.toml"] = CONFIG
    team["b.toml"] = '[profile.other]\nproject = "ACME"\nrepo_paths = ["acme/data"]\n'
    with pytest.raises(jt.ConfigError) as exc:
        jt.load_config(personal(tmp_path, "a.toml", "b.toml"))
    assert str(exc.value) == (
        f"profiles 'data' ({TEAM_A}) and 'other' ({TEAM_B}) both claim 'acme/data'"
    )


def test_includes_do_not_nest(tmp_path, team):
    team["a.toml"] = CONFIG + '[[include]]\nrepo = "~/x"\nref = "main"\npath = "b"\n'
    with pytest.raises(jt.ConfigError, match="do not nest"):
        jt.load_config(personal(tmp_path, "a.toml"))


def test_site_must_be_set_somewhere(tmp_path, team):
    team["a.toml"] = CONFIG.replace('site = "acme.atlassian.net"\n', "")
    with pytest.raises(jt.ConfigError, match="set 'site' here or in an included"):
        jt.load_config(personal(tmp_path, "a.toml"))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[include]]\nrepo = "~/t"\npath = "a"\n', "include 1 lacks ref"),
        (
            '[[include]]\nrepo = "~/t"\nref = "main"\npath = "a"\nbranch = "x"\n',
            "unknown keys: branch",
        ),
        ('[[include]]\nrepo = "t"\nref = "main"\npath = "a"\n', "absolute"),
        ('[[include]]\nrepo = "~/t"\nref = ""\npath = "a"\n', "non-empty"),
        ('[include]\nrepo = "~/t"\nref = "main"\npath = "a"\n', r"\[\[include\]\]"),
    ],
)
def test_bad_include_fails_loudly(tmp_path, team, body, message):
    path = tmp_path / "personal.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(jt.ConfigError, match=message):
        jt.load_config(path)


@pytest.mark.parametrize("bom", ["", "\ufeff"])  # Windows editors may add one
def test_include_reads_the_ref_not_the_working_tree(tmp_path, bom):
    repo = tmp_path / "team"
    (repo / "docs").mkdir(parents=True)
    git(repo, "init", "--quiet", "--initial-branch=main")
    team_file = repo / "docs" / "jira-ticket.toml"
    team_file.write_text(bom + CONFIG, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "team config")
    git(repo, "switch", "--quiet", "-c", "feature")
    team_file.write_text(CONFIG.replace("acme.", "stale."), encoding="utf-8")
    path = tmp_path / "personal.toml"
    path.write_text(
        f"[[include]]\nrepo = '{repo.as_posix()}'\nref = 'main'\n"
        "path = 'docs/jira-ticket.toml'\n",
        encoding="utf-8",
    )
    assert jt.load_config(path).site == "acme.atlassian.net"


def test_include_of_a_missing_ref_shows_the_git_error(tmp_path):
    repo = tmp_path / "team"
    repo.mkdir()
    git(repo, "init", "--quiet")
    path = tmp_path / "personal.toml"
    path.write_text(
        f"[[include]]\nrepo = '{repo.as_posix()}'\nref = 'origin/develop'\n"
        "path = 'x.toml'\n",
        encoding="utf-8",
    )
    with pytest.raises(jt.ConfigError, match=r"cannot read .* origin/develop:x\.toml"):
        jt.load_config(path)


# --- profile selection -----------------------------------------------------


@pytest.mark.parametrize(
    ("project_path", "profile"),
    [
        ("acme/data/pipeline", "data"),
        ("acme/data/etl/jobs", "etl"),  # the longest match wins
        ("acme/data-archive/x", "general"),  # whole segments only
        (None, "general"),
    ],
)
def test_resolve_profile(config, project_path, profile):
    assert jt.resolve_profile(config, None, project_path)[0].name == profile


def test_explicit_profile_wins(config):
    profile, reason = jt.resolve_profile(config, "general", "acme/data/x")
    assert profile.name == "general" and "--profile" in reason


@pytest.mark.parametrize(
    "remote",
    [
        "git@gitlab.com:acme/data/etl.git",
        "https://gitlab.com/acme/data/etl.git",
        "ssh://git@gitlab.com:22/acme/data/etl",
    ],
)
def test_remote_path(remote):
    assert jt._REMOTE.match(remote)["path"] == "acme/data/etl"


@pytest.mark.parametrize(
    "remote",
    [
        "git@github.com:acme/data-etl.git",
        "https://github.com/acme/data-etl",
        "ssh://git@ssh.github.com:443/acme/data-etl.git",
        "https://x-access-token:abc@github.com/acme/data-etl.git",
        "git@github-work:acme/data-etl.git",  # an ~/.ssh/config host alias
        "https://github.example.com/acme/data-etl.git/",  # GitHub Enterprise
    ],
)
def test_github_remote_path(remote):
    assert jt._REMOTE.match(remote)["path"] == "acme/data-etl"


@pytest.mark.parametrize(
    "wanted", ["On Integration", "on-integration", " ON_INTEGRATION "]
)
def test_status_matches_loosely_and_returns_jira_spelling(config, wanted):
    assert jt.canonical(config.statuses["ACME"], wanted) == "on integration"


# --- drafts ----------------------------------------------------------------


def test_read_ticket_draft(tmp_path):
    path = draft(tmp_path, "# Fix the thing\n\n## Why\n\nBecause.\n")
    assert jt.read_ticket_draft(path) == ("Fix the thing", "## Why\n\nBecause.")


def test_draft_needs_a_title_line(tmp_path):
    with pytest.raises(jt.DraftError):
        jt.read_ticket_draft(draft(tmp_path, "## Not a title\n"))


def test_draft_may_start_with_a_byte_order_mark(tmp_path):
    # Windows PowerShell 5.1 writes one with `Set-Content -Encoding UTF8`.
    path = tmp_path / "jira.md"
    path.write_text("# Fix the thing\n\nBody.\n", encoding="utf-8-sig")
    assert jt.read_ticket_draft(path) == ("Fix the thing", "Body.")


def test_config_may_start_with_a_byte_order_mark(tmp_path):
    path = tmp_path / "jira-ticket.toml"
    path.write_text(CONFIG, encoding="utf-8-sig")
    assert jt.load_config(path).default_profile == "general"


def test_placeholders():
    found = jt.placeholders("see ACME-XXXXX and ACME-123", "ACME-XXXXX")
    assert found == ["ACME-XXXXX"]


@pytest.mark.parametrize(
    ("stdout", "key"),
    [('{"key": "ACME-7"}', "ACME-7"), ("Created ACME-8", None), ("[]", None)],
)
def test_created_key(stdout, key):
    assert jt.created_key(stdout) == key


def test_profile_type_must_be_on_the_shortlist(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        CONFIG.replace('project = "ACME"\n', 'project = "ACME"\ntype = "Story"\n', 1),
        encoding="utf-8",
    )
    with pytest.raises(jt.ConfigError, match="not in"):
        jt.load_config(path)


# --- subcommands -----------------------------------------------------------


def run(config, monkeypatch, argv):
    monkeypatch.setattr(jt, "load_config", lambda: config)
    return jt.main(argv)


def test_create_dry_run_sends_nothing(config, acli, monkeypatch, tmp_path, capsys):
    path = draft(tmp_path, "# Summary\n\nBody for ACME-XXXXX.\n")
    assert run(config, monkeypatch, ["create", "--file", str(path), "--dry-run"]) == 0
    assert all(call[:2] == ["workitem", "view"] for call in acli)
    out = capsys.readouterr().out
    assert "data (repo acme/data/pipeline is under acme/data)" in out
    assert "ACME-XXXXX" in out and "Dry run" in out


def test_create_applies_profile_and_sends_adf(
    config, acli, monkeypatch, tmp_path, capsys
):
    path = draft(tmp_path, "# Summary\n\n**Body**\n")
    argv = ["create", "--file", str(path), "--type", "bug", "--label", "extra"]
    assert run(config, monkeypatch, argv) == 0
    create, adf = acli[-2], acli[-1]
    flags = dict(zip(create[3::2], create[4::2], strict=False))
    assert flags["--parent"] == "ACME-100"
    assert flags["--label"] == "data,extra"
    assert flags["--assignee"] == "@me"
    assert flags["--type"] == "Bug"
    assert adf["content"][0]["content"][0]["marks"] == [{"type": "strong"}]
    assert not Path(flags["--description-file"]).exists()
    assert (
        "Created ACME-123: https://acme.atlassian.net/browse/ACME-123"
        in capsys.readouterr().out
    )


def test_transition_refuses_unlisted_status(config, acli, monkeypatch):
    assert run(config, monkeypatch, ["transition", "ACME-1", "Blocked"]) == 1
    assert acli == []


def test_transition_sends_the_jira_spelling(config, acli, monkeypatch):
    assert run(config, monkeypatch, ["transition", "ACME-1", "On Integration"]) == 0
    assert acli[-1][:6] == [
        "workitem",
        "transition",
        "--key",
        "ACME-1",
        "--status",
        "on integration",
    ]


def test_bad_key_is_rejected_before_any_call(config, acli, monkeypatch):
    assert run(config, monkeypatch, ["update", "acme 1", "--file", "x.md"]) == 2
    assert acli == []


def test_unlisted_type_is_refused(config, acli, monkeypatch, tmp_path):
    path = draft(tmp_path, "# Summary\n\nBody.\n")
    argv = ["create", "--file", str(path), "--type", "Story"]
    assert run(config, monkeypatch, argv) == 1
    assert not any(call[:2] == ["workitem", "create"] for call in acli)


def test_update_type_only(config, acli, monkeypatch):
    assert run(config, monkeypatch, ["update", "ACME-1", "--type", "BUG"]) == 0
    edit = acli[-1]
    assert edit[edit.index("--type") + 1] == "Bug"
    assert "--description-file" not in edit and "--summary" not in edit


def test_update_needs_file_or_type(config, acli, monkeypatch):
    assert run(config, monkeypatch, ["update", "ACME-1"]) == 2
    assert acli == []


def test_update_sends_the_summary_and_adf(config, acli, monkeypatch, tmp_path, capsys):
    path = draft(tmp_path, "# New summary\n\n**Body**\n")
    assert run(config, monkeypatch, ["update", "ACME-1", "--file", str(path)]) == 0
    edit, adf = acli[-2], acli[-1]
    assert edit[:5] == ["workitem", "edit", "--key", "ACME-1", "--yes"]
    assert edit[edit.index("--summary") + 1] == "New summary"
    assert adf["content"][0]["content"][0]["marks"] == [{"type": "strong"}]
    assert not Path(edit[edit.index("--description-file") + 1]).exists()
    assert (
        "Updated ACME-1: https://acme.atlassian.net/browse/ACME-1"
        in capsys.readouterr().out
    )


def test_transition_to_the_current_status_sends_nothing(
    config, acli, monkeypatch, capsys
):
    assert run(config, monkeypatch, ["transition", "ACME-1", "to do"]) == 0
    assert not any(call[:2] == ["workitem", "transition"] for call in acli)
    assert "Already there: nothing to do." in capsys.readouterr().out


def test_transition_any_sends_an_unlisted_status_as_written(config, acli, monkeypatch):
    assert run(config, monkeypatch, ["transition", "ACME-1", "Blocked", "--any"]) == 0
    assert acli[-1][:6] == [
        "workitem",
        "transition",
        "--key",
        "ACME-1",
        "--status",
        "Blocked",
    ]


def comment(tmp_path, text):
    path = tmp_path / "comment.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_comment_dry_run_warns_and_sends_nothing(
    config, acli, monkeypatch, tmp_path, capsys
):
    path = comment(tmp_path, "Fixed in ACME-XXXXX, see https://e.com/x\n")
    argv = ["comment", "ACME-1", "--file", str(path), "--dry-run"]
    assert run(config, monkeypatch, argv) == 0
    assert all(call[:2] == ["workitem", "view"] for call in acli)
    out = capsys.readouterr().out
    assert "Ticket:  ACME-1 'Old summary' (To Do)" in out
    assert "still contains ACME-XXXXX" in out
    assert "bare URL shown as its own text: https://e.com/x" in out
    assert "Dry run: nothing sent." in out


def test_comment_sends_adf_and_removes_the_temp_file(
    config, acli, monkeypatch, tmp_path, capsys
):
    path = comment(tmp_path, "**Merged.**\n")
    assert run(config, monkeypatch, ["comment", "ACME-1", "--file", str(path)]) == 0
    send, adf = acli[-2], acli[-1]
    assert send[:6] == ["workitem", "comment", "create", "--key", "ACME-1", "--json"]
    assert adf["content"][0]["content"][0]["marks"] == [{"type": "strong"}]
    assert not Path(send[send.index("--body-file") + 1]).exists()
    assert (
        "Commented on ACME-1: https://acme.atlassian.net/browse/ACME-1"
        in capsys.readouterr().out
    )


def test_comment_refuses_an_empty_file(config, acli, monkeypatch, tmp_path, capsys):
    path = comment(tmp_path, "\n  \n")
    assert run(config, monkeypatch, ["comment", "ACME-1", "--file", str(path)]) == 1
    assert acli == []
    assert "is empty" in capsys.readouterr().err


def test_defaults_shows_the_profile_and_why(config, acli, monkeypatch, capsys):
    assert run(config, monkeypatch, ["defaults"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert "Profile:  data (repo acme/data/pipeline is under acme/data)" in out
    assert "Parent:   ACME-100 'Old summary' (To Do)" in out
    assert "Types:    Task, Bug" in out
    assert "Statuses: To Do, on integration, Done" in out


def test_bare_urls_finds_only_links_that_show_their_url():
    doc = md_to_adf.convert("[label](https://a.com) https://b.com <https://c.com>")
    assert jt.bare_urls(doc) == ["https://b.com"]


def test_unconvertible_names_what_markdown_cannot_reproduce():
    doc = {
        "type": "doc",
        "content": [
            {"type": "embedCard", "attrs": {"url": "https://a.com"}},
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "x", "marks": [{"type": "underline"}]},
                    {"type": "text", "text": "y", "marks": [{"type": "strong"}]},
                ],
            },
        ],
    }
    assert jt.unconvertible(doc) == ["embedCard", "underline"]
    assert jt.unconvertible(md_to_adf.convert("# h\n\n- **x** <https://a.com>")) == []
    assert jt.unconvertible(None) == []


def test_checked_lists_the_text_of_checked_boxes():
    doc = md_to_adf.convert("- [x] **a** b\n- [ ] c\n  - [x] d")
    assert jt.checked(doc) == ["a b", "d"]
    assert jt.checked(None) == []


def test_update_warns_before_unchecking_a_box(config, monkeypatch, tmp_path, capsys):
    stored = md_to_adf.convert("- [x] done\n- [x] kept\n- [ ] open")
    fields = {"summary": "S", "status": {"name": "To Do"}, "description": stored}
    monkeypatch.setattr(jt, "fetch", lambda key, fields_=None: fields)
    path = draft(tmp_path, "# S\n\n- [ ] done\n- [x] kept\n- [x] open\n")
    argv = ["update", "ACME-1", "--file", str(path), "--dry-run"]
    assert run(config, monkeypatch, argv) == 0
    out = capsys.readouterr().out
    assert "checked in Jira but not in the draft" in out
    assert "  - done\n" in out
    assert "  - kept" not in out and "  - open" not in out


def test_update_warns_before_replacing_unconvertible_content(
    config, monkeypatch, tmp_path, capsys
):
    stored = {"type": "doc", "content": [{"type": "embedCard", "attrs": {}}]}
    fields = {"summary": "S", "status": {"name": "To Do"}, "description": stored}
    monkeypatch.setattr(jt, "fetch", lambda key, fields_=None: fields)
    path = draft(tmp_path, "# S\n\nBody.\n")
    argv = ["update", "ACME-1", "--file", str(path), "--dry-run"]
    assert run(config, monkeypatch, argv) == 0
    assert "has embedCard" in capsys.readouterr().out
