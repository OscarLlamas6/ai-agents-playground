"""Un "modelo" de juguete SIN red, para ver el bucle del agente sin gastar ni descargar nada.

No piensa: aplica dos reglas.
    · Si la última cosa que ve es una pregunta sobre un servicio conocido, PIDE la herramienta.
    · Si la última cosa que ve es el resultado de una herramienta, lo repite como respuesta.
Un modelo real decide con lenguaje; el bucle que lo rodea es exactamente el mismo.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeToolModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def bind_tools(self, tools, **kwargs):   # un modelo real recibe aquí los esquemas; este los ignora
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            reply = AIMessage(content=f"Resultado de la herramienta: {last.content}")
        else:
            service = next((s for s in ("correo", "vpn", "wiki") if s in last.text.lower()), None)
            reply = (
                AIMessage(content="", tool_calls=[{"name": "get_service_status", "args": {"service": service}, "id": "call_1"}])
                if service
                else AIMessage(content="No necesito herramientas para eso.")
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])
