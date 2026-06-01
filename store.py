import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.getenv("DB_PATH", "./devin_orchestrator.db")
        self._memory_connection = (
            sqlite3.connect(self.path, check_same_thread=False) if self.path == ":memory:" else None
        )
        self.init()

    def connect(self):
        if self._memory_connection is not None:
            return self._memory_connection
        return sqlite3.connect(self.path)

    def init(self) -> None:
        with self.connect() as con:
            con.execute("""
            CREATE TABLE IF NOT EXISTS pipelines (
                id TEXT PRIMARY KEY,
                repo TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                issue_title TEXT NOT NULL,
                status TEXT NOT NULL,
                pr_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """)
            con.execute("""
            CREATE TABLE IF NOT EXISTS stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                session_id TEXT,
                artifact TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(pipeline_id) REFERENCES pipelines(id)
            )
            """)

    def create_pipeline(self, repo: str, issue_number: int, issue_title: str) -> str:
        pipeline_id = f"{repo}#{issue_number}"
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO pipelines
                (id, repo, issue_number, issue_title, status, pr_url, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pipeline_id, repo, issue_number, issue_title, "started", None, now(), now()),
            )
        return pipeline_id

    def add_stage(self, pipeline_id: str, stage: str, status: str, session_id: str, artifact: str) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO stages
                (pipeline_id, stage, status, session_id, artifact, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (pipeline_id, stage, status, session_id, artifact, now()),
            )
            con.execute(
                "UPDATE pipelines SET status=?, updated_at=? WHERE id=?",
                (stage, now(), pipeline_id),
            )

    def set_pr(self, pipeline_id: str, pr_url: str) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE pipelines SET pr_url=?, status=?, updated_at=? WHERE id=?",
                (pr_url, "implementation", now(), pipeline_id),
            )

    def latest_pipeline_for_repo(self, repo: str) -> Dict[str, Any] | None:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM pipelines WHERE repo=? ORDER BY updated_at DESC LIMIT 1",
                (repo,),
            ).fetchone()
        return dict(row) if row else None

    def add_branch_update(self, repo: str, stage: str, artifact: Dict[str, Any]) -> Dict[str, Any] | None:
        pipeline = self.latest_pipeline_for_repo(repo)
        if not pipeline:
            return None

        self.add_stage(
            pipeline["id"],
            stage,
            "completed",
            artifact.get("session_id") or "",
            json.dumps(artifact, indent=2, sort_keys=True),
        )
        return pipeline

    def report(self) -> Dict[str, Any]:
        with self.connect() as con:
            con.row_factory = sqlite3.Row
            pipelines = [dict(r) for r in con.execute("SELECT * FROM pipelines ORDER BY updated_at DESC")]
            stages = [dict(r) for r in con.execute("SELECT * FROM stages ORDER BY id ASC")]

        by_pipeline: Dict[str, List[Dict[str, Any]]] = {}
        for stage in stages:
            by_pipeline.setdefault(stage["pipeline_id"], []).append(stage)

        for pipeline in pipelines:
            pipeline["stages"] = by_pipeline.get(pipeline["id"], [])

        return {
            "total_pipelines": len(pipelines),
            "pipelines": pipelines,
        }
