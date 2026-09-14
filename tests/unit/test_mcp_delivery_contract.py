"""MCP 1.0 打包和 CI/CD 的静态安全契约。"""

from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def test_mcp_spec_is_onedir_and_bundles_pinned_codex_runtime() -> None:
    spec = (PROJECT / "packaging" / "davinci-gw-mcp.spec").read_text(encoding="utf-8")
    assert "exclude_binaries=True" in spec
    assert "COLLECT(" in spec
    assert 'collect_data_files("codex_cli_bin")' in spec
    assert "DAVINCI_GW_BUILD_INFO" in spec


def test_pr_workflow_cannot_publish_or_use_privileged_pr_trigger() -> None:
    workflow = (PROJECT / ".github" / "workflows" / "mcp-pr.yml").read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "pull_request_target" not in workflow
    assert "contents: read" in workflow
    assert "gh release" not in workflow
    assert "upload-artifact" in workflow
    assert "test_mcp_executable.py" in workflow


def test_release_workflow_is_manual_and_only_packages_mcp() -> None:
    workflow = (PROJECT / ".github" / "workflows" / "mcp-release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "refs/heads/main" in workflow
    assert "gh release create" in workflow
    assert "davinci-gw-mcp-${{ inputs.version }}-win-x64.zip" in workflow
    assert "davinci-gw-gui" not in workflow
