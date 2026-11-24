"""MCP opcional: las herramientas de un servidor MCP se suman al grafo y, por defecto, exigen aprobación."""

import json
import sys
from pathlib import Path

import pytest
from conftest import call
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

pytest.importorskip("langchain_mcp_adapters")

from trello_ops.mcp_tools import load_mcp_tools  # noqa: E402
from trello_ops.state import Context  # noqa: E402


@pytest.fixture
def mcp_env(monkeypatch, tmp_path):
    conf = tmp_path / "servers.json"
    conf.write_text(json.dumps({"demo": {"transport": "stdio", "command": sys.executable,
                                         "args": [str(Path(__file__).parent / "mcp_demo_server.py")]}}))
    monkeypatch.setenv("USE_MCP", "1")
    monkeypatch.setenv("MCP_SERVERS_FILE", str(conf))
    monkeypatch.setenv("MCP_READONLY_TOOLS", "status_page")


def test_mcp_is_off_by_default():
    assert load_mcp_tools() == ({}, set())


def test_mcp_tools_default_to_approval_unless_declared_read_only(mcp_env):
    tools, needs_approval = load_mcp_tools()
    assert set(tools) == {"status_page", "notify_team"} and needs_approval == {"notify_team"}


def test_mcp_read_runs_and_mcp_write_waits_for_approval(mcp_env, make_graph):
    tools, needs_approval = load_mcp_tools()
    script = [call("status_page", {"service": "vpn"}, "m1"), call("notify_team", {"message": "VPN ok"}, "m2"), AIMessage("Avisé.")]
    graph, _ = make_graph(script, checkpointer=InMemorySaver(), extra_tools=tools, approval_tools=needs_approval)
    cfg = {"configurable": {"thread_id": "mcp"}}
    paused = graph.invoke({"messages": [{"role": "user", "content": "avisa"}]}, cfg, context=Context("u"))
    assert paused["__interrupt__"][0].value["actions"][0]["tool"] == "notify_team"
    assert any("vpn: operativo" in m.content for m in paused["messages"] if m.type == "tool")   # la lectura ya corrió
    final = graph.invoke(Command(resume=True), cfg, context=Context("u"))
    assert any("aviso enviado: VPN ok" in m.content for m in final["messages"] if m.type == "tool")


# ───────────── servidores externos: variables de entorno, prefijos, comodines y servidores caídos ─────────────


def test_env_placeholders_are_expanded_and_never_leaked(monkeypatch):
    from trello_ops.mcp_tools import expand_env

    monkeypatch.setenv("TOKEN_DE_PRUEBA", "s3creto")
    conn = {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer ${TOKEN_DE_PRUEBA}"}, "args": ["--zona=${ZONA:-GT}"]}
    assert expand_env(conn) == {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer s3creto"}, "args": ["--zona=GT"]}
    monkeypatch.delenv("TOKEN_DE_PRUEBA")
    with pytest.raises(RuntimeError, match="TOKEN_DE_PRUEBA") as err:
        expand_env(conn)
    assert "s3creto" not in str(err.value)


def test_prefix_and_wildcard_readonly(mcp_env, monkeypatch):
    monkeypatch.setenv("MCP_TOOL_PREFIX", "1")
    monkeypatch.setenv("MCP_READONLY_TOOLS", "demo_status*")
    tools, needs_approval = load_mcp_tools()
    assert set(tools) == {"demo_status_page", "demo_notify_team"} and needs_approval == {"demo_notify_team"}


def test_a_server_that_is_down_is_skipped_unless_strict(mcp_env, monkeypatch, capsys):
    conf = json.loads(Path(__import__("os").environ["MCP_SERVERS_FILE"]).read_text())
    conf["caido"] = {"transport": "stdio", "command": "comando-que-no-existe-xyz", "args": []}
    Path(__import__("os").environ["MCP_SERVERS_FILE"]).write_text(json.dumps(conf))
    tools, _ = load_mcp_tools()
    assert set(tools) == {"status_page", "notify_team"}                # los demás servidores siguen disponibles
    assert "«caido» omitido" in capsys.readouterr().err
    monkeypatch.setenv("MCP_STRICT", "1")
    with pytest.raises(RuntimeError, match="caido"):
        load_mcp_tools()


def test_mcp_results_are_plain_text_not_content_blocks(mcp_env):
    from trello_ops.mcp_tools import invoke_tool

    tools, _ = load_mcp_tools()
    assert invoke_tool(tools["status_page"], {"service": "vpn"}) == "vpn: operativo"
