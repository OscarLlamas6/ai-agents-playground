"""El modelo de chat. Una sola función: build_model() según LLM_PROVIDER (fake | openai | anthropic | local)."""

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
    provider = os.getenv("LLM_PROVIDER", "fake").strip().lower()
    if provider == "fake":
        from trello_ops.fake_model import FakeOpsModel

        return FakeOpsModel()
    if provider == "local":
        # LM Studio habla el protocolo de OpenAI: mismo cliente, otra dirección.
        return init_chat_model(os.getenv("LOCAL_LLM_MODEL", "google/gemma-4-12b-qat"), model_provider="openai",
                               base_url=local_url(), api_key="lm-studio", temperature=0)
    if provider not in DEFAULT_MODELS:
        raise ValueError(f"LLM_PROVIDER debe ser fake, openai, anthropic o local (recibido: {provider!r})")
    return init_chat_model(os.getenv("LLM_MODEL") or DEFAULT_MODELS[provider], model_provider=provider)
