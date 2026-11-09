# ai-agents-playground

Repositorio con múltiples implementaciones de agente de AI, cada una en su carpeta, como ejemplos "formales pero no complejos" para estudiar un concepto a la vez. Cada carpeta es un proyecto independiente (su propio entorno, su propio `.env.example` y su propio README).

| Carpeta | Qué muestra | Framework |
| --- | --- | --- |
| [`langchain-basic`](langchain-basic) | El agente más pequeño posible, con cada elemento numerado y visible: modelo, herramientas, prompt de sistema, agente, mensajes, bucle con límite, traza y salida. Incluye el mismo bucle escrito a mano | LangChain |
| [`langchain-agentic-rag`](langchain-agentic-rag) | RAG agéntico: la búsqueda es una herramienta; el agente decide si buscar, con qué consulta y cuántas veces; respuesta estructurada con citas verificadas, middleware y memoria de corto plazo | LangChain |

> La carpeta de cada agente nueva se añade a esta tabla. Empieza por `langchain-basic`; `langchain-agentic-rag` añade RAG, salida estructurada, middleware y memoria.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (recomendado) y Python 3.12. Con otro gestor, instala las dependencias de cada `pyproject.toml`.
- Opcionales: [Task](https://taskfile.dev/installation/) (cada agente trae un `Taskfile.yml`; `task` lista las tareas) y Docker con `docker compose` (cada agente trae su `Dockerfile` y su `docker-compose.yml`).
- Un modelo de lenguaje. Elige **uno**:
  - **LM Studio** (local, gratis): activa el servidor local y carga un modelo con soporte de herramientas de ≈ 12B de parámetros o más (se probó `google/gemma-4-12b-qat`). Uno de 3B no sirve para agentes.
  - **Anthropic** u **OpenAI** (de pago por uso, centavos en estos ejemplos): una API key.

## Cómo correr un agente

```bash
cp .env.example .env              # en la raíz (o dentro de la carpeta del agente) y completa lo que uses
cd langchain-basic
uv sync
uv run python agent.py --trace "¿Cómo está la VPN?"
```

Cada carpeta explica sus propios comandos en su README.

## Convenciones

- **Una carpeta por agente**, cada una con `README.md`, `.env.example`, `pyproject.toml`, `.python-version` (3.12), `Taskfile.yml`, `Dockerfile` y `docker-compose.yml` (con un `docker-compose.host.yml` para alcanzar LM Studio desde Linux).
- Identificadores de código en inglés; comentarios y documentación en español.
- Variables de entorno comunes: `LLM_PROVIDER` (`openai` por defecto, `anthropic` o `local` para LM Studio; algunos agentes admiten también `fake`, un modelo de juguete sin red), `LLM_MODEL` (opcional), `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LOCAL_LLM_URL` y `LOCAL_LLM_MODEL`. Modelos económicos por defecto: `claude-haiku-5-5` y `gpt-6-luna`.
- Cada proyecto tiene un `model.py` con `build_model()`: cambiar de proveedor es tocar solo `LLM_PROVIDER`.
- 🔑 Las llaves van en `.env`, nunca en el código. 🧪 Todos los datos y servicios de los ejemplos son ficticios o simulados.
- 💰 Cada README dice cuántas llamadas al modelo hace el agente y qué cuesta con un proveedor de pago.
