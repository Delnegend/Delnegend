#!/usr/bin/env python3
"""Regenerate the maintaining-projects tables. Requires an authenticated gh CLI."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

OWNER = os.environ.get("OWNER", "Delnegend")
README = Path(os.environ.get("README", "README.md"))
START = "<!-- maintaining-projects:start -->"
END = "<!-- maintaining-projects:end -->"
GROUPS = {
    "Apps": ["artefact", "differ", "freshdesk"],
    "Services": [
        "dpi-proxy", "hath-server-container",
        "l4d2-server-container", "mikrotik-mcp-server", "rsRPC",
        "segotep-digital", "terraria-server-container", "zero-g",
    ],
    "Libraries": ["axum-auth-gate"],
}
QUERY = """query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
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
        "ci": conclusion("ci"), "release": conclusion("release"),
        "issues": counts["issues"]["totalCount"],
        "prs": counts["pullRequests"]["totalCount"],
    }


def status_badge(label, status, url, alt):
    color = {"success": "005a9c", "unknown": "555555"}.get(status, "a64000")
    image = f"https://img.shields.io/static/v1?label=&message={quote(label, safe='')}&color={color}"
    return f"[![{alt}: {status}]({image})]({url})"


def row(project):
    repo = project["repo"]
    base = f"https://github.com/{OWNER}/{repo}"
    shields = f"https://img.shields.io/github"
    extension = project["extension"]
    color = {"success": "005a9c", "unknown": "555555"}.get(project["release"], "a64000")
    image = f"https://img.shields.io/static/v1?label=&message={quote(project['version'], safe='')}&color={color}"
    badges = [f"[![Release {project['version']}: {project['release']}]({image})]({base}/actions/workflows/release.{extension})"]
    if project["ci"] != "success":
        badges.append(status_badge("CI", project["ci"], f"{base}/actions/workflows/ci.{extension}", "CI"))
    if project["issues"]:
        badges.append(f"[![Open issues]({shields}/issues-raw/{OWNER}/{repo}?label=%F0%9F%90%9B&color=005a9c)]({base}/issues)")
    if project["prs"]:
        badges.append(f"[![Open pull requests]({shields}/issues-pr-raw/{OWNER}/{repo}?label=PR&color=005a9c)]({base}/pulls)")
    return f"| [{repo}]({base}) | {project['description']} | {' '.join(badges)} |"


def main():
    projects = [repo for group in GROUPS.values() for repo in group]
    with ThreadPoolExecutor(max_workers=len(projects)) as pool:
        data = dict(zip(projects, pool.map(collect, projects)))

    tables = []
    for title, repos in GROUPS.items():
        ordered = sorted((data[repo] for repo in repos), key=lambda p: (p["date"], row(p)), reverse=True)
        tables.extend([f"### {title}", "", "| Project | Description | Badges |", "| --- | --- | --- |"])
        tables.extend(row(project) for project in ordered)
        tables.append("")

    original = README.read_text(encoding="utf-8")
    if original.count(START) != 1 or original.count(END) != 1 or original.index(START) > original.index(END):
        raise ValueError(f"Expected one ordered pair of {START} and {END} in {README}")
    before, rest = original.split(START, 1)
    _, after = rest.split(END, 1)
    updated = before + START + "\n" + "\n".join(tables) + "\n" + END + after
    if updated == original:
        print(f"{README} is already up to date")
    else:
        README.write_text(updated, encoding="utf-8")
        print(f"{README} updated")


if __name__ == "__main__":
    main()
