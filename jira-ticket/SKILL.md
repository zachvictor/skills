---
name: jira-ticket
description: >
  Create, update, transition, and comment on Jira tickets from the CLI via `acli`, using
  Markdown drafts such as a ticket directory's `jira.md`. Converts the Markdown to Atlassian
  Document Format and applies per-profile defaults (project, type, assignee, parent epic,
  labels) from ~/.claude/jira-ticket.toml, choosing the profile from the repo's GitLab path.
  Use this skill whenever you write to Jira: filing a ticket, posting a draft, changing a
  ticket's summary or description, moving a ticket to another status, or adding a comment.
  Also use it when you draft or edit a `jira.md` or a Jira comment: it lists the Markdown
  that converts, including reference links, setext headings, HTML entities, tables, and
  `<url>` smart links (Inline, or Card for a tracking ticket), and what it changes. Trigger
  on phrases like "create the ticket", "file a Jira ticket", "post jira.md", "update
  the ticket", "move PROJ-123 to In Review", "mark it ready for review", or "comment on
  the ticket". Read-only lookups (view, search) can call `acli` directly.
---

# Jira ticket

This skill writes to Jira through `acli jira workitem`. It owns the mechanics: the Markdown
conversion, the defaults, and the confirmation step. The draft prose follows the user's
writing rules, not this skill.

## Authorization

A write to Jira is outward-facing, like a push or a posted MR. Treat it the same way:

- Run every write with `--dry-run` first, and show the plan to the user.
- Send only after the user authorizes that action, or after the user grants writes for the
  session. A session grant ends with the session.
- With a session grant, still run the dry run and show the plan, then send without waiting.
- Use `--any` (a status off the shortlist) only when the user names that status.

## The script

`python3 ~/.claude/skills/jira-ticket/scripts/jira_ticket.py <subcommand>`

| Subcommand                                   | Purpose                                                       |
|----------------------------------------------|---------------------------------------------------------------|
| `defaults [--profile P] [--repo PATH]`       | Print the profile that applies, the reason, and the epic      |
| `create --file DRAFT [--type T] [--label L]` | Create a ticket from a `# Summary` draft                      |
| `update KEY [--file DRAFT] [--type T]`       | Replace the summary and description, change the type, or both |
| `transition KEY STATUS [--any]`              | Move a ticket to a status on the shortlist                    |
| `comment KEY --file NOTE`                    | Add a comment from a Markdown file (no title line)            |

Every write takes `--dry-run` (print the plan, send nothing) and `--show-adf` (also print
the converted document). `create` and `defaults` take `--profile` and `--repo`.

A ticket draft has the summary on its first line as `# Summary`, then a blank line, then the
description in Markdown. This is the `jira.md` format.

## Config

The script reads `$JIRA_TICKET_CONFIG`, else `~/.claude/jira-ticket.toml`. The file lives
outside the skill because the site, the project, and the epic keys are private. See
`examples/jira-ticket.example.toml` for the format.

- A **profile** holds the defaults for one kind of work: project, type, assignee, parent
  epic, and labels.
- The script chooses the profile in this order: `--profile`, then the profile whose
  `gitlab_paths` match the origin remote of the repo, then `default_profile`.
- `[project.<KEY>] types` and `statuses` are the shortlists for a Jira project. The
  comments next to them say when each one applies. Read them before you choose.

When an epic closes and the next one opens, change `parent` in the profile. The dry run
prints the parent's summary and status, so a stale epic shows in every plan.

## Workflows

### Create a ticket

1. Write `jira.md` in the ticket directory.
2. Choose the type (see below). Run `create --file <dir>/jira.md --type <T> --dry-run`.
   Check the profile line and the parent line. For work outside the matched profile, add
   `--profile`.
3. Send on authorization: run the same command without `--dry-run`. It prints the new key
   and its URL.
4. Replace the placeholder key (for example `PROJ-XXXXX`) with the new key. Rename the ticket
   directory, and update `jira.md`, `mr.md`, `commit-msgs.txt`, and any placeholder links.

The dry run warns when the draft still contains a placeholder key. In a new ticket that is
usually a self-reference to remove.

## Markdown for drafts

The converter reads CommonMark with GFM tables. Use any of this in `jira.md` and comments:

| Write                                                                                 | Jira shows                                                      |
|---------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| `## Heading`, or a line underlined with `===` (level 1) or `---` (level 2)            | A heading                                                       |
| `[text](url)`                                                                         | A link                                                          |
| `[text][label]`, `[label][]`, or `[label]`, with `[label]: url` anywhere outside code | A link. The definition lines do not show.                       |
| A bare `https://...` URL                                                              | A link that shows the URL. Avoid it: see Link forms.            |
| `<url>`                                                                               | An Inline link, or a Card when it stands alone: see Link forms. |
| `&amp;`, `&rarr;`, `&#8594;`                                                          | The character                                                   |
| `` `code` `` and fenced code blocks                                                   | Code. A fence's language is kept. Long lines wrap.              |
| Lists, tables, block quotes, `---` after a blank line                                 | The same                                                        |

