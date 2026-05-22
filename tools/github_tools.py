"""GitHub tools — PR diff fetching, issue management, code search, and repo operations."""

import re
from typing import Optional

import requests
from config import GITHUB_TOKEN

_HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

_BASE = "https://api.github.com"


# ── URL parsing ───────────────────────────────────────────────────────────────

def _parse_pr_url(url: str) -> tuple[str, str, int]:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not m:
        raise ValueError(f"Not a valid GitHub PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def _parse_repo_url(url: str) -> tuple[str, str]:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git|/|$)", url)
    if not m:
        raise ValueError(f"Not a valid GitHub repo URL: {url}")
    return m.group(1), m.group(2)


# ── PR operations ─────────────────────────────────────────────────────────────

def get_pr_diff(pr_url: str) -> str:
    owner, repo, pr_number = _parse_pr_url(pr_url)
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/pulls/{pr_number}",
        headers={**_HEADERS, "Accept": "application/vnd.github.diff"},
        timeout=20,
    )
    resp.raise_for_status()
    diff = resp.text
    return diff[:12000] if len(diff) > 12000 else diff


def get_pr_metadata(pr_url: str) -> dict:
    owner, repo, pr_number = _parse_pr_url(pr_url)
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/pulls/{pr_number}",
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "title":         data["title"],
        "body":          data.get("body", ""),
        "author":        data["user"]["login"],
        "base":          data["base"]["ref"],
        "head":          data["head"]["ref"],
        "changed_files": data["changed_files"],
        "additions":     data["additions"],
        "deletions":     data["deletions"],
        "state":         data["state"],
        "url":           data["html_url"],
    }


def get_pr_comments(pr_url: str) -> list[dict]:
    owner, repo, pr_number = _parse_pr_url(pr_url)
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return [
        {"author": c["user"]["login"], "body": c["body"], "file": c.get("path", "")}
        for c in resp.json()
    ]


def create_pr(owner: str, repo: str, title: str, body: str,
              head: str, base: str = "main", draft: bool = True) -> dict:
    """Create a pull request. Returns {url, number}. draft=True by default."""
    resp = requests.post(
        f"{_BASE}/repos/{owner}/{repo}/pulls",
        headers=_HEADERS,
        json={"title": title, "body": body, "head": head, "base": base, "draft": draft},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"url": data["html_url"], "number": data["number"]}


def add_pr_comment(pr_url: str, body: str) -> dict:
    """Add a review comment to a PR (issue-level comment)."""
    owner, repo, pr_number = _parse_pr_url(pr_url)
    resp = requests.post(
        f"{_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments",
        headers=_HEADERS,
        json={"body": body},
        timeout=20,
    )
    resp.raise_for_status()
    return {"ok": True, "url": resp.json()["html_url"]}


# ── Issue operations ──────────────────────────────────────────────────────────

def list_issues(owner: str, repo: str, state: str = "open",
                labels: str = "", limit: int = 20) -> list[dict]:
    """List issues. state='open'|'closed'|'all'."""
    params = {"state": state, "per_page": limit}
    if labels:
        params["labels"] = labels
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/issues",
        headers=_HEADERS,
        params=params,
        timeout=20,
    )
    resp.raise_for_status()
    return [
        {
            "number": i["number"],
            "title":  i["title"],
            "body":   (i.get("body") or "")[:500],
            "labels": [l["name"] for l in i.get("labels", [])],
            "state":  i["state"],
            "url":    i["html_url"],
            "author": i["user"]["login"],
        }
        for i in resp.json()
        if "pull_request" not in i  # exclude PRs from issues list
    ]


def get_issue(owner: str, repo: str, issue_number: int) -> dict:
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/issues/{issue_number}",
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    i = resp.json()
    return {
        "number": i["number"],
        "title":  i["title"],
        "body":   i.get("body", ""),
        "labels": [l["name"] for l in i.get("labels", [])],
        "state":  i["state"],
        "url":    i["html_url"],
        "author": i["user"]["login"],
    }


def create_issue(owner: str, repo: str, title: str, body: str,
                 labels: Optional[list[str]] = None) -> dict:
    resp = requests.post(
        f"{_BASE}/repos/{owner}/{repo}/issues",
        headers=_HEADERS,
        json={"title": title, "body": body, "labels": labels or []},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"number": data["number"], "url": data["html_url"]}


def close_issue(owner: str, repo: str, issue_number: int, comment: str = "") -> dict:
    if comment:
        requests.post(
            f"{_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments",
            headers=_HEADERS,
            json={"body": comment},
            timeout=20,
        )
    resp = requests.patch(
        f"{_BASE}/repos/{owner}/{repo}/issues/{issue_number}",
        headers=_HEADERS,
        json={"state": "closed"},
        timeout=20,
    )
    resp.raise_for_status()
    return {"ok": True}


# ── Code / file operations ────────────────────────────────────────────────────

def get_file_content(owner: str, repo: str, path: str, ref: str = "main") -> str:
    """Get decoded file content from a repo. Returns raw text."""
    import base64
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/contents/{path}",
        headers=_HEADERS,
        params={"ref": ref},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content", "")


def search_code(owner: str, repo: str, query: str, limit: int = 10) -> list[dict]:
    """Search code within a specific repo."""
    resp = requests.get(
        f"{_BASE}/search/code",
        headers=_HEADERS,
        params={"q": f"{query} repo:{owner}/{repo}", "per_page": limit},
        timeout=20,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    return [
        {"path": i["path"], "url": i["html_url"], "sha": i["sha"]}
        for i in items
    ]


def list_repo_files(owner: str, repo: str, path: str = "") -> str:
    resp = requests.get(
        f"{_BASE}/repos/{owner}/{repo}/contents/{path}",
        headers=_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    items = resp.json()
    lines = [f"{'📁' if i['type']=='dir' else '📄'} {i['name']}" for i in items]
    return "\n".join(lines)


def get_repo_info(owner: str, repo: str) -> dict:
    resp = requests.get(f"{_BASE}/repos/{owner}/{repo}", headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    d = resp.json()
    return {
        "name":        d["name"],
        "description": d.get("description", ""),
        "language":    d.get("language", ""),
        "stars":       d["stargazers_count"],
        "default_branch": d["default_branch"],
        "topics":      d.get("topics", []),
        "url":         d["html_url"],
    }


# ── Commit operations ─────────────────────────────────────────────────────────

def create_or_update_file(owner: str, repo: str, path: str, content: str,
                          message: str, branch: str = "main") -> dict:
    """Create or update a single file in a GitHub repo via the API."""
    import base64

    # Get existing SHA if file exists (required for updates)
    sha = None
    try:
        existing = requests.get(
            f"{_BASE}/repos/{owner}/{repo}/contents/{path}",
            headers=_HEADERS,
            params={"ref": branch},
            timeout=10,
        )
        if existing.status_code == 200:
            sha = existing.json().get("sha")
    except Exception:
        pass

    payload: dict = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(
        f"{_BASE}/repos/{owner}/{repo}/contents/{path}",
        headers=_HEADERS,
        json=payload,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "ok": True,
        "sha": data["content"]["sha"],
        "url": data["content"]["html_url"],
    }


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_issues_for_prompt(issues: list[dict]) -> str:
    if not issues:
        return "No open issues."
    lines = []
    for i in issues:
        label_str = f" [{', '.join(i['labels'])}]" if i["labels"] else ""
        lines.append(f"#{i['number']}: {i['title']}{label_str}\n  {i['url']}")
        if i.get("body"):
            lines.append(f"  {i['body'][:200]}")
    return "\n\n".join(lines)


