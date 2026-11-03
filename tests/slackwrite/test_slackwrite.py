import argparse
import json
from pathlib import Path

import pytest

import slackwrite as sw

GROUP = "https://gitlab.com/acme/platform/data"
SITE = "acme.atlassian.net"
REPO = sw.Repo(Path("/r"), "gitlab.com", "acme/platform/data/query-api", "feat/2")


def mr(iid: int, title: str, key: str | None, repo: str = "query-api") -> sw.MR:
    url = f"{GROUP}/{repo}/-/merge_requests/{iid}"
    return sw.MR(sw.GITLAB, iid, url, title, key, False, f"feat/{iid}", "develop")


def url(iid: int, project: str = REPO.path) -> str:
    return f"https://gitlab.com/{project}/-/merge_requests/{iid}"


def ref(iid: int) -> sw.Ref:
    return sw.Ref(f"!{iid}", url(iid))


def node(
    iid: int, target: str = "develop", blockers: tuple = (), hidden: int = 0
) -> dict:
    """A GraphQL `mr` fragment, as GitLab returns it."""
    return {
        "iid": str(iid),
        "webUrl": url(iid),
        "title": f"feat: change {iid} (ACME-{iid})",
        "draft": False,
        "sourceBranch": f"feat/{iid}",
        "targetBranch": target,
        "blockingMergeRequests": {
            "hiddenCount": hidden,
            "visibleMergeRequests": list(blockers),
        },
    }


def blocker(iid: int, state: str = "opened", project: str = REPO.path) -> dict:
    return {
        "iid": str(iid),
        "webUrl": url(iid, project),
        "state": state,
        "project": {"fullPath": project},
    }


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


@pytest.mark.parametrize(
    ("host", "forge"),
    [
        ("github.com", sw.GITHUB),
        ("gitlab.com", sw.GITLAB),
        ("gitlab.example.com", sw.GITLAB),
        ("github.com.example.com", sw.GITLAB),
    ],
)
def test_repo_forge_is_github_only_on_github_com(host, forge):
    assert sw.Repo(Path("/r"), host, "acme/query-api", "main").forge is forge


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


def test_render_one_mr_puts_each_note_on_a_line():
    text = sw.render_mr("query-api", [mr(5, "chore: tidy", None)], SITE, ["A.", "B."])
    assert text.splitlines()[2:] == ["A.  ", "B.  ", "_Please review_"]


def test_render_many_mrs_puts_each_note_in_a_paragraph():
    mrs = [mr(5, "chore: tidy", None), mr(6, "fix: bug", None)]
    text = sw.render_mr("query-api", mrs, SITE, ["A.", "B."])
    assert text.endswith("fix: bug\n\nA.\n\nB.\n\n_Please review_")


# --- GraphQL -----------------------------------------------------------------


def test_graphql_sends_the_query_to_the_repo_host(monkeypatch):
    calls = []

    def fake_run(*cmd, stdin=None):
        calls.append((cmd, json.loads(stdin)))
        return '{"data": {"project": {"id": 1}}}'

    monkeypatch.setattr(sw, "run", fake_run)
    assert sw.graphql(REPO, "query", path=REPO.path) == {"project": {"id": 1}}
    [(cmd, body)] = calls
    assert cmd[:5] == ("glab", "api", "graphql", "--hostname", "gitlab.com")
    assert body == {"query": "query", "variables": {"path": REPO.path}}


def test_gitlab_graphql_reports_a_project_that_the_user_cannot_see(monkeypatch):
    monkeypatch.setattr(sw, "run", lambda *a, **k: '{"data": {"project": null}}')
    with pytest.raises(sw.SlackwriteError, match="no project"):
        sw.gitlab_graphql(REPO, "query")


# --- parse_mr ----------------------------------------------------------------


def test_parse_mr():
    assert sw.parse_mr(node(7, target="feat/6"), REPO) == sw.MR(
        sw.GITLAB, 7, url(7), "feat: change 7", "ACME-7", False, "feat/7", "feat/6"
    )


