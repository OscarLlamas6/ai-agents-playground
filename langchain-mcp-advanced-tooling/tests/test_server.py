"""El servidor MCP probado como lo vería un cliente: en memoria, con el protocolo real."""

import asyncio
import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session as connect

from hubspot_client import FakeHubSpot
from mcp_server import build_server
from settings import Settings


def make(body, crm=None):
    crm = crm or FakeHubSpot()
    server = build_server(Settings(), crm)

    async def go():
        async with connect(server._mcp_server) as session:
            return await body(session, crm)

    return asyncio.run(go())


def data(result):
    return json.loads(result.content[0].text)


def test_lists_three_tools_with_descriptive_metadata():
    async def body(s, crm):
        return {t.name: t for t in (await s.list_tools()).tools}

    tools = make(body)
    assert set(tools) == {"search_contacts", "get_contact_notes", "add_note"}
    assert tools["search_contacts"].annotations.readOnlyHint is True
    assert tools["add_note"].annotations.readOnlyHint is False
    assert tools["add_note"].inputSchema["required"] == ["contact_id", "body"]
    assert "pattern" in tools["add_note"].inputSchema["properties"]["contact_id"]   # el tipo ES el esquema


def test_search_returns_contacts():
    async def body(s, crm):
        return data(await s.call_tool("search_contacts", {"query": "Cafetales"}))

    out = make(body)
    assert out["count"] == 2 and {c["name"] for c in out["contacts"]} == {"Ana Morales", "Marta Solís"}


def test_get_notes():
    async def body(s, crm):
        return data(await s.call_tool("get_contact_notes", {"contact_id": "101"}))

    assert "cotización" in make(body)["notes"][0]["body"]


def test_add_note_writes_to_the_crm():
    async def body(s, crm):
        r = data(await s.call_tool("add_note", {"contact_id": "101", "body": "Enviar propuesta el lunes"}))
        return r, crm.writes

    r, writes = make(body)
    assert r["created"] and len(writes) == 1 and writes[0]["contact_id"] == "101"


def test_each_add_note_call_creates_a_new_note():
    async def body(s, crm):
        for _ in range(2):
            await s.call_tool("add_note", {"contact_id": "101", "body": "Llamar el lunes"})
        return len(crm.writes)

    assert make(body) == 2   # la herramienta NO es idempotente: por eso su anotación lo declara


@pytest.mark.parametrize("tool,args", [
    ("search_contacts", {"query": "x"}),                                       # demasiado corto
    ("search_contacts", {"query": "ana", "limit": 500}),                       # fuera de rango
    ("get_contact_notes", {"contact_id": "abc"}),                              # no es numérico
    ("add_note", {"contact_id": "101", "body": "x" * 5000}),                   # demasiado largo
    ("add_note", {"contact_id": "101"}),                                       # falta un argumento
])
def test_invalid_arguments_come_back_as_a_tool_error_the_model_can_read(tool, args):
    async def body(s, crm):
        r = await s.call_tool(tool, args)
        return r.isError, r.content[0].text, crm.writes

    is_error, text, writes = make(body)
    assert is_error and "validation error" in text and writes == []


def test_unknown_contact_is_a_tool_error():
    async def body(s, crm):
        r = await s.call_tool("add_note", {"contact_id": "999", "body": "Hola mundo"})
        return r.isError, r.content[0].text

    is_error, text = make(body)
    assert is_error and "no existe" in text


def test_resource_template_and_prompt():
    async def body(s, crm):
        templates = (await s.list_resource_templates()).resourceTemplates
        card = (await s.read_resource("hubspot://contact/102")).contents[0].text
        prompts = (await s.list_prompts()).prompts
        got = await s.get_prompt("prepare_call", {"contact_id": "102"})
        return templates, card, prompts, got

    templates, card, prompts, got = make(body)
    assert templates[0].uriTemplate == "hubspot://contact/{contact_id}"
    assert "Luis Pérez" in card and "Transportes Quetzal" in card
    assert prompts[0].name == "prepare_call" and "102" in got.messages[0].content.text


# ── check_hubspot.py: las pistas dicen qué corregir (sin red) ──
def test_check_hubspot_hints_map_status_codes_to_fixes():
    import check_hubspot
    from hubspot_client import CRMError

    assert "token" in check_hubspot.hint(CRMError("HubSpot respondió 401 en POST /x")).lower()
    assert "permisos" in check_hubspot.hint(CRMError("HubSpot respondió 403 en POST /x"))
    assert "v3" in check_hubspot.hint(CRMError("HubSpot respondió 404 en POST /x"))
    assert "internet" in check_hubspot.hint(CRMError("No se pudo contactar a HubSpot (ConnectError)"))
