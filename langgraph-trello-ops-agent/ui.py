"""Interfaz Streamlit sencilla: un chat que consume la API por HTTP (no importa el agente).

    uv run uvicorn api:app --port 8011      # en una terminal
    uv run streamlit run ui.py              # en otra  (API_URL cambia la dirección de la API)
"""

import os

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8011")

st.set_page_config(page_title="Agente de operaciones", page_icon="🗂️")
st.title("🗂️ Agente de operaciones de Trello")


def api(method: str, path: str, **kwargs):
    """Llama a la API; devuelve (json, error). Un 4xx/5xx trae el `detail` de FastAPI."""
    try:
        r = requests.request(method, f"{API_URL}{path}", timeout=60, **kwargs)
    except requests.RequestException:
        return None, f"No se pudo conectar con la API en {API_URL}"
    if r.status_code >= 400:
        return None, f"{r.status_code}: {r.json().get('detail', r.text)}"
    return (r.json() if r.content else None), None


def new_session() -> None:
    st.session_state.update(session_id=None, history=[], pending=None)


if "history" not in st.session_state:
    new_session()

with st.sidebar:
    user_id = st.text_input("Usuario", value="demo")
    if st.button("Nueva sesión"):
        new_session()
    st.caption("Sesión (thread_id)")
    st.code(st.session_state.session_id or "(se crea con el primer mensaje)")
    health, error = api("GET", "/health")
    st.caption(f"API: {health['status']} · modelo {health['llm_provider']} · Trello {health['trello_mode']}" if health else f"API: {error}")


def absorb(reply: dict) -> None:
    """Guarda la respuesta de la API en el historial de la pantalla."""
    st.session_state.session_id = reply["session_id"]
    st.session_state.pending = reply["pending_actions"] or None
    if reply["answer"]:
        usage = reply["usage"]
        detail = f"herramientas: {', '.join(reply['tools_used']) or 'ninguna'} · llamadas al modelo: {usage['model_calls']} · tokens: {usage['input_tokens']}/{usage['output_tokens']}"
        st.session_state.history.append(("assistant", reply["answer"], detail))


for role, text, *detail in st.session_state.history:
    with st.chat_message(role):
        st.write(text)
        if detail:
            st.caption(detail[0])

if st.session_state.pending:                          # el grafo está en pausa: una persona decide
    with st.chat_message("assistant"):
        st.warning("El agente quiere escribir en Trello y espera tu aprobación:")
        for action in st.session_state.pending:
            st.write(f"• {action['summary']}")
        col_yes, col_no = st.columns(2)
        decision = True if col_yes.button("Aprobar", type="primary") else False if col_no.button("Rechazar") else None
    if decision is not None:
        reply, error = api("POST", f"/sessions/{st.session_state.session_id}/approve", json={"approved": decision, "reason": "" if decision else "rechazada en la interfaz"})
        if error:
            st.error(error)
        else:
            st.session_state.history.append(("user", "✅ Aprobada" if decision else "⛔ Rechazada", None))
            absorb(reply)
            st.rerun()
elif prompt := st.chat_input("Escribe tu solicitud (p. ej. «Muestra mis tableros»)"):
    st.session_state.history.append(("user", prompt, None))
    reply, error = api("POST", "/chat", json={"message": prompt, "session_id": st.session_state.session_id, "user_id": user_id})
    if error:
        st.session_state.history.append(("assistant", f"⚠️ {error}", None))
    else:
        absorb(reply)
    st.rerun()
