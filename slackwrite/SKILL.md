---
name: slackwrite
description: >
  Drafts Slack messages from repo data with fixed templates, writes each draft to a file
  in tmp/, and copies it to the clipboard as rich text that pastes cleanly into Slack. It
  does not connect to Slack. Types: `mr` asks for review of open GitLab merge requests or
  GitHub pull requests, either the current branch's MR or PR, or all of the user's open
  ones in the repo. The message states the merge order that the forge records (stacked
  MRs and PRs, GitLab MR dependencies), and Claude checks memory and past sessions for an
  order that the forge does not record. Use this skill whenever the user wants a Slack
  message about their MRs or PRs. Trigger on phrases like "/slackwrite for this MR",
  "slackwrite mr", "announce this MR in Slack", "ask for review in Slack", "Slack message
  for my open MRs", "Slack message asking for review of these PRs", or "draft a Slack post
  for the MR".
---

# Slackwrite

This skill prepares Slack messages. It does not post them. The user pastes each message
into Slack.

A script gathers the data, fills in a fixed template, writes the draft, and copies it.
Do not write these messages by hand. Do not gather the message data with separate `git`,
`glab`, or `gh` calls. The script keeps the output the same every time. The one part that
you write is a note on a merge order that the forge does not record (see Merge order).

In this skill, "MR" means a GitLab merge request or a GitHub pull request. The message
uses the forge's own words: `MR !12` on GitLab, `PR #12` on GitHub.

## Choose the type

Take the type from the user's request. "For this MR" and "/slackwrite mr" both mean `mr`.

| Type | Message                     | Data source                                                               |
|------|-----------------------------|---------------------------------------------------------------------------|
| `mr` | Asks for review of open MRs | The upstream of the current branch, then `glab` (GitLab) or `gh` (GitHub) |

If the request matches no type, tell the user which types exist. Do not improvise a
message outside the script.

## Run the script

Run it from inside the repo working tree:

`~/.claude/skills/slackwrite/scripts/slackwrite.py <type> [options]`

| Command                         | The message covers                                               |
|---------------------------------|------------------------------------------------------------------|
| `slackwrite.py mr`              | The open MR whose source branch is the current branch's upstream |
| `slackwrite.py mr --mine`       | Every open, non-draft MR that the user authored in the repo      |
| `slackwrite.py mr --after A:B`  | Either form, with the order "A merges after B" (repeatable)      |
| `slackwrite.py mr ... --note T` | The same, with the sentence `T` above the closing request        |

The script prints the message, the path of the draft, and a line from slackcopy. Then do
the checks in Merge order. Show the final message to the user, and say that it is on the
clipboard.

If the script fails, report its error. Common causes:

- The branch has no upstream. The user must push it with `git push -u`.
- The branch has no open MR, or it has more than one.
- `glab` is not authenticated. Suggest `! glab auth login --web`.
- `gh` is not authenticated. Suggest `! gh auth login --web`.
- The CLI's user cannot see the project.
- The jira-ticket config is missing or has no `site`.

## Merge order

The script states the merge order that the forge records:

- **Stacked on:** the MR targets the source branch of another open MR. A target that is
  a base branch does not count:
  - On GitLab, a base branch is the default branch or a protected branch.
  - On GitHub, a base branch is the default branch, a branch with a classic protection
    rule, or a branch where a ruleset requires a pull request. Other ruleset rules do not
    count, because a ruleset such as "require signed commits" can apply to every branch.
- **Blocked by** (GitLab only): GitLab lists an unmerged MR as a dependency of the MR. A
  blocker that is also the stack parent is not stated twice. GitHub has no MR
  dependencies.
- **Merges after:** an order that you add with `--after A:B`, for "A merges after B". A
  must be in the message. A and B are MR numbers, with or without the `!` or `#`.
- **Start with:** for two or more MRs, the listed MRs that others wait for and that wait
  for none.

The bulleted list follows the merge order. It starts in MR number order, and moves each
MR after the listed MRs that it waits for. If the order has a cycle, the script warns
and the list ignores the step that closes it.

The forge does not record every order. A past session may have found that one MR must
merge first, or that two MRs change the same files. Look for such an order on every run:

1. Run the script. Take each MR's reference and ticket key from the message.
2. Run `find_leads.py` with one group of terms per MR:

   `~/.claude/skills/slackwrite/scripts/find_leads.py '!12,ACME-1' '!13,ACME-2'`

   Give each MR's reference: `!N` on GitLab, `#N` on GitHub. The script also matches
   `<repo>!N` or `<repo>#N`, and the URL of the MR. It prints every lead in the repo's
   Claude Code memory files, then the newest leads in past session transcripts. A lead is a passage that names two of the MRs, or one MR and
   a merge-order phrase. Read the memory leads first. They are the curated record.
