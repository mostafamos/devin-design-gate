import ast
import html
import json
from collections import defaultdict
from typing import Any


STAGE_ORDER = {
    "triage": 10,
    "spec": 20,
    "lld": 30,
    "implementation": 40,
    "verification": 50,
    "branch-update": 60,
    "pr-open": 70,
    "pr-opened": 70,
    "pr-reopened": 70,
    "pr-ready-for-review": 70,
    "pr-synchronize": 70,
    "pr-edited": 70,
    "pr-closed": 80,
}


def parse_artifact(value: Any) -> dict[str, Any]:
    """Return artifact JSON/dict safely, regardless of how older rows stored it."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if not isinstance(value, str):
        return {}

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return {}
    return parsed if isinstance(parsed, dict) else {}


def first_line(value: Any) -> str:
    if not value:
        return ""
    return next((line.strip("# ").strip() for line in str(value).splitlines() if line.strip()), "")


def format_dt(value: Any) -> str:
    if not value:
        return ""
    return str(value).replace("T", " ").replace("+00:00", " UTC")


def session_href(session_id: str, artifact: dict[str, Any]) -> str:
    if artifact.get("url"):
        return str(artifact["url"])
    if session_id.startswith("devin-"):
        return f"https://app.devin.ai/sessions/{session_id.removeprefix('devin-')}"
    return ""


def stage_rank(stage_name: str) -> int:
    return STAGE_ORDER.get(stage_name, 999)


def enrich_stage(stage: dict[str, Any]) -> dict[str, Any]:
    artifact = parse_artifact(stage.get("artifact"))
    session_id = str(stage.get("session_id") or artifact.get("session_id") or artifact.get("id") or "")
    title = (
        artifact.get("message")
        or artifact.get("summary")
        or first_line(stage.get("artifact"))
        or "Stage completed"
    )

    return {
        **stage,
        "artifact_map": artifact,
        "session_id": session_id,
        "session_url": session_href(session_id, artifact),
        "display_title": str(title),
        "display_stage": str(stage.get("stage", "")).replace("-", " ").title(),
        "display_status": str(stage.get("status", "")),
        "display_created_at": format_dt(stage.get("created_at")),
        "rank": stage_rank(str(stage.get("stage", ""))),
    }


def split_runs(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split one pipeline's events into human-readable runs.

    Preferred behavior:
    - if a stage has a run_id/attempt/run_number, use it;
    - otherwise start a new run when the stage order resets, e.g. verification -> triage.
    This fixes the old flat timeline where several executions looked like one long run.
    """
    enriched = [enrich_stage(stage) for stage in stages]
    if not enriched:
        return []

    has_explicit_run = any(
        stage.get("run_id")
        or stage.get("attempt")
        or stage.get("run_number")
        or stage["artifact_map"].get("run_id")
        or stage["artifact_map"].get("attempt")
        or stage["artifact_map"].get("run_number")
        for stage in enriched
    )

    runs: list[dict[str, Any]] = []
    if has_explicit_run:
        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for stage in enriched:
            key = str(
                stage.get("run_id")
                or stage.get("attempt")
                or stage.get("run_number")
                or stage["artifact_map"].get("run_id")
                or stage["artifact_map"].get("attempt")
                or stage["artifact_map"].get("run_number")
                or "run-1"
            )
            by_key[key].append(stage)
        for index, (key, run_stages) in enumerate(by_key.items(), start=1):
            runs.append(make_run(index=index, key=key, stages=run_stages))
        return runs

    current: list[dict[str, Any]] = []
    previous_rank = -1
    for stage in enriched:
        rank = stage["rank"]
        starts_new_design_cycle = (
            current
            and rank <= previous_rank
            and str(stage.get("stage")) in {"triage", "spec"}
        )
        if starts_new_design_cycle:
            runs.append(make_run(index=len(runs) + 1, key=f"run-{len(runs) + 1}", stages=current))
            current = []
        current.append(stage)
        previous_rank = rank

    if current:
        runs.append(make_run(index=len(runs) + 1, key=f"run-{len(runs) + 1}", stages=current))
    return runs


