"""Línea de comandos del agente (Click). Un comando por tarea:

    uv run python cli.py ingest              # indexa el manual en Postgres + pgvector (hay que hacerlo una vez)
    uv run python cli.py ask --trace "¿Cada cuánto vence mi contraseña?"
    uv run python cli.py chat --user ana     # conversación con memoria; el id de sesión se imprime y se puede reutilizar
    uv run python cli.py log                 # bitácora de consultas (tokens) y huecos del manual
    uv run python cli.py serve               # la API HTTP (FastAPI) en http://localhost:8000
"""

import uuid

import click
from dotenv import find_dotenv, load_dotenv

from db import build_database
from retriever import IndexNotReady, Retriever
from service import MANUAL, AgentService


def print_trace(service: AgentService, session_id: str) -> None:
    """Muestra el camino de la última pregunta: 👤 pregunta, 🤖 llamadas a herramientas, 🔧 resultados."""
    messages = service.agent.get_state(service._config(session_id)).values["messages"]
    last = max(i for i, m in enumerate(messages) if m.type == "human")
    for m in messages[last:]:
        if m.type == "human":
            click.echo(f"👤 {m.content}")
        elif m.type == "ai":
            for call in m.tool_calls:
                if call["name"] != "Answer":                  # "Answer" es la salida estructurada, no una herramienta real
                    click.echo(f"🤖 pide {call['name']}({call['args']})")
        elif m.type == "tool" and m.name != "Answer":
            click.echo(f"🔧 {str(m.content)[:300]}")


def show(result: dict) -> None:
    click.echo(f"\n{result['answer']}")
    for c in result["citations"]:
        click.echo(f"  [{c['id']}] {c['section'] or '⚠️ no existe'}")
    for p in result["problems"]:
        click.echo(f"  ⚠️ {p}")
    u = result["usage"]
    click.echo(f"  ({u['model_calls']} llamadas al modelo · {u['input_tokens']} tokens de entrada · {u['output_tokens']} de salida)")


@click.group()
def cli() -> None:
    """RAG agéntico con LangChain, Postgres + pgvector, API y bitácora."""
    load_dotenv(find_dotenv(usecwd=True))


@cli.command()
@click.option("--force", is_flag=True, help="Reindexa aunque el índice ya esté al día.")
def ingest(force: bool) -> None:
    """Ingesta: fragmenta el manual, lo vectoriza y lo guarda en pgvector."""
    db = build_database()
    r = Retriever(MANUAL, db).ingest(force=force)
    db.close()
    if r["reindexed"]:
        click.echo(f"✅ Indexados {r['chunks']} fragmentos ({r['dims']} dimensiones) en Postgres + pgvector.")
    else:
        click.echo(f"✔ El índice ya estaba al día ({r['chunks']} fragmentos). Usa --force para reindexar.")


@cli.command()
@click.argument("question")
@click.option("--trace", is_flag=True, help="Muestra las herramientas que pidió el agente.")
@click.option("--user", default="demo", show_default=True, help="Identificador del usuario.")
@click.option("--session", "session_id", default=None, help="Id de una sesión existente para continuarla.")
def ask(question: str, trace: bool, user: str, session_id: str | None) -> None:
    """Hace una pregunta. Imprime el id de sesión para poder continuarla con --session."""
    service = AgentService.from_env()
    try:
        result = service.chat(question, session_id, user)
    except IndexNotReady as e:
        raise click.ClickException(str(e))
    if trace:
        print_trace(service, result["session_id"])
    show(result)
    click.echo(f"  sesión: {result['session_id']}")
    service.close()


@cli.command()
@click.option("--trace", is_flag=True)
@click.option("--user", default="demo", show_default=True)
@click.option("--session", "session_id", default=None, help="Continúa una sesión existente (el estado vive en Postgres).")
def chat(trace: bool, user: str, session_id: str | None) -> None:
    """Conversación con memoria de corto plazo (y de largo plazo por usuario)."""
    service = AgentService.from_env()
    session_id = session_id or str(uuid.uuid4())
    click.echo(f"sesión: {session_id}   (línea vacía para salir)")
    try:
        while (question := click.prompt("\nTú", default="", show_default=False).strip()):
            result = service.chat(question, session_id, user)
            if trace:
                print_trace(service, session_id)
            show(result)
    except IndexNotReady as e:
        raise click.ClickException(str(e))
    finally:
        service.close()


@cli.command()
@click.option("--limit", default=10, show_default=True)
def log(limit: int) -> None:
    """Bitácora de consultas (tokens, herramientas) y huecos del manual."""
    db = build_database()
    click.echo("Últimas consultas (query_log):")
    for r in db.recent_queries(limit):
        click.echo(f"  #{r.get('id', '-')} {r['question'][:55]!r:<58} in={r['input_tokens']:<6} out={r['output_tokens']:<5} "
                   f"llamadas={r['model_calls']} herramientas={list(r['tools_used'])} found={r['found']}")
    gaps = db.all_gaps()
    click.echo(f"\nHuecos del manual (manual_gaps): {len(gaps)}")
    for g in gaps:
        click.echo(f"  - {g['question']}  ({g['note']})")
    db.close()


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--auto-ingest", is_flag=True, help="Indexa el manual al arrancar si hace falta.")
def serve(host: str, port: int, auto_ingest: bool) -> None:
    """Arranca la API HTTP (FastAPI + uvicorn)."""
    import os

    import uvicorn

    if auto_ingest:
        os.environ["AUTO_INGEST"] = "1"
    uvicorn.run("api:app", host=host, port=port)


if __name__ == "__main__":
    cli()