def test_parse_mr_keeps_the_unmerged_blockers():
    blockers = (
        blocker(5),
        blocker(6, "merged"),
        blocker(7, "closed"),
        blocker(8, project="acme/other"),
    )
    parsed = sw.parse_mr(node(9, blockers=blockers), REPO)
    assert [b.label for b in parsed.blockers] == ["!5", "!7", "acme/other!8"]
    assert parsed.blockers[2].url == url(8, "acme/other")


def test_parse_mr_warns_about_hidden_blockers(capsys):
    sw.parse_mr(node(9, hidden=2), REPO)
    assert "!9 has 2 blocking MRs that you cannot see" in capsys.readouterr().err


# --- base branches -----------------------------------------------------------

PROJECT = {
    "repository": {"rootRef": "main"},
    "branchRules": {
        "nodes": [
            {"name": "All branches", "isProtected": False},
            {"name": "develop", "isProtected": True},
            {"name": "release/*", "isProtected": True},
            {"name": "v1.*", "isProtected": True},
        ]
    },
}


def test_base_branches_are_the_default_and_the_protected():
    assert sw.base_branches(PROJECT) == ["main", "develop", "release/*", "v1.*"]


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("main", True),
        ("develop", True),
        ("release/1.2", True),
        ("release/1.2/fix", True),
        ("v1.2", True),
        ("v12", False),
        ("release", False),
        ("feat/release/1", False),
        ("developer", False),
    ],
)
def test_is_base(branch, expected):
    assert sw.is_base(branch, sw.base_branches(PROJECT)) is expected


# --- stack_parents -----------------------------------------------------------


def test_stack_parents_does_not_query_for_base_targets(monkeypatch):
    monkeypatch.setattr(sw, "graphql", lambda *a, **k: pytest.fail("queried"))
    assert sw.stack_parents(REPO, [mr(1, "x", None)], ["develop"]) == {}


def test_stack_parents_queries_each_other_target_once(monkeypatch):
    calls = []

    def fake_graphql(repo, query, **variables):
        calls.append(variables)
        nodes = [{"iid": "1", "webUrl": url(1), "sourceBranch": "feat/1"}]
        return {"project": {"mergeRequests": {"nodes": nodes}}}

    monkeypatch.setattr(sw, "graphql", fake_graphql)
    mrs = [
        sw.parse_mr(node(2, target="feat/1"), REPO),
        sw.parse_mr(node(3, target="feat/1"), REPO),
        sw.parse_mr(node(4), REPO),
    ]
    assert sw.stack_parents(REPO, mrs, ["develop"]) == {"feat/1": [ref(1)]}
    assert calls == [{"path": REPO.path, "branches": ["feat/1"]}]


# --- dependencies and render_order -------------------------------------------


def test_dependencies_state_stacks_then_blocks():
    one = sw.parse_mr(node(1), REPO)
    # !2 is stacked on !1 and also blocked by it: one dependency, not two.
    two = sw.parse_mr(node(2, "feat/1", (blocker(1), blocker(9))), REPO)
    three = sw.parse_mr(node(3, blockers=(blocker(2),)), REPO)
    deps = sw.dependencies([one, two, three], {"feat/1": [ref(1)]})
    assert deps == [
        sw.Dep(ref(2), sw.STACKED, ref(1)),
        sw.Dep(ref(2), sw.BLOCKED, ref(9)),
        sw.Dep(ref(3), sw.BLOCKED, ref(2)),
    ]


def link(iid: int) -> str:
    return f"[!{iid}]({url(iid)})"


def test_render_order_for_many_mrs():
    mrs = [sw.parse_mr(node(i), REPO) for i in (12, 13, 14, 15)]
    deps = [
        sw.Dep(ref(12), sw.BLOCKED, ref(15)),
        sw.Dep(ref(13), sw.STACKED, ref(12)),
    ]
    assert sw.render_order(mrs, deps) == (
        f"Start with {link(15)}. {link(12)} is blocked by {link(15)}."
        f" {link(13)} is stacked on {link(12)}."
    )


def test_render_order_starts_only_with_listed_mrs():
    mrs = [sw.parse_mr(node(i), REPO) for i in (1, 2)]
    deps = [sw.Dep(ref(2), sw.BLOCKED, ref(9))]
    assert sw.render_order(mrs, deps) == f"{link(2)} is blocked by {link(9)}."


