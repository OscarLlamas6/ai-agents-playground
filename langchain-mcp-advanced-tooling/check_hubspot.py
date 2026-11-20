"""Comprueba tu cuenta de HubSpot ANTES de la clase: token, permisos y rutas, con mensajes que dicen qué corregir.

    uv run python check_hubspot.py                       # solo lecturas (no cambia nada)
    uv run python check_hubspot.py --write 123456789     # además crea UNA nota de prueba en ese contacto
    uv run python check_hubspot.py --version v3          # prueba la ruta heredada si la de fecha falla
    uv run python check_hubspot.py --query example.com --limit 20   # lista los contactos que coincidan (por defecto: «Morales»)

Lee HUBSPOT_TOKEN y HUBSPOT_API_VERSION del .env. Nunca imprime el token ni datos de los contactos más allá de su nombre.
"""

import argparse
import os
import re
import sys

from dotenv import find_dotenv, load_dotenv

from hubspot_client import CRMError, LiveHubSpot

HINTS = {
    "401": "El token no es válido o está mal copiado (¿espacios, comillas, otra cuenta?). Crea una Service Key nueva y vuelve a pegarla en HUBSPOT_TOKEN.",
    "403": "Al token le faltan permisos. En HubSpot: Desarrollo → Claves → Service keys → tu clave → Scopes: añade los de contactos (lectura y escritura) "
           "y, si falla las notas, el que pida el mensaje de HubSpot (lista en https://developers.hubspot.com/scopes).",
    "404": "La ruta no existe para esta versión. Prueba otra: HUBSPOT_API_VERSION=v3 (o --version v3).",
    "429": "HubSpot limitó la tasa de peticiones. Espera unos segundos y repite.",
}


def hint(error: CRMError) -> str:
    code = re.search(r"respondió (\d{3})", str(error))
    return HINTS.get(code.group(1), "") if code else "Revisa tu conexión a internet y que api.hubapi.com sea accesible."


def step(title: str, fn):
    try:
        result = fn()
    except CRMError as e:
        print(f"  ❌ {title}: {e}\n     → {hint(e)}")
        return None
    print(f"  ✅ {title}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba la conexión con HubSpot")
    ap.add_argument("--version", help="versión de la API (por defecto HUBSPOT_API_VERSION o 2026-03; 'v3' = ruta heredada)")
    ap.add_argument("--query", default="Morales", help="texto a buscar en nombre, empresa o correo (por defecto «Morales»); p. ej. example.com para ver todos los de prueba")
    ap.add_argument("--limit", type=int, default=5, help="máximo de contactos a listar (por defecto 5)")
    ap.add_argument("--write", metavar="CONTACT_ID", help="crea una nota de prueba en este contacto (id de HubSpot)")
    args = ap.parse_args()
    load_dotenv(find_dotenv(usecwd=True))

    token = os.getenv("HUBSPOT_TOKEN", "")
    version = args.version or os.getenv("HUBSPOT_API_VERSION", "2026-03")
    if not token:
        print("❌ Falta HUBSPOT_TOKEN en tu .env (ver docs/CUENTA_HUBSPOT.md en el repositorio del curso).")
        return 1
    print(f"HubSpot · versión de API: {version} · token: {len(token)} caracteres (no se muestra)\n")
    crm = LiveHubSpot(token, version)

    print("Lecturas")
    found = step("Buscar contactos (POST …/contacts/search)", lambda: crm.search_contacts(args.query, args.limit))
    contact_id = None
    if found is not None:
        if found:
            contact_id = found[0]["id"]
            print(f"     encontré {len(found)} para «{args.query}»:")
            for c in found:
                p = c["properties"]
                print(f"       - {p.get('firstname', '')} {p.get('lastname', '')} · {p.get('email', '')} · {p.get('company', '')} (id {c['id']})")
            if len(found) == args.limit:
                print(f"     (mostré el máximo: sube --limit si hay más)")
        else:
            print(f"     0 resultados para «{args.query}»: la conexión funciona, pero no hay coincidencias (¿importaste samples/hubspot_contactos.csv?).")
    if contact_id:
        step("Leer un contacto por id (GET …/contacts/{id})", lambda: crm.get_contact(contact_id))
        notes = step("Leer sus notas (POST …/notes/search)", lambda: crm.get_notes(contact_id, 3))
        if notes is not None:
            print(f"     {len(notes)} nota(s)")

    ok_write = True
    if args.write:
        print("\nEscritura")
        ok_write = step(f"Crear una nota de prueba en el contacto {args.write}", lambda: crm.create_note(args.write, "Nota de prueba del curso (dato ficticio).")) is not None
    else:
        print("\n(Escritura no probada: añade --write <id_del_contacto> para crear UNA nota de prueba.)")

    print("\nListo: si todo salió ✅, usa HUBSPOT_MODE=live." if found is not None and ok_write else "\nHay pasos con ❌: corrígelos o usa HUBSPOT_MODE=fake en clase.")
    return 0 if found is not None and ok_write else 1


if __name__ == "__main__":
    sys.exit(main())
