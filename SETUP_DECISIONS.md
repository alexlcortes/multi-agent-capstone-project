# Environment & Infrastructure Decisions

Running log of setup decisions made outside the schema contract, for the final README (guide Step 12, items 4/5/9). Each entry: what was decided, why, and what it costs us if we're wrong.

## Search provider: SerpAPI + Tavily, both behind one adapter

**Decision:** implement one search-provider interface in the MCP layer; both SerpAPI and Tavily are real, working implementations of it. Tavily is the default during development/testing; SerpAPI is used for final graded runs, since the capstone brief explicitly names it.

**Why:** the capstone brief names SerpAPI specifically, but the course demos build against Tavily, and this project is self-funded (personal OpenAI/SerpAPI keys, not course-VM credentials). SerpAPI's free tier is a one-time ~100 searches before billing kicks in (~$50/mo for 5k after that); the brief's own per-run budget allows up to 40 search calls, so 2–3 development runs would exhaust it. Tavily's free tier (~1,000 requests/month) makes iterative development affordable without touching the SerpAPI quota at all.

**Cost if wrong:** if the adapter silently only calls Tavily while claiming SerpAPI support, that's a real compliance gap the guide explicitly warns against ("do not silently claim SerpAPI support when the implementation only calls Tavily"). Mitigated by keeping both implementations real and documenting which one is active per run in the run-record log (Phase 7).

## Credential storage: environment variables (`.env`), not n8n's built-in credential store

**Decision:** all secrets (OpenAI key, SerpAPI key, Tavily key, Google OAuth client secret) live in `.env` files (gitignored), loaded as environment variables. n8n's native credential store is used only where n8n *requires* it structurally (its OpenAI/Google OAuth credential objects, which nodes reference by name) — but the underlying values are still sourced from environment variables via n8n's `$env` expressions / credential-from-env pattern wherever the node type allows it, not typed in by hand into the UI.

**Why:** n8n's credential store is encrypted at rest and fine for a single local instance, but it's UI-managed state that doesn't show up in the exported workflow JSON or in git — meaning it doesn't travel with the project the way a documented `.env.example` does, and it's a second place secrets could end up (screenshots of the n8n UI, exported workflow JSON if credentials get inadvertently embedded). Environment variables are the same mechanism used on the CrewAI side, so both implementations' secret-handling story is consistent and equally easy to describe in the README. Tradeoff: n8n's credential store gives you per-credential OAuth refresh handling for free (relevant for Google Docs, see below) — so this is genuinely a "use env vars for API keys, use n8n's store only for OAuth objects" split, not a pure either/or.

## Logging destination: local structured JSON Lines file, not a hosted service

**Decision:** every agent/tool/document-write event (per the Phase 7 run-record schema) is appended as one JSON object per line to a local `logs/runs.jsonl` file, per implementation (`n8n/logs/runs.jsonl`, `crewai/logs/runs.jsonl`). No external logging service (LangSmith, Firestore, etc.) for the graded submission.

**Why:** the capstone needs run records comparable across two independently-built implementations and reviewable by someone else (a grader) without needing an account on a third-party observability platform. A flat JSONL file is trivial to `grep`/`jq`, attach to the submission, and diff between the n8n and CrewAI runs. The course demos' LangSmith/Firestore examples are good references for the *shape* of what to log, not a requirement to actually run through those services.

**Cost if wrong:** if per-run logging volume or the need for a live dashboard grows, this doesn't scale past local development — acceptable here since the deliverable is a bounded, ≤12-minute, ≤$2.50 run, not a production service.

## Google Docs OAuth: deferred to the Docs Writer build (P6.2), not done during basic environment setup

**Decision:** Google Cloud Console project creation, OAuth consent screen, and the Docs/Drive API-scoped OAuth client are set up when the Docs Writer step is actually built, not during Step 3's environment pass.

**Why:** OAuth setup is the most failure-prone, order-dependent step in the whole guide (redirect URI must be copied from n8n *before* creating the Google OAuth client, or you hit `redirect_uri_mismatch`) and it exists to serve one specific node that doesn't exist yet. Setting it up now would mean debugging OAuth for a step with nothing downstream to test it against. Scopes needed when we get there: Docs API + Drive API only — Sheets API is confirmed optional by the expanded guide and is skipped unless a future need for Sheets actually arises.
