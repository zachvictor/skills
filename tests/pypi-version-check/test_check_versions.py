"""Tests for pypi-version-check/scripts/check_versions.py.

The script's contract is narrow: given a dependency file and a map of
package -> new version, rewrite *only* the version specifiers and leave the
rest of the file byte-for-byte alone. The failure mode that motivated most of
these tests is silent — a pattern that fails to match writes the file back
unchanged while the CLI still prints "Updated", so the tests here assert on
exact output rather than on "it didn't crash".

Nothing here touches the network: the tests of main() replace batch_lookup.
"""

import sys

import check_versions as cv
import pytest

UPDATES = {"requests": "2.32.5", "flask": "3.1.0", "pytest": "8.4.0"}


def update(tmp_path, src, updates=UPDATES, operator="~=", name="pyproject.toml"):
    path = tmp_path / name
    path.write_text(src)
    cv.update_pyproject_toml(path, updates, operator)
    return path.read_text()


# ---------------------------------------------------------------------------
# Quote agnosticism — TOML allows both styles and ruff's quote-style='single'
# emits the single-quoted form. Each pair below must round-trip identically
# apart from the quote character.
# ---------------------------------------------------------------------------

QUOTE_CASES = [
    pytest.param(
        '[project]\ndependencies = ["requests>=2.28.0"]\n',
        '[project]\ndependencies = ["requests~=2.32.5"]\n',
        id="pep621-double",
    ),
    pytest.param(
        "[project]\ndependencies = ['requests>=2.28.0']\n",
        "[project]\ndependencies = ['requests~=2.32.5']\n",
        id="pep621-single",
    ),
    pytest.param(
        "[project]\ndependencies = ['requests[security]>=2.28.0']\n",
        "[project]\ndependencies = ['requests[security]~=2.32.5']\n",
        id="pep621-single-with-extras",
    ),
    pytest.param(
        "[project]\ndependencies = ['requests>=2.28,<3.0']\n",
        "[project]\ndependencies = ['requests~=2.32.5']\n",
        id="pep621-single-compound-specifier",
    ),
    pytest.param(
        '[tool.poetry.dependencies]\nrequests = "^2.28.0"\n',
        '[tool.poetry.dependencies]\nrequests = "~=2.32.5"\n',
        id="poetry-simple-double",
    ),
    pytest.param(
        "[tool.poetry.dependencies]\nrequests = '^2.28.0'\n",
        "[tool.poetry.dependencies]\nrequests = '~=2.32.5'\n",
        id="poetry-simple-single",
    ),
    pytest.param(
        '[tool.poetry.dependencies]\nrequests = {version = "^2.28.0"}\n',
        '[tool.poetry.dependencies]\nrequests = {version = "~=2.32.5"}\n',
        id="poetry-table-double",
    ),
    pytest.param(
        "[tool.poetry.dependencies]\n"
        "requests = {version = '^2.28.0', extras = ['s']}\n",
        "[tool.poetry.dependencies]\n"
        "requests = {version = '~=2.32.5', extras = ['s']}\n",
        id="poetry-table-single",
    ),
]


@pytest.mark.parametrize(("src", "expected"), QUOTE_CASES)
def test_updates_regardless_of_quote_style(tmp_path, src, expected):
    assert update(tmp_path, src) == expected


def test_mixed_quotes_on_one_line_both_get_updated(tmp_path):
    src = "[project]\ndependencies = [\"requests>=2.28.0\", 'flask>=2.0']\n"
    assert update(tmp_path, src) == (
        "[project]\ndependencies = [\"requests~=2.32.5\", 'flask~=3.1.0']\n"
    )


def test_quote_styles_are_never_paired_across_a_string_boundary(tmp_path):
    # A pattern that closed on ['\"] rather than a backreference could match
    # from the opening ' of one element to the closing " of the next.
    src = "[project]\ndependencies = ['flask>=2.0', \"requests>=2.28.0\"]\n"
    assert update(tmp_path, src) == (
        "[project]\ndependencies = ['flask~=3.1.0', \"requests~=2.32.5\"]\n"
    )


def test_update_is_idempotent(tmp_path):
    src = "[project]\ndependencies = ['requests>=2.28.0', 'flask~=2.0']\n"
    once = update(tmp_path, src)
    assert update(tmp_path, once) == once


