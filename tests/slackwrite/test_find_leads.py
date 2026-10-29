import json
import os
from datetime import UTC, datetime
from pathlib import Path

import find_leads as fl
import pytest

import slackwrite as sw

TERMS = fl.Terms.parse(["!12,ACME-1", "!15,ACME-2"])
REPO = sw.Repo(Path("/r"), "gitlab.example.com", "acme/data/repo-x", "main")


def lead_texts(text: str) -> list[tuple[list[str], str]]:
    return list(fl.passages(text, TERMS))


# --- Terms -------------------------------------------------------------------


def test_terms_map_each_term_to_the_first_term_of_its_group():
    assert TERMS.owner == {
        "!12": "!12",
        "ACME-1": "!12",
        "!15": "!15",
        "ACME-2": "!15",
    }


def test_terms_refuse_a_group_without_terms():
    with pytest.raises(fl.LeadsError, match="no terms"):
        fl.Terms.parse(["!1", " , "])


def test_terms_refuse_a_term_in_two_groups():
    with pytest.raises(fl.LeadsError, match="more than one group"):
        fl.Terms.parse(["!1,ACME-1", "!2,ACME-1"])


def test_terms_match_whole_terms_only():
    # "_" and "/" bound a term, as in a ticket directory name.
    assert TERMS.named("!120 ACME-20 x!12 tmp/fix_ACME-1/") == ["!12"]


def test_terms_name_the_mrs_in_the_order_of_their_groups():
    assert TERMS.named("ACME-2, then !12") == ["!12", "!15"]


def test_terms_prefer_the_longest_term():
    terms = fl.Terms.parse(["!1,ACME-1", "!2,ACME-1-b"])
    assert terms.named("see ACME-1-b") == ["!2"]


SPELLED = fl.Terms.parse(["!12,ACME-1", "!13"], REPO)


@pytest.mark.parametrize(
    "text",
    [
        "!12",
        "repo-x!12",
        "acme/data/repo-x!12",
        "https://gitlab.example.com/acme/data/repo-x/-/merge_requests/12",
        "(https://gitlab.example.com/acme/data/repo-x/-/merge_requests/12#note_9)",
        "gitlab.example.com/acme/data/repo-x/-/merge_requests/12/diffs",
    ],
)
def test_terms_with_a_repo_match_each_spelling_of_an_mr(text):
    assert SPELLED.named(text) == ["!12"]


@pytest.mark.parametrize(
    "text",
    [
        "other-repo-x!12",  # another project
        "repo-x!120",
        "https://gitlab.example.com/acme/data/other/-/merge_requests/12",
        "https://gitlab.example.com/acme/data/repo-x/-/merge_requests/120",
        "https://my-gitlab.example.com/acme/data/repo-x/-/merge_requests/12",
    ],
)
def test_terms_with_a_repo_do_not_match_another_mr(text):
    assert SPELLED.named(text) == []


def test_terms_probe_only_for_the_terms_that_hold_no_other_term():
    assert sorted(SPELLED.probes) == sorted(
        [
            "!12",
            "gitlab.example.com/acme/data/repo-x/-/merge_requests/12",
            "ACME-1",
            "!13",
            "gitlab.example.com/acme/data/repo-x/-/merge_requests/13",
        ]
    )


def test_terms_with_a_repo_spell_out_only_mr_numbers():
    assert sorted(SPELLED.owner) == sorted(
        [
            "!12",
            "repo-x!12",
            "gitlab.example.com/acme/data/repo-x/-/merge_requests/12",
            "ACME-1",
            "!13",
            "repo-x!13",
            "gitlab.example.com/acme/data/repo-x/-/merge_requests/13",
        ]
    )


# --- passages ----------------------------------------------------------------


def test_a_passage_that_names_two_mrs_is_a_lead():
    assert lead_texts("!12 and !15 edit one file") == [
        (["!12", "!15"], "!12 and !15 edit one file")
    ]


def test_a_passage_with_one_mr_and_an_order_phrase_is_a_lead():
    assert lead_texts("ACME-1 is stacked on the docs branch") == [
        (["!12"], "ACME-1 is stacked on the docs branch")
    ]


@pytest.mark.parametrize(
    "text",
    ["!12 is green", "stacked on nothing here", "a code block for !12"],
)
def test_a_passage_without_two_mrs_or_an_order_phrase_is_not_a_lead(text):
    assert lead_texts(text) == []


