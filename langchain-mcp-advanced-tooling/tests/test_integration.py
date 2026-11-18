"""Configuración y el cliente real de HubSpot (con transporte simulado: sin red)."""

import httpx
import pytest

from hubspot_client import CRMError, LiveHubSpot
from settings import Settings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("HUBSPOT_MODE", "fake")
    monkeypatch.setenv("HUBSPOT_TOKEN", "pat-na1-secreto-1234567890")
    s = Settings.from_env()
    assert s.hubspot_mode == "fake" and s.hubspot_token and "pat-na1" not in repr(s)   # el token no sale en repr
    monkeypatch.setenv("HUBSPOT_MODE", "otro")
    with pytest.raises(ValueError):
        Settings.from_env()


def live(handler, version="2026-03"):
    client = httpx.Client(base_url=LiveHubSpot.BASE, headers={"Authorization": "Bearer pat-test"}, transport=httpx.MockTransport(handler))
    return LiveHubSpot("pat-test", version, client)


def test_live_requests_have_the_documented_shape():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.headers["authorization"], req.read().decode().replace(" ", "")))
        if req.url.path.endswith("/contacts/search"):
            return httpx.Response(200, json={"results": [{"id": "1", "properties": {"firstname": "Ana"}}]})
        if req.url.path.endswith("/notes/search"):
            return httpx.Response(200, json={"results": [{"id": "9", "properties": {"hs_note_body": "hola", "hs_timestamp": "2026-11-01T00:00:00Z"}}]})
        if req.method == "GET":
            return httpx.Response(200, json={"id": "1", "properties": {"firstname": "Ana"}})
        return httpx.Response(201, json={"id": "77"})

    crm = live(handler)
    assert crm.search_contacts("ana", 5)[0]["id"] == "1"
    assert crm.get_contact("1")["properties"]["firstname"] == "Ana"
    assert crm.get_notes("1", 5)[0]["body"] == "hola"
    assert crm.create_note("1", "texto") == {"id": "77"}
    assert [(m, p) for m, p, _, _ in seen] == [
        ("POST", "/crm/objects/2026-03/contacts/search"), ("GET", "/crm/objects/2026-03/contacts/1"),
        ("POST", "/crm/objects/2026-03/notes/search"), ("POST", "/crm/objects/2026-03/notes"),
    ]
    assert all(a == "Bearer pat-test" for _, _, a, _ in seen)
    assert '"associationTypeId":202' in seen[3][3] and '"to":{"id":"1"}' in seen[3][3]


def test_live_legacy_v3_path():
    paths = []
    live(lambda r: paths.append(r.url.path) or httpx.Response(200, json={"results": []}), version="v3").search_contacts("ana", 1)
    assert paths == ["/crm/v3/objects/contacts/search"]


def test_live_429_is_retried_once_and_errors_do_not_include_the_body(monkeypatch):
    monkeypatch.setattr("hubspot_client.time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "1"}) if len(calls) == 1 else httpx.Response(200, json={"results": []})

    assert live(handler).search_contacts("ana", 1) == [] and len(calls) == 2
    with pytest.raises(CRMError) as e:
        live(lambda r: httpx.Response(400, json={"message": "correo juan@real.com inválido"})).search_contacts("ana", 1)
    assert "juan@real.com" not in str(e.value) and "400" in str(e.value)


def test_live_mode_requires_a_token():
    with pytest.raises(CRMError):
        LiveHubSpot("")
