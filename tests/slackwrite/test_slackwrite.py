from pathlib import Path

import pytest

import slackwrite as sw

GROUP = "https://gitlab.com/acme/platform/data"
SITE = "acme.atlassian.net"


def mr(iid: int, title: str, key: str | None, repo: str = "query-api") -> sw.MR:
    return sw.MR(iid, f"{GROUP}/{repo}/-/merge_requests/{iid}", title, key, False)


# --- parse_remote ------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@gitlab.com:acme/platform/data/query-api.git",
         ("gitlab.com", "acme/platform/data/query-api")),
        ("https://gitlab.com/acme/platform/data/query-api.git",
         ("gitlab.com", "acme/platform/data/query-api")),
        ("https://gitlab.com/acme/query-api/", ("gitlab.com", "acme/query-api")),
        ("https://oauth2:token@gitlab.com/acme/query-api.git",
         ("gitlab.com", "acme/query-api")),
        ("ssh://git@gitlab.example.com:2222/acme/query-api.git",
         ("gitlab.example.com", "acme/query-api")),
    ],
)  # fmt: skip
def test_parse_remote(url, expected):
    assert sw.parse_remote(url) == expected


def test_parse_remote_rejects_a_local_path():
    with pytest.raises(sw.SlackwriteError, match="cannot read"):
        sw.parse_remote("/srv/git/query-api.git")


def test_repo_name_is_the_last_path_segment():
    repo = sw.Repo(Path("/r"), "gitlab.com", "acme/platform/query-api", "main")
    assert repo.name == "query-api"


# --- split_title -------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("feat(db): add a database (ACME-12)", ("feat(db): add a database", "ACME-12")),
        (
            "feat(db): add a database (ACME-12)  ",
            ("feat(db): add a database", "ACME-12"),
        ),
        ("fix: no ticket", ("fix: no ticket", None)),
        (
            "fix(ACME-12): key not at the end",
            ("fix(ACME-12): key not at the end", None),
        ),
        ("Draft: fix: wip (ACME-12)", ("Draft: fix: wip", "ACME-12")),
    ],
)
def test_split_title(title, expected):
    assert sw.split_title(title) == expected


# --- render_mr ---------------------------------------------------------------


def test_render_one_mr():
    text = sw.render_mr(
        "test-harness",
        [
            mr(
                18,
                "feat(postgres): add a Postgres 17 database",
                "ACME-284",
                "test-harness",
            )
        ],
        SITE,
    )
    assert text == (
        f"test-harness: [MR !18]({GROUP}/test-harness/-/merge_requests/18)  \n"
        "feat(postgres): add a Postgres 17 database"
        f" ([ACME-284](https://{SITE}/browse/ACME-284))  \n"
        "_Please review_"
    )


def test_render_one_mr_without_a_key():
    text = sw.render_mr("query-api", [mr(5, "chore: tidy", None)], SITE)
    assert text.splitlines()[1] == "chore: tidy  "


def test_render_many_mrs():
    text = sw.render_mr(
        "query-api",
        [
            mr(26, "refactor(config): parse env vars with tags", "ACME-40"),
            mr(27, "fix(deps): bump grpc to v1.83.2", None),
        ],
        SITE,
    )
    assert text == (
        "query-api MRs:\n"
        "\n"
        f"- [!26]({GROUP}/query-api/-/merge_requests/26)"
        " refactor(config): parse env vars with tags"
        f" ([ACME-40](https://{SITE}/browse/ACME-40))\n"
        f"- [!27]({GROUP}/query-api/-/merge_requests/27)"
        " fix(deps): bump grpc to v1.83.2\n"
        "\n"
        "_Please review_"
    )


# --- draft_path --------------------------------------------------------------


def test_draft_path_uses_the_ticket_directory(tmp_path):
    ticket_dir = tmp_path / "tmp" / "add-postgres_ACME-284"
    ticket_dir.mkdir(parents=True)
    assert sw.draft_path(tmp_path, "mr", "ACME-284") == ticket_dir / "slack-mr.md"


def test_draft_path_falls_back_without_a_ticket_directory(tmp_path):
    (tmp_path / "tmp" / "other_ACME-2840").mkdir(parents=True)
    expected = tmp_path / "tmp" / "slackwrite" / "slack-mr.md"
    assert sw.draft_path(tmp_path, "mr", "ACME-284") == expected
    assert sw.draft_path(tmp_path, "mr", None) == expected


def test_draft_path_ignores_a_file_with_a_matching_name(tmp_path):
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "notes_ACME-284").write_text("", encoding="utf-8")
    expected = tmp_path / "tmp" / "slackwrite" / "slack-mr.md"
    assert sw.draft_path(tmp_path, "mr", "ACME-284") == expected


def test_draft_path_refuses_two_ticket_directories(tmp_path):
    (tmp_path / "tmp" / "a_ACME-284").mkdir(parents=True)
    (tmp_path / "tmp" / "b_ACME-284").mkdir()
    with pytest.raises(sw.SlackwriteError, match="more than one"):
        sw.draft_path(tmp_path, "mr", "ACME-284")


# --- jira_site ---------------------------------------------------------------


def test_jira_site_reads_the_jira_ticket_config(tmp_path, monkeypatch):
    config = tmp_path / "jira.toml"
    config.write_text(
        f'site = " {SITE} "\n[profile.x]\nproject = "ACME"\n', encoding="utf-8"
    )
    monkeypatch.setenv(sw.JIRA_CONFIG_ENV, str(config))
    assert sw.jira_site() == SITE


def test_jira_site_requires_a_site(tmp_path, monkeypatch):
    config = tmp_path / "jira.toml"
    config.write_text('default_profile = "x"\n', encoding="utf-8")
    monkeypatch.setenv(sw.JIRA_CONFIG_ENV, str(config))
    with pytest.raises(sw.SlackwriteError, match="'site'"):
        sw.jira_site()


def test_jira_site_reports_a_missing_config(tmp_path, monkeypatch):
    monkeypatch.setenv(sw.JIRA_CONFIG_ENV, str(tmp_path / "missing.toml"))
    with pytest.raises(sw.SlackwriteError, match="cannot read"):
        sw.jira_site()
