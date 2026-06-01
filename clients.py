import os
import requests
from typing import Any, Dict


class DevinClient:
    def __init__(self) -> None:
        self.mock = os.getenv("MOCK_DEVIN", "true").lower() == "true"
        self.api_key = os.getenv("DEVIN_API_KEY", "dummy")
        self.base_url = os.getenv("DEVIN_API_BASE_URL", "https://api.devin.ai").rstrip("/")

    def create_session(self, stage: str, prompt: str) -> Dict[str, Any]:
        if self.mock:
            return {
                "id": f"mock-{stage}-session",
                "status": "completed",
                "output": f"# Mock {stage.title()} Artifact\n\nGenerated fake artifact text for {stage}.",
            }

        resp = requests.post(
            f"{self.base_url}/v1/sessions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


class GitHubClient:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN", "")
        self.api = "https://api.github.com"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def comment_issue(self, repo: str, issue_number: int, body: str) -> Dict[str, Any]:
        if not self.token:
            return {"ok": False, "skipped": True, "reason": "GITHUB_TOKEN is empty"}

        try:
            resp = requests.post(
                f"{self.api}/repos/{repo}/issues/{issue_number}/comments",
                headers=self._headers(),
                json={"body": body},
                timeout=30,
            )
            return {
                "ok": resp.ok,
                "status_code": resp.status_code,
                "body": resp.text[:500],
            }
        except Exception as e:
            return {
                "ok": False,
                "skipped": True,
                "reason": str(e),
            }

    def list_pull_requests(self, repo: str, state: str = "open") -> list[Dict[str, Any]]:
        resp = requests.get(
            f"{self.api}/repos/{repo}/pulls",
            headers=self._headers(),
            params={"state": state, "per_page": 100, "sort": "updated", "direction": "desc"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
