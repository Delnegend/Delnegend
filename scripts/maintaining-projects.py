#!/usr/bin/env python3
"""Regenerate the maintaining-projects tables and their badge images.

Requires an authenticated gh CLI and badgers-cli (cargo install badgers-cli).
Badges are written as SVG files under badges/ because GitHub strips inline
<svg> from a README; the tables link to those files.
"""

import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from urllib.parse import quote

OWNER = os.environ.get("OWNER", "Delnegend")
README = Path(os.environ.get("README", "README.md"))
BADGES = Path(os.environ.get("BADGES", "badges"))
BADGERS = os.environ.get("BADGERS_CLI", "badgers-cli")
# Logos come from Simple Icons (the set shields.io draws from), pinned so a new
# release of it cannot change the badges unannounced.
LOGO_URL = "https://raw.githubusercontent.com/simple-icons/simple-icons/16.38.1/icons/{slug}.svg"
BLUE, GREY, ORANGE, RED = "005a9c", "555555", "a64000", "c62828"
COLORS = {"success": BLUE, "unknown": GREY}
# Simple Icons draws Go as a wide wordmark that is tiny at the default width.
ICON_WIDTHS = {"Go": 26}
RENDERED: dict[str, str] = {}
START = "<!-- maintaining-projects:start -->"
END = "<!-- maintaining-projects:end -->"
GROUPS = {
    "Apps": ["artefact", "differ", "freshdesk"],
    "Services": [
        "dpi-proxy", "hath-server-container",
        "l4d2-server-container", "mikrotik-mcp-server", "rsRPC",
        "segotep-digital", "zero-g",
    ],
    "Libraries": ["axum-auth-gate"],
}
QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    primaryLanguage { name }
    issues(states: OPEN) { totalCount }
    pullRequests(states: OPEN) { totalCount }
  }
}"""


def api(endpoint, *args):
    result = subprocess.run(
        ["gh", "api", endpoint, *args], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def collect(repo):
    path = f"repos/{OWNER}/{repo}"
    details = api(path)
    counts = api("graphql", "-f", f"owner={OWNER}", "-f", f"name={repo}", "-f", f"query={QUERY}")["data"]["repository"]
    extension = "yaml" if repo == "l4d2-server-container" else "yml"
    branch = quote(details["default_branch"], safe="")

    def conclusion(workflow):
        runs = api(f"{path}/actions/workflows/{workflow}.{extension}/runs?branch={branch}&status=completed&per_page=1")["workflow_runs"]
        return (runs[0]["conclusion"] or "unknown") if runs else "unknown"

    try:
        latest = api(f"{path}/releases/latest")
        version, date = latest["tag_name"], latest["published_at"]
    except subprocess.CalledProcessError:
        tags = api(f"{path}/tags")
        version = tags[0]["name"] if tags else "-"
        date = api(f"{path}/commits/{quote(version, safe='')}")["commit"]["committer"]["date"] if tags else ""

    return {
        "repo": repo, "description": details["description"] or "-",
        "version": version, "date": date or "", "extension": extension,
        "language": counts["primaryLanguage"]["name"] if counts["primaryLanguage"] else None,
        "ci": conclusion("ci"), "release": conclusion("release"),
        "issues": counts["issues"]["totalCount"],
        "prs": counts["pullRequests"]["totalCount"],
    }


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@cache
def logo(language):
    """White Simple Icons logo as a data URI, or None if there is no such logo."""
    url = LOGO_URL.format(slug=quote(language.lower().replace(" ", ""), safe=""))
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            svg = response.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"warning: no logo for {language}: {error}", file=sys.stderr)
        return None
    if not svg.lstrip().startswith("<svg "):
        print(f"warning: logo for {language} is not an SVG", file=sys.stderr)
        return None
    svg = svg.replace("<svg ", '<svg fill="#fff" ', 1)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def label_only(svg):
    """Cut the status segment off a badge rendered with an empty status.

    badgers-cli always draws that segment, even when empty, which leaves a
    lopsided stub on the right. The segment is the last rect and the last
    stretch of the canvas, so drop it and shrink the canvas to match.
    """
    rect = re.search(r'<rect fill="[^"]*" x="([\d.]+)" width="[\d.]+" height="200" ?/>', svg)
    size = re.search(r'<svg width="[\d.]+" height="20" viewBox="0 0 [\d.]+ 200"', svg)
    if rect is None or size is None:
        raise RuntimeError("badgers-cli output changed: cannot find the status segment to remove")
    width = float(rect.group(1))
    svg = svg.replace(rect.group(0), "", 1)
    return svg.replace(size.group(0), f'<svg width="{width / 10:g}" height="20" viewBox="0 0 {width:g} 200"', 1)


def badge(name, alt, url, *, status, label=None, color=GREY, label_color=None, icon=None,
          icon_width=None, drop_status=False):
    """Render one badge to badges/<name> (written later) and return its markdown.

    With drop_status the badge is just its label; pass status="" for it.
    """
    args = [BADGERS, "badge", f"--status={status}", f"--color={color}"]
    if label_color:
        args.append(f"--label-color={label_color}")
    if label:
        args.append(f"--label={label}")
    if icon:
        args.append(f"--icon={icon}")
    if icon_width:
        args.append(f"--icon-width={icon_width}")
    try:
        result = subprocess.run(args, capture_output=True, encoding="utf-8", check=True)
    except FileNotFoundError:
        raise SystemExit(f"{BADGERS} not found; install it with: cargo install badgers-cli --locked")
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{BADGERS} failed for {name}: {error.stderr.strip()}") from error
    RENDERED[name] = label_only(result.stdout) if drop_status else result.stdout
    return f"[![{alt}]({BADGES.as_posix()}/{name})]({url})"


def write_badges():
    """Write the rendered badges and delete any that are no longer wanted."""
    BADGES.mkdir(exist_ok=True)
    changed = False
    for name, svg in RENDERED.items():
        path = BADGES / name
        if not path.exists() or path.read_text(encoding="utf-8") != svg:
            path.write_text(svg, encoding="utf-8")
            changed = True
    for stale in BADGES.glob("*.svg"):
        if stale.name not in RENDERED:
            stale.unlink()
            changed = True
    return changed


def row(project):
    repo = project["repo"]
    base = f"https://github.com/{OWNER}/{repo}"
    extension = project["extension"]
    color = COLORS.get(project["release"], ORANGE)
    age = ""
    if project["date"]:
        published = datetime.fromisoformat(project["date"].replace("Z", "+00:00"))
        days = int((datetime.now(timezone.utc) - published).total_seconds() / 86400)
        age = "today" if days == 0 else f"{days}d ago"
    label = f"{project['version']} · {age}" if age else project["version"]
    badges = [badge(
        f"{repo}-release.svg", f"Release {label}: {project['release']}",
        f"{base}/actions/workflows/release.{extension}",
        label=age or "release", status=project["version"], color=color,
    )]
    if project["language"]:
        language = project["language"]
        icon = logo(language)
        badges.insert(0, badge(
            f"language-{slug(language)}.svg", language, base, status=language,
            # badgers-cli needs a label or an icon; fall back to a label if the logo is missing.
            label=None if icon else "language", icon=icon, icon_width=ICON_WIDTHS.get(language),
        ))
    if project["ci"] == "failure":
        # A plain red "CI": the colour says it failed, the alt text spells it out.
        badges.append(badge(
            f"{repo}-ci.svg", "CI: failure", f"{base}/actions/workflows/ci.{extension}",
            label="CI", status="", color=RED, label_color=RED, drop_status=True,
        ))
    elif project["ci"] != "success":
        badges.append(badge(
            f"{repo}-ci.svg", f"CI: {project['ci']}", f"{base}/actions/workflows/ci.{extension}",
            label="CI", status=project["ci"], color=COLORS.get(project["ci"], ORANGE),
        ))
    if project["issues"]:
        badges.append(badge(
            f"{repo}-issues.svg", "Open issues", f"{base}/issues",
            label="issues", status=project["issues"], color=BLUE,
        ))
    if project["prs"]:
        badges.append(badge(
            f"{repo}-prs.svg", "Open pull requests", f"{base}/pulls",
            label="PRs", status=project["prs"], color=BLUE,
        ))
    return f"| [{repo}]({base}) | {project['description']} | {' '.join(badges)} |"


def main():
    projects = [repo for group in GROUPS.values() for repo in group]
    with ThreadPoolExecutor(max_workers=len(projects)) as pool:
        data = dict(zip(projects, pool.map(collect, projects)))

    rows = {repo: row(data[repo]) for repo in projects}
    tables = []
    for title, repos in GROUPS.items():
        ordered = sorted(repos, key=lambda repo: (data[repo]["date"], rows[repo]), reverse=True)
        tables.extend([f"### {title}", "", "| Project | Description | Badges |", "| --- | --- | --- |"])
        tables.extend(rows[repo] for repo in ordered)
        tables.append("")

    original = README.read_text(encoding="utf-8")
    if original.count(START) != 1 or original.count(END) != 1 or original.index(START) > original.index(END):
        raise ValueError(f"Expected one ordered pair of {START} and {END} in {README}")
    before, rest = original.split(START, 1)
    _, after = rest.split(END, 1)
    updated = before + START + "\n" + "\n".join(tables) + "\n" + END + after
    badges_changed = write_badges()
    if updated == original and not badges_changed:
        print(f"{README} and {BADGES}/ are already up to date")
    else:
        README.write_text(updated, encoding="utf-8")
        print(f"{README} and {BADGES}/ updated")


if __name__ == "__main__":
    main()
