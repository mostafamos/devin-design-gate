import json
import os
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

from clients import GitHubClient
from graph import run_pipeline, store
from report import render_report_html


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


class CheckReportPayload(BaseModel):
    upstream_repo: str | None = None
    target_repo: str | None = None
    state: str = "open"


def trigger_label() -> str:
    return os.getenv("TRIGGER_LABEL", "devin:design-first")


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


def _record_pull_request_event(payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action") or "updated"
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
        "message": f"Pull request {action}: {pr.get('title') or 'untitled PR'}",
        "pr_number": pr.get("number") or "",
        "pr_url": pr.get("html_url") or "",
        "base_repo": ((pr.get("base") or {}).get("repo") or {}).get("full_name") or "",
        "base_branch": (pr.get("base") or {}).get("ref") or "",
        "pusher": ((payload.get("sender") or {}).get("login")) or "",
        "timestamp": pr.get("updated_at") or "",
        "compare_url": pr.get("html_url") or "",
    }
    stage = f"pr-{action.replace('_', '-')}"
    pipeline = store.add_pr_update(repo, stage, pr.get("html_url") or "", artifact)
    return {
        "ok": True,
        "triggered": bool(pipeline),
        "reason": f"recorded pull request {action}" if pipeline else "no pipeline found for repo",
        "pipeline_id": pipeline.get("id") if pipeline else None,
        "branch": branch,
        "pr_url": pr.get("html_url") or "",
    }


def _pr_artifact(pr: dict[str, Any], action: str) -> dict[str, Any]:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    base_repo = base.get("repo") or {}
    user = pr.get("user") or {}
    return {
        "branch": head.get("ref") or "",
        "sha": head.get("sha") or "",
        "message": f"Pull request {action}: {pr.get('title') or 'untitled PR'}",
        "pr_number": pr.get("number") or "",
        "pr_url": pr.get("html_url") or "",
        "base_repo": base_repo.get("full_name") or "",
        "base_branch": base.get("ref") or "",
        "pusher": user.get("login") or "",
        "timestamp": pr.get("updated_at") or "",
        "compare_url": pr.get("html_url") or "",
    }


def check_and_update_report(
    upstream_repo: str | None = None,
    target_repo: str | None = None,
    state: str = "open",
) -> dict[str, Any]:
    upstream = upstream_repo or os.getenv("UPSTREAM_REPO", "apache/superset")
    target = target_repo or os.getenv("TARGET_REPO")
    if not target:
        raise HTTPException(status_code=400, detail="target_repo or TARGET_REPO is required")

    github_client = GitHubClient()
    try:
        prs = github_client.list_pull_requests(upstream, state=state)
    except Exception as exc:
        return {
            "ok": False,
            "updated": 0,
            "matched": 0,
            "reason": f"could not query GitHub pull requests: {exc}",
            "target_repo": target,
            "upstream_repo": upstream,
        }

    pipeline = next(
        (candidate for candidate in store.report().get("pipelines", []) if candidate["repo"] == target),
        None,
    )
    if not pipeline:
        return {
            "ok": True,
            "updated": 0,
            "matched": 0,
            "reason": "no pipeline found for target repo",
            "target_repo": target,
            "upstream_repo": upstream,
        }

    matched = []
    updated = []
    skipped = []
    for pr in prs:
        head = pr.get("head") or {}
        head_repo = head.get("repo") or {}
        branch = head.get("ref") or ""
        if head_repo.get("full_name") != target or not branch.startswith("devin/"):
            continue

        pr_url = pr.get("html_url") or ""
        stage = f"pr-{(pr.get('state') or 'open').replace('_', '-')}"
        matched.append({"number": pr.get("number"), "url": pr_url, "branch": branch})
        if pr_url and store.has_pr_event(pipeline["id"], pr_url):
            skipped.append({"number": pr.get("number"), "reason": "already recorded"})
            continue

        artifact = _pr_artifact(pr, pr.get("state") or "open")
        store.add_pr_update(target, stage, pr_url, artifact)
        updated.append({"number": pr.get("number"), "url": pr_url, "branch": branch, "stage": stage})

    return {
        "ok": True,
        "updated": len(updated),
        "matched": len(matched),
        "target_repo": target,
        "upstream_repo": upstream,
        "updated_prs": updated,
        "skipped_prs": skipped,
        "report_url": "/report-html#latest-run",
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

    if x_github_event == "pull_request" and payload.get("action") in {
        "opened",
        "reopened",
        "ready_for_review",
        "synchronize",
        "edited",
        "closed",
    }:
        return _record_pull_request_event(payload)

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


@app.get("/check-and-update-report")
def check_and_update_report_get(
    upstream_repo: str | None = None,
    target_repo: str | None = None,
    state: str = "open",
):
    return check_and_update_report(upstream_repo=upstream_repo, target_repo=target_repo, state=state)


@app.get("/check")
def check_and_update_report_short_get(
    upstream_repo: str | None = None,
    target_repo: str | None = None,
    state: str = "open",
):
    return check_and_update_report(upstream_repo=upstream_repo, target_repo=target_repo, state=state)


@app.get("/chec-and-update-report")
def check_and_update_report_typo_get(
    upstream_repo: str | None = None,
    target_repo: str | None = None,
    state: str = "open",
):
    return check_and_update_report(upstream_repo=upstream_repo, target_repo=target_repo, state=state)


@app.post("/check-and-update-report")
def check_and_update_report_post(payload: CheckReportPayload):
    return check_and_update_report(
        upstream_repo=payload.upstream_repo,
        target_repo=payload.target_repo,
        state=payload.state,
    )


@app.post("/check")
def check_and_update_report_short_post(payload: CheckReportPayload):
    return check_and_update_report(
        upstream_repo=payload.upstream_repo,
        target_repo=payload.target_repo,
        state=payload.state,
    )


@app.post("/chec-and-update-report")
def check_and_update_report_typo_post(payload: CheckReportPayload):
    return check_and_update_report(
        upstream_repo=payload.upstream_repo,
        target_repo=payload.target_repo,
        state=payload.state,
    )


@app.get("/report.json")
def report_json():
    return store.report()


@app.get("/report-state")
def report_state():
    report = store.report()
    stages = [stage for pipeline in report.get("pipelines", []) for stage in pipeline.get("stages", [])]
    latest_stage_id = max((int(stage.get("id") or 0) for stage in stages), default=0)
    latest_updated_at = max((str(pipeline.get("updated_at") or "") for pipeline in report.get("pipelines", [])), default="")
    return {
        "total_pipelines": report.get("total_pipelines", 0),
        "total_events": len(stages),
        "latest_stage_id": latest_stage_id,
        "latest_updated_at": latest_updated_at,
    }


@app.get("/report", response_class=HTMLResponse)
@app.get("/report-html", response_class=HTMLResponse)
def report_html():
    return HTMLResponse(render_report_html(store.report()))
