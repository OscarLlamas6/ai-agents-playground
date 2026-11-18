"""Interfaz sencilla en Streamlit. NO importa el agente: es un cliente más de la API (como Bruno o curl).

    uv run streamlit run ui.py                 # con la API en http://localhost:8000 (o API_URL=...)
"""

import os

import httpx
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Mesa de ayuda · agente RAG", page_icon="💬")
st.title("💬 Mesa de ayuda de Nimbus Soft")
st.caption("Un agente con RAG (Postgres + pgvector), tickets, equipos y memoria. Esta pantalla solo habla con la API.")

# El estado de la interfaz: el id de sesión y los mensajes ya mostrados. La conversación REAL vive en el servidor.
if "session_id" not in st.session_state:
    st.session_state.session_id = None
    st.session_state.messages = []

with st.sidebar:
    st.header("Sesión")
    user_id = st.text_input("Usuario", value="demo")
    st.code(st.session_state.session_id or "(sesión nueva: el servidor asignará un id)")
    if st.button("🆕 Nueva sesión"):
        st.session_state.session_id, st.session_state.messages = None, []
        st.rerun()
    existing = st.text_input("Continuar una sesión por id")
    if existing and st.button("Continuar"):
        r = httpx.get(f"{API_URL}/sessions/{existing}", timeout=30)
        if r.status_code == 200:
            st.session_state.session_id = existing
            st.session_state.messages = [{"role": m["role"], "content": m["content"]} for m in r.json()
                                         if m["role"] in ("user", "assistant") and m.get("content")]
            st.rerun()
        else:
            st.error(f"No existe esa sesión ({r.status_code}).")

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("meta"):
            st.caption(m["meta"])

if prompt := st.chat_input("Escribe tu pregunta…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            r = httpx.post(f"{API_URL}/chat", json={"message": prompt, "session_id": st.session_state.session_id, "user_id": user_id}, timeout=120)
        except httpx.HTTPError as e:
            reply = {"answer": f"⚠️ No pude hablar con la API en {API_URL}: {e}", "meta": ""}
        else:
            if r.status_code == 200:
                data = r.json()
                st.session_state.session_id = data["session_id"]                       # el id que el servidor nos devuelve
                cites = ", ".join(f"[{c['id']}] {c['section']}" for c in data["citations"]) or "sin citas"
                u = data["usage"]
                reply = {"answer": data["answer"], "meta": f"🔧 {', '.join(data['tools_used']) or 'sin herramientas'} · 📎 {cites} · "
                         f"🪙 {u['input_tokens']} entrada / {u['output_tokens']} salida · {u['model_calls']} llamadas"}
            else:
                reply = {"answer": f"⚠️ La API respondió {r.status_code}: {r.json().get('detail', r.text)}", "meta": ""}
        st.markdown(reply["answer"])
        if reply["meta"]:
            st.caption(reply["meta"])
    st.session_state.messages.append({"role": "assistant", "content": reply["answer"], "meta": reply["meta"]})
