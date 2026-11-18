"""Cliente de Trello: simulado y real (con una sesión HTTP falsa: se comprueban rutas, cabeceras, guardas y reintentos)."""

import pytest

from trello_ops.trello_client import FakeTrello, LiveTrello, TrelloError, build_trello


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else {}

    def json(self):
        return self._data


class FakeSession:
    """Registra las peticiones y responde según (método, ruta) con una lista de respuestas."""

    def __init__(self, routes):
        self.routes, self.calls, self.headers = routes, [], {}

    def request(self, method, url, params=None, timeout=None):
        self.calls.append((method, url.replace("https://api.trello.com/1", ""), params, timeout))
        queue = self.routes[(method, self.calls[-1][1])]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def live(routes, **kw):
    return LiveTrello("KEY123", "TOKEN456", session=FakeSession(routes), sleep=lambda s: None, **kw)


CARD = {"id": "c1", "name": "X", "idList": "l1", "idBoard": "b1"}
ROUTES = {("GET", "/cards/c1"): [Resp(data=CARD)], ("GET", "/lists/l1"): [Resp(data={"id": "l1", "name": "Por hacer", "idBoard": "b1"})],
          ("GET", "/lists/l2"): [Resp(data={"id": "l2", "name": "Hecho", "idBoard": "b1"})],
          ("PUT", "/cards/c1"): [Resp(data=CARD)], ("POST", "/cards"): [Resp(data={"id": "n1", "name": "Nueva"})],
          ("POST", "/cards/c1/actions/comments"): [Resp(data={"id": "a1"})]}


def test_fake_trello_seed_and_writes():
    t = FakeTrello()
    assert [b["name"] for b in t.list_boards()] == ["Operaciones TI", "Migración a la nube"]
    t.move_card("c1", "l_ops_doing")
    assert t.get_card("c1")["listName"] == "En curso"
    with pytest.raises(TrelloError):
        t.move_card("c1", "l_cl_doing")          # otro tablero
    with pytest.raises(TrelloError):
        t.create_card("no-existe", "x")


def test_live_auth_goes_in_header_not_in_url():
    t = live({("GET", "/members/me/boards"): [Resp(data=[{"id": "b1", "name": "B"}])]})
    t.list_boards()
    method, path, params, timeout = t.session.calls[0]
    assert path == "/members/me/boards" and timeout == 15
    assert "KEY123" not in str(params) and 'oauth_consumer_key="KEY123"' in t.session.headers["Authorization"]


def test_live_writes_blocked_without_allowed_boards():
    t = live(ROUTES)
    with pytest.raises(TrelloError, match="TRELLO_WRITE_BOARDS"):
        t.create_card("l1", "Nueva")
    assert not [c for c in t.session.calls if c[0] in ("POST", "PUT")]


def test_live_board_outside_allowlist_is_refused():
    t = live(ROUTES, write_boards={"otro"})
    with pytest.raises(TrelloError, match="no está en TRELLO_WRITE_BOARDS"):
        t.add_comment("c1", "hola")


def test_live_write_endpoints_when_allowed():
    t = live(ROUTES, write_boards={"b1"})
    t.create_card("l1", "Nueva", "desc")
    t.move_card("c1", "l2")
    t.add_comment("c1", "hola")
    writes = [c for c in t.session.calls if c[0] in ("POST", "PUT")]
    assert writes == [("POST", "/cards", {"idList": "l1", "name": "Nueva", "desc": "desc"}, 15),
                      ("PUT", "/cards/c1", {"idList": "l2"}, 15),
                      ("POST", "/cards/c1/actions/comments", {"text": "hola"}, 15)]


def test_live_dry_run_validates_but_does_not_write():
    t = live(ROUTES, write_boards={"b1"}, dry_run=True)
    assert t.create_card("l1", "Nueva")["dryRun"] and t.move_card("c1", "l2")["dryRun"] and t.add_comment("c1", "x")["dryRun"]
    assert not [c for c in t.session.calls if c[0] in ("POST", "PUT")]


def test_live_retries_transient_errors_and_hides_credentials():
    t = live({("GET", "/members/me/boards"): [Resp(429), Resp(503), Resp(data=[])]})
    assert t.list_boards() == [] and len(t.session.calls) == 3
    t = live({("GET", "/members/me/boards"): [Resp(401)]})
    with pytest.raises(TrelloError, match="401") as e:
        t.list_boards()
    assert "TOKEN456" not in str(e.value) and "KEY123" not in str(e.value)


def test_live_requires_credentials_and_mode_selection(monkeypatch):
    with pytest.raises(TrelloError, match="TRELLO_API_KEY"):
        LiveTrello("", "")
    assert isinstance(build_trello(), FakeTrello)
    monkeypatch.setenv("TRELLO_MODE", "otro")
    with pytest.raises(ValueError):
        build_trello()
