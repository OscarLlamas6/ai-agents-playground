"""Interfaz Streamlit sencilla: un chat que consume la API por HTTP (no importa el agente).

    uv run streamlit run ui.py          # con la API corriendo en API_URL (por defecto http://127.0.0.1:8001)
"""

import os

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://127.0.0.1:8001")

st.set_page_config(page_title="Agente MCP de HubSpot", page_icon="💬")
st.title("💬 Agente MCP de HubSpot")


def api(method: str, path: str, **kwargs):
    """Una llamada a la API. Devuelve (JSON o None, mensaje de error o None)."""
    try:
        r = httpx.request(method, f"{API_URL}{path}", timeout=60, **kwargs)
    except httpx.HTTPError as e:
        return None, f"No se pudo contactar a la API en {API_URL} ({type(e).__name__})"
    if r.status_code >= 400:
        return None, f"La API respondió {r.status_code}: {r.json().get('detail', r.text)}"
    return (r.json() if r.content else None), None


ss = st.session_state
ss.setdefault("session_id", None)
ss.setdefault("messages", [])      # [{"role", "content", "meta"}]
ss.setdefault("pending", None)     # la acción que espera aprobación, si la hay


def add_reply(data: dict) -> None:
    """Guarda en pantalla la respuesta de /chat o /approve."""
    ss.session_id = data["session_id"]
    ss.pending = data["pending_action"] if data["status"] == "awaiting_approval" else None
    u = data["usage"]
    meta = f"🔧 {', '.join(data['tools_used']) or 'sin herramientas'} · 🔢 {u['input_tokens']} entrada / {u['output_tokens']} salida · {u['model_calls']} llamadas al modelo"
    if data["answer"]:
        ss.messages.append({"role": "assistant", "content": data["answer"], "meta": meta})


# ── Barra lateral: usuario y sesión ──
with st.sidebar:
    user_id = st.text_input("user_id", value="demo")
    if st.button("Nueva sesión"):
        ss.session_id, ss.messages, ss.pending = None, [], None
        st.rerun()
    st.caption("session_id (= thread_id del agente)")
    st.code(ss.session_id or "(se crea con el primer mensaje)")
    previous, _ = api("GET", "/sessions", params={"user_id": user_id})
    if previous:
        chosen = st.selectbox("Retomar una sesión", [""] + [s["session_id"] for s in previous])
        if chosen and chosen != ss.session_id:
            history, error = api("GET", f"/sessions/{chosen}")
            if error:
                st.error(error)
            else:
                ss.session_id = chosen
                ss.messages = [{"role": "user" if m["role"] == "human" else "assistant", "content": m["content"], "meta": None}
                               for m in history["messages"] if m["role"] in ("human", "ai") and m["content"]]
                ss.pending = history["pending_action"]
                st.rerun()
    health, _ = api("GET", "/health")
    st.caption(f"API: {health['status']} · modelo {health['model_provider']} · HubSpot {health['hubspot_mode']}" if health else "API: sin conexión")

# ── Conversación ──
for m in ss.messages:
    with st.chat_message(m["role"]):
        st.write(m["content"])
        if m["meta"]:
            st.caption(m["meta"])

# ── Aprobación humana pendiente ──
if ss.pending:
    with st.chat_message("assistant"):
        st.warning(f"El agente quiere ejecutar **{ss.pending['tool']}** (escribe en el CRM)")
        st.json(ss.pending["args"])
        col1, col2 = st.columns(2)
        decision = None
        if col1.button("✅ Aprobar", key="approve"):
            decision = True
        if col2.button("⛔ Rechazar", key="reject"):
            decision = False
        if decision is not None:
            data, error = api("POST", f"/sessions/{ss.session_id}/approve", json={"approved": decision})
            if error:
                st.error(error)
            else:
                add_reply(data)
                st.rerun()

# ── Entrada ──
if prompt := st.chat_input("Escribe tu mensaje (p. ej. 'Resume las notas de Luis.')", disabled=bool(ss.pending)):
    ss.messages.append({"role": "user", "content": prompt, "meta": None})
    data, error = api("POST", "/chat", json={"message": prompt, "session_id": ss.session_id, "user_id": user_id})
    if error:
        ss.messages.append({"role": "assistant", "content": f"⚠️ {error}", "meta": None})
    else:
        add_reply(data)
    st.rerun()
