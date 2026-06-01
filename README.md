# Devin Design Gate

Minimal FastAPI + LangGraph orchestrator for a design-gated Devin remediation workflow.

## Run

```bash
cp .env.example .env
# edit .env with GITHUB_TOKEN, TARGET_REPO, and PUBLIC_WEBHOOK_URL
pip install -r requirements.txt
uvicorn app:app --reload
```

## Cloudflare Tunnel

Quick Cloudflare tunnels change URL every time they restart.

```bash
cloudflared tunnel --url http://localhost:8000
```

When cloudflared prints a new URL, set this in `.env`:

```bash
PUBLIC_WEBHOOK_URL=https://your-current-cloudflare-url.trycloudflare.com/github/webhook
```

Then update the GitHub webhook Payload URL to the same value:

```text
https://your-current-cloudflare-url.trycloudflare.com/github/webhook
```

In GitHub webhook settings, enable `Issues` events. Re-adding the `devin:design-first` label sends an `issues.labeled` event and triggers the pipeline again.

Enable `Pushes` and `Pull requests` too if you want `/report` to update when Devin pushes more commits to a `devin/*` branch or synchronizes a pull request.

## Health

```bash
curl http://localhost:8000/health
```

Browser URLs:

```text
http://localhost:8000/health
http://localhost:8000/github/webhook
http://localhost:8000/report
http://localhost:8000/docs
```

With the current Cloudflare tunnel, replace the host with your latest tunnel base URL:

```text
https://your-current-cloudflare-url.trycloudflare.com/health
https://your-current-cloudflare-url.trycloudflare.com/github/webhook
https://your-current-cloudflare-url.trycloudflare.com/report
https://your-current-cloudflare-url.trycloudflare.com/docs
```

What each URL does:

```text
/health         Confirms the app is running and shows TARGET_REPO and MOCK_DEVIN.
/github/webhook Browser-safe status page for the webhook endpoint. GitHub must POST here.
/report         Shows the HTML pipeline report with sessions, stages, PRs, and branch updates.
/report.json    Returns the raw stored pipeline runs and stage history as JSON.
/docs           FastAPI interactive API page for trying endpoints manually.
```

## Test GitHub token can comment

Use a real issue number in your fork.

```bash
curl -X POST http://localhost:8000/github/comment-test ^
  -H "Content-Type: application/json" ^
  -d "{\"issue_number\":1}"
```

## Simulate full mock pipeline

```bash
curl -X POST http://localhost:8000/simulate ^
  -H "Content-Type: application/json" ^
  -d "{\"issue_number\":1,\"issue_title\":\"Reports: preserve actionable failure reason when scheduled report execution fails\",\"issue_body\":\"Operators need actionable failure reasons.\"}"
```

## Report

```bash
curl http://localhost:8000/report
curl http://localhost:8000/report.json
```

## Label Trigger Flow

When GitHub sends `POST /github/webhook` with:

```text
X-GitHub-Event: issues
action: labeled
label.name: devin:design-first
```

the app starts the design-gated pipeline:

```text
triage -> spec -> lld -> implementation -> verification
```

Each stage is saved to SQLite and appears in `/report`. Because the app does not dedupe labeled events, removing `devin:design-first` and adding it again starts another full run.

Push webhooks for branches named `devin/*` and pull request `synchronize` webhooks are recorded as extra report events on the latest pipeline for that repo. The report links to the compare URL or PR so you can see when Devin pushed a new change after the initial run.

Keep `MOCK_DEVIN=true` until GitHub comments, SQLite, `/simulate`, and `/report` all work.
