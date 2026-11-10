# jira-ticket

Files and edits Jira tickets from Markdown drafts through the Atlassian CLI. [SKILL.md](./SKILL.md)
says what the skill does. This page says how to set it up with Claude Code or GitHub Copilot,
on macOS, Linux, or Windows.

## Requirements

- **Python 3.11 or later.** The scripts use only the standard library.
- **Git**, to read the repo's origin remote when the skill chooses a profile.
- **The Atlassian CLI, `acli`**, signed in to your Jira site.

## 1. Copy the skill where your agent looks

| Agent          | Personal (all your projects)    | Project (commit it for the team) |
|----------------|---------------------------------|----------------------------------|
| Claude Code    | `~/.claude/skills/jira-ticket`  | `.claude/skills/jira-ticket`     |
| GitHub Copilot | `~/.copilot/skills/jira-ticket` | `.github/skills/jira-ticket`     |

On Windows, `~` is your user profile folder, such as `C:\Users\name`.

macOS or Linux:

```bash
git clone https://github.com/zachvictor/skills
mkdir -p ~/.claude/skills
cp -r skills/jira-ticket ~/.claude/skills/
```

Windows, in PowerShell:

```powershell
git clone https://github.com/zachvictor/skills
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
Copy-Item -Recurse skills\jira-ticket "$HOME\.claude\skills\"
```

For Copilot, write `.copilot` in place of `.claude`. Or let the [`skills`](https://github.com/vercel-labs/skills)
CLI find your agents. It needs Node.js and runs on all three systems:

```bash
npx skills add https://github.com/zachvictor/skills --skill jira-ticket
```

Notes for Copilot:

- Copilot also reads a project's `.claude/skills/`, so one project copy there serves both
  agents. Some Copilot surfaces, such as VS Code, also read `~/.claude/skills/`.
- Use the skill where Copilot can run terminal commands: agent mode in VS Code, Visual Studio,
  or JetBrains, or the Copilot CLI. The cloud agent on GitHub.com has no `acli` and no Jira
  sign-in, so it cannot use the skill.

## 2. Install `acli` and sign in

macOS and Linux: follow Atlassian's [install guide](https://developer.atlassian.com/cloud/acli/guides/install-acli/).

Windows, in PowerShell. This puts `acli.exe` in a folder of its own and adds that folder to
your user `Path`. On an ARM machine, write `acli_windows_arm64` in place of `acli_windows_amd64`.

```powershell
$dir = "$env:LOCALAPPDATA\Programs\acli"
New-Item -ItemType Directory -Force $dir | Out-Null
Invoke-WebRequest -Uri https://acli.atlassian.com/windows/latest/acli_windows_amd64/acli.exe -OutFile "$dir\acli.exe"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
[Environment]::SetEnvironmentVariable("Path", "$userPath;$dir", "User")
```

Open a new terminal, then sign in. The login opens a browser.

```
acli auth login
acli auth status
```

Restart VS Code or Claude Code after you change `Path`, so that the agent sees `acli`.

## 3. Write the config

The script reads its defaults from one TOML file. Python finds the file, not the agent, so the
same path works for Claude Code and for Copilot:

| System          | Default config path                      |
|-----------------|------------------------------------------|
| macOS and Linux | `~/.claude/jira-ticket.toml`             |
| Windows         | `%USERPROFILE%\.claude\jira-ticket.toml` |

If your team keeps a config file in a repo, include it, and do not copy it. A copy goes stale
when the team changes a profile, such as when an epic closes. Clone the team's repo, then
write a config with an `[[include]]` table that points at your clone:

```toml
# ~/.claude/jira-ticket.toml
[[include]]
repo = "~/src/acme/platform/data"
ref = "origin/main"
path = "docs/jira-ticket.toml"
```

The script reads the file as committed at `ref`, not from the working tree. With a remote
branch such as `origin/main`, the file is as current as your last `git fetch` in the clone.
On Windows, write `repo` with forward slashes. [`examples/include.example.toml`](./examples/include.example.toml) gives the rules,
and shows how to replace a team profile with your own.

Otherwise, copy the example there and replace its values. macOS or Linux:

```bash
cp ~/.claude/skills/jira-ticket/examples/jira-ticket.example.toml ~/.claude/jira-ticket.toml
```

Windows, in PowerShell:

```powershell
Copy-Item "$HOME\.claude\skills\jira-ticket\examples\jira-ticket.example.toml" "$HOME\.claude\jira-ticket.toml"
```

To keep the file somewhere else, such as when you use Copilot and have no `.claude` folder,
point `JIRA_TICKET_CONFIG` at it. Restart the agent afterward so that it inherits the variable.

```bash
# macOS or Linux: add to ~/.zshrc or ~/.bashrc
export JIRA_TICKET_CONFIG="$HOME/.config/jira-ticket.toml"
```

```powershell
# Windows
[Environment]::SetEnvironmentVariable("JIRA_TICKET_CONFIG", "$HOME\.config\jira-ticket.toml", "User")
```

Do not put the config inside the skill folder. A reinstall replaces that folder, and a
project install is committed with the repo.

## 4. Check the setup

Run `defaults` from a repo you file tickets for. It prints the profile that applies, the reason,
and the parent epic, so it checks Python, `acli`, and the config together.

```bash
python3 ~/.claude/skills/jira-ticket/scripts/jira_ticket.py defaults
```

```powershell
py -3 "$HOME\.claude\skills\jira-ticket\scripts\jira_ticket.py" defaults
```

On Windows, run Python as `py -3` or `python`. The `python3` command is often missing there, or
it opens the Microsoft Store.
