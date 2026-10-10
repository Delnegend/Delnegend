#!/usr/bin/env bash
#
# Regenerate the "Maintaining projects" table in README.md.
#
# One row per project: the project name as a link, its newest released version
# with how long ago that was, and the newest completed workflow run on the
# default branch. Runs hourly from .github/workflows/update-projects.yml; run it
# by hand the same way:
#
#   ./scripts/maintaining-projects.sh
#
# Needs `gh` authenticated (GH_TOKEN) with read access to the projects.
set -euo pipefail

OWNER="${OWNER:-Delnegend}"
README="${README:-README.md}"
START_MARKER="<!-- maintaining-projects:start -->"
END_MARKER="<!-- maintaining-projects:end -->"

# The projects the table lists. Add a repo name to include it.
PROJECTS=(
  artefact
  headless-brave
  l4d2-linux-server
  mikrotik-mcp-server
  segotep-digital
  gallery-preprocessor
)

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# ---------------------------------------------------------------------------
# One project -> one line: version <TAB> release date <TAB> CI verdict
# ---------------------------------------------------------------------------
collect() {
  local repo="$1" branch version date conclusion

  branch="$(gh api "repos/$OWNER/$repo" --jq .default_branch 2>/dev/null || echo main)"

  # Newest release first; fall back to the newest tag and its commit date, so a
  # project that tags without publishing a release still gets a version.
  version="$(gh api "repos/$OWNER/$repo/releases/latest" --jq .tag_name 2>/dev/null || true)"
  date="$(gh api "repos/$OWNER/$repo/releases/latest" --jq .published_at 2>/dev/null || true)"
  if [ -z "$version" ]; then
    version="$(gh api "repos/$OWNER/$repo/tags" --jq '.[0].name' 2>/dev/null || true)"
    if [ -n "$version" ]; then
      date="$(gh api "repos/$OWNER/$repo/commits/$version" --jq .commit.committer.date 2>/dev/null || true)"
    fi
  fi

  # The newest completed run on the default branch. In-flight runs are skipped
  # so a running CI shows its last verdict instead of an interim cross.
  conclusion="$(gh run list --repo "$OWNER/$repo" --branch "$branch" \
    --limit 1 --status completed --json conclusion --jq '.[0].conclusion' 2>/dev/null || true)"

  case "$conclusion" in
    success) ci="✅" ;;
    "")      ci="❔" ;;
    *)       ci="❌" ;;
  esac

  printf '%s\t%s\t%s\n' "${version:-—}" "${date:-}" "$ci"
}

# Parallel: one subshell per project, then wait for all of them.
pids=()
for repo in "${PROJECTS[@]}"; do
  ( collect "$repo" >"$workdir/$repo" ) &
  pids+=("$!")
done
for pid in "${pids[@]}"; do wait "$pid"; done

# ---------------------------------------------------------------------------
# Render the table, newest release first
# ---------------------------------------------------------------------------
ago() {
  local date="$1" days
  [ -n "$date" ] || { echo ""; return; }
  days=$(( ( $(date -u +%s) - $(date -u -d "$date" +%s) ) / 86400 ))
  case "$days" in
    0) echo "today" ;;
    1) echo "1 day ago" ;;
    *) echo "$days days ago" ;;
  esac
}

{
  for repo in "${PROJECTS[@]}"; do
    IFS=$'\t' read -r version date ci <"$workdir/$repo"
    if [ "$version" = "—" ]; then
      cell="—"
    else
      cell="[\`$version\`](https://github.com/$OWNER/$repo/releases/tag/$version)"
      when="$(ago "$date")"
      [ -n "$when" ] && cell="$cell · $when"
    fi
    # The release date is the sort key; the row is the rest of the line.
    printf '%s\t| [%s](https://github.com/%s/%s) | %s | %s |\n' \
      "$date" "$repo" "$OWNER" "$repo" "$cell" "$ci"
  done
} | sort -r | cut -f2- >"$workdir/rows"

{
  echo "| Project | Version | CI |"
  echo "| --- | --- | --- |"
  cat "$workdir/rows"
} >"$workdir/table"

# Splice it between the markers, replacing whatever was there before.
awk -v start="$START_MARKER" -v end="$END_MARKER" -v table="$workdir/table" '
  $0 == start { print; while ((getline line < table) > 0) print line; inside = 1; next }
  $0 == end   { inside = 0 }
  !inside     { print }
' "$README" >"$workdir/README"

# The splice only works if the markers are still in the file. Without this the
# script would exit 0 having changed nothing, and the table would quietly stop
# being refreshed.
if ! grep -q '^| Project | Version | CI |$' "$workdir/README"; then
  echo "error: $START_MARKER / $END_MARKER not found in $README" >&2
  exit 1
fi

if cmp -s "$README" "$workdir/README"; then
  echo "README.md is already up to date"
else
  mv "$workdir/README" "$README"
  echo "README.md updated"
fi
