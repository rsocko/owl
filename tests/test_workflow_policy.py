import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def load_workflow(path: Path) -> dict:
    # PyYAML follows YAML 1.1 and parses "on" as a boolean; normalize it for workflow checks.
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


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
    for condition in (
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
