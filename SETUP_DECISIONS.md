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

## Cost and retry tracking (n8n): post-run script and a server-log count, both approximate

**Decision:** token usage and estimated cost are computed after each run by `n8n/scripts/run_usage.js`, which reads the stored execution from n8n's database and appends a `usage_summary` event to `runs.jsonl`. Retries are approximated inside the workflow by `rate_limit_errors_logged`: the number of HTTP 429 events n8n printed to `logs/n8n_server.log` during the run. There is no true per-node retry count.

**Why:** the guide asks for retry count, token usage and estimated cost per run. In n8n, LLM sub-node token data is unreadable from Code nodes (tested: `No data found from main input`) and stored executions carry no attempt counter, so a workflow node cannot capture either. A post-run script and a log-offset count were the least invasive options that produce a real number.

**Cost if wrong:** both figures are approximations and are labelled that way in the log. The cost is a **lower bound**: n8n's token counts are character-based estimates that exclude hidden reasoning tokens, and search-provider fees are not included. Prices in the script (gpt-5-mini, $0.25 / $2.00 per 1M input/output tokens, verified 2026-09-19) must be updated by hand if they change. The 429 count undercounts silently-successful retries and only works if n8n's output is redirected to `n8n/logs/n8n_server.log`. `budget.max_cost_usd` is recorded but not enforced. The CrewAI implementation should log attempts and provider-reported usage directly, so the two runs are compared with the limitation stated. Before quoting cost in the README, check the OpenAI usage dashboard for the run's time window.

## Docs Writer: Google Docs REST API via HTTP Request nodes, not a single built-in node

**Decision:** the Docs Writer creates the document with `POST /v1/documents` and fills it with one `documents.batchUpdate` call, using n8n's HTTP Request node with the `Google Docs account` OAuth credential, rather than a single n8n Google Docs node.

**Why:** one `batchUpdate` request list can insert all text and apply heading styles, bullets, bold labels and hyperlinks (source titles as clickable links) in a single atomic write, and the layout logic stays in a Code node that can be unit-tested locally with no Google account (`n8n/docs_writer/test_local.js`). The built-in Google Docs node was not evaluated in depth, so this is a chosen approach, not a proven necessity.

**Cost if wrong:** the raw API calls are more code to maintain than a built-in node would be, and the retry settings have to be set by hand per node (the write step is deliberately left without retry, because re-sending `insertText` would duplicate the document; see `n8n/RELIABILITY_AND_VALIDATION.md`).

## Docs Writer output: private documents in the developer's Drive

**Decision:** each run creates a new Google Doc in the authorizing account's Drive and returns its URL (`document_url`) in the HTTP response and the run log. No sharing settings are applied.

**Why:** setting a sharing policy is a separate, consequential choice (it exposes generated content and, via Drive, could reach people the developer didn't intend), and no requirement for it was found while building. The private default is the safe one.

**Cost if wrong:** a grader or teammate opening `document_url` will get an access request until the document is shared. Decide how the deliverable reaches reviewers (share the document, export it to PDF, or attach it to the submission) before submission. Every full run also leaves one more document in Drive, so test runs need occasional manual cleanup.

## CrewAI: the "6 LLM calls per role" budget counts reasoning calls, not per-tool steps

**Decision:** in the CrewAI implementation, the brief's cap of 6 LLM calls per agent role is applied to an agent's reasoning and planning calls. The Research Agent's one-LLM-step-per-tool-call loop is treated as tool execution and is bounded by the plan instead (at most `max_search_calls`, 40). Research's `max_iter` is 50, the same as n8n's Research node. Head Planner, Analyst and Strategy keep `max_iter=6`. Actual LLM calls per role are counted from `LLMCallStartedEvent` (`wire3_gtm/verify_handoff.py`) and reported, not assumed.

**Why:** an agent that calls tools makes one LLM request per tool call plus a final one, so a 14-call plan takes 15 requests (measured on 2026-09-20: Head Planner 1, Research 15). A literal cap of 6 stopped Research after about 6 of 15 planned calls (`max_iter=6` gave 6/15 executed), which silently truncated the evidence set. n8n's Research Agent has the same per-call pattern with `maxIterations` 50, so this keeps the two implementations comparable.

**Cost if wrong:** the brief's per-role budget is not literally met for Research: 15 LLM calls against a cap of 6. The comparison must state this for both implementations. If a grader reads the cap literally, the alternatives are issuing the plan's calls in parallel within one or two LLM steps, or executing the plan in code with the agent only reporting. `max_iter` is a ceiling, not a counter, and nothing yet fails a run that goes over 6 reasoning calls.

## CrewAI Docs Writer: added after the text-only flow was shown stable, with its own Google credentials

**Decision:** the CrewAI Docs Writer (`wire3_gtm/docs_content.py`, `docs_google.py`, `pipeline.run_docs`) is a separate milestone that reads a finished run's saved artifacts. It was built only after two fresh end-to-end runs (Planner→Research→Analyst→Strategy) completed cleanly. "Clean" meant every step `ok`, no salvage, and under the brief's 12 minutes: run `run-20260920-211110` took 7.6 min (Analyst 2 attempts), run `run-20260920-211852` took 9.4 min (Analyst 3 attempts). The one earlier full run had failed in the Analyst after 11.7 min; that failure (an invented evidence id) is what `drop_invented_ids` now handles.

**Why last:** the guide says not to let OAuth block the project and to inspect the validated Strategy output as local JSON or Markdown first; the Docs Writer is a renderer, not one of the four agents; and it must only receive validated structured content. A document write also has side effects a text run does not: each run creates a real Google Doc, and an automatic retry could duplicate it.

**How it differs from the n8n Docs Writer:** it also renders the Analyst's tables (competitor and feature comparison, pricing matrix, themes, SWOT, 7P, assumptions/unknowns/conflicts) and a research-scope section with evidence-quality caveats (top-tier source share, unsupported numbers, dropped ids, salvage). The brief lists these sections as required; the n8n document renders only the Strategy artifact. Tables are rendered as labelled bullet lists, as in n8n, not Google Docs tables.

**Credentials (CrewAI cannot use n8n's):** the Google OAuth token in n8n lives in n8n's encrypted store. CrewAI needs its own: create an OAuth client of type **Desktop app** in the same Google Cloud project (Docs and Drive APIs are already enabled), download it to `crewai/.google/client_secret.json` (gitignored), then run `uv run python -m wire3_gtm google-auth` once (opens a browser); the token is saved to `crewai/.google/token.json` (gitignored). Scopes are `documents` and `drive.file` (only files this app creates, enough to create the Doc and export a PDF). If the consent screen is in Testing mode, refresh tokens expire after 7 days and `google-auth` must be rerun.

**Reliability choices:** the document id is saved the moment the Doc exists, and a rerun verifies that document instead of creating another. `batchUpdate` is deliberately not retried (an ambiguous failure could insert the text twice); create, read and export retry up to 3 times. Before anything is written the content is re-validated with the same checks as the guardrails, and the requests are applied to a simulated document (UTF-16 indexing, as Google uses) and verified locally.

**Cost if wrong:** the Google path has been tested only against a fake client and a simulated read-back, not a live Google account, until `google-auth` has been run. The post-write check confirms headings, source ids, hyperlinks and orphaned ids; it does not check that the text reads well.
