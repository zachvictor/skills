---
name: slackwrite
description: >
  Drafts Slack messages from repo data with fixed templates, writes each draft to a file
  in tmp/, and copies it to the clipboard as rich text that pastes cleanly into Slack. It
  does not connect to Slack. Types: `mr` asks for review of open GitLab merge requests,
  either the current branch's MR or all of the user's open MRs in the repo. The message
  states the merge order that GitLab records (stacked MRs, MR dependencies), and Claude
  checks memory and past sessions for an order that GitLab does not record. Use this
  skill whenever the user wants a Slack message about their MRs. Trigger on phrases like
  "/slackwrite for this MR", "slackwrite mr", "announce this MR in Slack", "ask for review
  in Slack", "Slack message for my open MRs", or "draft a Slack post for the MR".
---

# Slackwrite

This skill prepares Slack messages. It does not post them. The user pastes each message
into Slack.

A script gathers the data, fills in a fixed template, writes the draft, and copies it.
Do not write these messages by hand. Do not gather the message data with separate `git`
or `glab` calls. The script keeps the output the same every time. The one part that you
write is a note on a merge order that GitLab does not record (see Merge order).

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

| Command                         | The message covers                                               |
|---------------------------------|------------------------------------------------------------------|
| `slackwrite.py mr`              | The open MR whose source branch is the current branch's upstream |
| `slackwrite.py mr --mine`       | Every open, non-draft MR that the user authored in the repo      |
| `slackwrite.py mr ... --note T` | The same, with the sentence `T` above the closing request        |

The script prints the message, the path of the draft, and a line from slackcopy. Then do
the checks in Merge order. Show the final message to the user, and say that it is on the
clipboard.

If the script fails, report its error. Common causes:

- The branch has no upstream. The user must push it with `git push -u`.
- The branch has no open MR, or it has more than one.
- `glab` is not authenticated. Suggest `! glab auth login --web`.
- The glab user cannot see the project.
- The jira-ticket config is missing or has no `site`.

## Merge order

The script states the merge order that GitLab records:

- **Stacked on:** the MR targets the source branch of another open MR. A target that is
  the default branch or a protected branch does not count.
- **Blocked by:** GitLab lists an unmerged MR as a dependency of the MR. A blocker that
  is also the stack parent is not stated twice.
- **Start with:** for two or more MRs, the listed MRs that others wait for and that wait
  for none.

GitLab does not record every order. A past session may have found that one MR must merge
first, or that two MRs change the same files. Look for such an order on every run:

1. Run the script. Take each MR's reference and ticket key from the message.
2. Run `find_leads.py` with one group of terms per MR:

   `~/.claude/skills/slackwrite/scripts/find_leads.py '!12,ACME-1' '!13,ACME-2'`

   It prints every lead in the repo's Claude Code memory files, then the newest leads in
   past session transcripts. A lead is a passage that names two of the MRs, or one MR and
   a merge-order phrase. Read the memory leads first. They are the curated record.
3. Treat each lead as a claim from its date, not as a fact. Keep a lead only if it still
   holds. Check it against the current state, for example:
   - Read the MR's description: `glab mr view <iid>`.
   - Compare the files that two MRs change:
     `git diff --name-only origin/<target>...origin/<source>`.
   - Read the file that the lead names, on the MR's branch:
     `git show origin/<source>:<path>`.
   - Drop a lead about a merged MR, or about an order that the script already states.
4. Act on each lead that holds:
   - If it is an order that the reviewers need, run the script again with `--note`. Write
     one or two short sentences in the style of the message:
     `--note 'Merge !13 after !12 too: its migration has the same race condition.'`
   - If the lead offers more than one fix, or the fix changes an MR, do not choose. Ask
     the user.
5. In the reply, list each lead that you kept or that needs a decision. Give its source
   (the file and the date) and how you checked it. Say in one line how many leads you
   dropped.

Do not add a note that you could not check. Report it to the user instead.

## What the script does

1. The upstream of the current branch names a remote. The URL of that remote gives the
   GitLab host and the project path. The last segment of the path is the repo name in the
   message.
2. One GraphQL query through `glab api graphql` gives each MR's number, URL, title, draft
   state, branches, and blocking MRs. It also gives the project's default branch and
   protected branches.
3. A second query runs only when an MR targets a branch that is neither the default
   branch nor protected. It finds the open MRs whose source branch is that target.
4. A ticket key in parentheses at the end of a title, as in
   `feat(db): add a table (ACME-123)`, becomes a Jira link. The script removes the key
   from the title that it shows. A title without a key gets no Jira link.
5. The Jira site comes from `site` in the jira-ticket config: `$JIRA_TICKET_CONFIG`, else
   `~/.claude/jira-ticket.toml`.
6. One MR gets a three-line message. Two or more MRs get a bulleted list, ordered by MR
   number. The merge order and the note go above the closing request: on a line each for
   one MR, as a paragraph each for more.

The script warns on stderr when the branch's MR is a draft, and when an MR has blocking
MRs that the user cannot see. `--mine` leaves drafts out.

## What find_leads.py searches

- The project directories in `~/.claude/projects` (or `$CLAUDE_CONFIG_DIR/projects`)
  whose names start with the name of the repo's main worktree. These include the
  worktrees under it.
- In each one: `memory/*.md`, dated by the last change of the file, and the session
  transcripts `*.jsonl`, dated by each entry. Dates are UTC.
- It skips the current session (`$CLAUDE_CODE_SESSION_ID`), because its content is
  already in context.
- It removes the line numbers of file reads and the markers of diff lines, so a line
  that was read many times gives one lead. It shows each passage once, at its newest
  date. `--limit` caps the transcript leads (default 30).

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
