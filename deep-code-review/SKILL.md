---
name: deep-code-review
description: >
  Review discipline for a diff, branch, or MR/PR: severity-labelled findings in a numbered
  report, no approve/reject verdict, and a checklist for what a change dropped rather than
  what it added. Use when the user asks for a deep review, a self-review before posting, or
  names this skill. A plain "review this" or a /code-review bug hunt does not need it.
---

# Stance

- Present findings; don't render a verdict. The human approves or rejects. If nothing is
  worth flagging, say so plainly: "I reviewed the diff and found no concerns."
- Distinguish what this change introduced from what it inherited. Note pre-existing problems
  briefly and attribute them correctly; don't block the change on them.
- Scope to what the change touches. Don't redesign the feature in a review.
- When a concern depends on context you lack (public or internal? sensitive data?), ask a
  question rather than issuing a verdict.
- If the change is too large or mixes concerns, say so; that is itself a finding.
- Project conventions live in the repo's `CLAUDE.md` or `AGENTS.md`. Read them first.

# Severity

| Label         | Meaning                                                  |
|---------------|----------------------------------------------------------|
| `blocking:`   | Must be resolved before merge.                           |
| `concern:`    | Real, worth addressing, not a blocker. The default tier. |
| `question:`   | Need context before judging. Default when unsure.        |
| `suggestion:` | An alternative worth considering, not a mandate.         |
| `nit:`        | Style or polish. Take it or leave it.                    |

Don't inflate. A misleading name is a concern; a synonym preference is a nit; a missing auth
check is blocking. If one nit recurs many times, that pattern is a concern.

# Read for what got dropped

The diff shows what was added. Bugs in refactors hide in what the new version no longer
does. For each modified function, list the old version's arguments, return values, and side
effects, then confirm each is preserved, intentionally changed with a test, or intentionally
removed with a callsite sweep. Green tests establish none of these; they cover units, and
these bugs live on the wires between units. Read the diff forward (does the new code do
everything the old code did?) and backward (does it reference anything the old code never
produced?). Trace one real input through the whole call graph.

Mechanically detectable seam bugs, each worth a grep:

- **Dropped arguments.** A hardcoded `=None` or `=False` beside a dict or object that still
  carries the value. Blocking.
- **Swallowed return values.** A bool- or error-returning helper called as if it returned
  nothing. Blocking.
- **Undefined variables in fallback paths.** Typos the happy path never runs. In shell, check
  whether `set -u` is on. Blocking.
- **User strings concatenated into program text.** SQL, shell, jq, regex. Pass data as
  parameters, never as program text. Blocking.
- **Stale references to deleted things,** and **docs that announce a migration the code has
  not finished.** Grep for the old name before calling adjacent code "preserved." Blocking.
- **Early returns inside `with` or `try/finally`** that skip summary or exit-status logic.
  Concern.
- **Functions whose only callers are tests.** Dead after a split. Concern.

Also look for what is missing entirely: tests the description claims, error handling,
validation, a migration, config for a new flag, docs for a new env var.

# Pin the seams with contract tests

For every seam a refactor crosses (a changed signature, renamed dict keys, a reordered
interface), ask for one test that pins the wire, not the body: stub the downstream
collaborator with a capturing fake, run the upstream code on realistic input, and assert on
what the fake received. It runs in milliseconds and fails only when the contract changes.

# Report format

Number findings, most severe first, and use this template so each is addressable:

```
## F1 [blocking] <short title>

**Where:** <file:line or function>
**Why it matters:** <the actual risk>
**Evidence:** <excerpt, grep result, or reasoning>
**Suggested fix:** <concrete code or one line>
```

Lead with what works. Cap at roughly ten findings; more than that means the change should be
split, and that becomes F0. Don't be the reviewer who always finds one more thing.

# Author mode

For an MR/PR title: imperative, present tense, no process words ("Fix row count", not
"Fixing" or "Investigate"). For the description: context, what changed, why, how to verify,
stated scope and known limits. Write the final state, not the development journey. For
commit messages, defer to the `commit-msg` skill when installed; otherwise Conventional
Commits, imperative subject under 72 characters.
