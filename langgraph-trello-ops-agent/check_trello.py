"""Comprueba tu Trello contra lo que esperan las demos del proyecto. SOLO LEE: no crea ni modifica nada.

    uv run python check_trello.py        # o: task trello:check

Las demos (CLI, Bruno, guion) usan los datos de `seed_data()` de trello_client.py: dos tableros («Operaciones TI» y
«Migración a la nube»), sus listas y cinco tarjetas. Con TRELLO_MODE=live esos datos tienen que existir en tu cuenta de
Trello (con los mismos nombres); este script dice qué falta y qué valor poner en TRELLO_WRITE_BOARDS.
Con TRELLO_MODE=fake (por defecto) se comprueba contra el Trello simulado: sirve de autoprueba y no necesita cuenta.
"""

import os
import re
import sys

from dotenv import find_dotenv, load_dotenv

from trello_ops.trello_client import TrelloError, build_trello, seed_data

HINTS = {
    "401": "La clave o el token no son válidos o caducaron (el token del manual dura 1 día con expiration=1day). Genera uno nuevo.",
    "403": "Al token le faltan permisos: autoriza con scope=read,write.",
    "404": "La ruta o el recurso no existen: revisa el id del tablero.",
    "429": "Trello limitó la tasa de peticiones: espera unos segundos y repite.",
}


def hint(error: TrelloError) -> str:
    code = re.search(r"\b(401|403|404|429)\b", str(error))
    return HINTS.get(code.group(1), "") if code else "Revisa tu conexión a internet y TRELLO_API_KEY / TRELLO_TOKEN."


def check(trello) -> tuple[list[str], list[str], dict[str, str]]:
    """Compara los tableros, listas y tarjetas de `trello` con `seed_data()`, por nombre.

    Devuelve (líneas correctas, problemas, {nombre de tablero: id}). Una tarjeta en otra lista solo es un aviso: las demos mueven tarjetas.
    """
    data = seed_data()
    boards_by_name = {b["name"]: b["id"] for b in trello.list_boards()}
    ok, problems, ids = [], [], {}
    for want_board in data["boards"]:
        name = want_board["name"]
        if name not in boards_by_name:
            problems.append(f"Falta el tablero «{name}»")
            continue
        board_id = boards_by_name[name]
        ids[name] = board_id
        ok.append(f"Tablero «{name}» ({board_id})")
        have_lists = {x["name"] for x in trello.list_lists(board_id)}
        want_lists = [x["name"] for x in data["lists"] if x["idBoard"] == want_board["id"]]
        for lname in want_lists:
            (ok if lname in have_lists else problems).append(f"  Lista «{lname}»" if lname in have_lists else f"Falta la lista «{lname}» en «{name}»")
        cards = {c["name"]: c.get("listName", "") for c in trello.list_cards(board_id)}
        list_names = {x["id"]: x["name"] for x in data["lists"]}
        for want in (c for c in data["cards"] if next(x for x in data["lists"] if x["id"] == c["idList"])["idBoard"] == want_board["id"]):
            expected_list = list_names[want["idList"]]
            if want["name"] not in cards:
                problems.append(f"Falta la tarjeta «{want['name']}» (en la lista «{expected_list}» de «{name}»)")
            elif cards[want["name"]] != expected_list:
                ok.append(f"  Tarjeta «{want['name']}» (aviso: está en «{cards[want['name']]}», se esperaba «{expected_list}»)")
            else:
                ok.append(f"  Tarjeta «{want['name']}» en «{expected_list}»")
    return ok, problems, ids


def main() -> int:
    load_dotenv(find_dotenv(usecwd=True))
    mode = os.getenv("TRELLO_MODE", "fake")
    print(f"TRELLO_MODE = {mode}" + ("  (autoprueba contra el Trello simulado)" if mode == "fake" else ""))
    try:
        ok, problems, ids = check(build_trello())
    except (TrelloError, ValueError) as exc:
        print(f"❌ {exc}\n   → {hint(exc) if isinstance(exc, TrelloError) else 'Revisa TRELLO_MODE.'}")
        return 1
    for line in ok:
        print(f"  ✅ {line.strip()}" if not line.startswith("  ") else f"     ✅ {line.strip()}")
    for line in problems:
        print(f"  ❌ {line}")
    if mode == "live":
        write = {b.strip() for b in os.getenv("TRELLO_WRITE_BOARDS", "").split(",") if b.strip()}
        ops = ids.get("Operaciones TI")
        print()
        if not write:
            print("  ⚠️  TRELLO_WRITE_BOARDS está vacío: las escrituras están BLOQUEADAS." + (f"\n     Añade al .env: TRELLO_WRITE_BOARDS={ops}" if ops else ""))
        elif ops and ops not in write:
            print(f"  ⚠️  «Operaciones TI» ({ops}) no está en TRELLO_WRITE_BOARDS: no se podrá escribir en él.")
        else:
            print(f"  ✅ TRELLO_WRITE_BOARDS permite escribir en {len(write)} tablero(s).")
        if os.getenv("TRELLO_DRY_RUN") == "1":
            print("  ℹ️  TRELLO_DRY_RUN=1: las escrituras se validan pero NO se hacen. Ponlo en 0 para escribir de verdad.")
    print()
    print("  ✅ Tu Trello tiene lo que esperan las demos." if not problems else f"  ❌ Faltan {len(problems)} elemento(s): créalos con los mismos nombres (manual, sección 3, paso 0).")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