def test_render_order_for_one_mr():
    deps = [
        sw.Dep(ref(2), sw.STACKED, ref(1)),
        sw.Dep(ref(2), sw.BLOCKED, ref(9)),
    ]
    mrs = [sw.parse_mr(node(2), REPO)]
    assert sw.render_order(mrs, deps) == (
        f"Stacked on {link(1)}. Blocked by {link(9)}."
    )


def test_render_order_without_dependencies():
    assert sw.render_order([sw.parse_mr(node(1), REPO)], []) == ""


def listed(*iids: int) -> list[sw.MR]:
    return [sw.parse_mr(node(i), REPO) for i in iids]


def test_render_order_follows_the_list():
    deps = [
        sw.Dep(ref(14), sw.AFTER, ref(15)),
        sw.Dep(ref(12), sw.BLOCKED, ref(15)),
    ]
    assert sw.render_order(listed(15, 12, 14), deps) == (
        f"Start with {link(15)}. {link(12)} is blocked by {link(15)}."
        f" {link(14)} merges after {link(15)}."
    )


def test_render_order_for_one_mr_with_an_added_order():
    deps = [sw.Dep(ref(14), sw.AFTER, ref(15))]
    assert sw.render_order(listed(14), deps) == f"Merges after {link(15)}."


# --- after_pair, after_deps, and merge_order ---------------------------------


@pytest.mark.parametrize("text", ["14:15", "!14:!15", "!14:15"])
def test_after_pair(text):
    assert sw.after_pair(text) == (14, 15)


@pytest.mark.parametrize("text", ["14", "14:14", "14:014", "a:b", "14:15:1", " 14:15"])
def test_after_pair_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError, match="A:B"):
        sw.after_pair(text)


def test_after_deps_add_an_order():
    assert sw.after_deps(listed(12, 14, 15), [], [(14, 15)]) == [
        sw.Dep(ref(14), sw.AFTER, ref(15))
    ]


def test_after_deps_link_an_mr_outside_the_message():
    assert sw.after_deps(listed(14), [], [(14, 42)]) == [
        sw.Dep(ref(14), sw.AFTER, ref(42))
    ]


def test_after_deps_refuse_a_waiting_mr_outside_the_message():
    with pytest.raises(sw.SlackwriteError, match="!7 is not listed"):
        sw.after_deps(listed(1), [], [(7, 1)])


def test_after_deps_skip_an_order_already_stated(capsys):
    deps = [sw.Dep(ref(12), sw.BLOCKED, ref(15))]
    pairs = [(12, 15), (15, 1), (15, 1)]
    assert sw.after_deps(listed(12, 15), deps, pairs) == [
        sw.Dep(ref(15), sw.AFTER, ref(1))
    ]
    assert capsys.readouterr().err.count("already states this order") == 2


def iids(mrs: list[sw.MR]) -> list[int]:
    return [mr.iid for mr in mrs]


def test_merge_order_keeps_number_order_without_deps():
    assert iids(sw.merge_order(listed(1, 2, 3), [])) == [1, 2, 3]


def test_merge_order_moves_each_mr_after_what_it_waits_for():
    deps = [
        sw.Dep(ref(12), sw.BLOCKED, ref(15)),
        sw.Dep(ref(13), sw.STACKED, ref(12)),
    ]
    assert iids(sw.merge_order(listed(12, 13, 14, 15), deps)) == [
        15,
        12,
        13,
        14,
    ]


def test_merge_order_places_what_an_mr_waits_for_in_number_order():
    deps = [sw.Dep(ref(1), sw.AFTER, ref(3)), sw.Dep(ref(1), sw.BLOCKED, ref(2))]
    assert iids(sw.merge_order(listed(1, 2, 3), deps)) == [2, 3, 1]


def test_merge_order_ignores_mrs_outside_the_message():
    deps = [sw.Dep(ref(2), sw.BLOCKED, ref(9))]
    assert iids(sw.merge_order(listed(1, 2), deps)) == [1, 2]