def test_operator_is_honoured(tmp_path):
    src = "[project]\ndependencies = ['requests>=2.28.0']\n"
    assert update(tmp_path, src, operator="==") == (
        "[project]\ndependencies = ['requests==2.32.5']\n"
    )


def test_multiline_arrays_and_dependency_groups(tmp_path):
    src = (
        "[project]\n"
        "dependencies = [\n"
        "    'requests>=2.28.0',\n"
        "]\n"
        "\n"
        "[dependency-groups]\n"
        "dev = [\n"
        "    'pytest~=8.0',\n"
        "]\n"
    )
    assert update(tmp_path, src) == (
        "[project]\n"
        "dependencies = [\n"
        "    'requests~=2.32.5',\n"
        "]\n"
        "\n"
        "[dependency-groups]\n"
        "dev = [\n"
        "    'pytest~=8.4.0',\n"
        "]\n"
    )


# ---------------------------------------------------------------------------
# Unpinned entries. These reach the updater (the CLI marks them "pin it"), so
# leaving them alone meant the CLI reported success having changed nothing.
# ---------------------------------------------------------------------------


def test_unpinned_dependency_gets_pinned(tmp_path):
    src = '[project]\ndependencies = ["requests"]\n'
    assert update(tmp_path, src) == '[project]\ndependencies = ["requests~=2.32.5"]\n'


def test_unpinned_dependency_with_extras_gets_pinned(tmp_path):
    src = "[project]\ndependencies = ['requests[security]']\n"
    assert update(tmp_path, src) == (
        "[project]\ndependencies = ['requests[security]~=2.32.5']\n"
    )


def test_unpinned_and_pinned_entries_coexist(tmp_path):
    src = "[project]\ndependencies = [\n    'requests',\n    'flask>=2.0',\n]\n"
    assert update(tmp_path, src) == (
        "[project]\ndependencies = [\n    'requests~=2.32.5',\n    'flask~=3.1.0',\n]\n"
    )


def test_unpinned_optional_dependencies_get_pinned(tmp_path):
    src = "[project.optional-dependencies]\nweb = ['flask']\n"
    assert update(tmp_path, src) == (
        "[project.optional-dependencies]\nweb = ['flask~=3.1.0']\n"
    )


@pytest.mark.parametrize(
    "src",
    [
        pytest.param('[project]\nname = "requests"\n', id="project-name"),
        pytest.param('[project]\nkeywords = ["requests", "http"]\n', id="keywords"),
        pytest.param(
            '[project]\nclassifiers = [\n    "Framework :: requests",\n]\n',
            id="classifiers",
        ),
        pytest.param(
            '[tool.poetry]\npackages = [{include = "requests"}]\n',
            id="poetry-packages-table",
        ),
        pytest.param(
            '[project.scripts]\nrequests = "requests:main"\n',
            id="console-script-entry-point",
        ),
    ],
)
def test_bare_package_name_outside_a_dependency_array_is_untouched(tmp_path, src):
    assert update(tmp_path, src) == src


def test_include_group_table_is_not_mistaken_for_a_bare_dependency(tmp_path):
    # PEP 735 groups may hold tables as well as strings; only the strings are
    # dependencies. `{include-group = "pytest"}` names a group, not a package.
    src = '[dependency-groups]\ndev = [{include-group = "pytest"}, "requests"]\n'
    assert update(tmp_path, src) == (
        '[dependency-groups]\ndev = [{include-group = "pytest"}, "requests~=2.32.5"]\n'
    )


# ---------------------------------------------------------------------------
# Scoping: `pkg = "<string>"` is a dependency only inside a Poetry dependency
# table. Elsewhere the same shape is an entry point or ordinary config.
# ---------------------------------------------------------------------------


def test_poetry_group_dependencies_are_updated(tmp_path):
    src = "[tool.poetry.group.dev.dependencies]\npytest = '^8.0'\n"
    assert update(tmp_path, src) == (
        "[tool.poetry.group.dev.dependencies]\npytest = '~=8.4.0'\n"
    )


