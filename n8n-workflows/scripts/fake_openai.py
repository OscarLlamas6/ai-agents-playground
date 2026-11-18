"""Servidor falso compatible con la API de OpenAI (Chat Completions) para probar los workflows con LLM SIN modelo real.

NO es un modelo: responde con salidas fijas según lo que pida n8n (extracción, agente con herramientas, salida estructurada).
Sirve para comprobar el cableado (credenciales, herramientas, parser de salida, sub-workflows) sin gastar dinero.

Uso:   python scripts/fake_openai.py [--port 8765] [--verbose]
Luego, en la credencial de n8n: URL http://host.docker.internal:8765/v1 y cualquier llave.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERBOSE = False


def _texto(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""


def decidir(body: dict) -> dict:
    """Devuelve el mensaje del asistente: texto o tool_calls."""
    msgs = body.get("messages", [])
    tools = {t["function"]["name"]: t["function"] for t in body.get("tools", []) if t.get("type") == "function"}
    visto = [m for m in msgs if m.get("role") == "tool"]
    contexto = " ".join(_texto(m) for m in msgs).lower()

    # 1) Salida estructurada/extracción: n8n expone una herramienta cuyo esquema es el que debemos devolver.
    for nombre, f in tools.items():
        props = (f.get("parameters") or {}).get("properties", {})
        if "categoria" in props and "severidad" in props:
            sev = "critica" if "sin servicio" in contexto or "toda" in contexto else "alta" if "no responde" in contexto else "media"
            return _llamada(nombre, {"categoria": "red", "severidad": sev, "resumen": "Clasificación de prueba (modelo falso)",
                                     "requiere_accion": True, "confianza": 0.9})
    # 2) Agente: primero usa herramientas de consulta, luego entrega la salida final por la herramienta del parser.
    consulta = [n for n in tools if "estado" in n or "runbook" in n]
    ya = {m.get("name") for m in visto}
    for n in consulta:
        if n not in ya and not any(n in json.dumps(m.get("tool_calls", [])) for m in msgs if m.get("role") == "assistant"):
            args = {"input": "vpn"} if "estado" in n else {"consulta": "vpn gateway"}
            if "estado" in n:
                props = (tools[n].get("parameters") or {}).get("properties", {})
                args = {k: "vpn" for k in props} or {"input": "vpn"}
            return _llamada(n, args)
    for nombre, f in tools.items():
        props = (f.get("parameters") or {}).get("properties", {})
        if "diagnostico" in props or (set(props) == {"output"}):
            propuesta = {"diagnostico": "Gateway VPN sin túneles activos (modelo falso).",
                         "accion": {"tipo": "reiniciar_servicio", "objetivo": "vpn-gw-01", "justificacion": "Runbook RB-VPN-01"},
                         "riesgo": "medio", "runbook_id": "RB-VPN-01"}
            if "diagnostico" in props:
                return _llamada(nombre, propuesta)
            return _llamada(nombre, {"output": propuesta})
    # 2b) Salida estructurada por TEXTO: n8n pega en el prompt las instrucciones de formato con el esquema JSON.
    if "json schema" in contexto or "json value that adheres" in contexto:
        if '"categoria"' in contexto and '"severidad"' in contexto:
            sev = "critica" if "sin servicio" in contexto or "toda" in contexto else "alta" if "no responde" in contexto else "media"
            return _json_en_texto({"categoria": "red", "severidad": sev, "resumen": "Clasificación de prueba (modelo falso)",
                                   "requiere_accion": True, "confianza": 0.9})
        if '"diagnostico"' in contexto:
            return _json_en_texto({"diagnostico": "Gateway VPN sin túneles activos (modelo falso).",
                                   "accion": {"tipo": "reiniciar_servicio", "objetivo": "vpn-gw-01", "justificacion": "Runbook RB-VPN-01"},
                                   "riesgo": "medio", "runbook_id": "RB-VPN-01"})
    # 3) Chat normal
    return {"role": "assistant", "content": "Respuesta de prueba del modelo falso. " + (_texto(msgs[-1])[:80] if msgs else "")}


def _json_en_texto(obj: dict) -> dict:
    return {"role": "assistant", "content": "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```"}


def _llamada(nombre: str, args: dict) -> dict:
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": f"call_{int(time.time()*1000) % 10**8}", "type": "function",
                            "function": {"name": nombre, "arguments": json.dumps(args, ensure_ascii=False)}}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": "gpt-6-luna", "object": "model"}]})
        self._json(404, {"error": "no encontrado"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if VERBOSE:
            print("→ petición:", json.dumps({k: body.get(k) for k in ("model", "stream", "tool_choice")}),
                  "| tools:", [t["function"]["name"] for t in body.get("tools", [])],
                  "| roles:", [m["role"] for m in body.get("messages", [])], file=sys.stderr, flush=True)
        msg = decidir(body)
        if VERBOSE:
            print("← respuesta:", json.dumps(msg, ensure_ascii=False)[:300], file=sys.stderr, flush=True)
        base = {"id": "chatcmpl-falso", "created": int(time.time()), "model": body.get("model", "falso")}
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if body.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            delta = dict(msg)
            if delta.get("tool_calls"):
                delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(delta["tool_calls"])]
            trozos = [{**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {**base, "object": "chat.completion.chunk",
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}], "usage": usage}]
            for t in trozos:
                self.wfile.write(f"data: {json.dumps(t)}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json(200, {**base, "object": "chat.completion", "usage": usage,
                         "choices": [{"index": 0, "message": msg,
                                      "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]})


def main() -> None:
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    VERBOSE = a.verbose
    print(f"Modelo falso en http://0.0.0.0:{a.port}/v1 (Ctrl+C para salir)")
    ThreadingHTTPServer(("0.0.0.0", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