def test_merge_order_breaks_a_cycle(capsys):
    deps = [sw.Dep(ref(1), sw.AFTER, ref(2)), sw.Dep(ref(2), sw.AFTER, ref(1))]
    assert iids(sw.merge_order(listed(1, 2), deps)) == [2, 1]
    assert "cycle at !2 -> !1" in capsys.readouterr().err


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


# --- GitHub ------------------------------------------------------------------

GH_REPO = sw.Repo(Path("/r"), "github.com", "acme/query-api", "feat/9")


def pr_url(number: int, path: str = GH_REPO.path) -> str:
    return f"https://github.com/{path}/pull/{number}"


def pr_ref(number: int) -> sw.Ref:
    return sw.Ref(f"#{number}", pr_url(number))


def base_ref(parents: tuple = (), protected: bool = False, rules: tuple = ()) -> dict:
    """The base ref in a GraphQL `pr` fragment, as GitHub returns it."""
    return {
        "branchProtectionRule": {"id": "BPR_1"} if protected else None,
        "rules": {"nodes": [{"type": t} for t in rules]},
        "associatedPullRequests": {"nodes": list(parents)},
    }


def parent(number: int, path: str = GH_REPO.path) -> dict:
    return {
        "number": number,
        "url": pr_url(number, path),
        "repository": {"nameWithOwner": path},
    }


def pr_node(
    number: int, base: str = "main", ref: dict | None = None, draft: bool = False
) -> dict:
    """A GraphQL `pr` fragment, as GitHub returns it."""
    return {
        "number": number,
        "url": pr_url(number),
        "title": f"feat: change {number} (ACME-{number})",
        "isDraft": draft,
        "headRefName": f"feat/{number}",
        "baseRefName": base,
        "baseRef": base_ref() if ref is None else ref,
    }


def pr_link(number: int) -> str:
    return f"[#{number}]({pr_url(number)})"


def test_graphql_sends_a_github_query_through_gh(monkeypatch):
    calls = []

    def fake_run(*cmd, stdin=None):
        calls.append(cmd)
        return '{"data": {"repository": null}}'

    monkeypatch.setattr(sw, "run", fake_run)
    assert sw.graphql(GH_REPO, "query") == {"repository": None}
    assert calls[0][:5] == ("gh", "api", "graphql", "--hostname", "github.com")


def test_parse_pr():
    assert sw.parse_pr(pr_node(9, base="feat/8")) == sw.MR(
        sw.GITHUB, 9, pr_url(9), "feat: change 9", "ACME-9", False, "feat/9", "feat/8"
    )


def test_a_pr_ref_uses_a_hash():
    assert sw.parse_pr(pr_node(9)).ref == pr_ref(9)


def test_render_one_pr():
    text = sw.render_mr("query-api", [sw.parse_pr(pr_node(9))], SITE)
    assert text.splitlines()[0] == f"query-api: [PR #9]({pr_url(9)})  "


def test_render_many_prs():
    prs = [sw.parse_pr(pr_node(i)) for i in (8, 9)]
    text = sw.render_mr("query-api", prs, SITE)
    assert text.splitlines()[:3] == [
        "query-api PRs:",
        "",
        f"- {pr_link(8)} feat: change 8 ([ACME-8](https://{SITE}/browse/ACME-8))",
    ]


@pytest.mark.parametrize(
    ("branch", "ref", "expected"),
    [
        ("main", base_ref(), True),
        ("develop", base_ref(protected=True), True),
        ("develop", base_ref(rules=("REQUIRED_SIGNATURES", "PULL_REQUEST")), True),
        # A ruleset that applies to every branch does not make a base branch.
        ("feat/8", base_ref(rules=("REQUIRED_SIGNATURES", "DELETION")), False),
        ("feat/8", base_ref(), False),
    ],
)
def test_gh_is_base(branch, ref, expected):
    assert sw.gh_is_base(branch, ref, "main") is expected


def test_gh_parents_are_the_open_prs_from_the_base_branch():
    ref = base_ref(parents=(parent(8), parent(3, "acme/other")))
    node = pr_node(9, base="feat/8", ref=ref)
    assert sw.gh_parents(node, GH_REPO, "main") == [
        pr_ref(8),
        sw.Ref("acme/other#3", pr_url(3, "acme/other")),
    ]