@pytest.mark.parametrize(
    "src",
    [
        pytest.param(
            '[tool.black]\nrequests = "not-a-dependency"\n', id="unrelated-tool-table"
        ),
        pytest.param('[project.scripts]\nflask = "flask.cli:main"\n', id="entry-point"),
        pytest.param(
            '[tool.poetry.scripts]\npytest = "pytest:main"\n', id="poetry-cli"
        ),
    ],
)
def test_key_value_outside_a_poetry_dependency_table_is_untouched(tmp_path, src):
    assert update(tmp_path, src) == src


def test_poetry_table_scoping_ends_at_the_next_header(tmp_path):
    src = (
        "[tool.poetry.dependencies]\n"
        "requests = '^2.28.0'\n"
        "\n"
        "[tool.poetry.scripts]\n"
        "flask = 'flask.cli:main'\n"
    )
    assert update(tmp_path, src) == (
        "[tool.poetry.dependencies]\n"
        "requests = '~=2.32.5'\n"
        "\n"
        "[tool.poetry.scripts]\n"
        "flask = 'flask.cli:main'\n"
    )


# ---------------------------------------------------------------------------
# Poetry entries carrying no version constraint. `pkg = "*"` used to be dropped
# by the parser, so it never reached the updater to be pinned. Entries sourced
# from somewhere other than PyPI stay dropped — there is no release to compare
# against, and reporting them would only promise a pin that cannot happen.
# ---------------------------------------------------------------------------

POETRY_DEP_CASES = [
    pytest.param('requests = "^2.28.0"', [("requests", "2.28.0")], id="pinned"),
    pytest.param("requests = '^2.28.0'", [("requests", "2.28.0")], id="pinned-single"),
    pytest.param('requests = "*"', [("requests", None)], id="star"),
    pytest.param("requests = '*'", [("requests", None)], id="star-single-quoted"),
    pytest.param('requests = {version = "*"}', [("requests", None)], id="table-star"),
    pytest.param(
        "requests = {version = '*', extras = ['socks']}",
        [("requests", None)],
        id="table-star-with-extras",
    ),
    pytest.param('requests = {git = "https://e.com/r.git"}', [], id="git-source"),
    pytest.param('requests = {path = "../requests"}', [], id="path-source"),
    pytest.param('requests = {url = "https://e.com/r.whl"}', [], id="url-source"),
    pytest.param(
        'requests = [{version = "^1.0", python = "<3.11"},'
        ' {version = "^2.0", python = ">=3.11"}]',
        [],
        id="multiple-constraints",
    ),
]


@pytest.mark.parametrize(("line", "expected"), POETRY_DEP_CASES)
def test_poetry_dependency_version_extraction(tmp_path, line, expected):
    path = tmp_path / "pyproject.toml"
    path.write_text(f"[tool.poetry.dependencies]\npython = '^3.11'\n{line}\n")
    assert cv.parse_pyproject_toml(path) == expected


def test_unpinned_poetry_dependency_gets_pinned(tmp_path):
    src = "[tool.poetry.dependencies]\npython = '^3.11'\nrequests = '*'\n"
    assert update(tmp_path, src) == (
        "[tool.poetry.dependencies]\npython = '^3.11'\nrequests = '~=2.32.5'\n"
    )


def test_unpinned_poetry_table_keeps_its_other_keys(tmp_path):
    src = '[tool.poetry.dependencies]\nrequests = {version = "*", extras = ["socks"]}\n'
    assert update(tmp_path, src) == (
        '[tool.poetry.dependencies]\nrequests = {version = "~=2.32.5",'
        ' extras = ["socks"]}\n'
    )


def test_unpinned_poetry_group_dependency_gets_pinned(tmp_path):
    src = '[tool.poetry.group.dev.dependencies]\npytest = "*"\n'
    assert update(tmp_path, src) == (
        '[tool.poetry.group.dev.dependencies]\npytest = "~=8.4.0"\n'
    )


@pytest.mark.parametrize(
    "line",
    [
        pytest.param('requests = {git = "https://e.com/r.git"}', id="git-source"),
        pytest.param('requests = {path = "../requests"}', id="path-source"),
    ],
)
def test_non_pypi_poetry_dependency_is_left_alone(tmp_path, line):
    src = f"[tool.poetry.dependencies]\n{line}\n"
    assert update(tmp_path, src) == src


