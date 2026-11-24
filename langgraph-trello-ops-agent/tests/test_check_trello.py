"""El comprobador de Trello (solo lectura) compara una cuenta contra los datos que esperan las demos. Sin red: Trello simulado."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from check_trello import check  # noqa: E402

from trello_ops.trello_client import FakeTrello  # noqa: E402


def test_simulated_trello_has_everything_the_demos_expect():
    ok, problems, ids = check(FakeTrello())
    assert problems == [] and set(ids) == {"Operaciones TI", "Migración a la nube"}
    assert any("Actualizar parches del servidor de archivos" in line for line in ok)


def test_missing_board_list_and_card_are_reported():
    trello = FakeTrello()
    del trello.cards["c2"]                      # «Actualizar parches del servidor de archivos»
    del trello.lists["l_ops_review"]            # «En revisión» (y su tarjeta c3 deja de tener lista: se retira)
    del trello.cards["c3"]
    del trello.boards["b_cloud"]                # todo «Migración a la nube»
    for lid in [i for i, x in trello.lists.items() if x["idBoard"] == "b_cloud"]:
        del trello.lists[lid]
    for cid in [i for i, c in trello.cards.items() if c["idList"] not in trello.lists]:
        del trello.cards[cid]
    _, problems, _ = check(trello)
    text = "\n".join(problems)
    assert "Falta el tablero «Migración a la nube»" in text
    assert "Falta la lista «En revisión» en «Operaciones TI»" in text
    assert "Falta la tarjeta «Actualizar parches del servidor de archivos»" in text


def test_a_moved_card_is_only_a_warning():
    trello = FakeTrello()
    trello.move_card("c3", "l_ops_done")        # las demos mueven tarjetas: no es un error
    ok, problems, _ = check(trello)
    assert problems == [] and any("aviso: está en «Hecho»" in line for line in ok)
