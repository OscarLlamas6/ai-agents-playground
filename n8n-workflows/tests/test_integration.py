"""Pruebas contra un n8n real corriendo en localhost:5678 (modo simulado, sin LLM): `task up && task setup && task test:integration`."""
import json
import time
from pathlib import Path

import pytest
import requests

import n8n_client as c

TICKETS = Path(__file__).resolve().parent.parent / "data" / "tickets"
pytestmark = pytest.mark.integration


def ticket(nombre):
    return json.loads((TICKETS / nombre).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True, scope="module")
def n8n_disponible():
    try:
        requests.get(f"{c.BASE}/healthz", timeout=3).raise_for_status()
    except Exception:
        pytest.skip("n8n no está corriendo en localhost:5678")
    c.clear_log()


def test_sin_llave_es_403():
    assert c.send_ticket(ticket("01_critico_vpn.json"), api_key="").status_code == 403
    assert c.send_ticket(ticket("01_critico_vpn.json"), api_key="incorrecta").status_code == 403


def test_ticket_invalido_es_400():
    r = c.send_ticket(ticket("07_invalido.json"))
    assert r.status_code == 400 and r.json()["errores"]


def test_baja_severidad_se_resuelve_sin_aprobacion():
    assert c.send_ticket(ticket("04_bajo_contrasena.json")).status_code == 202
    e = c.wait_for("TCK-1004", "auditoria")
    assert e and e["estado"] == "resuelto_automatico" and e["accion"]["tipo"] == "ninguna"


def test_critico_espera_aprobacion_y_ejecuta_en_dry_run():
    assert c.send_ticket(ticket("01_critico_vpn.json")).status_code == 202
    assert c.approval_url("TCK-1001")
    assert not c.read_log("accion", "TCK-1001"), "la acción no debe correr antes de aprobar"
    assert c.approve("TCK-1001", "Aprobar", "TS") == 200
    e = c.wait_for("TCK-1001", "auditoria")
    assert e and e["estado"] == "ejecutada_dry_run" and e["dry_run"] is True


def test_rechazo_no_ejecuta_la_accion():
    c.send_ticket(ticket("02_alto_correo.json"))
    c.approve("TCK-1002", "Rechazar", "TS", "no ahora")
    e = c.wait_for("TCK-1002", "auditoria")
    assert e and e["estado"] == "rechazada"
    assert not c.read_log("accion", "TCK-1002")


def test_inyeccion_no_cambia_la_lista_blanca():
    c.send_ticket(ticket("05_inyeccion.json"))
    url = c.approval_url("TCK-1005")
    assert url
    aviso = [e for e in c.read_log("notificacion", "TCK-1005") if e.get("url_aprobacion")][-1]
    assert "reiniciar_servicio sobre erp-app-01" in aviso["mensaje"]      # no 'todos los servidores'
    c.approve("TCK-1005", "Rechazar", "TS")


def test_falla_de_cmdb_dispara_el_error_workflow():
    c.send_ticket(ticket("06_falla_cmdb.json"))
    limite = time.time() + 40
    e = None
    while time.time() < limite and not e:
        e = next((x for x in c.read_log("error") if "Triaje" in (x.get("workflow") or "")), None)
        time.sleep(1)
    assert e, "no llegó el aviso del error workflow"