def test_python_constraint_is_never_reported_or_rewritten(tmp_path):
    src = "[tool.poetry.dependencies]\npython = '*'\n"
    path = tmp_path / "pyproject.toml"
    path.write_text(src)
    assert cv.parse_pyproject_toml(path) == []
    assert update(tmp_path, src) == src


# ---------------------------------------------------------------------------
# Dependency-array scoping
# ---------------------------------------------------------------------------


def test_bracket_delta_ignores_brackets_inside_strings_and_comments():
    assert cv._bracket_delta('keywords = ["a]b"]\n') == 0
    assert cv._bracket_delta("dependencies = [  # see [the docs]\n") == 1
    assert cv._bracket_delta("    'requests[security]>=2.0',\n") == 0
    assert cv._bracket_delta("]\n") == -1


def test_dep_array_spans_selects_only_dependency_arrays():
    text = (
        "[project]\n"
        'name = "demo"\n'
        'keywords = ["requests"]\n'
        'dependencies = ["requests"]\n'
        "\n"
        "[dependency-groups]\n"
        'dev = ["pytest"]\n'
    )
    spans = cv._dep_array_spans(text)
    selected = [text[s:e] for s, e in spans]
    assert selected == ['dependencies = ["requests"]\n', 'dev = ["pytest"]\n']


def test_dep_array_spans_covers_a_multiline_array_with_nested_brackets():
    text = "[project]\ndependencies = [\n    'requests[security]>=2.0',\n]\nx = 1\n"
    ((start, end),) = cv._dep_array_spans(text)
    assert text[start:end] == "dependencies = [\n    'requests[security]>=2.0',\n]\n"


# ---------------------------------------------------------------------------
# The pyproject.toml parser reads every table that holds dependencies.
# ---------------------------------------------------------------------------


def test_pyproject_parser_reads_every_dependency_table(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text(
        "[project]\n"
        'dependencies = ["requests>=2.28.0"]\n'
        "\n[project.optional-dependencies]\n"
        'web = ["flask[async]~=2.0"]\n'
        "\n[dependency-groups]\n"
        'dev = ["pytest>=8.0", {include-group = "lint"}]\n'
        'lint = ["ruff"]\n'
        "\n[tool.uv]\n"
        'dev-dependencies = ["mypy==1.11.0"]\n'
        "\n[tool.poetry.dependencies]\n"
        'python = "^3.11"\n'
        'httpx = "^0.27.0"\n'
        "\n[tool.poetry.group.docs.dependencies]\n"
        'mkdocs = "1.6.0"\n',
        encoding="utf-8",
    )
    assert cv.parse_pyproject_toml(path) == [
        ("requests", "2.28.0"),
        ("flask", "2.0"),
        ("pytest", "8.0"),
        ("ruff", None),
        ("mypy", "1.11.0"),
        ("httpx", "0.27.0"),
        ("mkdocs", "1.6.0"),
    ]


# ---------------------------------------------------------------------------
# The other formats — these share _NAME / _EXTRAS / _OP with the pyproject
# patterns, so they are the regression guard for that extraction.
# ---------------------------------------------------------------------------


def test_requirements_txt_round_trip(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text(
        "# a comment\nrequests==2.28.0  # inline\nflask[async]>=2.0\n-e .\nunpinned\n"
    )
    assert cv.parse_requirements_txt(path) == [
        ("requests", "2.28.0"),
        ("flask", "2.0"),
        ("unpinned", None),
    ]
    cv.update_requirements_txt(path, UPDATES, "~=")
    assert path.read_text() == (
        "# a comment\nrequests~=2.32.5  # inline\nflask[async]~=3.1.0\n-e .\nunpinned\n"
    )


def test_setup_py_round_trip(tmp_path):
    path = tmp_path / "setup.py"
    path.write_text(
        "setup(\n    install_requires=['requests>=2.28.0', \"flask~=2.0\"],\n)\n"
    )
    assert cv.parse_setup_py(path) == [("requests", "2.28.0"), ("flask", "2.0")]
    cv.update_setup_py(path, UPDATES, "~=")
    assert path.read_text() == (
        "setup(\n    install_requires=['requests~=2.32.5', \"flask~=3.1.0\"],\n)\n"
    )


def test_setup_cfg_round_trip(tmp_path):
    path = tmp_path / "setup.cfg"
    path.write_text("[options]\ninstall_requires =\n    requests>=2.28.0\n")
    assert cv.parse_setup_cfg(path) == [("requests", "2.28.0")]
    cv.update_setup_cfg(path, UPDATES, "~=")
    assert path.read_text() == "[options]\ninstall_requires =\n    requests~=2.32.5\n"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            "install_requires =\n    requests>=2.28.0\n\n    # a comment\n    click\n",
            [("requests", "2.28.0"), ("click", None)],
            id="multi-line",
        ),
        pytest.param(
            "install_requires = requests>=2.28.0\n",
            [("requests", "2.28.0")],
            id="same-line",
        ),
    ],
)
def test_setup_cfg_value_ends_at_the_next_key(tmp_path, value, expected):
    path = tmp_path / "setup.cfg"
    path.write_text(
        f"[options]\n{value}python_requires = >=3.8\npackages = find:\n"
        "\n[options.extras_require]\ndev = pytest\n",
        encoding="utf-8",
    )
    assert cv.parse_setup_cfg(path) == expected


