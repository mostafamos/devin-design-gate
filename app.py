import json
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
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


@app.get("/report")
def report():
    return store.report()