3. Treat each lead as a claim from its date, not as a fact. Keep a lead only if it still
   holds. Check it against the current state, for example:
   - Read the MR's description: `glab mr view <iid>` or `gh pr view <number>`.
   - Compare the files that two MRs change:
     `git diff --name-only origin/<target>...origin/<source>`.
   - Read the file that the lead names, on the MR's branch:
     `git show origin/<source>:<path>`.
   - Drop a lead about a merged MR, or about an order that the script already states.
4. Act on each lead that holds:
   - If it is an order that the reviewers need, run the script again with `--after`, so
     the order also sets the list. Give the reason with `--note`, in one or two short
     sentences in the style of the message:
     `--after 13:12 --note '!13 has the same race condition as !12.'`
   - If the reviewers need the lead but it sets no order, such as a decision not to
     block, use `--note` alone.
   - If the lead offers more than one fix, or the fix changes an MR, do not choose. Ask
     the user.
5. In the reply, list each lead that you kept or that needs a decision. Give its source
   (the file and the date) and how you checked it. Say in one line how many leads you
   dropped.

Do not add a note that you could not check. Report it to the user instead.

## What the script does

1. The upstream of the current branch names a remote. The URL of that remote gives the
   host and the project path. The last segment of the path is the repo name in the
   message.
2. A remote on `github.com` is a GitHub repo, and `gh` supplies the data and the auth. A
   remote on any other host is a GitLab instance, and `glab` supplies them.
3. On GitLab, one GraphQL query through `glab api graphql` gives each MR's number, URL,
   title, draft state, branches, and blocking MRs. It also gives the project's default
   branch and protected branches. A second query runs only when an MR targets a branch
   that is not a base branch. It finds the open MRs whose source branch is that target.
4. On GitHub, one GraphQL query through `gh api graphql` gives each PR's number, URL,
   title, draft state, and branches, and the repo's default branch. For each PR's base
   branch, it also gives the protection and the open PRs from that branch. With
   `--mine`, the query searches the repo for `is:pr is:open draft:false author:@me`.
5. A ticket key in parentheses at the end of a title, as in
   `feat(db): add a table (ACME-123)`, becomes a Jira link. The script removes the key
   from the title that it shows. A title without a key gets no Jira link.
6. The Jira site comes from `site` in the jira-ticket config: `$JIRA_TICKET_CONFIG`, else
   `~/.claude/jira-ticket.toml`.
7. One MR gets a three-line message. Two or more MRs get a bulleted list in merge order.
   The merge order and the note go above the closing request: on a line each for one MR,
   as a paragraph each for more.

The script warns on stderr when the branch's MR is a draft, and when a GitLab MR has
blocking MRs that the user cannot see. `--mine` leaves drafts out.

## What find_leads.py searches

- The repo is the one that `slackwrite.py` uses. The upstream of the current branch gives
  the host and the project path.
- An MR reference in the forge's form (`!N` on GitLab, `#N` on GitHub) also matches
  `<repo>!N` or `<repo>#N`, and the URL of the MR. Other terms match as given.
- It searches the project directories in `~/.claude/projects` (or
  `$CLAUDE_CONFIG_DIR/projects`) for these paths:
  - the main worktree, and each worktree whose path starts with the path of the main
    worktree
  - each clone whose path ends in the project path, and the worktrees of that clone
- In each one: `memory/*.md`, dated by the last change of the file, and the session
  transcripts `*.jsonl`, dated by each entry. Dates are UTC.
- It skips the current session (`$CLAUDE_CODE_SESSION_ID`), because its content is
  already in context.
- It skips the work of this skill in a transcript: the load of the skill, each tool call
  that runs, reads, or edits `slackwrite.py` or `find_leads.py`, and the result of that
  call. These repeat what the skill printed.
- It removes the line numbers of file reads and the markers of diff lines, so a line
  that was read many times gives one lead. It shows each passage once, at its newest
  date.
- `--limit` caps the transcript leads (default 30). `--per-session` caps the leads from
  one session (default 5), so that one long session cannot fill the list.
- The source of a lead is its project directory, then the memory file or the first 8
  characters of the session ID.

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
- When a type needs data from several endpoints (for example, an MR with its merge state
  and its deployments), consider one GraphQL query through `glab api graphql` or
  `gh api graphql`.
- For a type whose body is prose, such as a status update, let Claude write the prose to
  a file. The script then frames the prose with its template and copies it.