@pytest.mark.parametrize(
    "indent", [pytest.param("  ", id="indented"), pytest.param("", id="flush")]
)
def test_conda_pip_list_ends_at_the_next_conda_package(tmp_path, indent):
    path = tmp_path / "environment.yml"
    path.write_text(
        f"dependencies:\n{indent}- python=3.11\n{indent}- pip:\n"
        f"{indent}  - requests>=2.28.0\n\n{indent}  # a comment\n{indent}  - flask\n"
        f"{indent}- numpy=1.26\nchannels:\n{indent}- conda-forge\n",
        encoding="utf-8",
    )
    assert cv.parse_conda_env(path) == [("requests", "2.28.0"), ("flask", None)]


def test_conda_env_round_trip(tmp_path):
    path = tmp_path / "environment.yml"
    path.write_text("dependencies:\n  - pip:\n    - requests>=2.28.0\n")
    assert cv.parse_conda_env(path) == [("requests", "2.28.0")]
    cv.update_conda_env(path, UPDATES, "~=")
    assert path.read_text() == "dependencies:\n  - pip:\n    - requests~=2.32.5\n"


def test_pipfile_round_trip(tmp_path):
    path = tmp_path / "Pipfile"
    path.write_text(
        "[packages]\nrequests = \"==2.28.0\"\nflask = {version = '==2.0.1'}\n"
    )
    assert cv.parse_pipfile(path) == [("requests", "2.28.0"), ("flask", "2.0.1")]
    cv.update_pipfile(path, UPDATES, "~=")
    assert path.read_text() == (
        "[packages]\nrequests = \"~=2.32.5\"\nflask = {version = '~=3.1.0'}\n"
    )


def test_pipfile_inline_table_keeps_its_other_keys(tmp_path):
    path = tmp_path / "Pipfile"
    path.write_text('[packages]\nrequests = {version = "==2.28.0", extras = ["s"]}\n')
    cv.update_pipfile(path, UPDATES, "~=")
    assert path.read_text() == (
        '[packages]\nrequests = {version = "~=2.32.5", extras = ["s"]}\n'
    )


def test_pipfile_reads_only_the_package_sections(tmp_path):
    path = tmp_path / "Pipfile"
    path.write_text(
        '[[source]]\nurl = "https://pypi.org/simple"\n'
        '\n[packages]\nrequests = "==2.28.0"\n'
        '\n[dev-packages]\npytest = "*"\n'
        '\n[requires]\npython_version = "3.11"\n',
        encoding="utf-8",
    )
    assert cv.parse_pipfile(path) == [("requests", "2.28.0"), ("pytest", None)]


def test_detect_parser_routing():
    from pathlib import Path

    assert cv.detect_parser(Path("pyproject.toml")) is cv.parse_pyproject_toml
    assert cv.detect_parser(Path("requirements-dev.txt")) is cv.parse_requirements_txt
    assert cv.detect_parser(Path("environment.yaml")) is cv.parse_conda_env
    assert cv.detect_parser(Path("README.md")) is None


