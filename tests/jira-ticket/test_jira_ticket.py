"""Tests for jira-ticket/scripts/jira_ticket.py.

`acli` is never run: each test that reaches Jira patches `run_acli` with a
fake that records the call and answers `workitem view` with canned fields.
"""

import json
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
gitlab_paths = ["acme/data"]

[profile.etl]
project = "ACME"
gitlab_paths = ["acme/data/etl"]
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "jira-ticket.toml"
    path.write_text(CONFIG, encoding="utf-8")
    return jt.load_config(path)


@pytest.fixture
def acli(monkeypatch):
    """Record every acli call; answer `view` with fixed fields."""
    calls = []

    def fake(args):
        calls.append(args)
        if args[:2] == ["workitem", "view"]:
            fields = {"summary": "Old summary", "status": {"name": "To Do"}}
            return json.dumps({"key": args[2], "fields": fields})
        if args[:2] == ["workitem", "create"]:
            # Capture the ADF file while it still exists.
            flag = args.index("--description-file")
            calls.append(json.loads(Path(args[flag + 1]).read_text(encoding="utf-8")))
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
        ('[profile.x]\nproject = "A"\ngitlab_paths = ["acme/data/"]\n', "both claim"),
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


def test_missing_config_names_the_example(tmp_path):
    with pytest.raises(jt.ConfigError, match="example"):
        jt.load_config(tmp_path / "absent.toml")


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
