"""Web search and URL fetch tools."""

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS


def search(query: str, num_results: int = 5) -> str:
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=num_results):
            results.append(f"**{r['title']}**\n{r['href']}\n{r['body']}\n")
    return "\n---\n".join(results) if results else "No results found."


def fetch_url(url: str, max_chars: int = 4000) -> str:
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [l for l in text.splitlines() if l.strip()]
        return "\n".join(lines)[:max_chars]
    except Exception as e:
        return f"[Error fetching {url}: {e}]"
