#!/usr/bin/env python3
"""
check_versions.py — Check Python dependencies against PyPI for latest versions.

Supports: requirements.txt, pyproject.toml, setup.cfg, setup.py, Pipfile,
          environment.yml / environment.yaml (conda)

Usage:
    python check_versions.py <dependency_file> [--update] [--operator OP]

Options:
    --update        Rewrite the file in place with updated versions
    --operator OP   Version operator to use when updating (default: ~=)
                    Common choices: ~= (compatible), ==, >=, ^= (Poetry)

Exit codes:
    0   All dependencies are up to date (or file was updated successfully)
    1   One or more dependencies are outdated
    2   Error (file not found, parse failure, etc.)

Only stdlib — no pip, no jq, no third-party packages required.
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# PyPI lookup
# ---------------------------------------------------------------------------


def get_latest_version(package_name: str) -> str | None:
    """Return the latest version string from PyPI, or None on failure."""
    # Normalize: PyPI uses lowercase, hyphens
    normalized = re.sub(r"[-_.]+", "-", package_name).lower()
    url = f"https://pypi.org/pypi/{normalized}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["info"]["version"]
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, Exception):
        return None


def batch_lookup(packages: list[str]) -> dict[str, str | None]:
    """Look up latest versions for a list of packages in parallel."""
    results = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(get_latest_version, p): p for p in packages}
        for fut in as_completed(futures):
            pkg = futures[fut]
            results[pkg] = fut.result()
    return results


# ---------------------------------------------------------------------------
# Parsers — each returns list of (package_name, current_version_or_None)
# ---------------------------------------------------------------------------

# Matches lines like:  requests==2.31.0  or  flask>=2.0  or  pytest~=7.0
_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9_][A-Za-z0-9._-]*)"  # package name
    r"\s*(?:\[.*?\])?"  # optional extras [security]
    r"\s*([~=!<>^]=?|===?)?"  # operator (optional)
    r"\s*([0-9][0-9A-Za-z.*]*)?",  # version (optional)
    re.MULTILINE,
)


def parse_requirements_txt(path: Path) -> list[tuple[str, str | None]]:
    """Parse requirements.txt (also handles constraints files)."""
    deps = []
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _REQ_RE.match(line)
        if m:
            deps.append((m.group(1), m.group(3) or None))
    return deps


def _parse_poetry_deps(
    section: dict,
) -> list[tuple[str, str | None]]:
    """Extract (name, version) pairs from a Poetry dependency mapping."""
    deps = []
    for name, val in section.items():
        if name.lower() == "python":
            continue
        ver_str = (
            val
            if isinstance(val, str)
            else (val.get("version", "") if isinstance(val, dict) else "")
        )
        ver = re.sub(r"[^0-9.]", "", ver_str).strip(".")
        if ver:
            deps.append((name, ver))
    return deps


def _parse_pyproject_toml_tomllib(path: Path) -> list[tuple[str, str | None]] | None:
    """Try parsing with tomllib (Python 3.11+). Returns None if unavailable."""
    try:
        import tomllib
    except ImportError:
        return None

    with open(path, "rb") as f:
        data = tomllib.load(f)

    deps = []

    # PEP 621: [project] dependencies and optional-dependencies
    for dep_str in data.get("project", {}).get("dependencies", []):
        m = _REQ_RE.match(dep_str)
        if m:
            deps.append((m.group(1), m.group(3) or None))
    for group_deps in data.get("project", {}).get("optional-dependencies", {}).values():
        for dep_str in group_deps:
            m = _REQ_RE.match(dep_str)
            if m:
                deps.append((m.group(1), m.group(3) or None))

    # PEP 735: [dependency-groups] — used by uv and modern tooling
    # Each group is an array of dependency strings, same format as PEP 621
    for group_deps in data.get("dependency-groups", {}).values():
        for item in group_deps:
            # Items can be strings ("pytest>=8.0") or tables ({include-group = "..."})
            if isinstance(item, str):
                m = _REQ_RE.match(item)
                if m:
                    deps.append((m.group(1), m.group(3) or None))

    # uv: [tool.uv] dev-dependencies (same array-of-strings format as PEP 621)
    uv = data.get("tool", {}).get("uv", {})
    for dep_str in uv.get("dev-dependencies", []):
        m = _REQ_RE.match(dep_str)
        if m:
            deps.append((m.group(1), m.group(3) or None))

    # Poetry: [tool.poetry.dependencies], [tool.poetry.dev-dependencies],
    #         [tool.poetry.group.*.dependencies]
    poetry = data.get("tool", {}).get("poetry", {})
    for section_key in ("dependencies", "dev-dependencies"):
        deps.extend(_parse_poetry_deps(poetry.get(section_key, {})))

    for group in poetry.get("group", {}).values():
        deps.extend(_parse_poetry_deps(group.get("dependencies", {})))

    return deps


# Regex for detecting Poetry dependency section headers
_POETRY_DEP_SECTION_RE = re.compile(
    r"^\[tool\.poetry\.(?:dependencies|dev-dependencies|group\.[^]]+\.dependencies)\]",
    re.MULTILINE,
)


def _parse_pyproject_toml_regex(path: Path) -> list[tuple[str, str | None]]:
    """Regex fallback for Python < 3.11 where tomllib is unavailable."""
    text = path.read_text()
    deps = []

    # PEP 621: dependencies = ["requests>=2.28", "flask~=2.0"]
    # These are string items inside arrays — safe to grab globally because
    # only dependency strings look like "package>=version" inside quotes.
    _PEP621_RE = (
        r'"([A-Za-z0-9_][A-Za-z0-9._-]*'
        r'(?:\[.*?\])?\s*(?:[~=!<>^]=?|===?)\s*[0-9][^"]*)"'
    )
    dep_strings = re.findall(_PEP621_RE, text)
    for ds in dep_strings:
        m = _REQ_RE.match(ds)
        if m:
            deps.append((m.group(1), m.group(3) or None))

    # Poetry: key = "^2.28" or key = {version = "^2.28", ...}
    # Only match these inside Poetry dependency sections to avoid picking up
    # config keys like requires-python, target-version, etc.
    seen = {d[0].lower() for d in deps}
    lines = text.splitlines()
    in_poetry_deps = False
    for line in lines:
        stripped = line.strip()
        # Track which TOML section we're in
        if stripped.startswith("["):
            in_poetry_deps = bool(_POETRY_DEP_SECTION_RE.match(stripped))
            continue
        if not in_poetry_deps:
            continue

        # Match: package = "^1.0"
        m_inline = re.match(r'^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*=\s*"([^"]*)"', stripped)
        if not m_inline:
            # Match: package = {version = "^1.0", ...}
            m_inline = re.match(
                r'^([A-Za-z0-9_][A-Za-z0-9._-]*)\s*=\s*\{[^}]*version\s*=\s*"([^"]*)"',
                stripped,
            )
        if m_inline:
            name, ver_str = m_inline.group(1), m_inline.group(2)
            if name.lower() in seen or name.lower() == "python":
                continue
            ver = re.sub(r"[^0-9.]", "", ver_str).strip(".")
            if ver:
                deps.append((name, ver))
                seen.add(name.lower())

    return deps


def parse_pyproject_toml(path: Path) -> list[tuple[str, str | None]]:
    """Parse pyproject.toml — handles PEP 621 dependencies and Poetry.

    Uses tomllib (Python 3.11+) when available for accurate parsing,
    with a regex fallback for older Python versions.
    """
    result = _parse_pyproject_toml_tomllib(path)
    if result is not None:
        return result
    return _parse_pyproject_toml_regex(path)


def parse_setup_cfg(path: Path) -> list[tuple[str, str | None]]:
    """Parse setup.cfg [options] install_requires."""
    text = path.read_text()
    deps = []
    in_deps = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("install_requires"):
            in_deps = True
            # Value might be on same line after '='
            after_eq = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            if after_eq:
                m = _REQ_RE.match(after_eq)
                if m:
                    deps.append((m.group(1), m.group(3) or None))
            continue
        if in_deps:
            if (
                stripped
                and not stripped.startswith("[")
                and not stripped.startswith("#")
            ):
                m = _REQ_RE.match(stripped)
                if m:
                    deps.append((m.group(1), m.group(3) or None))
            elif stripped.startswith("[") or (
                stripped
                and "=" in stripped
                and not stripped[0].isspace()
                and line[0] != " "
                and line[0] != "\t"
            ):
                break
            elif not stripped:
                # blank line might end the section — but often they continue
                continue
    return deps


def parse_setup_py(path: Path) -> list[tuple[str, str | None]]:
    """Best-effort parse of setup.py install_requires list."""
    text = path.read_text()
    deps = []
    # Find install_requires=[...] block
    m = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", text, re.DOTALL)
    if m:
        for dep_str in re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)):
            dm = _REQ_RE.match(dep_str)
            if dm:
                deps.append((dm.group(1), dm.group(3) or None))
    return deps


def parse_pipfile(path: Path) -> list[tuple[str, str | None]]:
    """Parse Pipfile [packages] and [dev-packages] sections."""
    text = path.read_text()
    deps = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and ("packages" in stripped.lower()):
            in_section = True
            continue
        elif stripped.startswith("["):
            in_section = False
            continue
        if in_section and "=" in stripped:
            name = stripped.split("=")[0].strip().strip('"').strip("'")
            val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            if name.lower() in ("python_version", "python_full_version"):
                continue
            ver = re.sub(r"[^0-9.]", "", val).strip(".")
            deps.append((name, ver if ver else None))
    return deps


def parse_conda_env(path: Path) -> list[tuple[str, str | None]]:
    """Parse conda environment.yml — extract pip dependencies section."""
    text = path.read_text()
    deps = []
    in_pip = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "- pip:":
            in_pip = True
            continue
        if in_pip:
            if stripped.startswith("- "):
                dep = stripped[2:].strip()
                m = _REQ_RE.match(dep)
                if m:
                    deps.append((m.group(1), m.group(3) or None))
            elif (
                not stripped.startswith("#")
                and stripped
                and not stripped.startswith("-")
            ):
                # exited pip section
                in_pip = False
    return deps


# ---------------------------------------------------------------------------
# File type detection
# ---------------------------------------------------------------------------

PARSERS = {
    "requirements.txt": parse_requirements_txt,
    "requirements-dev.txt": parse_requirements_txt,
    "requirements_dev.txt": parse_requirements_txt,
    "constraints.txt": parse_requirements_txt,
    "pyproject.toml": parse_pyproject_toml,
    "setup.cfg": parse_setup_cfg,
    "setup.py": parse_setup_py,
    "Pipfile": parse_pipfile,
    "environment.yml": parse_conda_env,
    "environment.yaml": parse_conda_env,
}


def detect_parser(path: Path):
    """Return the appropriate parser function for the given file."""
    name = path.name
    if name in PARSERS:
        return PARSERS[name]
    # Fallback heuristics
    if name.endswith(".txt") and "require" in name.lower():
        return parse_requirements_txt
    if name.endswith(".txt") and "constraint" in name.lower():
        return parse_requirements_txt
    if name.endswith(".toml"):
        return parse_pyproject_toml
    if name.endswith((".yml", ".yaml")):
        return parse_conda_env
    return None


# ---------------------------------------------------------------------------
# Updaters — rewrite file with new versions
# ---------------------------------------------------------------------------


def update_requirements_txt(path: Path, updates: dict[str, str], operator: str):
    """Rewrite a requirements-style file with updated versions."""
    lines = path.read_text().splitlines()
    new_lines = []
    for line in lines:
        stripped = line.split("#")[0].strip()
        if stripped and not stripped.startswith("-"):
            m = _REQ_RE.match(stripped)
            if m and m.group(1) in updates:
                pkg = m.group(1)
                extras = ""
                extras_m = re.search(r"(\[.*?\])", stripped)
                if extras_m:
                    extras = extras_m.group(1)
                comment = ""
                if "#" in line:
                    comment = "  " + line[line.index("#") :]
                new_lines.append(f"{pkg}{extras}{operator}{updates[pkg]}{comment}")
                continue
        new_lines.append(line)
    path.write_text("\n".join(new_lines) + "\n")


def update_pyproject_toml(path: Path, updates: dict[str, str], operator: str):
    """Rewrite pyproject.toml dependency version strings."""
    text = path.read_text()

    # Update PEP 621 style: "package>=1.0" → "package~=2.0"
    for pkg, ver in updates.items():
        # Match "pkg(extras)?OP version" inside quotes
        pattern = re.compile(
            r'("'
            + re.escape(pkg)
            + r'(?:\[.*?\])?\s*)([~=!<>^]=?|===?)(\s*[0-9][^"]*")',
            re.IGNORECASE,
        )
        if pattern.search(text):
            text = pattern.sub(lambda m: m.group(1) + operator + ver + '"', text)
        else:
            # Poetry style: pkg = "^1.0"  → pkg = "~=2.0"
            pattern2 = re.compile(
                r"^(" + re.escape(pkg) + r'\s*=\s*")([^"]*)(")',
                re.MULTILINE | re.IGNORECASE,
            )
            text = pattern2.sub(
                lambda m: m.group(1) + operator + ver + m.group(3), text
            )

            # Poetry table style: pkg = {version = "^1.0", ...}
            pattern3 = re.compile(
                r"^(" + re.escape(pkg) + r'\s*=\s*\{[^}]*version\s*=\s*")([^"]*)(")',
                re.MULTILINE | re.IGNORECASE,
            )
            text = pattern3.sub(
                lambda m: m.group(1) + operator + ver + m.group(3), text
            )

    path.write_text(text)


def update_setup_cfg(path: Path, updates: dict[str, str], operator: str):
    """Rewrite setup.cfg install_requires."""
    text = path.read_text()
    for pkg, ver in updates.items():
        pattern = re.compile(
            r"(" + re.escape(pkg) + r"(?:\[.*?\])?\s*)([~=!<>^]=?|===?)(\s*[0-9]\S*)",
            re.IGNORECASE,
        )
        text = pattern.sub(lambda m: m.group(1) + operator + ver, text)
    path.write_text(text)


def update_setup_py(path: Path, updates: dict[str, str], operator: str):
    """Rewrite setup.py install_requires."""
    text = path.read_text()
    for pkg, ver in updates.items():
        # Match inside quoted strings
        pattern = re.compile(
            r"""(['"])("""
            + re.escape(pkg)
            + r"""(?:\[.*?\])?\s*)([~=!<>^]=?|===?)(\s*[0-9][^'"]*['"])""",
            re.IGNORECASE,
        )
        text = pattern.sub(
            lambda m: m.group(1) + m.group(2) + operator + ver + m.group(1),
            text,
        )
    path.write_text(text)


def update_pipfile(path: Path, updates: dict[str, str], operator: str):
    """Rewrite Pipfile package versions."""
    lines = path.read_text().splitlines()
    new_lines = []
    for line in lines:
        replaced = False
        for pkg, ver in updates.items():
            if line.strip().lower().startswith(pkg.lower()):
                # Preserve quoting style
                if '= "' in line or "= '" in line:
                    q = '"' if '"' in line else "'"
                    new_lines.append(f"{pkg} = {q}{operator}{ver}{q}")
                    replaced = True
                    break
                elif "= {" in line:
                    # Table inline — update version inside
                    new_line = re.sub(
                        r'(version\s*=\s*["\'])([^"\']*)',
                        lambda m: m.group(1) + operator + ver,
                        line,
                    )
                    new_lines.append(new_line)
                    replaced = True
                    break
        if not replaced:
            new_lines.append(line)
    path.write_text("\n".join(new_lines) + "\n")


def update_conda_env(path: Path, updates: dict[str, str], operator: str):
    """Rewrite conda environment.yml pip section."""
    text = path.read_text()
    for pkg, ver in updates.items():
        pattern = re.compile(
            r"(-\s+"
            + re.escape(pkg)
            + r"(?:\[.*?\])?\s*)([~=!<>^]=?|===?)(\s*[0-9]\S*)",
            re.IGNORECASE,
        )
        text = pattern.sub(lambda m: m.group(1) + operator + ver, text)
    path.write_text(text)


UPDATERS = {
    parse_requirements_txt: update_requirements_txt,
    parse_pyproject_toml: update_pyproject_toml,
    parse_setup_cfg: update_setup_cfg,
    parse_setup_py: update_setup_py,
    parse_pipfile: update_pipfile,
    parse_conda_env: update_conda_env,
}


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def format_table(rows: list[tuple[str, str, str, str]]) -> str:
    """Format a simple text table."""
    if not rows:
        return "  (no dependencies found)"
    headers = ("Package", "Current", "Latest", "Status")
    widths = [max(len(r[i]) for r in [headers] + rows) for i in range(4)]
    sep = "  ".join("-" * w for w in widths)
    hdr = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    lines = [hdr, sep]
    for row in rows:
        lines.append("  ".join(val.ljust(w) for val, w in zip(row, widths)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Check Python dependencies against PyPI for latest versions.",
    )
    parser.add_argument("file", help="Path to dependency file")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Rewrite the file with latest versions",
    )
    parser.add_argument(
        "--operator",
        default="~=",
        help="Version operator to use when updating (default: ~=)",
    )
    args = parser.parse_args()

    path = Path(args.file).resolve()
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        sys.exit(2)

    parse_fn = detect_parser(path)
    if parse_fn is None:
        print(f"Error: don't know how to parse {path.name}", file=sys.stderr)
        supported = (
            "requirements.txt, pyproject.toml, setup.cfg,"
            " setup.py, Pipfile, environment.yml"
        )
        print(f"Supported: {supported}", file=sys.stderr)
        sys.exit(2)

    deps = parse_fn(path)
    if not deps:
        print(f"No dependencies found in {path.name}")
        sys.exit(0)

    pkg_names = [d[0] for d in deps]
    print(f"Checking {len(pkg_names)} packages against PyPI...\n")
    latest = batch_lookup(pkg_names)

    rows = []
    outdated = {}
    for name, current_ver in deps:
        latest_ver = latest.get(name)
        if latest_ver is None:
            rows.append((name, current_ver or "any", "???", "⚠ not found"))
        elif current_ver is None:
            rows.append((name, "unpinned", latest_ver, "📌 pin it"))
            outdated[name] = latest_ver
        elif current_ver == latest_ver:
            rows.append((name, current_ver, latest_ver, "✅ up to date"))
        else:
            rows.append((name, current_ver, latest_ver, "⬆ outdated"))
            outdated[name] = latest_ver

    print(format_table(rows))
    print()

    not_found = sum(1 for r in rows if "not found" in r[3])
    if not outdated and not_found == 0:
        print("All dependencies are up to date!")
        sys.exit(0)
    elif not outdated and not_found > 0:
        print(f"⚠ {not_found} package(s) could not be found on PyPI.")
        print("  This may indicate network issues or private/internal packages.")
        sys.exit(1)

    print(f"{len(outdated)} package(s) can be updated.\n")

    if args.update:
        updater = UPDATERS.get(parse_fn)
        if updater is None:
            print("Error: no updater for this file type", file=sys.stderr)
            sys.exit(2)
        updater(path, outdated, args.operator)
        print(f"✅ Updated {path.name} with operator '{args.operator}'")
        sys.exit(0)
    else:
        print(f"Run with --update to rewrite {path.name} (operator: {args.operator})")
        sys.exit(1)


if __name__ == "__main__":
    main()