# ---------------------------------------------------------------------------
# Line endings. Text mode reads "\r\n" as "\n", and on Windows it writes "\n"
# as "\r\n". These tests compare bytes, so that neither translation can hide.
# ---------------------------------------------------------------------------

LINE_ENDING_CASES = [
    pytest.param(
        cv.update_requirements_txt,
        "requirements.txt",
        ["# pins", "requests==2.28.0", ""],
        ["# pins", "requests~=2.32.5", ""],
        id="requirements",
    ),
    pytest.param(
        cv.update_pyproject_toml,
        "pyproject.toml",
        [
            "[project]",
            'dependencies = ["requests>=2.28.0", "pytest"]',
            "",
            "[tool.poetry.dependencies]",
            'flask = "^2.0.1"',
            "",
        ],
        [
            "[project]",
            'dependencies = ["requests~=2.32.5", "pytest~=8.4.0"]',
            "",
            "[tool.poetry.dependencies]",
            'flask = "~=3.1.0"',
            "",
        ],
        id="pyproject",
    ),
    pytest.param(
        cv.update_setup_cfg,
        "setup.cfg",
        ["[options]", "install_requires =", "    requests>=2.28.0", ""],
        ["[options]", "install_requires =", "    requests~=2.32.5", ""],
        id="setup-cfg",
    ),
    pytest.param(
        cv.update_setup_py,
        "setup.py",
        ["setup(", '    install_requires=["requests>=2.28.0"],', ")", ""],
        ["setup(", '    install_requires=["requests~=2.32.5"],', ")", ""],
        id="setup-py",
    ),
    pytest.param(
        cv.update_pipfile,
        "Pipfile",
        ["[packages]", 'requests = "==2.28.0"', ""],
        ["[packages]", 'requests = "~=2.32.5"', ""],
        id="pipfile",
    ),
    pytest.param(
        cv.update_conda_env,
        "environment.yml",
        ["name: x", "dependencies:", "  - pip:", "    - requests==2.28.0", ""],
        ["name: x", "dependencies:", "  - pip:", "    - requests~=2.32.5", ""],
        id="conda",
    ),
]


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize(("updater", "name", "src", "want"), LINE_ENDING_CASES)
def test_updaters_keep_the_line_endings_of_the_file(
    tmp_path, eol, updater, name, src, want
):
    path = tmp_path / name
    path.write_bytes(eol.join(src).encode("utf-8"))
    updater(path, UPDATES, "~=")
    assert path.read_bytes() == eol.join(want).encode("utf-8")


