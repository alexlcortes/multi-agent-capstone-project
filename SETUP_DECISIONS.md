# Environment & Infrastructure Decisions

Running log of setup decisions made outside the schema contract, for the final README (guide Step 12, items 4/5/9). Each entry: what was decided, why, and what it costs us if we're wrong.

## Search provider: SerpAPI + Tavily, both behind one adapter

**Decision:** implement one search-provider interface in the MCP layer; both SerpAPI and Tavily are real, working implementations of it. Tavily is the default during development/testing; SerpAPI is used for final graded runs, since the capstone brief explicitly names it.

**Why:** the capstone brief names SerpAPI specifically, but the course demos build against Tavily, and this project is self-funded (personal OpenAI/SerpAPI keys, not course-VM credentials). SerpAPI's free tier is a one-time ~100 searches before billing kicks in (~$50/mo for 5k after that); the brief's own per-run budget allows up to 40 search calls, so 2–3 development runs would exhaust it. Tavily's free tier (~1,000 requests/month) makes iterative development affordable without touching the SerpAPI quota at all.

**Cost if wrong:** if the adapter silently only calls Tavily while claiming SerpAPI support, that's a real compliance gap the guide explicitly warns against ("do not silently claim SerpAPI support when the implementation only calls Tavily"). Mitigated by keeping both implementations real and documenting which one is active per run in the run-record log (Phase 7).

## Credential storage: environment variables (`.env`), not n8n's built-in credential store

**Decision:** API keys (OpenAI, SerpAPI, Tavily) live in `.env` files (gitignored), loaded as environment variables. n8n's native credential store is used only where n8n *requires* it structurally (its OpenAI/Google OAuth credential objects, which nodes reference by name) — but the underlying values are still sourced from environment variables via n8n's `$env` expressions / credential-from-env pattern wherever the node type allows it, not typed in by hand into the UI.

**Exception — the Google OAuth client secret (updated when Docs Writer was built):** it is *not* in any `.env`. The client ID and secret were entered directly into n8n's `Google Docs account` credential form, and n8n stores them (and the resulting access/refresh tokens) encrypted in its own database under the gitignored `n8n/.n8n/` folder. OAuth tokens have to live in n8n's store anyway for its automatic refresh, and there was no environment-variable route for the client secret in this setup. Consequence: the Google secret does not travel with the repo — a fresh checkout has to repeat the OAuth setup below and re-create that credential. Google shows a client secret in full only at creation, but the console lets you add a new secret later if it is lost.

**Why:** n8n's credential store is encrypted at rest and fine for a single local instance, but it's UI-managed state that doesn't show up in the exported workflow JSON or in git — meaning it doesn't travel with the project the way a documented `.env.example` does, and it's a second place secrets could end up (screenshots of the n8n UI, exported workflow JSON if credentials get inadvertently embedded). Environment variables are the same mechanism used on the CrewAI side, so both implementations' secret-handling story is consistent and equally easy to describe in the README. Tradeoff: n8n's credential store gives you per-credential OAuth refresh handling for free (relevant for Google Docs, see below) — so this is genuinely a "use env vars for API keys, use n8n's store only for OAuth objects" split, not a pure either/or.

## Logging destination: local structured JSON Lines file, not a hosted service

**Decision:** every agent/tool/document-write event (per the Phase 7 run-record schema) is appended as one JSON object per line to a local `logs/runs.jsonl` file, per implementation (`n8n/logs/runs.jsonl`, `crewai/logs/runs.jsonl`). No external logging service (LangSmith, Firestore, etc.) for the graded submission.

**Why:** the capstone needs run records comparable across two independently-built implementations and reviewable by someone else (a grader) without needing an account on a third-party observability platform. A flat JSONL file is trivial to `grep`/`jq`, attach to the submission, and diff between the n8n and CrewAI runs. The course demos' LangSmith/Firestore examples are good references for the *shape* of what to log, not a requirement to actually run through those services.

**Cost if wrong:** if per-run logging volume or the need for a live dashboard grows, this doesn't scale past local development — acceptable here since the deliverable is a bounded, ≤12-minute, ≤$2.50 run, not a production service.

