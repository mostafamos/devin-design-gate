SUPERSET_REPORTS_CONTEXT = """
Superset area: Alerts & Reports.
Relevant architecture:
- Celery Beat schedules report jobs.
- Celery Worker executes scheduled reports.
- Redis/broker can affect async execution.
- Report execution may render chart/dashboard screenshots.
- Notification layer may send email or Slack.
Goal: preserve actionable failure reasons. Do not redesign Reports.
"""


def triage_prompt(repo: str, issue_title: str, issue_body: str) -> str:
    return f"""
Repository: {repo}
Issue: {issue_title}

{SUPERSET_REPORTS_CONTEXT}

TRIAGE ONLY.
Classify impact, suspected files, likely root cause, risk, and test strategy.

Issue body:
{issue_body}
"""


def spec_prompt(repo: str, issue_title: str, triage: str) -> str:
    return f"""
Repository: {repo}
Issue: {issue_title}

{SUPERSET_REPORTS_CONTEXT}

Create a concise technical specification:
problem, non-goals, acceptance criteria, test plan, observability expectations.

Triage:
{triage}
"""


def lld_prompt(repo: str, issue_title: str, spec: str) -> str:
    return f"""
Repository: {repo}
Issue: {issue_title}

Create a low-level design:
files to inspect, functions/classes, smallest code change, edge cases, test commands.

Spec:
{spec}
"""


def implementation_prompt(repo: str, issue_number: int, issue_title: str, spec: str, lld: str) -> str:
    return f"""
Repository: {repo}
Issue #{issue_number}: {issue_title}

Implement only this approved design.
Open a PR to {repo}.
Avoid unrelated refactors.
PR body must include root cause, files changed, tests run, risks.

SPEC:
{spec}

LLD:
{lld}
"""


def verification_prompt(repo: str, issue_title: str, pr_url: str, spec: str, lld: str) -> str:
    return f"""
Repository: {repo}
Issue: {issue_title}
PR: {pr_url}

Verify PR against spec and LLD.
Return verdict, risk, test evidence, and follow-up.

SPEC:
{spec}

LLD:
{lld}
"""
