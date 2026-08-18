import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def load_workflow(path: Path) -> dict:
    # PyYAML follows YAML 1.1 and parses "on" as a boolean; normalize it for workflow checks.
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def publication_event_allowed(
    *,
    visibility: str,
    event_name: str,
    conclusion: str = "",
    workflow_event: str = "",
    head_branch: str = "",
    same_repository: bool = False,
    ref: str = "",
) -> bool:
    if visibility != "public":
        return False
    automatic = (
        event_name == "workflow_run"
        and conclusion == "success"
        and workflow_event == "push"
        and head_branch == "main"
        and same_repository
    )
    manual = event_name == "workflow_dispatch" and ref == "refs/heads/main"
    return automatic or manual


def test_actions_are_sha_pinned_and_pull_requests_have_no_top_level_write_permissions() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        source = path.read_text(encoding="utf-8")
        workflow = load_workflow(path)
        assert "pull_request_target" not in workflow["on"]
        for action in re.findall(r"^\s*uses:\s*([^#\s]+)", source, re.MULTILINE):
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action), f"{path}: {action}"
        if "pull_request" in workflow["on"]:
            assert "write" not in workflow.get("permissions", {}).values()


def test_publisher_only_follows_successful_same_repository_main_ci() -> None:
    path = WORKFLOWS / "publish-container.yml"
    source = path.read_text(encoding="utf-8")
    workflow = load_workflow(path)
    assert "pull_request" not in workflow["on"]
    assert "push" not in workflow["on"]
    assert workflow["on"]["workflow_run"] == {"workflows": ["CI"], "types": ["completed"]}
    expected_gate = (
        "github.event.repository.visibility == 'public' && "
        "((github.event_name == 'workflow_run' && "
        "github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.event == 'push' && "
        "github.event.workflow_run.head_branch == 'main' && "
        "github.event.workflow_run.head_repository.id == github.event.repository.id) || "
        "(github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'))"
    )
    actual_gate = re.sub(r"\s+", " ", workflow["jobs"]["prepare"]["if"]).strip()
    assert actual_gate == expected_gate
    assert workflow["jobs"]["prepare"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["publish"]["needs"] == ["prepare"]
    for condition in (
        "github.event.repository.visibility == 'public'",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.id == github.event.repository.id",
        "github.ref == 'refs/heads/main'",
    ):
        assert condition in source
    assert "ghcr.io/rsocko/owl" in source
    assert "--attest type=sbom" in source
    assert "actions/attest@" in source
    assert "sha_tag=sha-${SOURCE_SHA}" in source
    assert "^[0-9a-f]{40}$" in source
    assert 'git cat-file -e "${source_sha}^{commit}"' in source
    assert 'git merge-base --is-ancestor "${source_sha}" "${trusted_main_sha}"' in source


@pytest.mark.parametrize("event_name", ["workflow_run", "workflow_dispatch"])
def test_private_repository_events_cannot_enter_publisher_prepare(event_name: str) -> None:
    assert not publication_event_allowed(
        visibility="private",
        event_name=event_name,
        conclusion="success",
        workflow_event="push",
        head_branch="main",
        same_repository=True,
        ref="refs/heads/main",
    )


def test_public_same_repository_main_push_can_enter_publisher_prepare() -> None:
    assert publication_event_allowed(
        visibility="public",
        event_name="workflow_run",
        conclusion="success",
        workflow_event="push",
        head_branch="main",
        same_repository=True,
    )


def test_public_main_manual_dispatch_can_reach_exact_ancestor_verification() -> None:
    assert publication_event_allowed(
        visibility="public",
        event_name="workflow_dispatch",
        ref="refs/heads/main",
    )


def test_codeql_waits_for_public_cutover() -> None:
    workflow = load_workflow(WORKFLOWS / "codeql.yml")
    assert workflow["jobs"]["analyze"]["if"] == "github.event.repository.visibility == 'public'"
