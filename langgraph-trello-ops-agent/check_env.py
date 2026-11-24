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
    ("TRELLO_MODE", get("TRELLO_MODE", "fake") + ("  · dry-run" if get("TRELLO_DRY_RUN") == "1" else "")),
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
if get("USE_MCP") == "1" and not get("MCP_SERVERS_FILE"):
    warnings.append("USE_MCP=1 necesita MCP_SERVERS_FILE.")
print()
for w in warnings:
    print("  ⚠️ ", w)
if not warnings:
    print("  ✅ Configuración coherente.")
