"""Pruebas SIN red: la estructura de los workflows exportados y de los datos de ejemplo.

No ejecutan n8n; verifican lo que se puede romper al editar a mano o re-exportar un JSON:
referencias entre nodos, sub-workflows, credenciales vacías, autenticación del webhook, límites y datos ficticios.
"""
import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((RAIZ / "workflows").glob("*.json"))}
TICKETS = sorted((RAIZ / "data" / "tickets").glob("*.json"))
IDS = {w["id"]: w for w in WORKFLOWS.values()}


def nodos(w):
    return {n["name"]: n for n in w["nodes"]}


def test_hay_siete_workflows():
    assert len(WORKFLOWS) == 7


@pytest.mark.parametrize("nombre", WORKFLOWS)
def test_estructura_basica(nombre):
    w = WORKFLOWS[nombre]
    n = nodos(w)
    assert len(n) == len(w["nodes"]), "nombres de nodo duplicados"
    assert len({x["id"] for x in w["nodes"]}) == len(w["nodes"]), "ids de nodo duplicados"
    assert re.fullmatch(r"[A-Za-z0-9]{16,}", w["id"])
    for origen, salidas in w["connections"].items():
        assert origen in n, f"conexión desde un nodo inexistente: {origen}"
        for tipo, ramas in salidas.items():
            for rama in ramas:
                for destino in rama:
                    assert destino["node"] in n, f"{origen} → {destino['node']} no existe"


@pytest.mark.parametrize("nombre", WORKFLOWS)
def test_sin_secretos_en_el_json(nombre):
    w = WORKFLOWS[nombre]
    texto = json.dumps(w)
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}|sk-ant-|Bearer\s+[A-Za-z0-9._-]{20,}|AKIA[0-9A-Z]{16}", texto)
    for n in w["nodes"]:
        for tipo, ref in n.get("credentials", {}).items():
            assert set(ref) == {"id", "name"}, f"{n['name']}: una credencial solo debe llevar id y nombre"
            assert ref["id"].startswith("cred-s12-")


@pytest.mark.parametrize("nombre", WORKFLOWS)
def test_todo_esta_inactivo_en_el_json(nombre):
    assert WORKFLOWS[nombre]["active"] is False      # se publica con la CLI (scripts/import_workflows.sh)


def test_referencias_a_sub_workflows_y_error_workflow():
    for w in WORKFLOWS.values():
        for n in w["nodes"]:
            wid = n["parameters"].get("workflowId")
            if isinstance(wid, dict):
                assert wid["value"] in IDS, f"{w['name']}/{n['name']} apunta a un workflow que no existe"
        ew = w["settings"].get("errorWorkflow")
        if ew:
            assert ew in IDS


def test_triaje_webhook_autenticado_y_responde_pronto():
    t = nodos(IDS["S12TriajeTickets01"])
    wh = t["Webhook tickets"]
    assert wh["parameters"]["authentication"] == "headerAuth"
    assert wh["parameters"]["httpMethod"] == "POST"
    assert wh["parameters"]["responseMode"] == "responseNode"
    assert IDS["S12TriajeTickets01"]["settings"]["errorWorkflow"] == "S12ErrorHandler001"


def test_triaje_por_defecto_es_simulado_y_dry_run():
    cfg = nodos(IDS["S12TriajeTickets01"])["Config"]["parameters"]["assignments"]["assignments"]
    valores = {a["name"]: a["value"] for a in cfg}
    assert valores["modo"] == "simulado"
    assert valores["dry_run"] is True


def test_aprobacion_humana_con_caducidad():
    p = nodos(IDS["S12TriajeTickets01"])["Aprobación humana"]["parameters"]
    assert p["resume"] == "form" and p["limitWaitTime"] is True
    opciones = [o["option"] for o in p["formFields"]["values"][0]["fieldOptions"]["values"]]
    assert opciones == ["Aprobar", "Rechazar"]


def test_la_accion_pasa_por_la_aprobacion_antes_de_ejecutarse():
    w = IDS["S12TriajeTickets01"]
    c = w["connections"]
    assert c["Aprobación humana"]["main"][0][0]["node"] == "Resolver decisión"
    assert c["¿Aprobada?"]["main"][0][0]["node"] == "Ejecutar acción (dry-run)"
    # nada llega a la acción sin pasar por la aprobación
    entran = [o for o, s in c.items() for ramas in s.get("main", []) for d in ramas if d["node"] == "Ejecutar acción (dry-run)"]
    assert entran == ["¿Aprobada?"]


def test_el_agente_no_tiene_herramientas_de_escritura():
    w = IDS["S12TriajeTickets01"]
    herramientas = [o for o, s in w["connections"].items() if "ai_tool" in s]
    assert set(herramientas) == {"consultar_estado_servicio", "buscar_runbook", "mcp_s12 (opcional)"}


def test_lista_blanca_de_acciones_coincide_en_esquema_y_codigo():
    t = nodos(IDS["S12TriajeTickets01"])
    esquema = json.loads(t["Esquema de la propuesta"]["parameters"]["inputSchema"])
    en_esquema = set(esquema["properties"]["accion"]["properties"]["tipo"]["enum"])
    codigo = t["Normalizar propuesta"]["parameters"]["jsCode"]
    en_codigo = set(re.findall(r"'([a-z_]+)'", re.search(r"PERMITIDAS = \[(.*?)\]", codigo).group(1)))
    assert en_esquema == en_codigo


def test_llamadas_http_con_reintentos():
    for w in WORKFLOWS.values():
        for n in w["nodes"]:
            if n["type"] == "n8n-nodes-base.httpRequest":
                assert n.get("retryOnFail") is True and n.get("maxTries", 0) >= 3, f"{w['name']}/{n['name']}"
                assert n["parameters"]["options"].get("timeout")


def test_mcp_servidor_con_autenticacion():
    p = nodos(IDS["S12ServidorMcp00001"])["Servidor MCP"]["parameters"]
    assert p["authentication"] == "headerAuth"


@pytest.mark.parametrize("ruta", TICKETS, ids=lambda p: p.name)
def test_tickets_de_ejemplo_ficticios(ruta):
    t = json.loads(ruta.read_text(encoding="utf-8"))
    texto = json.dumps(t)
    assert "@" not in texto, "no uses correos, ni siquiera ficticios con aspecto real"
    assert not re.search(r"\d{4}[ -]?\d{4}", texto.replace("TCK-", "")) or ruta.name.startswith("07")
    if not ruta.name.startswith("07"):
        assert re.fullmatch(r"TCK-[A-Z0-9-]{1,20}", t["ticket_id"])
        assert len(t["descripcion"]) >= 10
        assert t["solicitante"].startswith("usuario.ficticio")


def test_hay_un_ticket_por_cada_ruta_del_flujo():
    nombres = [p.name for p in TICKETS]
    for esperado in ("critico", "alto", "media", "bajo", "inyeccion", "falla_cmdb", "invalido"):
        assert any(esperado in n for n in nombres), esperado
