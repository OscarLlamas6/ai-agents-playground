"""Muestra qué configuración leerá el proyecto, SIN imprimir ninguna llave. Úsalo antes de la clase: `task check`.

    uv run python check_env.py

Recuerda: python-dotenv carga UN solo `.env`, el más cercano subiendo desde esta carpeta (no combina varios).
"""

import os

from dotenv import find_dotenv, load_dotenv

env_file = find_dotenv(usecwd=True)
load_dotenv(env_file)
get = os.getenv

llm, emb = get("LLM_PROVIDER", "fake"), get("EMBEDDINGS_PROVIDER", "fake")
rows = [
    (".env que se lee", env_file or "(ninguno: todo simulado)"),
    ("LLM_PROVIDER", f"{llm}" + (f" · modelo {get('LLM_MODEL') or 'por defecto'}" if llm in ("openai", "anthropic") else "")),
    ("EMBEDDINGS_PROVIDER", f"{emb}" + ("" if get("EMBEDDINGS_PROVIDER") else "  (no definido: valor por defecto)")),
    ("OPENAI_API_KEY", "definida" if get("OPENAI_API_KEY") else "NO definida"),
    ("TRELLO_MODE", get("TRELLO_MODE", "fake") + ("  · dry-run (no escribe)" if get("TRELLO_DRY_RUN") == "1" else "")),
    ("TRELLO_API_KEY / TOKEN", ("definidos" if get("TRELLO_API_KEY") and get("TRELLO_TOKEN") else "NO definidos") if get("TRELLO_MODE") == "live" else "(no se usan: Trello simulado)"),
    ("TRELLO_WRITE_BOARDS", (f"{len([b for b in get('TRELLO_WRITE_BOARDS', '').split(',') if b.strip()])} tablero(s)" if get("TRELLO_WRITE_BOARDS") else "vacío: escrituras BLOQUEADAS") if get("TRELLO_MODE") == "live" else "(no se usa: Trello simulado)"),
    ("MAX_STEPS", get("MAX_STEPS", "8")),
    ("OPS_DB (la API)", get("OPS_DB", ".data/ops.sqlite")),
    ("USE_MCP", "1 · " + get("MCP_SERVERS_FILE", "(sin MCP_SERVERS_FILE)") if get("USE_MCP") == "1" else "0 (MCP apagado)"),
]
for name, value in rows:
    print(f"  {name:<22} {value}")

warnings = []
if llm == "openai" and not get("OPENAI_API_KEY"):
    warnings.append("LLM_PROVIDER=openai pero no hay OPENAI_API_KEY: el modelo fallará.")
if llm == "openai" and emb == "fake":
    warnings.append("El modelo es real pero los embeddings son falsos: añade EMBEDDINGS_PROVIDER=openai al .env del proyecto.")
if emb == "openai" and not get("OPENAI_API_KEY"):
    warnings.append("EMBEDDINGS_PROVIDER=openai pero no hay OPENAI_API_KEY: el grafo no podrá construirse.")
if get("TRELLO_MODE") == "live" and not (get("TRELLO_API_KEY") and get("TRELLO_TOKEN")):
    warnings.append("TRELLO_MODE=live necesita TRELLO_API_KEY y TRELLO_TOKEN (manual, sección 3, paso 0).")
if get("TRELLO_MODE") == "live" and not get("TRELLO_WRITE_BOARDS"):
    warnings.append("TRELLO_MODE=live sin TRELLO_WRITE_BOARDS: se puede leer, pero toda escritura se rechaza. Ejecuta `task trello:check` para ver el id.")
if get("TRELLO_MODE") == "live" and get("TRELLO_DRY_RUN") == "1":
    warnings.append("TRELLO_DRY_RUN=1: las escrituras se validan pero NO se hacen. Ponlo en 0 para escribir en Trello de verdad.")
if get("USE_MCP") == "1" and not get("MCP_SERVERS_FILE"):
    warnings.append("USE_MCP=1 necesita MCP_SERVERS_FILE.")
print()
for w in warnings:
    print("  ⚠️ ", w)
if not warnings:
    print("  ✅ Configuración coherente.")
