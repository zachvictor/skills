# Skills

Agent skills for use with agentic coding tools. These skills follow the [Agent Skills](https://agentskills.io) open standard and work with Claude Code, Codex, OpenCode, and other compatible tools.

## Skills

| Skill                                       | Description                                                                                                                                                                                         |
|---------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| [commit-msg](./commit-msg/)                 | Composes a [Conventional Commits](https://www.conventionalcommits.org/) message for staged changes, appends it to `tmp/commit-msgs.txt`, and copies it to the clipboard.                            |
| [pypi-version-check](./pypi-version-check/) | Checks Python dependencies against PyPI to ensure versions are current before committing. Supports `requirements.txt`, `pyproject.toml`, `setup.cfg`, `setup.py`, `Pipfile`, and `environment.yml`. |

## Installation

Copy any skill folder into the skills directory for your tool:

```bash
# Claude Code
cp -r commit-msg ~/.claude/skills/

# Codex
cp -r commit-msg ~/.codex/skills/

# OpenCode
cp -r commit-msg ~/.config/opencode/skills/
```

Or install into a project so the whole team gets it:

```bash
# Claude Code
cp -r commit-msg .claude/skills/

# Codex
cp -r commit-msg .codex/skills/

# OpenCode
cp -r commit-msg .opencode/skills/
```

You can also use Vercel's [`skills`](https://github.com/vercel-labs/skills) CLI to auto-detect your tools:

```bash
npx skills add https://github.com/zachvictor/skills --skill commit-msg
```

## Structure

Each skill is a self-contained directory following the [Agent Skills specification](https://agentskills.io/specification):

```
skill-name/
├── SKILL.md          # Instructions and metadata (required)
└── scripts/          # Helper scripts (optional)
    └── ...
```

## License

MIT
