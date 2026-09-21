"""Docs Writer, Google layer: auth, create/write/read-back/export, and the
post-write verification. Kept apart from docs_content so the content logic runs
and tests with no Google account.

Credentials live OUTSIDE the tracked project files, in crewai/.google/ (gitignored):
  client_secret.json   OAuth *Desktop app* client downloaded from Google Cloud
  token.json           written by `python -m wire3_gtm google-auth`
Scopes are the narrowest that work: documents, and drive.file (only files this
app created, enough to create the document and export it as PDF).
"""

import json
import os
from pathlib import Path

from wire3_gtm.docs_content import DocumentPlan, EV_RE, to_docs_requests

GOOGLE_DIR = Path(__file__).parent.parent / ".google"
CLIENT_SECRET = Path(os.environ.get("GOOGLE_CLIENT_SECRETS", GOOGLE_DIR / "client_secret.json"))
TOKEN = GOOGLE_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/documents", "https://www.googleapis.com/auth/drive.file"]


class DocsAuthError(Exception):
    pass


def authorize() -> Path:
    """One-time interactive consent (opens a browser). Run by the user."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CLIENT_SECRET.exists():
        raise DocsAuthError(f"OAuth client file not found: {CLIENT_SECRET}. See docs in SETUP_DECISIONS.md.")
    creds = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES).run_local_server(port=0)
    GOOGLE_DIR.mkdir(exist_ok=True)
    TOKEN.write_text(creds.to_json())
    TOKEN.chmod(0o600)
    return TOKEN


def credentials():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN.exists():
        raise DocsAuthError("no Google token; run: uv run python -m wire3_gtm google-auth")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise DocsAuthError(f"Google token expired or revoked ({exc}); run: uv run python -m wire3_gtm google-auth") from exc
        TOKEN.write_text(creds.to_json())
    return creds


class GoogleDocs:
    def __init__(self, creds=None):
        from googleapiclient.discovery import build

        creds = creds or credentials()
        self.docs = build("docs", "v1", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    def create(self, title: str) -> str:
        return self.docs.documents().create(body={"title": title}).execute(num_retries=3)["documentId"]

    def write(self, document_id: str, requests: list[dict]) -> None:
        # num_retries=0 on purpose: batchUpdate is not idempotent, so an automatic
        # retry after an ambiguous failure could insert the whole text twice.
        self.docs.documents().batchUpdate(documentId=document_id, body={"requests": requests}).execute(num_retries=0)

    def read(self, document_id: str) -> dict:
        return self.docs.documents().get(documentId=document_id).execute(num_retries=3)

    def export_pdf(self, document_id: str) -> bytes:
        return self.drive.files().export(fileId=document_id, mimeType="application/pdf").execute(num_retries=3)


def url_for(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


# --- post-write verification (works on a real read-back or on simulate_docs) ---

def flatten(doc: dict) -> tuple[str, set[str], list[tuple[str, str]]]:
    """(plain text, link urls, [(paragraph text, namedStyleType)]) from a Docs API document."""
    text, links, paragraphs = [], set(), []
    for el in (doc.get("body") or {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        p = ""
        for run in para.get("elements", []):
            tr = run.get("textRun")
            if not tr:
                continue
            p += tr.get("content", "")
            url = ((tr.get("textStyle") or {}).get("link") or {}).get("url")
            if url:
                links.add(url)
        text.append(p)
        paragraphs.append((p.rstrip("\n"), (para.get("paragraphStyle") or {}).get("namedStyleType", "")))
    return "".join(text), links, paragraphs


def verify(expected: dict, doc: dict) -> list[str]:
    """Same checks as the n8n Docs Writer, plus that section headings really are
    headings (a heading pasted as plain text would satisfy a text search)."""
    text, links, paragraphs = flatten(doc)
    errors = []
    if doc.get("title") != expected["title"]:
        errors.append(f"Title mismatch: expected {expected['title']!r}, got {doc.get('title')!r}.")
    heading_styles = {t: s for t, s in paragraphs}
    for h in expected["section_headings"]:
        if h not in text:
            errors.append(f"Missing section heading: {h}")
        elif heading_styles.get(h) != "HEADING_2":
            errors.append(f"Section heading is not styled as a heading: {h}")
    for i in expected["source_ids"]:
        if f"[{i}]" not in text:
            errors.append(f"Source {i} missing from Appendix B.")
    for url in expected["link_urls"]:
        if url not in links:
            errors.append(f"Missing hyperlink for source URL: {url}")
    orphaned = sorted(set(EV_RE.findall(text)) - set(expected["allowed_evidence_ids"]))
    if orphaned:
        errors.append(f"Orphaned evidence_id(s) in document: {orphaned}")
    return errors


def simulate_docs(plan: DocumentPlan, requests: list[dict] | None = None) -> dict:
    return simulate_requests(plan.title, requests or to_docs_requests(plan))


def simulate_requests(title: str, requests: list[dict]) -> dict:
    """Apply our own batchUpdate requests to an empty document, in UTF-16 code
    units like Google does, and return it in the Docs API 'get' shape. Lets the
    index math (headings, bold, links landing on the right characters) be tested
    with no Google account."""
    units: list[bytes] = []  # one UTF-16 code unit (2 bytes) each; an astral char is 2 units
    bold: list[bool] = []
    link: list[str | None] = []
    para_style: dict[int, str] = {}  # body index where the paragraph starts -> named style
    for r in requests:
        if "insertText" in r:
            raw = r["insertText"]["text"].encode("utf-16-le")
            units = [raw[i:i + 2] for i in range(0, len(raw), 2)]
            bold, link = [False] * len(units), [None] * len(units)
        elif "updateParagraphStyle" in r:
            u = r["updateParagraphStyle"]
            para_style[u["range"]["startIndex"]] = u["paragraphStyle"]["namedStyleType"]
        elif "updateTextStyle" in r:
            rng, ts = r["updateTextStyle"]["range"], r["updateTextStyle"]["textStyle"]
            for i in range(rng["startIndex"] - 1, rng["endIndex"] - 1):  # body index 1 == unit 0
                if "bold" in ts:
                    bold[i] = True
                if "link" in ts:
                    link[i] = ts["link"]["url"]
    NL = "\n".encode("utf-16-le")
    content, start = [], 0
    while start < len(units):
        end = start
        while units[end] != NL:
            end += 1
        end += 1  # include the newline
        runs, cur, key = [], b"", None
        for i in range(start, end):
            k = (bold[i], link[i])
            if key is not None and k != key:
                runs.append((cur, key))
                cur = b""
            cur, key = cur + units[i], k
        runs.append((cur, key))
        content.append({"paragraph": {
            "elements": [{"textRun": {
                "content": c.decode("utf-16-le"),
                "textStyle": ({"bold": True} if k[0] else {}) | ({"link": {"url": k[1]}} if k[1] else {})}}
                for c, k in runs],
            "paragraphStyle": {"namedStyleType": para_style.get(start + 1, "NORMAL_TEXT")}}})
        start = end
    return {"title": title, "body": {"content": content}}