def test_a_lead_stays_within_its_line():
    assert lead_texts("!12 is green\nmerge order\n!15 is green") == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("    12\t- !12 depends on it", "- !12 depends on it"),
        ("+!12 depends on it", "!12 depends on it"),
        ("-!12 depends on it", "!12 depends on it"),
        ("- !12 depends on it", "- !12 depends on it"),
    ],
)
def test_passages_drop_line_numbers_and_diff_markers(line, expected):
    assert lead_texts(line) == [(["!12"], expected)]


def test_a_long_line_is_clipped_around_the_lead():
    line = "a " * 300 + "!12 blocked by it" + " z" * 300
    [(_, text)] = lead_texts(line)
    assert text.startswith("…") and text.endswith("…")
    assert "!12 blocked by it" in text
    assert len(text) < len(line)


def test_overlapping_leads_in_a_line_merge():
    line = "!12 conflicts" + " x" * 50 + " with !15 too"
    assert lead_texts(line) == [(["!12", "!15"], " ".join(line.split()))]


# --- sources -----------------------------------------------------------------


def write_transcript(path: Path, entries: list[object]) -> None:
    lines = [e if isinstance(e, str) else json.dumps(e) for e in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def entry(day: str, text: str) -> dict:
    return {
        "timestamp": f"{day}T01:02:03.000Z",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def test_transcript_leads_read_every_string_of_an_entry(tmp_path):
    t = tmp_path / "s.jsonl"
    write_transcript(
        t,
        [
            {"type": "mode", "mode": "normal"},
            entry("2026-10-20", "Merge !12 after !15."),
            {"toolUseResult": {"stdout": ["!12 is blocked by !15"]}},
            '{"text": "!12 blocked by',  # an entry written only in part
        ],
    )
    leads = list(fl.transcript_leads(t, "wt/s", TERMS))
    assert leads == [
        fl.Lead("2026-10-20", "wt/s", ("!12", "!15"), "Merge !12 after !15."),
        fl.Lead("", "wt/s", ("!12", "!15"), "!12 is blocked by !15"),
    ]


def test_strings_walk_nested_values():
    value = {"a": ["x", {"b": "y"}, 3, None], "c": "z"}
    assert list(fl.strings(value)) == ["x", "y", "z"]


# --- this skill's own blocks -------------------------------------------------


def blocks_entry(*blocks: dict) -> dict:
    return {"timestamp": "2026-10-20T01:00:00Z", "message": {"content": list(blocks)}}


def call(tool_id: str, name: str = "Bash", **tool_input: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def result(tool_id: str, text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": text}


def text(value: str) -> dict:
    return {"type": "text", "text": value}


def test_transcript_leads_skip_the_skills_own_calls_and_results(tmp_path):
    t = tmp_path / "s.jsonl"
    run = "~/.claude/skills/slackwrite/scripts/slackwrite.py mr --mine"
    write_transcript(
        t,
        [
            # The call names no MR, but its result does.
            blocks_entry(text("Merge !12 after !15."), call("own", command=run)),
            {
                **blocks_entry(result("own", "!12 is blocked by !15")),
                "toolUseResult": {"stdout": "!12 is blocked by !15"},
            },
            # A logged API request repeats the result beside other blocks.
            blocks_entry(
                result("own", "!12 is blocked by !15"),
                result("other", "!12 and !15 edit one file"),
            ),
            blocks_entry(call("lf", command="find_leads.py '!12' '!15'")),
            blocks_entry(call("rd", "Read", file_path="/r/scripts/find_leads.py")),
            blocks_entry(result("rd", "    9\tsuch as `!12,ACME-1` and `!15`")),
        ],
    )
    leads = [lead.text for lead in fl.transcript_leads(t, "s", TERMS)]
    assert leads == ["Merge !12 after !15.", "!12 and !15 edit one file"]


def test_a_test_file_is_not_one_of_the_skills_scripts():
    own: set[str] = set()
    entry = blocks_entry(call("t", command="pytest tests/x/test_find_leads.py"))
    assert fl.drop_own(entry, own) == entry
    assert own == set()


def test_drop_own_drops_a_load_of_this_skill():
    load = "Base directory for this skill: /h/.claude/skills/slackwrite\n\n# Slackwrite"
    other = "Base directory for this skill: /h/.claude/skills/commit-msg\n\n!12"
    entry = blocks_entry(text(load), text(other))
    assert fl.drop_own(entry, set())["message"]["content"] == [text(other)]


# --- project_dirs ------------------------------------------------------------


def test_project_dirs_take_every_clone_and_worktree(tmp_path):
    names = [
        "-Users-z-klon-acme-data-repo-x",  # this clone
        "-Users-z-klon-acme-data-repo-x--wt-a",  # a worktree under it
        "-Users-z-klon-acme-data-repo-x-b",  # a worktree beside it
        "-Users-z-acme-data-repo-x",  # another clone
        "-Users-z-acme-data-repo-x-tmp-wt-c",  # a worktree of the other clone
        "-Users-z-acme-data-repo-xy",  # another repo
        "-Users-z-acme-data-other-repo-x",  # another repo
        "-Users-z-acme-data",  # the group
    ]
    for name in names:
        (tmp_path / name).mkdir()
    (tmp_path / "-Users-z-acme-data-repo-x.txt").write_text("", encoding="utf-8")
    dirs = fl.project_dirs(Path("/Users/z/klon/acme/data/repo-x"), REPO, tmp_path)
    assert [d.name for d in dirs] == sorted(names[:5])


def test_project_dirs_take_a_clone_outside_a_mirrored_tree(tmp_path):
    for name in ["-Users-z-code-repo-x", "-Users-z-code-repo-x-wt", "-Users-z-code"]:
        (tmp_path / name).mkdir()
    dirs = fl.project_dirs(Path("/Users/z/code/repo.x"), REPO, tmp_path)
    assert [d.name for d in dirs] == ["-Users-z-code-repo-x", "-Users-z-code-repo-x-wt"]


# --- find_leads and render ---------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> Path:
    d = tmp_path / "-r"
    (d / "memory").mkdir(parents=True)
    md = d / "memory" / "notes.md"
    md.write_text("# Notes\n\nMerge !12 after !15.\n", encoding="utf-8")
    stamp = datetime(2026, 10, 24, 23, 30, tzinfo=UTC).timestamp()
    os.utime(md, (stamp, stamp))
    write_transcript(
        d / "aaaaaaaa-1.jsonl",
        [
            entry("2026-10-21", "Merge !12 after !15."),
            entry("2026-10-21", "!12 conflicts with ACME-2"),
        ],
    )
    write_transcript(
        d / "bbbbbbbb-2.jsonl",
        [
            entry("2026-10-23", "!12 conflicts with ACME-2"),
            entry("2026-10-22", "ACME-1 depends on the seed"),
        ],
    )
    write_transcript(d / "cccccccc-3.jsonl", [entry("2026-10-27", "!12 and !15")])
    return d


def test_find_leads(project):
    memory, transcripts = fl.find_leads([project], TERMS, "cccccccc-3")
    assert memory == [
        fl.Lead(
            "2026-10-24",
            "-r/memory/notes.md",
            ("!12", "!15"),
            "Merge !12 after !15.",
        )
    ]
    # A memory text is not repeated; a repeated text keeps its newest date; the
    # skipped session gives nothing.
    assert transcripts == [
        fl.Lead(
            "2026-10-23",
            "-r/bbbbbbbb",
            ("!12", "!15"),
            "!12 conflicts with ACME-2",
        ),
        fl.Lead("2026-10-22", "-r/bbbbbbbb", ("!12",), "ACME-1 depends on the seed"),
    ]


def test_render_shows_every_memory_lead_and_the_newest_transcript_leads(project):
    memory, transcripts = fl.find_leads([project], TERMS, None)
    text = fl.render(memory, transcripts, limit=1, per_session=5)
    assert text == (
        "## Memory leads: 1\n\n"
        "2026-10-24  -r/memory/notes.md  !12 !15\n"
        "  Merge !12 after !15.\n\n"
        "## Transcript leads: 3 in 2 sessions, 1 shown"
        " (newest first, at most 5 a session)\n\n"
        "2026-10-27  -r/cccccccc  !12 !15\n"
        "  !12 and !15"
    )


def test_render_caps_the_transcript_leads_of_one_session(project):
    memory, transcripts = fl.find_leads([project], TERMS, None)
    text = fl.render(memory, transcripts, limit=30, per_session=1)
    assert "3 in 2 sessions, 2 shown" in text
    assert "!12 conflicts with ACME-2" in text  # the newest of -r/bbbbbbbb
    assert "depends on the seed" not in text


def test_per_source_keeps_the_order():
    pairs = [("a", "1"), ("b", "1"), ("a", "2"), ("a", "3"), ("b", "2")]
    leads = [fl.Lead("d", source, (), text) for source, text in pairs]
    assert [lead.text for lead in fl.per_source(leads, 2)] == ["1", "1", "2", "2"]
    assert [lead.source for lead in fl.per_source(leads, 2)] == ["a", "b", "a", "b"]
