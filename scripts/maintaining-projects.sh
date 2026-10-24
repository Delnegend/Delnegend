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
  differ
  freshdesk
  hath-server-container
  headless-brave
  l4d2-server-container
  mikrotik-mcp-server
  rsRPC
  segotep-digital
  terraria-server-container
  zero-g
)

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

# ---------------------------------------------------------------------------
# One project -> one line: version, release date, description, and a verdict
# plus URL for each of the CI and Release columns
# ---------------------------------------------------------------------------
verdict() {
  case "$1" in
    success)   echo "✅" ;;
    "" | null) echo "❔" ;;
    *)         echo "❌" ;;
  esac
}

collect() {
  local repo="$1" branch description version date result conclusion url ci rel rel_url rel_conclusion

  branch="$(gh api "repos/$OWNER/$repo" --jq .default_branch 2>/dev/null || echo main)"
  description="$(gh api "repos/$OWNER/$repo" --jq '.description // ""' 2>/dev/null || true)"
  case "$description" in "" | null) description="" ;; esac

  # Newest release first; fall back to the newest tag and its commit date, so a
  # project that tags without publishing a release still gets a version. A repo
  # with no release makes `gh api` exit non-zero and print the 404 body, and an
  # empty tag list answers "null" — both mean "nothing to report", not a version.
  version=""
  date=""
  if release="$(gh api "repos/$OWNER/$repo/releases/latest" --jq '.tag_name' 2>/dev/null)"; then
    version="$release"
    date="$(gh api "repos/$OWNER/$repo/releases/latest" --jq '.published_at' 2>/dev/null || true)"
  fi
  if [ -z "$version" ] || [ "$version" = "null" ]; then
    version="$(gh api "repos/$OWNER/$repo/tags" --jq '.[0].name' 2>/dev/null || true)"
    case "$version" in
      "" | null) version=""; date="" ;;
      *) date="$(gh api "repos/$OWNER/$repo/commits/$version" \
           --jq '.commit.committer.date' 2>/dev/null || true)" ;;
    esac
  fi

  # Both verdicts come from one query. A column headed CI should not report
  # the status of a release or cleanup job, so it prefers the workflow named
  # "CI" and falls back to the newest completed run when a repo names its gate
  # something else. Release matches the release workflow and reports nothing
  # rather than substituting an unrelated run under a Release heading.
  result="$(gh run list --repo "$OWNER/$repo" --branch "$branch" \
    --limit 30 --status completed --json workflowName,conclusion,url \
    --jq '([.[] | select(.workflowName == "CI")][0] // .[0] // {}) as $ci
           | ([.[] | select(.workflowName | startswith("Release"))][0] // {}) as $rel
           | "\($ci.conclusion // "") \($ci.url // "") \($rel.conclusion // "") \($rel.url // "")"' 2>/dev/null || true)"
  read -r conclusion url rel_conclusion rel_url <<<"$result"
  ci="$(verdict "$conclusion")"
  rel="$(verdict "$rel_conclusion")"

  # Unit separator, not a tab: bash's `read` collapses runs of whitespace
  # delimiters, so an empty date would shift a verdict into its place.
  printf '%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\n' \
    "${version:-—}" "$date" "$description" "$ci" "$url" "$rel" "$rel_url"
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
  # "today" stays spelled out: it is the common case for the projects that
  # release often, and "0d ago" reads worse than it sounds.
  case "$days" in
    0) echo "today" ;;
    *) echo "${days}d ago" ;;
  esac
}

{
  for repo in "${PROJECTS[@]}"; do
    IFS=$'\x1f' read -r version date description ci url rel rel_url <"$workdir/$repo"
    if [ -z "$description" ]; then
      description_cell="—"
    else
      description_cell="$description"
    fi
    if [ "$version" = "—" ]; then
      cell="—"
    else
      cell="[\`$version\`](https://github.com/$OWNER/$repo/releases/tag/$version)"
      when="$(ago "$date")"
      # A table row is one line, so the version and its age need an explicit
      # break. "· " read as a run-on across to the columns either side.
      [ -n "$when" ] && cell="$cell<br>$when"
    fi
    if [ -n "$url" ]; then
      ci_cell="[$ci]($url)"
    else
      ci_cell="$ci"
    fi
    if [ -n "$rel_url" ]; then
      rel_cell="[$rel]($rel_url)"
    else
      rel_cell="$rel"
    fi
    # The release date is the sort key; the row is the rest of the line. A
    # project with no release yet sorts to the bottom rather than the top.
    printf '%s\t| [%s](https://github.com/%s/%s) | %s | %s | %s | %s |\n' \
      "${date:-0000-00-00}" "$repo" "$OWNER" "$repo" "$description_cell" "$cell" "$ci_cell" "$rel_cell"
  done
} | sort -r | cut -f2- >"$workdir/rows"

{
  echo "| Project | Description | Version | CI | Release |"
  # :---: centres the column. A single emoji is narrow enough that a left
  # aligned Release reads as if it belonged to the CI column beside it.
  echo "| --- | --- | :---: | --- | :---: |"
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
if ! grep -q '^| Project | Description | Version | CI | Release |$' "$workdir/README"; then
  echo "error: $START_MARKER / $END_MARKER not found in $README" >&2
  exit 1
fi

if cmp -s "$README" "$workdir/README"; then
  echo "README.md is already up to date"
else
  mv "$workdir/README" "$README"
  echo "README.md updated"
fi
