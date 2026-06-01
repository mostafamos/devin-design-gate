import ast
import html
import json
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from graph import run_pipeline, store
from clients import GitHubClient


app = FastAPI(title="Devin Design Gate", version="0.1.0")


class SimulatePayload(BaseModel):
    repo: str | None = None
    issue_number: int
    issue_title: str
    issue_body: str = ""
    issue_url: str = ""


class CommentTestPayload(BaseModel):
    repo: str | None = None
    issue_number: int


def trigger_label() -> str:
    return os.getenv("TRIGGER_LABEL", "devin:design-first")


def _parse_artifact(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _first_line(value: str | None) -> str:
    if not value:
        return ""
    return next((line.strip("# ").strip() for line in value.splitlines() if line.strip()), "")


def _format_dt(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ").replace("+00:00", " UTC")


def _session_href(session_id: str, artifact: dict[str, Any]) -> str:
    if artifact.get("url"):
        return str(artifact["url"])
    if session_id.startswith("devin-"):
        return f"https://app.devin.ai/sessions/{session_id.removeprefix('devin-')}"
    return ""


def render_report_html(report_data: dict[str, Any]) -> str:
    pipelines = report_data.get("pipelines", [])
    completed = sum(1 for pipeline in pipelines if pipeline.get("status") in {"verification", "completed"})
    stage_count = sum(len(pipeline.get("stages", [])) for pipeline in pipelines)

    pipeline_cards = []
    for index, pipeline in enumerate(pipelines):
        stages = pipeline.get("stages", [])
        latest_stage = stages[-1] if stages else {}
        stage_items = []
        for stage in stages:
            artifact = _parse_artifact(stage.get("artifact"))
            session_id = stage.get("session_id") or artifact.get("session_id") or artifact.get("id") or ""
            session_href = _session_href(str(session_id), artifact)
            title = artifact.get("message") or artifact.get("summary") or _first_line(stage.get("artifact")) or "Stage completed"
            details = []
            if artifact.get("branch"):
                details.append(f"Branch: {html.escape(str(artifact['branch']))}")
            if artifact.get("sha"):
                details.append(f"Commit: {html.escape(str(artifact['sha'])[:12])}")
            if artifact.get("pusher"):
                details.append(f"Pushed by: {html.escape(str(artifact['pusher']))}")
            if artifact.get("compare_url"):
                safe_compare = html.escape(str(artifact["compare_url"]), quote=True)
                details.append(f'<a href="{safe_compare}">Compare changes</a>')
            if session_href:
                safe_session_href = html.escape(session_href, quote=True)
                safe_session_id = html.escape(str(session_id))
                details.append(f'<a href="{safe_session_href}">{safe_session_id}</a>')
            detail_html = " ".join(f"<span>{item}</span>" for item in details)
            stage_items.append(f"""
                <li class="stage stage-{html.escape(stage.get('stage', ''))}">
                    <div class="stage-marker"></div>
                    <div>
                        <div class="stage-head">
                            <strong>{html.escape(stage.get('stage', '').replace('-', ' ').title())}</strong>
                            <span>{html.escape(stage.get('status', ''))}</span>
                        </div>
                        <p>{html.escape(str(title))}</p>
                        <div class="meta">{detail_html}</div>
                        <time>{html.escape(_format_dt(stage.get('created_at')))}</time>
                    </div>
                </li>
            """)

        safe_pr = html.escape(pipeline.get("pr_url") or "", quote=True)
        pr_link = f'<a href="{safe_pr}">Open PR</a>' if safe_pr else "<span>No PR yet</span>"
        run_id = ' id="latest-run"' if index == 0 else ""
        pipeline_cards.append(f"""
            <article class="run-card"{run_id}>
                <div class="run-top">
                    <div>
                        <p class="eyebrow">{html.escape(pipeline.get('repo', ''))} #{html.escape(str(pipeline.get('issue_number', '')))}</p>
                        <h2>{html.escape(pipeline.get('issue_title', 'Untitled issue'))}</h2>
                    </div>
                    <span class="status">{html.escape(str(pipeline.get('status', '')))}</span>
                </div>
                <div class="run-meta">
                    <span>Current stage: <strong>{html.escape(str(latest_stage.get('stage', pipeline.get('status', ''))))}</strong></span>
                    <span>Updated: <strong>{html.escape(_format_dt(pipeline.get('updated_at')))}</strong></span>
                    {pr_link}
                </div>
                <ol class="timeline">
                    {''.join(stage_items) if stage_items else '<li class="empty">No stages recorded yet.</li>'}
                </ol>
            </article>
        """)

    cards_html = "".join(pipeline_cards) or '<section class="empty-state">No pipeline runs recorded yet.</section>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Devin Design Gate Report</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --accent: #0f766e;
      --accent-soft: #d9f4ef;
      --warn: #a16207;
      --shadow: 0 16px 40px rgba(23, 32, 51, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      padding: 32px clamp(20px, 5vw, 72px) 20px;
      background: #172033;
      color: white;
    }}
    header h1 {{ margin: 0 0 8px; font-size: clamp(28px, 4vw, 44px); font-weight: 750; }}
    header p {{ margin: 0; color: #cbd5e1; max-width: 780px; }}
    main {{ padding: 24px clamp(20px, 5vw, 72px) 48px; }}
    .stats {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .stat, .run-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .stat {{ padding: 16px; }}
    .stat span {{ display: block; color: var(--muted); font-size: 13px; }}
    .stat strong {{ display: block; margin-top: 6px; font-size: 28px; }}
    .run-card {{ padding: 22px; margin-top: 16px; }}
    .run-top {{ display: flex; gap: 16px; justify-content: space-between; align-items: start; }}
    .eyebrow {{ margin: 0 0 6px; color: var(--accent); font-size: 13px; font-weight: 700; }}
    h2 {{ margin: 0; font-size: 22px; line-height: 1.25; }}
    .status {{
      flex: 0 0 auto;
      border-radius: 999px;
      background: var(--accent-soft);
      color: #115e59;
      padding: 6px 10px;
      font-size: 13px;
      font-weight: 700;
      text-transform: capitalize;
    }}
    .run-meta {{ display: flex; flex-wrap: wrap; gap: 10px 16px; margin: 16px 0 10px; color: var(--muted); font-size: 14px; }}
    a {{ color: #0b63ce; text-decoration: none; font-weight: 650; }}
    a:hover {{ text-decoration: underline; }}
    .timeline {{ list-style: none; margin: 18px 0 0; padding: 0; }}
    .stage {{ display: grid; grid-template-columns: 18px 1fr; gap: 12px; padding: 0 0 18px; position: relative; }}
    .stage:not(:last-child)::before {{ content: ""; position: absolute; left: 8px; top: 20px; bottom: 0; width: 2px; background: var(--line); }}
    .stage-marker {{ width: 18px; height: 18px; border-radius: 50%; background: var(--accent); border: 4px solid var(--accent-soft); margin-top: 2px; z-index: 1; }}
    .stage-head {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }}
    .stage-head strong {{ font-size: 16px; }}
    .stage-head span {{ color: var(--muted); font-size: 13px; text-transform: capitalize; }}
    .stage p {{ margin: 5px 0 8px; color: #344054; line-height: 1.45; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }}
    .meta span {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: #475467;
      background: #fbfcfe;
      font-size: 13px;
    }}
    time {{ color: var(--muted); font-size: 13px; }}
    .empty, .empty-state {{ color: var(--muted); padding: 20px; }}
    @media (max-width: 720px) {{
      .stats {{ grid-template-columns: 1fr; }}
      .run-top {{ display: block; }}
      .status {{ display: inline-block; margin-top: 12px; }}
      .run-card {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Devin Design Gate Report</h1>
    <p>Pipeline sessions, stage outcomes, PR activity, and recent Devin branch updates.</p>
  </header>
  <main>
    <section class="stats">
      <div class="stat"><span>Total runs</span><strong>{len(pipelines)}</strong></div>
      <div class="stat"><span>Completed runs</span><strong>{completed}</strong></div>
      <div class="stat"><span>Recorded events</span><strong>{stage_count}</strong></div>
    </section>
    {cards_html}
  </main>
</body>
</html>"""


def _push_branch(payload: dict[str, Any]) -> str:
    ref = payload.get("ref") or ""
    return ref.removeprefix("refs/heads/")


def _record_push_event(payload: dict[str, Any]) -> dict[str, Any]:
    repository = payload.get("repository") or {}
    repo = repository.get("full_name") or os.getenv("TARGET_REPO")
    if not repo:
        raise HTTPException(status_code=400, detail="repository.full_name or TARGET_REPO is required")

    branch = _push_branch(payload)
    if not branch.startswith("devin/"):
        return {"ok": True, "triggered": False, "reason": "ignored branch", "branch": branch}

    head_commit = payload.get("head_commit") or {}
    artifact = {
        "branch": branch,
        "sha": payload.get("after") or head_commit.get("id") or "",
        "message": head_commit.get("message") or "Devin branch received a new push.",
        "pusher": (payload.get("pusher") or {}).get("name") or (payload.get("sender") or {}).get("login") or "",
        "timestamp": head_commit.get("timestamp") or "",
        "compare_url": payload.get("compare") or "",
    }
    pipeline = store.add_branch_update(repo, "branch-update", artifact)
    return {
        "ok": True,
        "triggered": bool(pipeline),
        "reason": "recorded devin branch push" if pipeline else "no pipeline found for repo",
        "pipeline_id": pipeline.get("id") if pipeline else None,
        "branch": branch,
    }


def _record_pull_request_sync(payload: dict[str, Any]) -> dict[str, Any]:
    pr = payload.get("pull_request") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    repo = head_repo.get("full_name") or (payload.get("repository") or {}).get("full_name") or os.getenv("TARGET_REPO")
    branch = head.get("ref") or ""
    if not repo:
        raise HTTPException(status_code=400, detail="pull_request.head.repo.full_name or TARGET_REPO is required")
    if not branch.startswith("devin/"):
        return {"ok": True, "triggered": False, "reason": "ignored branch", "branch": branch}

    artifact = {
        "branch": branch,
        "sha": head.get("sha") or "",
        "message": f"Pull request synchronized: {pr.get('title') or 'untitled PR'}",
        "pusher": ((payload.get("sender") or {}).get("login")) or "",
        "timestamp": pr.get("updated_at") or "",
        "compare_url": pr.get("html_url") or "",
    }
    pipeline = store.add_branch_update(repo, "pr-synchronize", artifact)
    return {
        "ok": True,
        "triggered": bool(pipeline),
        "reason": "recorded pull request synchronization" if pipeline else "no pipeline found for repo",
        "pipeline_id": pipeline.get("id") if pipeline else None,
        "branch": branch,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "target_repo": os.getenv("TARGET_REPO", ""),
        "mock_devin": os.getenv("MOCK_DEVIN", "true"),
    }


@app.post("/simulate")
def simulate(payload: SimulatePayload):
    repo = payload.repo or os.getenv("TARGET_REPO")
    if not repo:
        raise HTTPException(status_code=400, detail="repo or TARGET_REPO is required")

    return run_pipeline(
        repo=repo,
        issue_number=payload.issue_number,
        issue_title=payload.issue_title,
        issue_body=payload.issue_body,
        issue_url=payload.issue_url,
    )


async def parse_github_payload(request: Request) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON webhook payload must be an object")
        return payload

    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        raw_payload = form.get("payload")
        if not raw_payload:
            raise HTTPException(status_code=400, detail="form webhook payload field is required")
        try:
            payload = json.loads(str(raw_payload))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid form payload JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="form webhook payload must decode to an object")
        return payload

    raise HTTPException(status_code=415, detail=f"unsupported content-type: {content_type}")


@app.post("/github/webhook")
async def github_webhook(request: Request, x_github_event: str | None = Header(default=None)):
    payload = await parse_github_payload(request)

    if x_github_event == "push":
        return _record_push_event(payload)

    if x_github_event == "pull_request" and payload.get("action") == "synchronize":
        return _record_pull_request_sync(payload)

    if x_github_event != "issues":
        return {"ok": True, "triggered": False, "reason": "ignored event"}

    if payload.get("action") != "labeled":
        return {"ok": True, "triggered": False, "reason": "ignored action"}

    label = payload.get("label") or {}
    label_name = label.get("name", "")
    expected_label = trigger_label()
    if label_name != expected_label:
        return {
            "ok": True,
            "triggered": False,
            "reason": "ignored label",
            "label": label_name,
            "expected_label": expected_label,
        }

    issue = payload.get("issue") or {}
    repository = payload.get("repository") or {}
    repo = repository.get("full_name") or os.getenv("TARGET_REPO")
    if not repo:
        raise HTTPException(status_code=400, detail="repository.full_name or TARGET_REPO is required")

    issue_number = issue.get("number")
    issue_title = issue.get("title")
    if issue_number is None or not issue_title:
        raise HTTPException(status_code=400, detail="issue.number and issue.title are required")

    result = run_pipeline(
        repo=repo,
        issue_number=int(issue_number),
        issue_title=issue_title,
        issue_body=issue.get("body") or "",
        issue_url=issue.get("html_url") or "",
    )
    return {"ok": True, "triggered": True, "label": label_name, "result": result}


@app.get("/github/webhook")
def github_webhook_info():
    return {
        "ok": True,
        "message": "GitHub webhook endpoint is alive. Configure GitHub to send POST issues events here.",
        "trigger_label": trigger_label(),
    }


@app.post("/github/comment-test")
def github_comment_test(payload: CommentTestPayload):
    repo = payload.repo or os.getenv("TARGET_REPO")
    if not repo:
        raise HTTPException(status_code=400, detail="repo or TARGET_REPO is required")
    result = GitHubClient().comment_issue(
        repo=repo,
        issue_number=payload.issue_number,
        body="GitHub token test from Devin Design Gate orchestrator.",
    )
    return {"ok": True, "result": result}


@app.get("/report.json")
def report_json():
    return store.report()


@app.get("/report", response_class=HTMLResponse)
def report():
    return HTMLResponse(render_report_html(store.report()))
