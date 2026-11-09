"""Elemento 1 del agente: EL MODELO.

`build_model()` devuelve un modelo de chat de LangChain según `LLM_PROVIDER` (en tu `.env`):

    anthropic → Claude        (necesita ANTHROPIC_API_KEY)   · por defecto claude-haiku-5-5
    openai    → GPT           (necesita OPENAI_API_KEY)      · por defecto gpt-6-luna
    local     → LM Studio u otro servidor compatible con OpenAI (LOCAL_LLM_URL y LOCAL_LLM_MODEL)
    fake      → un "modelo" de juguete sin red (para ver el bucle sin gastar ni instalar nada)

`LLM_MODEL` fija otro modelo de Anthropic u OpenAI. Los tres reales comparten la misma interfaz
(`invoke`, `stream`, `bind_tools`): por eso el resto del código no cambia al cambiar de proveedor.
"""

import os

from dotenv import find_dotenv, load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-6-luna"}


def local_url() -> str:
    """URL de LM Studio. Dentro de Docker, `localhost` es el contenedor: se traduce a la máquina anfitriona."""
    url = os.getenv("LOCAL_LLM_URL", "http://localhost:1235/v1")
    if os.getenv("RUNNING_IN_DOCKER") == "1":
        url = url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    return url


def build_model() -> BaseChatModel:
    load_dotenv(find_dotenv(usecwd=True))
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()

    if provider == "fake":
        from fake_model import FakeToolModel

        return FakeToolModel()
    if provider == "local":
        # LM Studio habla el formato de OpenAI: mismo cliente, otra dirección.
        return init_chat_model(
            os.getenv("LOCAL_LLM_MODEL", "google/gemma-4-12b-qat"),
            model_provider="openai",
            base_url=local_url(),
            api_key="not-used",   # el servidor local no la valida, pero el cliente exige un texto
            temperature=0,        # repetible; en Anthropic/OpenAI no se fija: algunos modelos solo aceptan el valor por defecto
            max_retries=0,
            timeout=300,          # sin GPU, un modelo local puede tardar
        )
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"LLM_PROVIDER='{provider}' no válido. Usa: anthropic, openai, local o fake.")
    return init_chat_model(os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider], model_provider=provider)
