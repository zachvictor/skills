---
name: slackwrite
description: >
  Drafts Slack messages from repo data with fixed templates, writes each draft to a file
  in tmp/, and copies it to the clipboard as rich text that pastes cleanly into Slack. It
  does not connect to Slack. Types: `mr` asks for review of open GitLab merge requests,
  either the current branch's MR or all of the user's open MRs in the repo. Use this skill
  whenever the user wants a Slack message about their MRs. Trigger on phrases like
  "/slackwrite for this MR", "slackwrite mr", "announce this MR in Slack", "ask for review
  in Slack", "Slack message for my open MRs", or "draft a Slack post for the MR".
---

# Slackwrite

This skill prepares Slack messages. It does not post them. The user pastes each message
into Slack.

A script gathers the data, fills in a fixed template, writes the draft, and copies it.
Do not write these messages by hand. Do not gather the data with separate `git` or `glab`
calls. The script keeps the output the same every time.

## Choose the type

Take the type from the user's request. "For this MR" and "/slackwrite mr" both mean `mr`.

| Type | Message                            | Data source                                     |
|------|------------------------------------|-------------------------------------------------|
| `mr` | Asks for review of open GitLab MRs | The upstream of the current branch, then `glab` |

If the request matches no type, tell the user which types exist. Do not improvise a
message outside the script.

## Run the script

Run it from inside the repo working tree:

`~/.claude/skills/slackwrite/scripts/slackwrite.py <type> [options]`

| Command                   | The message covers                                               |
|---------------------------|------------------------------------------------------------------|
| `slackwrite.py mr`        | The open MR whose source branch is the current branch's upstream |
| `slackwrite.py mr --mine` | Every open, non-draft MR that the user authored in the repo      |

The script prints the message, the path of the draft, and a line from slackcopy. Show the
message to the user, and say that it is on the clipboard.

If the script fails, report its error. Common causes:

- The branch has no upstream. The user must push it with `git push -u`.
- The branch has no open MR, or it has more than one.
- `glab` is not authenticated. Suggest `! glab auth login --web`.
- The jira-ticket config is missing or has no `site`.

## What the script does

1. The upstream of the current branch names a remote. The URL of that remote gives the
   GitLab host and the project path. The last segment of the path is the repo name in the
   message.
2. One `glab mr list` call gives the number, URL, title, and draft state of each MR.
3. A ticket key in parentheses at the end of a title, as in
   `feat(db): add a table (ACME-123)`, becomes a Jira link. The script removes the key
   from the title that it shows. A title without a key gets no Jira link.
4. The Jira site comes from `site` in the jira-ticket config: `$JIRA_TICKET_CONFIG`, else
   `~/.claude/jira-ticket.toml`.
5. One MR gets a three-line message. Two or more MRs get a bulleted list, ordered by MR
   number.

The script warns on stderr when the branch's MR is a draft. `--mine` leaves drafts out.

## Where the draft goes

- For one MR with a ticket key: `tmp/<title>_<KEY>/slack-mr.md` under the repo root, if
  that ticket directory exists.
- In all other cases: `tmp/slackwrite/slack-mr.md`.

Each run replaces the file.

## Clipboard

`scripts/slackcopy` converts the Markdown draft to HTML with pandoc. It puts the HTML and
the Markdown source on the macOS clipboard. Slack's composer keeps the links and the
emphasis from the HTML. The script needs macOS, zsh, and pandoc.

This copy of slackcopy belongs to the skill, so the skill does not depend on a copy on
`PATH`. If you keep another copy, compare the two with `diff` when either one changes.

## Add a type

- Add a subcommand to `slackwrite.py`, with its own gather function and its own
  templates.
- Keep the data gathering in the script, so the output stays deterministic.
- When a type needs data from several GitLab endpoints (for example, an MR with its merge
  state and its deployments), consider one GraphQL query through `glab api graphql`.
- For a type whose body is prose, such as a status update, let Claude write the prose to
  a file. The script then frames the prose with its template and copies it.