Rules:

- **A line break inside a paragraph is a space**, as in the rendered Markdown. End a line
  with two spaces or a backslash for a hard break.
- **Code is literal.** A URL or a link inside a code span or a code block stays code.
- **Link text is prose.** Inside link text, a code span or a `<url>` card becomes plain
  link text. The Jira editor cannot show code style on a link.
- **Code inside bold loses the bold**, because the Jira editor cannot show both marks.
- **ADF forbids some nestings**, such as a table or a heading in a list item. The script
  stops with an error that names the nesting.
- **Dunder names need code spans.** As in CommonMark, `__init__` renders as bold "init".
- Other syntax stays literal text.

### Link forms

The Jira editor shows a link in one of five forms. Choose the form by where the link sits:

| Jira form        | Write                                         | Use it for                                                                                                 |
|------------------|-----------------------------------------------|------------------------------------------------------------------------------------------------------------|
| Hyperlinked text | `[PROJ-123](url)`, `[Wikipedia: Widget](url)` | A reference inside a sentence or a list item. It is short and keeps the flow.                              |
| Inline           | `<url>` in a list item or a table cell        | A reference that stands alone in a list or a table. For a ticket, Jira shows the key, summary, and status. |
| Card             | `<url>` alone in a top-level paragraph        | A summary view of a few related tickets, as in a tracking ticket. Most tickets do not need it.             |
| URL              | A bare URL                                    | Nothing. A protocol and a path are hard to read. The dry run warns.                                        |
| Embed            | (no syntax)                                   | Nothing. It puts a whole ticket in the description. The converter never produces it.                       |

- Do not put an Inline link in a sentence. Its status badge breaks the flow.
- When a list of references helps, end the description with a `## References` list, one
  `<url>` per item.
- A Card also shows the assignee, the update time, the start of the description, the
  priority, and an "Open preview panel" button, which opens the ticket in a side panel.
  Give each Card its own paragraph, with a blank line between Cards. ADF allows a Card only
  at the top level, so a `<url>` in a list, a table, or a block quote stays Inline.
- For one standalone Inline reference, use a one-item list. A lone `<url>` paragraph is a
  Card. The dry run counts each `blockCard`, so check that count.

### Choose the type

- Choose the type from the `types` shortlist by the content of the ticket. Do not ask the
  user first. Ask only when the content does not settle it.
- The user may name a type. If it looks wrong for the content, say so and give the reason.
  Then follow the user's decision.
- `--type` matches the shortlist without regard to case and sends Jira's spelling.

### Update a ticket

`update` replaces the whole description. Someone may have edited the ticket in Jira since
the draft was written, so check first:

1. Read the ticket: `acli jira workitem view KEY --json --fields summary,description,status`.
2. If Jira has text that the draft lacks, add that text to the draft, or ask the user.
3. Run `update KEY --file <dir>/jira.md --dry-run`, then send on authorization.

If the dry run warns that the description in Jira has content no draft can express, such as
a Card or an Embed link, do not send. Ask the user.

To change only the type, run `update KEY --type <T> --dry-run`.

### Move a ticket

Run `transition KEY "<status>" --dry-run`. The script matches the status to the shortlist
without regard to case, spaces, hyphens, or underscores, and sends Jira's spelling. A ticket
that is already in the status is left alone.

### Comment on a ticket

Write the comment as Markdown, then run `comment KEY --file <note.md> --dry-run`.

### Read a ticket

Reads need no authorization. Call `acli` directly:

- `acli jira workitem view KEY --json --fields summary,status,parent,labels,description`
- `acli jira workitem search --jql '<JQL>' --fields key,summary,status --json`

## Gotchas

- **Send text through the script, not raw `acli`.** `acli` takes plain text or ADF, not
  Markdown. A raw `--description "**x**"` shows the asterisks in Jira.
- **A pasted ticket key stays text.** Jira turns a key into a card only when someone types
  it in the editor. Write `<url>` for a card, or a labeled link.
- **`acli` cannot change a parent.** `workitem edit` has no parent flag. Set the right
  profile at creation, or change the parent in the Jira UI.
- **Auth.** If a call fails with an auth error, ask the user to run `acli auth login` in
  a terminal. It opens a browser for OAuth. Check the result with `acli auth status`.

