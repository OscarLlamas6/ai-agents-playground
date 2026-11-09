"""Un modelo de chat "guionado": responde con mensajes preparados de antemano. Sin red, sin costo."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedModel(BaseChatModel):
    script: list  # lista de AIMessage; se entrega uno por llamada
    seen: list = []   # cuántos mensajes recibió en cada llamada

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self  # las herramientas las "conoce" el guion, no el modelo

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(len(messages))
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def final(answer: str, citations: list[str], found: bool = True, call_id: str = "fin") -> AIMessage:
    return call("Answer", {"answer": answer, "citations": citations, "found": found}, call_id)
