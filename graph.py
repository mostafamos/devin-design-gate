import os
from typing import Optional, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from clients import DevinClient, GitHubClient
from store import Store
from prompts import (
    triage_prompt,
    spec_prompt,
    lld_prompt,
    implementation_prompt,
    verification_prompt,
)

load_dotenv()


class PipelineState(TypedDict, total=False):
    repo: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_url: str
    pipeline_id: str
    triage: str
    spec: str
    lld: str
    pr_url: str
    verification: str


store = Store()
devin = DevinClient()
github = GitHubClient()


def _artifact(result: dict) -> str:
    return result.get("output") or str(result)


def _session_id(result: dict) -> str:
    return result.get("id") or result.get("session_id") or ""


def _devin_push_token() -> str:
    return os.getenv("LET-DEVIN-PUSH") or os.getenv("LET_DEVIN_PUSH") or ""


def triage_node(state: PipelineState) -> PipelineState:
    result = devin.create_session("triage", triage_prompt(state["repo"], state["issue_title"], state["issue_body"]))
    artifact = _artifact(result)
    session_id = _session_id(result)
    store.add_stage(state["pipeline_id"], "triage", "completed", session_id, artifact)
    github.comment_issue(state["repo"], state["issue_number"], f"Design-gate stage completed: triage\n\nSession: `{session_id}`")
    return {"triage": artifact}


def spec_node(state: PipelineState) -> PipelineState:
    result = devin.create_session("spec", spec_prompt(state["repo"], state["issue_title"], state["triage"]))
    artifact = _artifact(result)
    session_id = _session_id(result)
    store.add_stage(state["pipeline_id"], "spec", "completed", session_id, artifact)
    github.comment_issue(state["repo"], state["issue_number"], f"Design-gate stage completed: spec\n\nSession: `{session_id}`")
    return {"spec": artifact}


def lld_node(state: PipelineState) -> PipelineState:
    result = devin.create_session("lld", lld_prompt(state["repo"], state["issue_title"], state["spec"]))
    artifact = _artifact(result)
    session_id = _session_id(result)
    store.add_stage(state["pipeline_id"], "lld", "completed", session_id, artifact)
    github.comment_issue(state["repo"], state["issue_number"], f"Design-gate stage completed: LLD\n\nSession: `{session_id}`")
    return {"lld": artifact}


def implementation_node(state: PipelineState) -> PipelineState:
    result = devin.create_session(
        "implementation",
        implementation_prompt(
            state["repo"],
            state["issue_number"],
            state["issue_title"],
            state["spec"],
            state["lld"],
            push_token=_devin_push_token(),
        ),
    )
    artifact = _artifact(result)
    mock_pr = f"https://github.com/{state['repo']}/pull/mock-{state['issue_number']}"
    pr_url = result.get("pr_url") or mock_pr
    session_id = _session_id(result)
    store.add_stage(state["pipeline_id"], "implementation", "completed", session_id, artifact)
    store.set_pr(state["pipeline_id"], pr_url)
    github.comment_issue(state["repo"], state["issue_number"], f"Implementation stage completed\n\nPR: {pr_url}\nSession: `{session_id}`")
    return {"pr_url": pr_url}


def verification_node(state: PipelineState) -> PipelineState:
    result = devin.create_session(
        "verification",
        verification_prompt(state["repo"], state["issue_title"], state["pr_url"], state["spec"], state["lld"]),
    )
    artifact = _artifact(result)
    session_id = _session_id(result)
    store.add_stage(state["pipeline_id"], "verification", "completed", session_id, artifact)
    github.comment_issue(state["repo"], state["issue_number"], f"Verification completed\n\nSession: `{session_id}`")
    return {"verification": artifact}


def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("triage_stage", triage_node)
    graph.add_node("spec_stage", spec_node)
    graph.add_node("lld_stage", lld_node)
    graph.add_node("implementation_stage", implementation_node)
    graph.add_node("verification_stage", verification_node)

    graph.set_entry_point("triage_stage")
    graph.add_edge("triage_stage", "spec_stage")
    graph.add_edge("spec_stage", "lld_stage")
    graph.add_edge("lld_stage", "implementation_stage")
    graph.add_edge("implementation_stage", "verification_stage")
    graph.add_edge("verification_stage", END)
    return graph.compile()


compiled_graph = build_graph()


def run_pipeline(repo: str, issue_number: int, issue_title: str, issue_body: str, issue_url: str = ""):
    pipeline_id = store.create_pipeline(repo, issue_number, issue_title)
    github.comment_issue(repo, issue_number, "Design-gated Devin remediation pipeline started.")
    final_state = compiled_graph.invoke({
        "repo": repo,
        "issue_number": issue_number,
        "issue_title": issue_title,
        "issue_body": issue_body,
        "issue_url": issue_url,
        "pipeline_id": pipeline_id,
    })
    return {"pipeline_id": pipeline_id, "status": "completed", "final_state": final_state}
