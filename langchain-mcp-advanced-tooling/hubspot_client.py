"""El CRM detrás del servidor MCP: una interfaz mínima y dos implementaciones.

    FakeHubSpot  → CRM simulado en memoria con datos 100 % ficticios (modo `fake`, por defecto, sin cuenta ni red).
    LiveHubSpot  → HubSpot real por HTTPS con un token de Service Key o de app privada (modo `live`).

Operaciones (las mínimas para mostrar LECTURAS y una ESCRITURA):
    search_contacts · get_contact · get_notes (lecturas) · create_note (escritura, asociada a un contacto)
"""

from __future__ import annotations

import itertools
import time
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

# Propiedades del contacto que se piden a HubSpot (lista cerrada: nunca "todas").
CONTACT_PROPERTIES = ["firstname", "lastname", "email", "phone", "company", "jobtitle", "lifecyclestage"]
NOTE_ASSOCIATION_TYPE_CONTACT = 202  # nota → contacto (tipo de asociación definido por HubSpot)


class CRMError(Exception):
    """Error del CRM con un mensaje seguro de mostrar (sin cuerpo de respuesta ni credenciales)."""


class CRM(Protocol):
    def search_contacts(self, query: str, limit: int) -> list[dict[str, Any]]: ...
    def get_contact(self, contact_id: str) -> dict[str, Any]: ...
    def get_notes(self, contact_id: str, limit: int) -> list[dict[str, Any]]: ...
    def create_note(self, contact_id: str, body: str) -> dict[str, Any]: ...


# ──────────────────────────── Simulado ────────────────────────────


def _seed_contacts() -> dict[str, dict[str, Any]]:
    """Cuatro contactos inventados (dominios example.com / example.org, teléfonos de ficción)."""
    rows = [
        ("101", "Ana", "Morales", "ana.morales@example.com", "+502 5555 0101", "Cafetales del Norte", "Gerente de compras", "customer"),
        ("102", "Luis", "Pérez", "luis.perez@example.org", "+502 5555 0102", "Transportes Quetzal", "Jefe de logística", "opportunity"),
        ("103", "Marta", "Solís", "marta.solis@example.com", "+502 5555 0103", "Cafetales del Norte", "Contadora", "lead"),
        ("104", "Diego", "Ruiz", "diego.ruiz@example.net", "+502 5555 0104", "Textiles Atitlán", "Director", "lead"),
    ]
    keys = ["firstname", "lastname", "email", "phone", "company", "jobtitle", "lifecyclestage"]
    return {r[0]: {"id": r[0], "properties": dict(zip(keys, r[1:]))} for r in rows}


def _seed_notes() -> dict[str, list[dict[str, Any]]]:
    return {
        "101": [{"id": "n-1", "body": "Pidió cotización de 200 bolsas. Prefiere contacto por la mañana.", "timestamp": "2026-11-02T15:00:00Z"}],
        "102": [{"id": "n-2", "body": "Llamada de seguimiento: Luis confirma que renovará el contrato en enero.", "timestamp": "2026-11-05T16:30:00Z"}],
        "103": [{"id": "n-3", "body": "Envió los datos de facturación. Pendiente: confirmar NIT.", "timestamp": "2026-11-06T10:10:00Z"}],
        "104": [],
    }


class FakeHubSpot:
    """CRM en memoria. Imita lo justo de HubSpot: búsqueda por texto, notas asociadas y ids que crecen."""

    def __init__(self) -> None:
        self.contacts = _seed_contacts()
        self.notes = _seed_notes()
        self._ids = itertools.count(100)
        self.writes: list[dict[str, Any]] = []  # bitácora de escrituras, para que las pruebas midan efectos

    def search_contacts(self, query: str, limit: int) -> list[dict[str, Any]]:
        q = query.lower()
        found = [c for c in self.contacts.values() if any(q in str(v).lower() for v in c["properties"].values())]
        return [{"id": c["id"], "properties": dict(c["properties"])} for c in found[:limit]]

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        if contact_id not in self.contacts:
            raise CRMError(f"El contacto {contact_id} no existe")
        c = self.contacts[contact_id]
        return {"id": c["id"], "properties": dict(c["properties"])}

    def get_notes(self, contact_id: str, limit: int) -> list[dict[str, Any]]:
        return [dict(n) for n in self.notes.get(contact_id, [])[:limit]]

    def create_note(self, contact_id: str, body: str) -> dict[str, Any]:
        if contact_id not in self.contacts:
            raise CRMError(f"El contacto {contact_id} no existe")
        note = {"id": f"n-{next(self._ids)}", "body": body, "timestamp": datetime.now(timezone.utc).isoformat()}
        self.notes.setdefault(contact_id, []).append(note)
        self.writes.append({"contact_id": contact_id, **note})
        return {"id": note["id"]}


