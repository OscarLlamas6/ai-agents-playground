"""El agente completo (modelo guionado + servidor MCP en memoria): lecturas, escritura con aprobación y dos servidores."""

import uuid

from conftest import ScriptedModel, call, say
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as connect

from agent import approve_all, build_agent, reject_all, run_turn
from fake_model import ScriptedFakeModel
from hubspot_client import FakeHubSpot
from runtime import in_memory

NOTE = "Agrega una nota a Ana: 'Enviar la propuesta el lunes'"


def test_read_flow_search_then_notes_then_answer(run):
    async def go():
        async with in_memory() as (tools, _):
            return await run_turn(build_agent(ScriptedFakeModel(), tools), "Resume las notas de Luis.", str(uuid.uuid4()))

    r = run(go())
    names = [c["name"] for m in r["messages"] if m.type == "ai" for c in m.tool_calls]
    assert names == ["search_contacts", "get_contact_notes"] and r["approvals"] == []
    assert "renovará el contrato" in r["messages"][-1].text


def test_write_waits_for_approval_and_saves_when_approved(run):
    async def go():
        crm = FakeHubSpot()
        async with in_memory(crm=crm) as (tools, _):
            r = await run_turn(build_agent(ScriptedFakeModel(), tools), NOTE, str(uuid.uuid4()), approve_all)
        return crm.writes, r

    writes, r = run(go())
    assert len(writes) == 1 and writes[0]["body"] == "Enviar la propuesta el lunes"
    assert r["approvals"][0]["action"] == "add_note" and r["approvals"][0]["decision"] == "approve"


def test_write_is_not_executed_when_rejected(run):
    async def go():
        crm = FakeHubSpot()
        async with in_memory(crm=crm) as (tools, _):
            r = await run_turn(build_agent(ScriptedFakeModel(), tools), NOTE, str(uuid.uuid4()), reject_all)
        return crm.writes, r

    writes, r = run(go())
    assert writes == [] and r["approvals"][0]["decision"] == "reject"


def test_without_the_approval_middleware_the_write_goes_straight_through(run):
    async def go():
        crm = FakeHubSpot()
        async with in_memory(crm=crm) as (tools, _):
            await run_turn(build_agent(ScriptedFakeModel(), tools, approve_writes=False), NOTE, str(uuid.uuid4()))
        return len(crm.writes)

    assert run(go()) == 1


def test_tool_error_reaches_the_model_as_a_message_and_it_can_retry(run):
    model = ScriptedModel(script=[call("get_contact_notes", {"contact_id": "abc"}), call("get_contact_notes", {"contact_id": "101"}, "c2"), say("Listo.")])

    async def go():
        async with in_memory() as (tools, _):
            return await run_turn(build_agent(model, tools), "notas", str(uuid.uuid4()))

    tool_msgs = [m.text for m in run(go())["messages"] if m.type == "tool"]
    assert "validation error" in tool_msgs[0] and "cotización" in tool_msgs[1]


def test_tool_call_limit_stops_a_runaway_loop(run):
    script = [call("search_contacts", {"query": "Ana"}, f"c{i}") for i in range(10)] + [say("fin")]

    async def go():
        crm = FakeHubSpot()
        seen = []
        original = crm.search_contacts
        crm.search_contacts = lambda q, n: seen.append(q) or original(q, n)
        async with in_memory(crm=crm) as (tools, _):
            await run_turn(build_agent(ScriptedModel(script=script), tools), "busca", "t", recursion_limit=60)
        return len(seen)

    assert run(go()) == 8


def test_two_servers_at_once_with_prefixed_tool_names(run):
    clock = FastMCP("reloj")

    @clock.tool()
    def get_current_time(timezone: str) -> str:
        """Hora de una zona (de mentira)."""
        return f"2026-11-19T10:00:00 en {timezone}"

    async def go():
        from mcp_server import build_server
        from settings import Settings

        async with connect(build_server(Settings(), FakeHubSpot())._mcp_server) as s1, connect(clock._mcp_server) as s2:
            tools = await load_mcp_tools(s1, server_name="hubspot", tool_name_prefix=True) + await load_mcp_tools(s2, server_name="time", tool_name_prefix=True)
            names = [t.name for t in tools]
            r = await run_turn(build_agent(ScriptedFakeModel(), tools), "¿Qué hora es en America/Guatemala?", str(uuid.uuid4()))
            return names, r

    names, r = run(go())
    assert names == ["hubspot_search_contacts", "hubspot_get_contact_notes", "hubspot_add_note", "time_get_current_time"]
    assert "America/Guatemala" in r["messages"][-1].text
