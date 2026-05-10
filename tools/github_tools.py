"""GitHub tools — PR diff fetching and repo browsing."""

import re
import requests
from config import GITHUB_TOKEN

_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _parse_pr_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Not a valid GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def get_pr_diff(pr_url: str) -> str:
    owner, repo, pr_number = _parse_pr_url(pr_url)
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
        headers={**_HEADERS, "Accept": "application/vnd.github.diff"},
        timeout=20,
    )
    resp.raise_for_status()
    diff = resp.text
    return diff[:12000] if len(diff) > 12000 else diff


def get_pr_metadata(pr_url: str) -> dict:
    owner, repo, pr_number = _parse_pr_url(pr_url)
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "title": data["title"],
        "body": data.get("body", ""),
        "author": data["user"]["login"],
        "base": data["base"]["ref"],
        "head": data["head"]["ref"],
        "changed_files": data["changed_files"],
        "additions": data["additions"],
        "deletions": data["deletions"],
        "state": data["state"],
    }


def list_repo_files(owner: str, repo: str, path: str = "") -> str:
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    items = resp.json()
    lines = [f"{'📁' if i['type']=='dir' else '📄'} {i['name']}" for i in items]
    return "\n".join(lines)
