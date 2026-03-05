#!/usr/bin/env bash
# append-msg.sh — Append a commit message to tmp/commit-msgs.txt and copy to clipboard.
# Usage: append-msg.sh "commit message text"

set -euo pipefail

msg="${1:?Usage: append-msg.sh \"commit message text\"}"
dir="tmp"
file="$dir/commit-msgs.txt"

mkdir -p "$dir"

if [ -s "$file" ]; then
    printf '\n---\n\n' >> "$file"
fi

printf '%s\n' "$msg" >> "$file"

# Copy to clipboard (macOS: pbcopy, Linux/Wayland: wl-copy, Linux/X11: xclip)
if command -v pbcopy &>/dev/null; then
    printf '%s' "$msg" | pbcopy
elif command -v wl-copy &>/dev/null; then
    printf '%s' "$msg" | wl-copy
elif command -v xclip &>/dev/null; then
    printf '%s' "$msg" | xclip -selection clipboard
else
    echo "Warning: no clipboard utility found; message not copied." >&2
fi
