from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from scripts.release_package import (
    ReleaseProject,
    load_projects,
    parse_release_tag,
    resolve_release,
)

ROOT = Path(__file__).resolve().parents[1]


def _project(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        data = tomllib.load(file)
    return data["project"]  # type: ignore[no-any-return]


def test_release_projects_use_public_names() -> None:
    root_project = _project(ROOT / "pyproject.toml")
    assert root_project["name"] == "agentgraph-server"
    assert root_project["version"]

    connector_projects = [
        _project(path) for path in sorted((ROOT / "packages").glob("*/pyproject.toml"))
    ]
    assert len(connector_projects) == 5
    assert all(project["name"].startswith("agentgraph-connector-") for project in connector_projects)
    assert all(project["version"] for project in connector_projects)
    connector_requirements = {
        requirement.split(">=", maxsplit=1)[0]
        for requirement in root_project["optional-dependencies"]["all"]
    }
    assert connector_requirements == {project["name"] for project in connector_projects}


def test_connectors_declare_server_compatibility() -> None:
    for path in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        project = _project(path)
        minimum = "0.9.0" if project["name"] == "agentgraph-connector-google" else "0.9.1"
        assert f"agentgraph-server>={minimum},<1.0" in project["dependencies"]


def test_rss_connector_declares_web_connector_compatibility() -> None:
    rss_project = _project(ROOT / "packages" / "agentgraph-connector-rss" / "pyproject.toml")

    assert "agentgraph-connector-web>=0.7.1,<0.8" in rss_project["dependencies"]


def test_release_tag_resolves_each_workspace_project() -> None:
    projects = load_projects(ROOT)

    assert set(projects) == {"agentgraph-server", "agentgraph-connector-discord", "agentgraph-connector-google", "agentgraph-connector-rss", "agentgraph-connector-slack", "agentgraph-connector-web"}
    for project in projects.values():
        assert resolve_release(f"{project.name}-v{project.version}", projects) == project


@pytest.mark.parametrize(
    "tag",
    ["v0.5.3", "agentgraph-server-v", "extension-v0.1.5"],
)
def test_parse_release_tag_rejects_invalid_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="Release tags must use"):
        parse_release_tag(tag)


def test_release_tag_rejects_unknown_package() -> None:
    with pytest.raises(ValueError, match="unknown package"):
        resolve_release("agentgraph-connector-missing-v0.5.3", {})


def test_release_tag_rejects_mismatched_version() -> None:
    projects = {
        "agentgraph-server": ReleaseProject(
            name="agentgraph-server", version="0.5.3", path=ROOT / "pyproject.toml"
        )
    }

    with pytest.raises(ValueError, match="does not match"):
        resolve_release("agentgraph-server-v0.5.4", projects)