def test_gh_parents_skip_a_base_branch():
    node = pr_node(9, base="main", ref=base_ref(parents=(parent(1),)))
    assert sw.gh_parents(node, GH_REPO, "main") == []


def test_gh_parents_skip_a_deleted_base_branch():
    node = pr_node(9, base="feat/8")
    node["baseRef"] = None
    assert sw.gh_parents(node, GH_REPO, "main") == []


def fake_gh(monkeypatch, data: dict) -> list[tuple[str, dict]]:
    calls = []

    def fake_graphql(repo, query, **variables):
        calls.append((query, variables))
        return data

    monkeypatch.setattr(sw, "graphql", fake_graphql)
    return calls


REPOSITORY = {"defaultBranchRef": {"name": "main"}}


def test_github_prs_mine(monkeypatch):
    nodes = [
        pr_node(10),
        pr_node(9, base="feat/8", ref=base_ref(parents=(parent(8),))),
        pr_node(8),
    ]
    calls = fake_gh(monkeypatch, {"repository": REPOSITORY, "search": {"nodes": nodes}})
    prs, parents = sw.github_prs(GH_REPO, mine=True)
    assert [pr.iid for pr in prs] == [8, 9, 10]
    assert parents == {"feat/8": [pr_ref(8)]}
    [(query, variables)] = calls
    assert query == sw.GH_QUERY_MINE
    assert variables == {
        "owner": "acme",
        "name": "query-api",
        "search": "repo:acme/query-api is:pr is:open draft:false author:@me",
    }


def test_github_prs_for_the_branch(monkeypatch):
    repository = {**REPOSITORY, "pullRequests": {"nodes": [pr_node(9)]}}
    calls = fake_gh(monkeypatch, {"repository": repository})
    prs, parents = sw.github_prs(GH_REPO, mine=False)
    assert [pr.iid for pr in prs] == [9]
    assert parents == {}
    assert calls[0][1] == {"owner": "acme", "name": "query-api", "branch": "feat/9"}


@pytest.mark.parametrize(
    ("mine", "nodes", "message"),
    [
        (False, [], "no open PR for branch 'feat/9' in acme/query-api"),
        (False, [pr_node(9), pr_node(7)], "more than one open PR: #7, #9"),
        (True, [], "you have no open, non-draft PRs in acme/query-api"),
    ],
)
def test_github_prs_refuse(monkeypatch, mine, nodes, message):
    repository = {**REPOSITORY, "pullRequests": {"nodes": nodes}}
    fake_gh(monkeypatch, {"repository": repository, "search": {"nodes": nodes}})
    with pytest.raises(sw.SlackwriteError, match=message):
        sw.github_prs(GH_REPO, mine)


def test_a_stack_of_prs_sets_the_list_and_the_order(monkeypatch):
    nodes = [
        pr_node(6),
        pr_node(8),
        pr_node(9, base="feat/8", ref=base_ref(parents=(parent(8),))),
        pr_node(10),
    ]
    fake_gh(monkeypatch, {"repository": REPOSITORY, "search": {"nodes": nodes}})
    prs, parents = sw.github_prs(GH_REPO, mine=True)
    deps = sw.dependencies(prs, parents)
    assert [pr.iid for pr in sw.merge_order(prs, deps)] == [6, 8, 9, 10]
    assert sw.render_order(prs, deps) == (
        f"Start with {pr_link(8)}. {pr_link(9)} is stacked on {pr_link(8)}."
    )


@pytest.mark.parametrize("text", ["#14:#15", "#14:15"])
def test_after_pair_takes_a_pr_reference(text):
    assert sw.after_pair(text) == (14, 15)


def test_after_deps_link_a_pr_outside_the_message():
    prs = [sw.parse_pr(pr_node(14))]
    assert sw.after_deps(prs, [], [(14, 42)]) == [
        sw.Dep(pr_ref(14), sw.AFTER, pr_ref(42))
    ]


def test_after_deps_name_a_pr_that_is_not_listed():
    with pytest.raises(sw.SlackwriteError, match="#7 is not listed"):
        sw.after_deps([sw.parse_pr(pr_node(1))], [], [(7, 1)])
