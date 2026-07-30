"""Tests for pypi-version-check/scripts/check_versions.py.

The script's contract is narrow: given a dependency file and a map of
package -> new version, rewrite *only* the version specifiers and leave the
rest of the file byte-for-byte alone. The failure mode that motivated most of
these tests is silent — a pattern that fails to match writes the file back
unchanged while the CLI still prints "Updated", so the tests here assert on
exact output rather than on "it didn't crash".

Nothing here touches the network; only the parsers and updaters are exercised.
"""

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
# Regex fallback parser (the Python < 3.11 path, where tomllib is absent).
# Its failure mode is quiet: no deps parsed means the CLI prints
# "No dependencies found" and exits 0.
# ---------------------------------------------------------------------------

FALLBACK_CASES = [
    pytest.param(
        '[project]\ndependencies = ["requests>=2.28.0", "flask~=2.0"]\n',
        [("requests", "2.28.0"), ("flask", "2.0")],
        id="pep621-double",
    ),
    pytest.param(
        "[project]\ndependencies = ['requests>=2.28.0', 'flask~=2.0']\n",
        [("requests", "2.28.0"), ("flask", "2.0")],
        id="pep621-single",
    ),
    pytest.param(
        "[tool.poetry.dependencies]\npython = '^3.11'\nrequests = '^2.28.0'\n",
        [("requests", "2.28.0")],
        id="poetry-simple-single",
    ),
    pytest.param(
        "[tool.poetry.dependencies]\nrequests = {version = '^2.28.0'}\n",
        [("requests", "2.28.0")],
        id="poetry-table-single",
    ),
    pytest.param(
        '[tool.poetry.dependencies]\nrequests = {version = "^2.28.0"}\n',
        [("requests", "2.28.0")],
        id="poetry-table-double",
    ),
]


@pytest.mark.parametrize(("src", "expected"), FALLBACK_CASES)
def test_regex_fallback_parser_reads_both_quote_styles(tmp_path, src, expected):
    path = tmp_path / "pyproject.toml"
    path.write_text(src)
    assert cv._parse_pyproject_toml_regex(path) == expected


def test_regex_fallback_agrees_with_tomllib_on_a_single_quoted_file(tmp_path):
    src = (
        "[project]\n"
        "dependencies = ['requests>=2.28.0']\n"
        "\n"
        "[tool.poetry.dependencies]\n"
        "flask = '^2.0.1'\n"
    )
    path = tmp_path / "pyproject.toml"
    path.write_text(src)
    fallback = cv._parse_pyproject_toml_regex(path)
    assert fallback == cv._parse_pyproject_toml_tomllib(path)


def test_regex_fallback_skips_config_keys_outside_dependency_sections(tmp_path):
    src = "[tool.ruff]\ntarget-version = 'py312'\nline-length = 88\n"
    path = tmp_path / "pyproject.toml"
    path.write_text(src)
    assert cv._parse_pyproject_toml_regex(path) == []


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


def test_conda_env_round_trip(tmp_path):
    path = tmp_path / "environment.yml"
    path.write_text("dependencies:\n  - pip:\n    - requests>=2.28.0\n")
    assert cv.parse_conda_env(path) == [("requests", "2.28.0")]
    cv.update_conda_env(path, UPDATES, "~=")
    assert path.read_text() == "dependencies:\n  - pip:\n    - requests~=2.32.5\n"


def test_pipfile_round_trip(tmp_path):
    path = tmp_path / "Pipfile"
    path.write_text('[packages]\nrequests = "==2.28.0"\n')
    assert cv.parse_pipfile(path) == [("requests", "2.28.0")]
    cv.update_pipfile(path, UPDATES, "~=")
    assert path.read_text() == '[packages]\nrequests = "~=2.32.5"\n'


def test_detect_parser_routing():
    from pathlib import Path

    assert cv.detect_parser(Path("pyproject.toml")) is cv.parse_pyproject_toml
    assert cv.detect_parser(Path("requirements-dev.txt")) is cv.parse_requirements_txt
    assert cv.detect_parser(Path("environment.yaml")) is cv.parse_conda_env
    assert cv.detect_parser(Path("README.md")) is None
