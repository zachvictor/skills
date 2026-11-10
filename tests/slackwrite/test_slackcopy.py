"""Tests for slackwrite/scripts/slackcopy.

slackcopy sets the clipboard through osascript. Each test puts a fake
osascript first on PATH. The fake writes the two flavors that slackcopy hands
it to files, so no test touches the clipboard, and the tests run on Linux too.
The tests need zsh and pandoc; CI installs both on Linux.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

SLACKCOPY = Path(__file__).parents[2] / "slackwrite" / "scripts" / "slackcopy"

# The fake osascript runs through its shebang, which Windows cannot do.
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not (shutil.which("zsh") and shutil.which("pandoc")),
    reason="slackcopy needs zsh and pandoc on a POSIX system",
)

FAKE_OSASCRIPT = """\
#!/bin/sh
printf '%s' "$SLACKCOPY_HTML" > "$CAPTURE/html"
printf '%s' "$SLACKCOPY_PLAIN" > "$CAPTURE/plain"
"""

Copy = Callable[..., tuple[str, str]]


@pytest.fixture
def slackcopy(tmp_path: Path) -> Copy:
    """Return a function that runs slackcopy and returns (HTML, plain)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "osascript"
    fake.write_bytes(FAKE_OSASCRIPT.encode("utf-8"))
    fake.chmod(0o755)
    capture = tmp_path / "capture"
    capture.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE": str(capture),
    }

    def copy(source: str, name: str = "draft.md") -> tuple[str, str]:
        draft = tmp_path / name
        draft.write_bytes(source.encode("utf-8"))
        # -f skips the user's startup files, which could put the real
        # osascript back in front of the fake.
        proc = subprocess.run(
            ["zsh", "-f", str(SLACKCOPY), str(draft)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return (
            (capture / "html").read_text(encoding="utf-8"),
            (capture / "plain").read_text(encoding="utf-8"),
        )

    return copy


def test_a_hard_break_has_no_newline_after_it(slackcopy: Copy):
    # Slack pastes a newline after <br /> as a space at the start of the line.
    html, _ = slackcopy("one  \ntwo  \n_three_\n")
    assert html == "<p>one<br />two<br /><em>three</em></p>"


def test_spaces_beside_a_link_become_nbsp(slackcopy: Copy):
    html, _ = slackcopy("see [MR !1](https://e.com/1) now\n")
    assert html == '<p>see&nbsp;<a href="https://e.com/1">MR !1</a>&nbsp;now</p>'


def test_top_level_blocks_are_separated_and_list_items_are_not(slackcopy: Copy):
    html, _ = slackcopy("Intro\n\n- a\n- b\n\nOutro\n")
    assert html.splitlines() == [
        "<p>Intro</p>",
        "<p><br></p>",
        "<ul>",
        "<li>a</li>",
        "<li>b</li>",
        "</ul>",
        "<p><br></p>",
        "<p>Outro</p>",
    ]


def test_the_plain_flavor_of_markdown_is_the_source(slackcopy: Copy):
    _, plain = slackcopy("one  \n[x](https://e.com)\n")
    assert plain == "one  \n[x](https://e.com)"


def test_html_is_copied_verbatim_with_a_text_render(slackcopy: Copy):
    source = '<p>one<br />\ntwo <a href="https://e.com">x</a></p>'
    html, plain = slackcopy(source, "draft.html")
    assert html == source
    assert plain == "one\ntwo x"