def test_each_line_keeps_its_own_ending(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_bytes(b"# pins\r\nrequests==2.28.0\nflask>=2.0\r\n")
    cv.update_requirements_txt(path, UPDATES, "~=")
    assert path.read_bytes() == b"# pins\r\nrequests~=2.32.5\nflask~=3.1.0\r\n"


@pytest.mark.parametrize(
    ("updater", "name", "src", "want"),
    [
        (
            cv.update_requirements_txt,
            "requirements.txt",
            "requests==2.28.0",
            "requests~=2.32.5",
        ),
        (
            cv.update_pipfile,
            "Pipfile",
            'requests = "==2.28.0"',
            'requests = "~=2.32.5"',
        ),
    ],
    ids=["requirements", "pipfile"],
)
def test_a_file_without_a_final_newline_does_not_gain_one(
    tmp_path, updater, name, src, want
):
    path = tmp_path / name
    path.write_bytes(src.encode("utf-8"))
    updater(path, UPDATES, "~=")
    assert path.read_bytes() == want.encode("utf-8")


# ---------------------------------------------------------------------------
# requirements.txt: an update changes the specifiers and nothing else.
# ---------------------------------------------------------------------------

REQUIREMENT_LINE_CASES = [
    pytest.param(
        'requests==2.28.0 ; python_version >= "3.8"',
        'requests~=2.32.5 ; python_version >= "3.8"',
        id="marker",
    ),
    pytest.param(
        "  flask>=2.0    # web", "  flask~=3.1.0    # web", id="indent-comment"
    ),
    pytest.param("requests >= 2.28.0", "requests ~=2.32.5", id="spaced-operator"),
    pytest.param("requests>=2.0,<3.0", "requests~=2.32.5", id="specifier-list"),
    pytest.param("requests (>=2.0, <3.0)", "requests ~=2.32.5", id="parenthesized"),
    pytest.param("flask[async] >=2.0", "flask[async] ~=3.1.0", id="extras"),
    pytest.param("requests==2.28.0 \\", "requests~=2.32.5 \\", id="continuation"),
    pytest.param(
        'pytest ; python_version >= "3.8"',
        'pytest~=8.4.0 ; python_version >= "3.8"',
        id="unpinned-marker",
    ),
    pytest.param("pytest  # tests", "pytest~=8.4.0  # tests", id="unpinned-comment"),
    pytest.param(
        "requests @ https://example.com/requests.whl",
        "requests @ https://example.com/requests.whl",
        id="direct-reference",
    ),
    pytest.param("    --hash=sha256:abc", "    --hash=sha256:abc", id="hash-line"),
    pytest.param("requests-oauthlib==1.0", "requests-oauthlib==1.0", id="longer-name"),
]


@pytest.mark.parametrize(("src", "want"), REQUIREMENT_LINE_CASES)
def test_requirements_update_changes_only_the_specifiers(tmp_path, src, want):
    path = tmp_path / "requirements.txt"
    path.write_bytes(f"{src}\n".encode())
    cv.update_requirements_txt(path, UPDATES, "~=")
    assert path.read_bytes() == f"{want}\n".encode()


def test_requirements_parser_skips_a_direct_reference(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_bytes(b"requests @ https://example.com/requests.whl\nflask>=2.0\n")
    assert cv.parse_requirements_txt(path) == [("flask", "2.0")]


# ---------------------------------------------------------------------------
# main(): the report and the exit status. The status is 0 when nothing needs a
# change and after --update, 1 when a package needs a change or is not on
# PyPI, and 2 when the file cannot be checked.
# ---------------------------------------------------------------------------


def check(monkeypatch, path, latest, *flags):
    """Run main() with `latest` as PyPI's answers; return the exit status."""
    monkeypatch.setattr(
        cv, "batch_lookup", lambda names: {name: latest.get(name) for name in names}
    )
    monkeypatch.setattr(sys, "argv", ["check_versions.py", str(path), *flags])
    with pytest.raises(SystemExit) as stop:
        cv.main()
    return stop.value.code


MAIN_CASES = [
    pytest.param(
        "requests==2.32.5\n", 0, "All dependencies are up to date!", id="current"
    ),
    pytest.param("requests==2.28.0\n", 1, "⬆ outdated", id="outdated"),
    pytest.param("requests\n", 1, "📌 pin it", id="unpinned"),
    pytest.param("private-pkg==1.0\n", 1, "could not be found on PyPI", id="not-found"),
    pytest.param("# no pins\n", 0, "No dependencies found", id="empty"),
]


@pytest.mark.parametrize(("src", "status", "message"), MAIN_CASES)
def test_main_reports_and_exits_without_changing_the_file(
    monkeypatch, capsys, tmp_path, src, status, message
):
    path = tmp_path / "requirements.txt"
    path.write_text(src, encoding="utf-8")
    assert check(monkeypatch, path, {"requests": "2.32.5"}) == status
    assert message in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == src


def test_main_update_rewrites_only_what_is_behind(monkeypatch, capsys, tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("requests==2.28.0\nflask==3.1.0\n", encoding="utf-8")
    latest = {"requests": "2.32.5", "flask": "3.1.0"}
    assert check(monkeypatch, path, latest, "--update", "--operator", ">=") == 0
    assert path.read_text(encoding="utf-8") == "requests>=2.32.5\nflask==3.1.0\n"
    assert "Updated requirements.txt with operator '>='" in capsys.readouterr().out


def test_main_refuses_a_missing_file_and_an_unknown_kind(monkeypatch, capsys, tmp_path):
    assert check(monkeypatch, tmp_path / "requirements.txt", {}) == 2
    assert "not found" in capsys.readouterr().err
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    assert check(monkeypatch, tmp_path / "README.md", {}) == 2
    assert "don't know how to parse README.md" in capsys.readouterr().err
