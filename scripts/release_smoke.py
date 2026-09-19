"""Exercise an installed AgentGraph server distribution end to end."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any, NoReturn, cast

EXPECTED_DEMO = {"entities": 9, "persons": 3, "edges": 15}
SERVER_PORT = 18765
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"


class SmokeTestError(RuntimeError):
    """Raised when an installed release does not satisfy the smoke contract."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    return parser.parse_args()


def fail(message: str) -> NoReturn:
    raise SmokeTestError(message)


def run_command(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    timeout: float = 60,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        fail(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result.stdout


def run_json(command: list[str], *, env: dict[str, str], cwd: Path) -> Any:
    output = run_command(command, env=env, cwd=cwd)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        fail(f"Expected JSON from {' '.join(command)}, got:\n{output}")
        raise AssertionError from exc


def as_object(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{description} was not an object: {value!r}")
    return cast(dict[str, Any], value)


def as_list(value: Any, description: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{description} was not a list: {value!r}")
    return cast(list[Any], value)


def wait_for_health(server: subprocess.Popen[str], server_log: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.poll() is not None:
            fail(
                f"AgentGraph server exited with code {server.returncode}:\n"
                f"{server_log.read_text(encoding='utf-8')}"
            )
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=1) as response:
                if response.status != 200:
                    time.sleep(0.5)
                    continue
            with urllib.request.urlopen(
                f"{SERVER_URL}/api/capabilities", timeout=1
            ) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    fail(
        "AgentGraph server did not become healthy:\n"
        f"{server_log.read_text(encoding='utf-8')}"
    )


def terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def assert_demo_result(result: Any) -> None:
    result_object = as_object(result, "Demo command result")
    for key, expected in EXPECTED_DEMO.items():
        actual = result_object.get(key)
        if actual != expected:
            fail(f"Demo result field {key!r}: expected {expected}, got {actual!r}")


def check_cli(env: dict[str, str], cwd: Path) -> str:
    version_output = run_command(["agentgraph", "--version"], env=env, cwd=cwd).strip()
    expected_version = installed_version("agentgraph-server")
    if version_output != f"agentgraph {expected_version}":
        fail(f"Unexpected CLI version output: {version_output!r}")

    search_result = as_list(
        run_json(
            ["agentgraph", "search", "webhook", "--limit", "3", "--json"],
            env=env,
            cwd=cwd,
        ),
        "CLI search result",
    )
    if not search_result:
        fail("CLI search returned no results")
    entity = as_object(search_result[0], "CLI search entity")
    if "id" not in entity:
        fail(f"CLI search returned an invalid entity: {entity!r}")
    entity_id = str(entity["id"])
    if "webhook" not in json.dumps(search_result).lower():
        fail("CLI search results did not contain the demo query")

    fetched = as_object(
        run_json(["agentgraph", "get", entity_id, "--json"], env=env, cwd=cwd),
        "CLI get result",
    )
    if fetched.get("id") != entity_id:
        fail(f"CLI get returned the wrong entity: {fetched!r}")

    edges = as_list(
        run_json(["agentgraph", "edges", entity_id, "--json"], env=env, cwd=cwd),
        "CLI edges result",
    )
    if not edges:
        fail(f"CLI edges returned no edges: {edges!r}")

    traversal = as_object(
        run_json(
            ["agentgraph", "traverse", entity_id, "--depth", "1", "--json"],
            env=env,
            cwd=cwd,
        ),
        "CLI traversal result",
    )
    if not traversal.get("nodes"):
        fail(f"CLI traversal returned no nodes: {traversal!r}")

    connectors = as_list(
        run_json(["agentgraph", "list-connectors", "--json"], env=env, cwd=cwd),
        "CLI connector result",
    )
    connector_objects = [as_object(item, "CLI connector item") for item in connectors]
    if not any(item.get("source") == "web" for item in connector_objects):
        fail(f"CLI connector discovery did not include web: {connectors!r}")
    return entity_id


def check_viewer(artifacts: Path) -> None:
    from playwright.sync_api import sync_playwright

    screenshot = artifacts / "viewer-failure.png"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            page.goto(f"{SERVER_URL}/viewer", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => window.__agentGraphViewer?.cy?.nodes().length > 0",
                timeout=30_000,
            )
            if page.title() != "AgentGraph Viewer":
                fail(f"Unexpected viewer title: {page.title()!r}")

            page.locator("#list-tab").click()
            page.locator("#node-list-body tr").first.wait_for(
                state="visible", timeout=10_000
            )
            page.locator("#search-input").fill("webhook")
            page.locator("#search-go-btn").click()
            page.locator("#node-list-body tr").first.wait_for(
                state="visible", timeout=10_000
            )
            page.locator("#node-list-body tr").first.click()
            page.locator("#detail").wait_for(state="visible", timeout=10_000)
            page.wait_for_function(
                "() => document.querySelector('#detail-body')?.innerText"
                ".toLowerCase().includes('webhook')",
                timeout=10_000,
            )
            detail = page.locator("#detail-body").inner_text().lower()
            if "webhook" not in detail:
                fail(f"Viewer detail panel did not contain demo content: {detail!r}")
        except BaseException:
            page.screenshot(path=str(screenshot), full_page=True)
            raise
        finally:
            page.close()
            browser.close()


def mcp_payload(result: Any) -> dict[str, Any]:
    structured = result.structuredContent
    if not isinstance(structured, dict):
        fail(f"MCP tool returned an error: {structured!r}")
    structured_object = cast(dict[str, Any], structured)
    if structured_object.get("status") != "ok":
        fail(f"MCP tool returned an error: {structured!r}")
    data = structured_object.get("data")
    if not isinstance(data, dict):
        fail(f"MCP tool returned invalid data: {structured!r}")
    return cast(dict[str, Any], data)


async def check_mcp(
    env: dict[str, str],
    cwd: Path,
    artifacts: Path,
    entity_id: str,
) -> None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    mcp_log_path = artifacts / "mcp-stderr.log"
    with mcp_log_path.open("w", encoding="utf-8") as mcp_log:
        parameters = StdioServerParameters(
            command="agentgraph",
            args=["mcp-serve"],
            env=env,
            cwd=cwd,
        )
        async with (
            stdio_client(parameters, errlog=mcp_log) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            required = {
                "search_entities_tool",
                "get_entity_tool",
                "traverse_graph_tool",
            }
            missing = required - tool_names
            if missing:
                fail(
                    f"MCP tool discovery missed {sorted(missing)}; "
                    f"found {sorted(tool_names)}"
                )

            search = await session.call_tool(
                "search_entities_tool",
                {"query": "webhook", "limit": 3},
            )
            search_data = mcp_payload(search)
            entities = search_data.get("entities")
            if not isinstance(entities, list) or not entities:
                fail(f"MCP search returned no entities: {search_data!r}")

            entity = await session.call_tool(
                "get_entity_tool",
                {"entity_id": entity_id},
            )
            entity_data = mcp_payload(entity)
            returned_entity = entity_data.get("entity")
            if not isinstance(returned_entity, dict):
                fail(f"MCP get returned the wrong entity: {entity_data!r}")
            returned_entity_object = cast(dict[str, Any], returned_entity)
            if returned_entity_object.get("id") != entity_id:
                fail(f"MCP get returned the wrong entity: {entity_data!r}")

            traversal = await session.call_tool(
                "traverse_graph_tool",
                {"entity_id": entity_id, "max_depth": 1},
            )
            traversal_data = mcp_payload(traversal)
            if not traversal_data.get("nodes"):
                fail(f"MCP traversal returned no nodes: {traversal_data!r}")


def check_skills(env: dict[str, str], project_dir: Path, cwd: Path) -> None:
    user_result = as_object(
        run_json(["agentgraph", "install-skill", "--json"], env=env, cwd=cwd),
        "User skill result",
    )
    if user_result.get("skill") != "agentgraph":
        fail(f"Unexpected user skill result: {user_result!r}")
    user_skill = Path(env["HOME"]) / ".agents" / "skills" / "agentgraph"
    if not (user_skill / "SKILL.md").is_file():
        fail(f"User skill was not installed at {user_skill}")
    references = {path.name for path in (user_skill / "references").glob("*.md")}
    if references != {"commands.md", "data-model.md", "operations.md"}:
        fail(f"Installed skill references were {sorted(references)}")
    claude_link = Path(env["HOME"]) / ".claude" / "skills" / "agentgraph"
    if not claude_link.is_symlink() or claude_link.resolve() != user_skill.resolve():
        fail(f"Claude skill link is incorrect: {claude_link}")

    project_result = as_object(
        run_json(
            [
                "agentgraph",
                "install-skill",
                "slack-auth",
                "--target",
                "project",
                "--no-claude",
                "--json",
            ],
            env=env,
            cwd=project_dir,
        ),
        "Project skill result",
    )
    if project_result.get("skill") != "slack-auth":
        fail(f"Unexpected project skill result: {project_result!r}")
    if not (
        project_dir / ".agents" / "skills" / "slack-auth" / "SKILL.md"
    ).is_file():
        fail("Project slack-auth skill was not installed")


def smoke(wheel: Path, artifacts: Path) -> None:
    if not wheel.is_file():
        fail(f"Wheel does not exist: {wheel}")
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agentgraph-release-smoke-") as temporary:
        root = Path(temporary)
        home = root / "home"
        config = root / "config"
        project = root / "project"
        home.mkdir()
        config.mkdir()
        project.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "AGENTGRAPH_CONFIG_DIR": str(config),
                "AGENTGRAPH_SERVER_PORT": str(SERVER_PORT),
                "AGENTGRAPH_SERVER_UDS_PATH": "none",
                "AGENTGRAPH_BACKEND_SQLITE_VECTOR_MODE": "bm25-only",
                "AGENTGRAPH_QUERY_TRANSPORT": "in-process",
                "PYTHONUNBUFFERED": "1",
            }
        )
        run_command(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                str(wheel),
            ],
            env=env,
            cwd=root,
            timeout=300,
        )
        demo = run_json(["agentgraph", "demo", "add", "--json"], env=env, cwd=root)
        assert_demo_result(demo)
        if not (config / "agentgraph.db").is_file():
            fail("Demo command did not create the configured database")

        server_log_path = artifacts / "server.log"
        with server_log_path.open("w", encoding="utf-8") as server_log:
            server = subprocess.Popen(
                ["agentgraph", "serve"],
                cwd=root,
                env={**env, "AGENTGRAPH_QUERY_TRANSPORT": "server"},
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                wait_for_health(server, server_log_path)
                server_env = {**env, "AGENTGRAPH_QUERY_TRANSPORT": "server"}
                entity_id = check_cli(server_env, root)
                check_viewer(artifacts)
                asyncio.run(check_mcp(server_env, root, artifacts, entity_id))
            finally:
                terminate(server)
        check_skills(env, project, root)


def main() -> int:
    arguments = parse_args()
    try:
        smoke(arguments.wheel.resolve(), arguments.artifacts.resolve())
    except Exception as exc:
        print(f"Release smoke test failed: {exc}", file=sys.stderr)
        return 1
    print("Release smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
