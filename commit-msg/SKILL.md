---
name: commit-msg
description: Composes a Conventional Commits message for staged changes, appends it to tmp/commit-msgs.txt, and copies it to the clipboard.
---

# Steps

1. Review the currently staged changes (`git diff --cached`).
2. Compose a Conventional Commits message.
3. Run `~/.claude/skills/commit-msg/scripts/append-msg.sh "<message>"` to append it to `tmp/commit-msgs.txt` and copy it to the clipboard.

# Guidelines

- **Format**: Follow the Conventional Commits standard. Use `type(scope): subject` when scope adds clarity; omit scope for small or single-concern projects.
- **Be concise**: A competent developer viewing the commit in `git show` or a GitLab/GitHub UI should understand the context without verbose explanations. Omit details that are obvious from the diff.
- **Line length**: Wrap the commit message body at 72 characters.
- **Body structure**: Use a bulleted list in the body when the commit covers multiple distinct changes. Otherwise, a brief statement or no body (header only) is enough.
- **Never execute the commit**: Only write the message. Let the user commit manually.

## On brevity in change descriptions

When describing changes in the body, include only what adds meaningful context beyond what's visible in the diff. Omit consequences that follow obviously from what has already been established (e.g., if the header has "switch to an async HTTP client", don't separately list "refactored methods to async" and "updated CLI to use asyncio.run").

## On breaking changes and new features

- Use `!` (breaking change) only when modifying or removing functionality that already exists in the base branch.
- Don't use migration/switch language for features being introduced for the first time. Describe them as new, even if the branch history involved trying other approaches.
- Focus on the final state vs. the base branch, not the development journey.
