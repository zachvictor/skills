---
name: pypi-version-check
description: >
  Check Python dependencies against PyPI to ensure versions are current before committing them.
  Use this skill whenever you are adding, updating, or reviewing Python dependencies in any project.
  This includes: writing or editing requirements.txt, pyproject.toml, setup.cfg, setup.py, Pipfile,
  or conda environment.yml files; scaffolding new Python projects; adding packages to an existing
  project; reviewing pull requests or code that introduces new dependencies; or any time you are
  about to pin or specify a Python package version. Also trigger when the user asks you to "check
  dependencies", "update packages", "are my packages up to date", or mentions outdated or stale
  dependencies. The key insight is that LLMs (including Claude) and humans alike tend to specify
  package versions from memory, which are often months or years behind the latest release. This
  skill corrects that by checking PyPI every time, so versions are never stale.
---

# PyPI Version Check

## Why this skill exists

When specifying Python package versions — whether writing a fresh `requirements.txt`, adding a
dependency to `pyproject.toml`, or scaffolding a new project — both humans and LLMs tend to go
from memory. The versions recalled are often months or years out of date. This skill ensures
that every version pinned in a dependency file reflects the **latest release on PyPI** at the
time of writing.

## The rule

**Never write a Python package version from memory.** Every time you create or modify a
dependency file, run the check script with `--update` so the versions are correct from the
start. The user's git diff is the review layer — there is no need to pause for approval.

## Supported file formats

| File                  | Format                  | Notes                                     |
|-----------------------|-------------------------|--------------------------------------------|
| `requirements.txt`    | pip requirements        | Also `requirements-dev.txt`, constraints   |
| `pyproject.toml`      | PEP 621 / Poetry        | Handles both standards                     |
| `setup.cfg`           | setuptools declarative  | `[options]` install_requires               |
| `setup.py`            | setuptools imperative   | Best-effort regex parse                    |
| `Pipfile`             | Pipenv                  | `[packages]` and `[dev-packages]`          |
| `environment.yml`     | Conda + pip             | Parses the `pip:` section                  |

## How to use

The companion script `check_versions.py` (in this skill's directory) uses only the Python
standard library and queries the PyPI JSON API directly. No pip, no jq, no third-party packages.

### Default workflow (automatic)

Whenever you write or modify a dependency file, immediately run:

```bash
~/.claude/skills/pypi-version-check/scripts/check_versions.py <dependency_file> --update
```

Always invoke the script using `~/.claude/skills/...` — not the resolved absolute path.
This matters because the user's permission rules match against the command string as
written, and `~/...` won't match `/Users/someone/...` or `/home/someone/...`.

That's it. The script will:
1. Parse the file and extract all packages and their versions.
2. Query PyPI in parallel for the latest version of each package.
3. Rewrite the file in place with current versions.
4. Print a summary table so you (and the user) can see what changed.

Do not ask the user whether they want to check or update. Just do it. The git diff will
show exactly what changed, and the user can revert or adjust from there.

### Choosing an operator

The default operator is `~=` (PEP 440 "compatible release"). This means `package~=2.3.1`
allows `>=2.3.1, <2.4.0` — patch updates yes, minor-version jumps no.

Use context to pick the right operator:

| Context                        | Operator  | Flag                  |
|--------------------------------|-----------|-----------------------|
| Most projects (default)        | `~=`      | (none needed)         |
| Poetry projects                | `^`       | `--operator "^"`      |
| Docker/CI lockfiles            | `==`      | `--operator "=="`     |
| Libraries (permissive)         | `>=`      | `--operator ">="`     |
| User explicitly requests one   | (theirs)  | `--operator "..."`    |

If the existing file already uses a consistent operator style, match it rather than
overriding with the default.

### Report-only mode

If the user specifically asks to *see* what's outdated without changing anything:

```bash
~/.claude/skills/pypi-version-check/scripts/check_versions.py <dependency_file>
```

Omitting `--update` prints the table but leaves the file untouched.

## Important notes

- **Network required**: The script calls `https://pypi.org/pypi/<package>/json`.
- **Private packages**: Packages not on public PyPI will show as "not found." This is
  expected for internal packages — don't treat it as an error.
- **Pre-releases**: The script returns the latest stable release (PyPI's `info.version`
  field), which excludes pre-releases by default.
- **Speed**: 10 concurrent threads, so even large files resolve in seconds.
- **Match existing style**: If the file already pins with `==`, don't switch to `~=`
  unless the user asks. Pass `--operator "=="` to preserve the convention.