def make_run(index: int, key: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    started_at = stages[0].get("display_created_at", "") if stages else ""
    updated_at = stages[-1].get("display_created_at", "") if stages else ""
    sort_at = stages[-1].get("created_at", "") if stages else ""
    latest = stages[-1] if stages else {}
    pr_events = [stage for stage in stages if str(stage.get("stage", "")).startswith("pr-")]
    failed = [stage for stage in stages if str(stage.get("status", "")).lower() in {"failed", "error"}]
    status = "failed" if failed else ("pr-open" if pr_events else str(latest.get("stage", "running")))
    sessions = [stage for stage in stages if stage.get("session_id")]

    return {
        "index": index,
        "key": key,
        "stages": stages,
        "started_at": started_at,
        "updated_at": updated_at,
        "sort_at": sort_at,
        "latest_stage": latest,
        "status": status,
        "session_count": len(sessions),
        "event_count": len(stages),
    }


def group_report(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    issues: dict[tuple[str, str], dict[str, Any]] = {}

    for pipeline in report_data.get("pipelines", []):
        repo = str(pipeline.get("repo", ""))
        issue_number = str(pipeline.get("issue_number", ""))
        key = (repo, issue_number)
        issue = issues.setdefault(
            key,
            {
                "repo": repo,
                "issue_number": issue_number,
                "issue_title": pipeline.get("issue_title", "Untitled issue"),
                "issue_url": pipeline.get("issue_url", ""),
                "status": pipeline.get("status", ""),
                "pr_url": pipeline.get("pr_url", ""),
                "updated_at": pipeline.get("updated_at", ""),
                "pipelines": [],
                "runs": [],
            },
        )
        issue["pipelines"].append(pipeline)
        issue["status"] = pipeline.get("status") or issue["status"]
        issue["pr_url"] = pipeline.get("pr_url") or issue["pr_url"]
        issue["updated_at"] = pipeline.get("updated_at") or issue["updated_at"]

        for run in split_runs(pipeline.get("stages", [])):
            run["pipeline_id"] = pipeline.get("id")
            run["pipeline_status"] = pipeline.get("status", "")
            issue["runs"].append(run)

    for issue in issues.values():
        issue["runs"].sort(key=lambda run: run.get("sort_at") or run.get("started_at", ""), reverse=True)
        if issue["runs"]:
            issue["latest_run_at"] = issue["runs"][0].get("sort_at") or issue["runs"][0].get("updated_at", "")

    return sorted(
        issues.values(),
        key=lambda issue: issue.get("latest_run_at") or issue.get("updated_at", ""),
        reverse=True,
    )


def escape(value: Any, quote: bool = False) -> str:
    return html.escape(str(value or ""), quote=quote)


PROGRESS_STEPS = [
    ("triage", "Triage"),
    ("spec", "Spec"),
    ("lld", "LLD"),
    ("implementation", "Implementation"),
    ("verification", "Verification"),
    ("branch-update", "Push"),
    ("pr-open", "PR"),
]


def progress_state(run: dict[str, Any]) -> dict[str, Any]:
    stage_names = [str(stage.get("stage", "")) for stage in run.get("stages", [])]
    completed = set(stage_names)
    if any(stage.startswith("pr-") for stage in stage_names):
        completed.add("pr-open")

    current = ""
    for key, _label in PROGRESS_STEPS:
        if key not in completed:
            current = key
            break

    if not current:
        current = "done"

    return {"completed": completed, "current": current}


def render_progress(run: dict[str, Any], is_latest: bool) -> str:
    if not is_latest:
        return ""

    state = progress_state(run)
    current = state["current"]
    completed = state["completed"]
    items = []
    for key, label in PROGRESS_STEPS:
        classes = ["progress-step"]
        if key in completed:
            classes.append("is-complete")
        if key == current:
            classes.append("is-current")
        items.append(f'<li class="{" ".join(classes)}"><span></span><b>{escape(label)}</b></li>')

    if current == "done":
        headline = "Current progress: PR opened"
    else:
        active_label = next((label for key, label in PROGRESS_STEPS if key == current), "Working")
        headline = f"Current progress: {active_label}"

    return f"""
      <div class="progress-box">
        <div class="progress-head">
          <strong>{escape(headline)}</strong>
          <span>Live report reloads when a new stage, push, or PR event arrives.</span>
        </div>
        <ol class="progress-tracker">
          {''.join(items)}
        </ol>
      </div>
    """


def render_stage(stage: dict[str, Any]) -> str:
    artifact = stage["artifact_map"]
    details = []

    if artifact.get("branch"):
        details.append(("Branch", artifact["branch"]))
    if artifact.get("sha"):
        details.append(("Commit", str(artifact["sha"])[:12]))
    if artifact.get("pusher"):
        details.append(("Pushed by", artifact["pusher"]))
    if artifact.get("pr_number"):
        details.append(("PR", f"#{artifact['pr_number']}"))
    if artifact.get("base_repo"):
        details.append(("Base", f"{artifact.get('base_repo')}:{artifact.get('base_branch', '')}".rstrip(":")))

    detail_html = "".join(
        f'<span class="pill"><b>{escape(label)}:</b> {escape(value)}</span>'
        for label, value in details
    )

    links = []
    if stage.get("session_url"):
        links.append(
            f'<a href="{escape(stage["session_url"], quote=True)}" target="_blank" rel="noreferrer">Open Devin session</a>'
        )
    if artifact.get("compare_url"):
        links.append(
            f'<a href="{escape(artifact["compare_url"], quote=True)}" target="_blank" rel="noreferrer">Compare changes</a>'
        )
    if artifact.get("pr_url"):
        links.append(
            f'<a href="{escape(artifact["pr_url"], quote=True)}" target="_blank" rel="noreferrer">Open PR</a>'
        )

    links_html = "".join(f"<span>{link}</span>" for link in links)

    return f"""
      <li class="stage-row">
        <div class="stage-main">
          <div class="stage-head">
            <strong>{escape(stage["display_stage"])}</strong>
            <span class="stage-status">{escape(stage["display_status"])}</span>
            <time>{escape(stage["display_created_at"])}</time>
          </div>
          <p>{escape(stage["display_title"])}</p>
          <div class="stage-details">{detail_html}</div>
          <div class="stage-links">{links_html}</div>
        </div>
      </li>
    """


def render_run(run: dict[str, Any], is_latest: bool = False) -> str:
    latest = run.get("latest_stage") or {}
    stage_html = "".join(render_stage(stage) for stage in run.get("stages", []))
    classes = "run-card run-card-latest" if is_latest else "run-card"
    anchor = ' id="latest-run"' if is_latest else ""
    latest_badge = '<span class="latest-badge">Most recent run</span>' if is_latest else ""
    progress_html = render_progress(run, is_latest)
    return f"""
    <section class="{classes}"{anchor}>
      <div class="run-header">
        <div>
          <p class="eyebrow">Run {run["index"]}{latest_badge}</p>
          <h3>{escape(latest.get("display_stage", "Run"))}</h3>
          <p class="run-subtitle">Started {escape(run.get("started_at"))} · Updated {escape(run.get("updated_at"))}</p>
        </div>
        <div class="run-badges">
          <span>{escape(run.get("status"))}</span>
          <span>{run.get("session_count", 0)} sessions</span>
          <span>{run.get("event_count", 0)} events</span>
        </div>
      </div>
      {progress_html}
      <ol class="stage-list">
        {stage_html or '<li class="empty">No events recorded for this run.</li>'}
      </ol>
    </section>
    """


def render_issue(issue: dict[str, Any], is_latest: bool) -> str:
    pr_url = issue.get("pr_url")
    pr_link = (
        f'<a href="{escape(pr_url, quote=True)}" target="_blank" rel="noreferrer">Open PR</a>'
        if pr_url
        else '<span>No PR recorded</span>'
    )
    issue_url = issue.get("issue_url")
    issue_link = (
        f'<a href="{escape(issue_url, quote=True)}" target="_blank" rel="noreferrer">Open issue</a>'
        if issue_url
        else ""
    )
    runs_html = "".join(render_run(run, is_latest and index == 0) for index, run in enumerate(issue.get("runs", [])))
    classes = "issue-card issue-card-latest" if is_latest else "issue-card"
    return f"""
    <article class="{classes}">
      <div class="issue-header">
        <div>
          <p class="eyebrow">{escape(issue.get("repo"))} #{escape(issue.get("issue_number"))}</p>
          <h2>{escape(issue.get("issue_title"))}</h2>
          <div class="issue-links">{issue_link}{pr_link}</div>
        </div>
        <span class="status">{escape(issue.get("status"))}</span>
      </div>
      <div class="issue-meta">
        <span>{len(issue.get("runs", []))} runs</span>
        <span>Updated {escape(format_dt(issue.get("updated_at")))}</span>
      </div>
      {runs_html or '<section class="empty-state">No runs recorded for this issue.</section>'}
    </article>
    """


def render_report_html(report_data: dict[str, Any]) -> str:
    issues = group_report(report_data)
    raw_stages = [stage for pipeline in report_data.get("pipelines", []) for stage in pipeline.get("stages", [])]
    latest_stage_id = max((int(stage.get("id") or 0) for stage in raw_stages), default=0)
    latest_updated_at = max((str(pipeline.get("updated_at") or "") for pipeline in report_data.get("pipelines", [])), default="")
    total_runs = sum(len(issue["runs"]) for issue in issues)
    total_events = sum(run.get("event_count", 0) for issue in issues for run in issue["runs"])
    total_sessions = sum(run.get("session_count", 0) for issue in issues for run in issue["runs"])
    completed_runs = sum(
        1
        for issue in issues
        for run in issue["runs"]
        if run.get("status") in {"verification", "completed", "pr-open", "pr-opened", "pr-open"}
    )

    issue_cards = "".join(render_issue(issue, index == 0) for index, issue in enumerate(issues))
    cards_html = issue_cards or '<section class="empty-state">No pipeline runs recorded yet.</section>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Devin Design Gate Report</title>
  <style>
    :root {{
      --bg: #f4f6f8;
      --panel: #ffffff;
      --ink: #111827;
      --muted: #667085;
      --line: #d0d7e2;
      --accent: #0f766e;
      --accent-2: #155eef;
      --accent-soft: #e7f6f3;
      --shadow: 0 18px 44px rgba(17, 24, 39, .08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }}
    header {{
      padding: 34px clamp(18px, 5vw, 76px) 22px;
      background: linear-gradient(135deg, #111827, #1f2937);
      color: white;
    }}
    header h1 {{ margin: 0 0 8px; font-size: clamp(30px, 4vw, 46px); letter-spacing: -0.03em; }}
    header p {{ margin: 0; color: #cbd5e1; max-width: 820px; line-height: 1.55; }}
    main {{ padding: 24px clamp(18px, 5vw, 76px) 52px; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .stat, .issue-card, .run-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: var(--shadow);
    }}
    .stat {{ padding: 16px; }}
    .stat span {{ display: block; color: var(--muted); font-size: 13px; font-weight: 650; }}
    .stat strong {{ display: block; margin-top: 7px; font-size: 30px; letter-spacing: -0.02em; }}
    .check-bar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin: 0 0 18px;
      padding: 14px 16px;
      border: 1px solid #f2d98d;
      border-radius: 14px;
      background: #fff8e8;
      color: #5c3a00;
      box-shadow: var(--shadow);
    }}
    .check-bar span {{ color: #7c4a00; }}
    .check-actions {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .check-countdown {{
      display: inline-flex;
      min-width: 76px;
      justify-content: center;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      background: #111827;
      color: #fff;
      font-variant-numeric: tabular-nums;
      font-weight: 800;
    }}
    .check-button {{
      border: 0;
      border-radius: 10px;
      padding: 9px 13px;
      background: var(--accent);
      color: #fff;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    .issue-card {{ padding: 24px; margin-top: 18px; }}
    .issue-header, .run-header {{
      display: flex;
      gap: 18px;
      justify-content: space-between;
      align-items: flex-start;
    }}
    .eyebrow {{ margin: 0 0 6px; color: var(--accent); font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }}
    h2, h3 {{ margin: 0; line-height: 1.25; letter-spacing: -0.02em; }}
    h2 {{ font-size: 24px; }}
    h3 {{ font-size: 18px; }}
    .status, .run-badges span, .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 13px;
      font-weight: 750;
      white-space: nowrap;
    }}
    .status {{ background: var(--accent-soft); color: #115e59; }}
    .issue-links, .stage-links {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 12px; }}
    .issue-meta {{ display: flex; flex-wrap: wrap; gap: 10px 16px; color: var(--muted); margin: 16px 0 4px; font-size: 14px; }}
    a {{ color: var(--accent-2); text-decoration: none; font-weight: 750; }}
    a:hover {{ text-decoration: underline; }}
    .issue-card-latest {{
      border-color: #7dd3fc;
      box-shadow: 0 22px 54px rgba(14, 116, 144, .16);
    }}
    .run-card {{ margin-top: 16px; padding: 18px; box-shadow: none; background: #fbfcfe; }}
    .run-card-latest {{
      border: 2px solid #0ea5e9;
      background: #f0f9ff;
      box-shadow: 0 16px 34px rgba(14, 165, 233, .18);
    }}
    .progress-box {{
      margin-top: 18px;
      padding: 14px;
      border: 1px solid #7dd3fc;
      border-radius: 12px;
      background: #ffffff;
    }}
    .progress-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: #0c4a6e;
      font-size: 14px;
    }}
    .progress-head span {{ color: var(--muted); }}
    .progress-tracker {{
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 8px;
      list-style: none;
      padding: 0;
      margin: 14px 0 0;
    }}
    .progress-step {{
      min-width: 0;
      padding: 10px 8px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8fafc;
      color: #475467;
      text-align: center;
      font-size: 12px;
      font-weight: 800;
    }}
    .progress-step span {{
      display: block;
      width: 12px;
      height: 12px;
      margin: 0 auto 6px;
      border-radius: 999px;
      background: #cbd5e1;
    }}
    .progress-step.is-complete {{
      border-color: #99f6e4;
      background: #ecfdf5;
      color: #115e59;
    }}
    .progress-step.is-complete span {{ background: #0f766e; }}
    .progress-step.is-current {{
      border-color: #0ea5e9;
      background: #e0f2fe;
      color: #075985;
      animation: pulseCurrent 1.1s ease-in-out infinite;
    }}
    .progress-step.is-current span {{
      background: #0ea5e9;
      box-shadow: 0 0 0 0 rgba(14, 165, 233, .55);
      animation: pulseDot 1.1s ease-out infinite;
    }}
    @keyframes pulseCurrent {{
      0%, 100% {{ box-shadow: 0 0 0 rgba(14, 165, 233, 0); }}
      50% {{ box-shadow: 0 0 0 4px rgba(14, 165, 233, .18); }}
    }}
    @keyframes pulseDot {{
      0% {{ box-shadow: 0 0 0 0 rgba(14, 165, 233, .55); }}
      100% {{ box-shadow: 0 0 0 8px rgba(14, 165, 233, 0); }}
    }}
    .latest-badge {{
      display: inline-flex;
      align-items: center;
      margin-left: 10px;
      padding: 4px 8px;
      border-radius: 999px;
      background: #0ea5e9;
      color: #ffffff;
      font-size: 12px;
      font-weight: 850;
      letter-spacing: 0;
      text-transform: none;
      vertical-align: middle;
    }}
    .run-subtitle {{ color: var(--muted); margin: 8px 0 0; font-size: 14px; }}
    .run-badges {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }}
    .run-badges span {{ background: #eef2f7; color: #344054; }}
    .stage-list {{ list-style: none; padding: 0; margin: 18px 0 0; border-top: 1px solid var(--line); }}
    .stage-row {{ padding: 15px 0; border-bottom: 1px solid var(--line); }}
    .stage-head {{ display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: baseline; }}
    .stage-status {{ color: var(--accent); font-size: 13px; font-weight: 800; text-transform: capitalize; }}
    time {{ color: var(--muted); font-size: 13px; }}
    .stage-row p {{ margin: 7px 0 10px; color: #344054; line-height: 1.5; }}
    .stage-details {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .pill {{ border: 1px solid var(--line); background: white; color: #475467; border-radius: 10px; gap: 4px; }}
    .empty, .empty-state {{ color: var(--muted); padding: 20px; }}
    @media (max-width: 820px) {{
      .stats {{ grid-template-columns: 1fr 1fr; }}
      .check-bar, .issue-header, .run-header {{ display: block; }}
      .check-actions, .status, .run-badges {{ margin-top: 12px; }}
      .progress-head {{ display: block; }}
      .progress-tracker {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 520px) {{
      .stats {{ grid-template-columns: 1fr; }}
      .issue-card {{ padding: 16px; }}
      .run-card {{ padding: 14px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Devin Design Gate Report</h1>
    <p>Issue-level pipeline report grouped by issue, split into individual runs, and showing every Devin session/stage separately.</p>
  </header>
  <main>
    <div class="check-bar">
      <div>
        <strong>Live report is active</strong>
        <span>When the countdown reaches zero, the report checks for new stages, pushes, and PRs.</span>
      </div>
      <div class="check-actions">
        <span class="check-countdown" id="check-countdown">30</span>
        <button class="check-button" id="check-now" type="button">Check now</button>
      </div>
    </div>
    <section class="stats">
      <div class="stat"><span>Issues</span><strong>{len(issues)}</strong></div>
      <div class="stat"><span>Total runs</span><strong>{total_runs}</strong></div>
      <div class="stat"><span>Sessions</span><strong>{total_sessions}</strong></div>
      <div class="stat"><span>Events</span><strong>{total_events}</strong></div>
    </section>
    {cards_html}
  </main>
  <script>
    const countdownEl = document.getElementById('check-countdown');
    const checkButton = document.getElementById('check-now');
    const checkUrl = '/check';
    const reportStateUrl = '/report-state';
    const initialReportState = {{
      latestStageId: {latest_stage_id},
      totalEvents: {total_events},
      latestUpdatedAt: '{escape(latest_updated_at)}'
    }};
    const countdownSeconds = 30;
    let remaining = countdownSeconds;
    let checking = false;

    function updateCountdown() {{
      countdownEl.textContent = String(remaining).padStart(2, '0');
    }}

    async function runCheck() {{
      if (checking) return;
      checking = true;
      checkButton.disabled = true;
      try {{
        const response = await fetch(checkUrl, {{ headers: {{ 'Accept': 'application/json' }} }});
        const data = await response.json();
        if (data && data.updated > 0) {{
          window.location.reload();
          return;
        }}
        const stateResponse = await fetch(reportStateUrl, {{ headers: {{ 'Accept': 'application/json' }} }});
        const state = await stateResponse.json();
        if (
          state &&
          (
            Number(state.latest_stage_id || 0) > initialReportState.latestStageId ||
            Number(state.total_events || 0) > initialReportState.totalEvents ||
            String(state.latest_updated_at || '') !== initialReportState.latestUpdatedAt
          )
        ) {{
          window.location.reload();
          return;
        }}
      }} catch (error) {{
        console.warn('check failed', error);
      }} finally {{
        checking = false;
        checkButton.disabled = false;
      }}
      remaining = countdownSeconds;
      updateCountdown();
    }}

    checkButton.addEventListener('click', runCheck);
    updateCountdown();
    window.setInterval(() => {{
      remaining -= 1;
      if (remaining <= 0) {{
        runCheck();
        return;
      }}
      updateCountdown();
    }}, 1000);
  </script>
</body>
</html>"""
