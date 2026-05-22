"""Generate a clean PDF resume from vault Resume.md using Playwright."""
import re
from pathlib import Path
from typing import Optional

VAULT_RESUME = (
    Path.home()
    / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Digital Brain"
    / "About Taran/Resume.md"
)
RESUME_PDF  = Path(__file__).parent.parent / "resume.pdf"
RESUME_HTML = Path(__file__).parent.parent / "tmp" / "resume.html"
# Per-job tailored resumes — one PDF per career job, keyed by job id.
CAREER_RESUMES = Path(__file__).parent.parent / "career_resumes"

_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;font-size:10pt;line-height:1.45;margin:.55in .6in;color:#222}}
h1{{font-size:17pt;border-bottom:2px solid #333;padding-bottom:5px;margin-bottom:4px}}
h2{{font-size:10.5pt;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
    border-bottom:1px solid #ccc;margin:12px 0 4px;padding-bottom:2px;color:#333}}
h3{{font-size:10pt;font-weight:700;margin:8px 0 1px}}
ul{{padding-left:17px;margin:3px 0}}
li{{margin-bottom:2px}}
p{{margin:2px 0;font-size:9.5pt;color:#555}}
hr{{border:none;border-top:1px solid #e0e0e0;margin:8px 0}}
strong{{font-weight:700}}
</style></head><body>{body}</body></html>"""


def _md_to_html(md: str) -> str:
    lines = md.split("\n")
    # Strip YAML frontmatter
    if lines and lines[0].strip() == "---":
        end = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), None)
        if end:
            lines = lines[end + 1:]

    out = []
    in_ul = False

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def bold(s):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

    for line in lines:
        s = line.strip()
        if not s:
            close_ul()
            continue
        if s.startswith("### "):
            close_ul()
            out.append(f"<h3>{bold(s[4:])}</h3>")
        elif s.startswith("## "):
            close_ul()
            out.append(f"<h2>{bold(s[3:])}</h2>")
        elif s.startswith("# "):
            close_ul()
            out.append(f"<h1>{bold(s[2:])}</h1>")
        elif s.startswith("- "):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{bold(s[2:])}</li>")
        elif s == "---":
            close_ul()
            out.append("<hr>")
        else:
            close_ul()
            out.append(f"<p>{bold(s)}</p>")

    close_ul()
    return "\n".join(out)


def generate_resume_pdf() -> Path:
    """Generate PDF from vault Resume.md. Returns path to PDF."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright not installed.")

    if not VAULT_RESUME.exists():
        raise FileNotFoundError(f"Resume not found at {VAULT_RESUME}")

    md   = VAULT_RESUME.read_text(encoding="utf-8")
    html = _TEMPLATE.format(body=_md_to_html(md))
    RESUME_HTML.parent.mkdir(parents=True, exist_ok=True)
    RESUME_HTML.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.goto(f"file://{RESUME_HTML}", wait_until="domcontentloaded")
        page.wait_for_timeout(400)
        page.pdf(
            path=str(RESUME_PDF),
            format="Letter",
            margin={"top": "0.5in", "bottom": "0.5in", "left": "0", "right": "0"},
        )
        browser.close()

    return RESUME_PDF


def generate_tailored_resume(job: dict) -> Optional[Path]:
    """Build a per-job tailored resume PDF.

    Takes the base vault Resume.md and swaps each tailored bullet in for its
    original (the career agent's Stage-2 output). Renders to
    career_resumes/<job_id>.pdf. Returns the PDF path, or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    if not VAULT_RESUME.exists():
        return None

    md = VAULT_RESUME.read_text(encoding="utf-8")

    # Swap tailored bullets in. `original` should match a resume line; if it
    # doesn't (Claude paraphrased it), that bullet is simply left untouched.
    bullets = (job.get("tailored") or {}).get("bullets") or []
    for b in bullets:
        orig = (b.get("original") or "").strip()
        new  = (b.get("tailored") or "").strip()
        if orig and new and orig in md:
            md = md.replace(orig, new, 1)

    html   = _TEMPLATE.format(body=_md_to_html(md))
    job_id = re.sub(r"[^A-Za-z0-9_-]", "", str(job.get("id", "job"))) or "job"
    CAREER_RESUMES.mkdir(parents=True, exist_ok=True)
    html_path = CAREER_RESUMES / f"{job_id}.html"
    pdf_path  = CAREER_RESUMES / f"{job_id}.pdf"
    html_path.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()
            page.goto(f"file://{html_path}", wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            page.pdf(
                path=str(pdf_path),
                format="Letter",
                margin={"top": "0.5in", "bottom": "0.5in", "left": "0", "right": "0"},
            )
            browser.close()
    except Exception:
        return None

    return pdf_path
