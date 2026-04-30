#!/usr/bin/env bash
# Waypoint Claude transcript enumerator (remote helper).
#
# Walks $CLAUDE_CONFIG_DIR/projects/ (default ~/.claude/projects) and emits
# one JSON object per resumable transcript to stdout. Designed to run
# under the existing `bash -ilc` SSH wrapper.
#
# Output begins with the literal line `__WP_BEGIN__` so the parent can
# discard rcfile noise that login shells may have written ahead of us.
#
# Per-line robustness comes from a two-stage jq: each line is parsed with
# `fromjson? // empty` (corrupt lines just drop) before being slurped
# into the metadata extractor. A truncated or unreadable file logs a
# stderr advisory and is skipped — never fails the batch.
#
# Optional env:
#   WAYPOINT_THREAD_ID    - if set, emit only the matching transcript.
#   WAYPOINT_THREAD_LIMIT - cap on transcripts emitted (default 200).
#                           When uncapped enumeration would exceed the
#                           cap, newest-by-mtime wins.
#
# Exit codes:
#   0  - success (possibly empty result)
#   64 - missing dependency (jq); stderr carries a human-readable line

set -u
LC_ALL=C
export LC_ALL

SENTINEL="__WP_BEGIN__"

if ! command -v jq >/dev/null 2>&1; then
    echo "waypoint-enumerator: jq is required on the remote host" >&2
    exit 64
fi
if ! command -v perl >/dev/null 2>&1; then
    echo "waypoint-enumerator: perl is required on the remote host" >&2
    exit 64
fi

ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
LIMIT="${WAYPOINT_THREAD_LIMIT:-200}"
FILTER_ID="${WAYPOINT_THREAD_ID:-}"

echo "$SENTINEL"

if [ ! -d "$ROOT" ]; then
    exit 0
fi

UUID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

# Collect candidate paths first so we can sort by mtime and apply the
# cap. ls -t handles both GNU and BSD; perl prints mtime as epoch.
shopt -s nullglob
candidates=()
for proj in "$ROOT"/*/; do
    for transcript in "$proj"*.jsonl; do
        base="${transcript##*/}"
        base="${base%.jsonl}"
        [[ "$base" =~ $UUID_RE ]] || continue
        if [ -n "$FILTER_ID" ] && [ "$base" != "$FILTER_ID" ]; then
            continue
        fi
        candidates+=("$transcript")
    done
done

if [ ${#candidates[@]} -eq 0 ]; then
    exit 0
fi

# Sort newest-first by mtime via a perl one-liner (portable across GNU
# and BSD; jq has no stat()). Output is `<mtime>\t<path>` per line.
sorted=$(
    perl -e '
        for my $f (@ARGV) {
            my @s = stat($f);
            next unless @s;
            print "$s[9]\t$f\n";
        }
    ' "${candidates[@]}" 2>/dev/null | sort -t $'\t' -k1,1nr
)

count=0
while IFS=$'\t' read -r mtime path; do
    [ -n "$path" ] || continue
    if [ "$count" -ge "$LIMIT" ]; then
        break
    fi
    base="${path##*/}"
    base="${base%.jsonl}"

    head_bytes=$(head -c 4194304 "$path" 2>/dev/null) || {
        echo "waypoint-enumerator: skipping unreadable $path" >&2
        continue
    }
    head_lines=$(printf '%s' "$head_bytes" | head -n 200)
    if [ -z "$head_lines" ]; then
        continue
    fi

    record=$(printf '%s\n' "$head_lines" \
        | jq -c -R 'fromjson? // empty' 2>/dev/null \
        | jq -cs --arg id "$base" --argjson mtime "$mtime" '
            map(select(type == "object")) as $rs |
            (first($rs[] | select(.cwd | type == "string" and length > 0) | .cwd) // null) as $cwd |
            (first($rs[] | select(.gitBranch | type == "string" and length > 0) | .gitBranch) // null) as $branch |
            (first(
                $rs[]
                | (.customTitle, .aiTitle)
                | select(type == "string" and length > 0)
            ) // null) as $title |
            (first(
                $rs[]
                | select(.type == "user" and (.message | type == "object"))
                | .message.content
                | if type == "string" then .
                  elif type == "array" then
                    map(select(type == "object" and .type == "text") | .text // "")
                    | join("\n")
                  else empty end
                | select(type == "string" and length > 0)
            ) // null) as $preview |
            (first($rs[] | select(.timestamp | type == "string") | .timestamp) // null) as $first_ts |
            if $cwd == null or $preview == null then empty
            else {
                id: $id,
                cwd: $cwd,
                branch: $branch,
                title: $title,
                preview: $preview,
                mtime: $mtime,
                first_ts: $first_ts
            }
            end
        ' 2>/dev/null
    ) || {
        echo "waypoint-enumerator: jq failed on $path" >&2
        continue
    }
    if [ -n "$record" ] && [ "$record" != "null" ]; then
        printf '%s\n' "$record"
        count=$((count + 1))
    fi
done <<< "$sorted"

exit 0