## Google Docs OAuth: deferred during environment setup, completed with the Docs Writer build (P6.2)

**Decision:** Google Cloud Console project creation, OAuth consent screen, and the Docs/Drive API-scoped OAuth client were deferred from Step 3's environment pass and set up when the Docs Writer step was built. **Status: done and verified** — the n8n credential shows "Account connected", and two full-document writes succeeded through it (an isolated test and a full pipeline run).

**Why deferred:** OAuth setup is the most failure-prone, order-dependent step in the whole guide (redirect URI must be known from n8n *before* creating the Google OAuth client, or you hit `redirect_uri_mismatch`) and it exists to serve one specific node. Setting it up early would have meant debugging OAuth with nothing downstream to test it against. Sheets API is confirmed optional by the expanded guide and was skipped.

**What was set up (so it can be reproduced):**
- Google Cloud project `wire3-gtm-capstone`, created with no parent organization.
- APIs enabled: **Google Docs API** and **Google Drive API** only.
- Google Auth Platform / consent screen: app name "Wire3 GTM Capstone", user type **External**, publishing status **Testing**, the developer's own Google account as support contact, contact email and the only **test user**. External was the only option because the project has no organization; the "Internal" type needs a Workspace org.
- OAuth client: type **Web application**, name "n8n Wire3 Docs Writer", authorized redirect URI `http://localhost:5678/rest/oauth2-credential/callback` (n8n's default callback for a local instance — it matches the redirect URL n8n displays in the credential form).
- n8n credential: "Google Docs account" (Google Docs OAuth2 API), client ID/secret pasted in, then *Sign in with Google*. Scopes are n8n's defaults for that credential type (the "Custom Scopes" toggle was left off).

**Cost if wrong / things to know:**
- **Refresh tokens expire after 7 days** while the consent screen is in Testing status. A run more than a week after the last sign-in can fail with an auth error; the fix is to open the credential in n8n and click *Sign in with Google* again. Publishing the app would lift this, but it invites Google's app-verification process and isn't worth it for a bounded capstone. **Re-authorize before any graded run.**
- Only the listed test user can authorize, and the redirect URI is localhost, so this setup works only for a locally running n8n. Hosting n8n elsewhere means adding that host's callback URL to the client.
- Google may take a few minutes to apply a newly added redirect URI; a fresh `redirect_uri_mismatch` right after creating the client is worth waiting out before debugging.
- During sign-in Google shows an "unverified app" warning in Testing mode; that is expected.

## Docs Writer: Google Docs REST API via HTTP Request nodes, not a single built-in node

**Decision:** the Docs Writer creates the document with `POST /v1/documents` and fills it with one `documents.batchUpdate` call, using n8n's HTTP Request node with the `Google Docs account` OAuth credential, rather than a single n8n Google Docs node.

**Why:** one `batchUpdate` request list can insert all text and apply heading styles, bullets, bold labels and hyperlinks (source titles as clickable links) in a single atomic write, and the layout logic stays in a Code node that can be unit-tested locally with no Google account (`n8n/docs_writer/test_local.js`). The built-in Google Docs node was not evaluated in depth, so this is a chosen approach, not a proven necessity.

**Cost if wrong:** the raw API calls are more code to maintain than a built-in node would be, and the retry settings have to be set by hand per node (the write step is deliberately left without retry, because re-sending `insertText` would duplicate the document; see `n8n/RELIABILITY_AND_VALIDATION.md`).

## Docs Writer output: private documents in the developer's Drive

**Decision:** each run creates a new Google Doc in the authorizing account's Drive and returns its URL (`document_url`) in the HTTP response and the run log. No sharing settings are applied.

**Why:** setting a sharing policy is a separate, consequential choice (it exposes generated content and, via Drive, could reach people the developer didn't intend), and no requirement for it was found while building. The private default is the safe one.

**Cost if wrong:** a grader or teammate opening `document_url` will get an access request until the document is shared. Decide how the deliverable reaches reviewers (share the document, export it to PDF, or attach it to the submission) before submission. Every full run also leaves one more document in Drive, so test runs need occasional manual cleanup.