# ──────────────────────────── Real ────────────────────────────


class LiveHubSpot:
    """HubSpot real. Se autentica con `Authorization: Bearer <token>` (Service Key o app privada).

    La versión de la API es configurable: una fecha (`2026-03`, ruta `/crm/objects/2026-03/...`) o `v3` (ruta heredada
    `/crm/v3/objects/...`). NO se ha probado contra una cuenta real: ver el README.
    """

    BASE = "https://api.hubapi.com"

    def __init__(self, token: str, api_version: str = "2026-03", client: httpx.Client | None = None) -> None:
        if not token:
            raise CRMError("Falta HUBSPOT_TOKEN para el modo live")
        self._v = api_version
        self._http = client or httpx.Client(
            base_url=self.BASE, headers={"Authorization": f"Bearer {token}"}, timeout=15.0, follow_redirects=False
        )

    def _path(self, object_type: str) -> str:
        return f"/crm/v3/objects/{object_type}" if self._v == "v3" else f"/crm/objects/{self._v}/{object_type}"

    def _request(self, method: str, path: str, json: dict | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            try:
                r = self._http.request(method, path, json=json)
            except httpx.HTTPError as e:
                raise CRMError(f"No se pudo contactar a HubSpot ({type(e).__name__})") from None
            if r.status_code == 429 and attempt == 1:  # límite de tasa: espera lo que pida HubSpot (máx. 5 s) y reintenta una vez
                time.sleep(min(float(r.headers.get("Retry-After", "1")), 5.0))
                continue
            if r.status_code >= 400:
                # Solo se informa el código: el cuerpo de la respuesta puede traer datos de personas.
                raise CRMError(f"HubSpot respondió {r.status_code} en {method} {path.split('?')[0]}")
            return r.json()
        raise CRMError("HubSpot limitó la tasa de peticiones (429)")

    def search_contacts(self, query: str, limit: int) -> list[dict[str, Any]]:
        data = self._request("POST", f"{self._path('contacts')}/search", {"query": query, "limit": limit, "properties": CONTACT_PROPERTIES})
        return [{"id": r["id"], "properties": r.get("properties", {})} for r in data.get("results", [])]

    def get_contact(self, contact_id: str) -> dict[str, Any]:
        data = self._request("GET", f"{self._path('contacts')}/{contact_id}?properties={','.join(CONTACT_PROPERTIES)}")
        return {"id": data["id"], "properties": data.get("properties", {})}

    def get_notes(self, contact_id: str, limit: int) -> list[dict[str, Any]]:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "associations.contact", "operator": "EQ", "value": contact_id}]}],
            "properties": ["hs_note_body", "hs_timestamp"],
            "sorts": [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}],
            "limit": limit,
        }
        data = self._request("POST", f"{self._path('notes')}/search", body)
        return [
            {"id": r["id"], "body": r.get("properties", {}).get("hs_note_body") or "", "timestamp": r.get("properties", {}).get("hs_timestamp")}
            for r in data.get("results", [])
        ]

    def create_note(self, contact_id: str, body: str) -> dict[str, Any]:
        payload = {
            "properties": {"hs_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "hs_note_body": body},
            "associations": [
                {"to": {"id": contact_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": NOTE_ASSOCIATION_TYPE_CONTACT}]}
            ],
        }
        return {"id": self._request("POST", self._path("notes"), payload)["id"]}
