"""El modelo de chat del agente. Una sola función: build_model()."""

import os

from langchain.chat_models import init_chat_model

# Modelos económicos por defecto (se cambian con LLM_MODEL).
DEFAULT_MODELS = {"anthropic": "claude-haiku-5-5", "openai": "gpt-6-luna"}


def local_url() -> str:
    """URL de LM Studio. Dentro de Docker, `localhost` es el contenedor: se traduce a la máquina anfitriona."""
    url = os.getenv("LOCAL_LLM_URL", "http://localhost:1235/v1")
    if os.getenv("RUNNING_IN_DOCKER") == "1":
        url = url.replace("localhost", "host.docker.internal").replace("127.0.0.1", "host.docker.internal")
    return url


def build_model():
    """Devuelve un modelo de chat de LangChain según LLM_PROVIDER (anthropic | openai | local).

    `init_chat_model` es la interfaz común de LangChain: el resto del código no sabe qué proveedor hay detrás.
    """
    provider = os.getenv("LLM_PROVIDER", "openai").lower()
    if provider == "local":
        # LM Studio habla el protocolo de OpenAI: se usa el proveedor "openai" apuntando a otra URL.
        return init_chat_model(
            os.getenv("LOCAL_LLM_MODEL", "google/gemma-4-12b-qat"),
            model_provider="openai",
            base_url=local_url(),
            api_key="lm-studio",
            temperature=0,
        )
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"LLM_PROVIDER debe ser anthropic, openai o local (recibido: {provider!r})")
    return init_chat_model(os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider], model_provider=provider)
