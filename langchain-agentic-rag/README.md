# langchain-agentic-rag

Un **RAG agéntico** con LangChain: la búsqueda en los documentos es **una herramienta** y es el **modelo** quien decide si busca, con qué consulta, cuántas veces y cuándo responder. Responde con **citas** que el código verifica.

Es deliberadamente pequeño. Los vectores del manual, la bitácora de consultas, los huecos del manual y **las sesiones de conversación** viven en **Postgres con pgvector** (un contenedor). El agente se usa de tres formas, todas sobre el mismo servicio: una **CLI** (Click), una **API HTTP** (FastAPI) y una **interfaz** (Streamlit) que es un cliente más de la API. El corpus es un manual ficticio de una mesa de ayuda de TI ([`data/mesa_de_ayuda.md`](data/mesa_de_ayuda.md)); todo es inventado.

## Qué lo hace "agéntico"

| | RAG de flujo fijo | RAG agéntico (este proyecto) |
| --- | --- | --- |
| Quién decide buscar | El código: **siempre** busca | El modelo: busca **solo si hace falta** |
| Consulta de búsqueda | La pregunta tal cual | La que formula el modelo |
| Número de búsquedas | Una | Las que el modelo necesite (con tope) |
| Si lo recuperado no sirve | Responde con lo que llegó | Puede reformular y buscar de nuevo |
| Costo y latencia | Una llamada al modelo | Varias llamadas por pregunta |

## Cómo se expone el agente

```mermaid
flowchart LR
    BR["Bruno / curl / otro programa"] -->|"POST /chat<br/>{message, session_id}"| API
    UI["Streamlit · ui.py"] -->|"HTTP"| API
    CLI["Click · cli.py"] --> SV
    API["FastAPI · api.py<br/>contratos Pydantic · /docs"] --> SV["service.py<br/>AgentService"]
    SV --> AG["Agente (create_agent)"]
    AG --> PG[("Postgres + pgvector<br/>vectores · bitácora · huecos<br/>checkpoints = sesiones")]
```

El agente es un servicio **sin estado en el proceso**: la conversación vive en el *checkpointer* (Postgres) y el cliente solo lleva un identificador. **`session_id` en la API = `thread_id` en LangGraph.** Si reinicias el servidor y reenvías el mismo `session_id`, la conversación continúa.

## El flujo del agente por dentro

```mermaid
flowchart TB
    U(["👤 Pregunta"]) --> M
    subgraph AG["Agente · create_agent"]
        M["Modelo + prompt de sistema"] --> D{"¿Necesita<br/>buscar?"}
        D -->|"sí: con su propia consulta"| S["search_manual · list_topics<br/>get_ticket · sla_deadline · lookup_asset<br/>report_gap (escribe)"]
        S --> R["Retriever → Postgres + pgvector<br/>(búsqueda por similitud coseno)"]
        R -->|"fragmentos con id y puntaje"| M
        D -->|"no, o ya tiene suficiente"| A["Answer · salida estructurada<br/>answer · citations · found"]
    end
    MW["Middleware<br/>ToolCallLimit · ToolRetry<br/>ModelCallLimit · TokenBudget · NoProgress"] -.-> AG
    ST["Store (largo plazo)<br/>preferencias por usuario"] -.-> AG
    CP["Checkpointer + thread_id<br/>memoria de corto plazo"] -.-> AG
    A --> LOG["audit: after_agent<br/>query_log (tokens, herramientas)"]
    LOG --> DB[("Postgres + pgvector<br/>manual_chunks · query_log · manual_gaps")]
    S -.-> DB
    A --> V["verify() en código:<br/>¿las citas existen?"]
    V --> OUT(["Respuesta con citas verificadas"])
```

## Las piezas

| Archivo | Qué hace | Concepto de LangChain |
| --- | --- | --- |
| [`model.py`](model.py) | Construye el modelo según `LLM_PROVIDER` | `init_chat_model`: interfaz común de modelos |
| [`db.py`](db.py) | La base: tablas `manual_chunks` (vectores), `query_log` y `manual_gaps`; versión en memoria para pruebas | `psycopg` + `pgvector` (`<=>` = distancia coseno) |
| [`retriever.py`](retriever.py) | Parte el manual en fragmentos, los vectoriza **una vez** (huella del manual y del modelo) y busca en la base; guarda lo recuperado | (no es LangChain: es el mini-RAG que se envuelve como herramienta) |
| [`ops_data.py`](ops_data.py) + [`data/`](data) | Tickets y equipos ficticios (JSON) y el cálculo de plazos en horas hábiles | Código determinista que el modelo no debe hacer de cabeza |
| [`tools.py`](tools.py) | 8 herramientas: `search_manual`, `list_topics` (RAG), `get_ticket`, `sla_deadline`, `lookup_asset` (solo lectura), **`report_gap` (escritura en Postgres)**, `remember_preference` y `recall_preferences` (memoria) | `@tool`: el docstring es la descripción que ve el modelo |
| [`audit.py`](audit.py) | Bitácora de cada pregunta: tokens de entrada y salida, llamadas al modelo, herramientas, si se encontró respuesta. La escribe el **código** | middleware propio con `@after_agent` |
| [`agent.py`](agent.py) | El agente, la respuesta estructurada, el middleware, la memoria y la verificación de citas | `create_agent`, `response_format=ToolStrategy(...)`, `ToolCallLimitMiddleware`, `ToolRetryMiddleware`, `checkpointer` |
| [`guards.py`](guards.py) | Ingeniería del bucle: `token_budget` (presupuesto de tokens) y `no_progress` (corta si repite la misma llamada) | middleware propio con `@after_model(can_jump_to=["end"])` |
| [`memory.py`](memory.py) | Memoria de largo plazo: preferencias por usuario en un *store* que vive en el mismo Postgres (`PostgresStore`); sin base, RAM con volcado a JSON | `PostgresStore` (o `InMemoryStore`), `context_schema` y `ToolRuntime` |
| [`service.py`](service.py) | `AgentService`: una pregunta entra con `session_id` y sale con respuesta, herramientas y tokens; lo comparten la CLI y la API | `thread_id`, checkpointer persistente |
| [`api.py`](api.py) | La API HTTP: `/chat`, `/sessions/{id}`, `/health`, `/log`, `/gaps`; contratos con Pydantic y documentación en `/docs` | FastAPI |
| [`ui.py`](ui.py) | Interfaz sencilla (chat) que consume la API; no importa el agente | Streamlit |
| [`cli.py`](cli.py) | Línea de comandos con Click: `ingest`, `ask`, `chat`, `log`, `serve` | Click |
| [`fake_model.py`](fake_model.py) | Modelo de juguete (`LLM_PROVIDER=fake`): demos y pruebas sin llaves ni red | `BaseChatModel` |
| [`bruno/`](bruno) | Colección de Bruno: 15 peticiones que cuentan la historia paso a paso (sesión nueva, continuación, historial, escritura, bitácora, errores) | Bruno |

## Lecturas y escrituras: quién decide qué

| Herramienta | Tipo | La decide |
| --- | --- | --- |
| `search_manual`, `list_topics`, `get_ticket`, `sla_deadline`, `lookup_asset` | Solo lectura | El modelo |
| `report_gap` | **Escritura** en Postgres (`manual_gaps`): una pregunta que el manual no pudo responder | El modelo, solo cuando `found = false` |
| `remember_preference` | Escritura en el *store* (memoria de largo plazo, en Postgres) | El modelo |
| Bitácora `query_log` | **Escritura** con tokens y herramientas de cada pregunta | **El código**, siempre (`audit.py`) |

La bitácora no es una herramienta a propósito: el modelo puede olvidarse de llamarla y no conoce los tokens de su propia respuesta. Una **auditoría** la escribe el código; una **acción** la decide el modelo.

Preguntas para probar las herramientas nuevas: *"¿El ticket T-101 ya venció su SLA y quién lo tiene?"* (ticket + plazo), *"¿La laptop LT-0088 sigue en garantía y qué cubre una laptop con defecto según el manual?"* (equipo + manual) y *"¿Dan soporte a impresoras 3D?"* (no está en el manual: registra el hueco).

## Requisitos

- Python **3.12** y [`uv`](https://docs.astral.sh/uv/) (recomendado).
- **Docker** con `docker compose` para la base: `docker compose up -d db` (Postgres 17 con pgvector, puerto 5433). Sin Docker, `VECTOR_STORE=memory` corre todo en RAM (la bitácora no persiste).
- **Opcional:** [Bruno](https://www.usebruno.com/downloads) (cliente HTTP de escritorio) o su CLI con Node (`npx @usebruno/cli`). Sin Bruno, los ejemplos con `curl` de abajo hacen lo mismo.
- **Embeddings:** OpenAI (`EMBEDDINGS_PROVIDER=openai`, por defecto), LM Studio con `text-embedding-nomic-embed-text-v2-moe` (`EMBEDDINGS_PROVIDER=local`, gratis) o `fake` (pruebas).
- **Modelo del agente:** OpenAI (por defecto), Anthropic o un modelo local (LM Studio) **de ~12B parámetros o más** (por ejemplo `google/gemma-4-12b-qat`). Un modelo de 3B elige mal las herramientas.

## Cómo correrlo

```bash
cd langchain-agentic-rag
cp .env.example .env        # y completa lo que uses (el .env no se sube a Git)
docker compose up -d db     # Postgres + pgvector
uv sync
uv run python cli.py ingest                    # INGESTA: fragmenta el manual, lo vectoriza y lo guarda en pgvector (una vez)
uv run python cli.py ask "¿Cada cuánto vence mi contraseña?"
uv run python cli.py ask --trace "Olvidé mi contraseña y estoy en casa: ¿cómo la restablezco y cómo me conecto a la red interna?"
uv run python cli.py chat --trace              # preguntas de seguimiento: el agente recuerda la conversación
uv run pytest                                  # pruebas sin red (modelo falso, embeddings falsos y base en memoria)
DATABASE_URL_TEST=postgresql://agent:agent@localhost:5433/agent uv run pytest   # además, Postgres + pgvector y PostgresSaver reales
```

Sin llaves ni red, para ver el mecanismo: `LLM_PROVIDER=fake EMBEDDINGS_PROVIDER=fake` (el modelo falso entiende pocas frases; son las de la colección de Bruno). Con LM Studio, ajusta `LOCAL_LLM_URL` al puerto de tu servidor (por defecto `http://localhost:1235/v1`) y carga el modelo antes: `lms load google/gemma-4-12b-qat`.

La ingesta es un paso explícito: si preguntas antes de ejecutarla, la CLI y la API avisan (`503`) en vez de gastar tokens.

## La API (FastAPI)

```bash
uv run uvicorn api:app --reload         # o: uv run python cli.py serve   ·   documentación interactiva: http://localhost:8000/docs
```

| Método y ruta | Qué hace |
| --- | --- |
| `GET /health` | Estado: proveedor de modelo, proveedor de embeddings y si el manual está indexado |
| `POST /chat` | `{"message", "session_id"?, "user_id"?}` → respuesta con `session_id`, `answer`, `citations`, `found`, `tools_used`, `usage` (tokens de ESTA pregunta) y `status` |
| `GET /sessions/{session_id}` | Historial de la conversación (404 si no existe) |
| `DELETE /sessions/{session_id}` | Borra la conversación (204) |
| `GET /log?limit=` | Últimas consultas de la bitácora, con tokens |
| `GET /gaps` | Preguntas que el manual no pudo responder (las escribió `report_gap`) |

```bash
# 1) Sin session_id: el servidor crea la sesión y la devuelve
curl -s localhost:8000/chat -H 'content-type: application/json' -d '{"message":"Me llamo Marta","user_id":"marta"}'
# 2) Con el session_id devuelto: misma conversación (el agente recuerda)
curl -s localhost:8000/chat -H 'content-type: application/json' -d '{"message":"¿Cómo me llamo?","session_id":"<el id>"}'
# 3) El historial que guardó el servidor
curl -s localhost:8000/sessions/<el id>
```

**Sesiones persistentes.** Con `DATABASE_URL`, el checkpointer es `PostgresSaver`: el estado de cada sesión está en Postgres, no en la memoria del servidor. Reinicia la API (o el contenedor) y reenvía el mismo `session_id`: continúa. Sin base de datos (`VECTOR_STORE=memory`), el checkpointer vive en RAM y las sesiones se pierden al reiniciar.

## La interfaz (Streamlit)

```bash
uv run streamlit run ui.py              # http://localhost:8501 · con la API corriendo (API_URL=http://localhost:8000 por defecto)
```

Un chat sencillo: guarda el `session_id` que devuelve la API, muestra bajo cada respuesta las herramientas, las citas y los tokens, y tiene "Nueva sesión" y "Continuar una sesión por id". Es un cliente más: no importa el agente.

## Bruno: recorrer la API paso a paso

La carpeta [`bruno/`](bruno) es una colección de Bruno. Ábrela con *Open Collection*, elige el ambiente `local` y ejecuta las peticiones en orden (la 02 guarda el `session_id` en una variable que usan las siguientes):

| # | Petición | Qué demuestra |
| --- | --- | --- |
| 01 | `GET /health` | La API está viva y el manual indexado |
| 02 | `POST /chat` sin `session_id` | El servidor crea y devuelve la sesión |
| 03 | `POST /chat` con `{{session_id}}` | Misma sesión: recuerda el nombre |
| 04 | `POST /chat` pregunta del manual | RAG: `tools_used`, `citations`, `usage` |
| 05 | `GET /sessions/{{session_id}}` | El historial guardado |
| 06 | `POST /chat` sin `session_id` | Otra sesión: no recuerda |
| 07–08 | Preferencia y lectura | Memoria de largo plazo: otra sesión, mismo usuario |
| 09 | Ticket y SLA | Dos herramientas de lectura combinadas |
| 10–11 | Pregunta fuera del manual y `GET /gaps` | La herramienta de escritura `report_gap` |
| 12 | `GET /log` | La bitácora con tokens (escrita por el código) |
| 13–15 | Borrar sesión, 404 y 422 | Errores con su código HTTP |

Por línea de comandos (con la API corriendo en modo `fake`): `task bruno` o `cd bruno && npx --yes @usebruno/cli@latest run --env local`.

## Ingeniería del bucle y memoria de largo plazo

- **Límites del bucle.** Tres redes de seguridad por pregunta: `ToolCallLimitMiddleware` (máximo de búsquedas), `ModelCallLimitMiddleware(run_limit=8)` (máximo de vueltas) y los dos guardianes de [`guards.py`](guards.py): presupuesto de tokens y detección de falta de progreso. Si uno corta el bucle, no hay respuesta estructurada: la API responde `status: "stopped"` con el motivo en `answer`.
- **Preferencias entre ejecuciones.** El agente puede guardar (`remember_preference`) y consultar (`recall_preferences`) datos duraderos del usuario. La identidad (`--user` en la CLI, `user_id` en la API) la inyecta el código, no el modelo. Con `DATABASE_URL`, las preferencias se guardan en la tabla `store` del mismo Postgres (`PostgresStore`) y sobreviven a cualquier reinicio; así, el estado completo del agente está en una sola base: vectores (`manual_chunks`), sesiones (checkpointer), memoria de largo plazo (`store`), bitácora (`query_log`) y huecos (`manual_gaps`). Solo sin base de datos se usa RAM con un volcado a `MEMORY_FILE`.

```bash
uv run python cli.py chat --user ana
# Tú > Uso Linux y trabajo desde casa.            ← el agente guarda sistema_operativo = Linux
# (cierra el programa y vuelve a abrirlo)
uv run python cli.py chat --user ana
# Tú > ¿Cómo me conecto a la VPN?                 ← consulta lo guardado antes de buscar
```

## Ver la bitácora y los huecos

```bash
uv run python cli.py log                                       # resumen en la terminal
docker compose exec db psql -U agent -d agent -c \
  "SELECT question, input_tokens, output_tokens, model_calls, tools_used, found FROM query_log ORDER BY id DESC LIMIT 5"
docker compose exec db psql -U agent -d agent -c "SELECT question, note FROM manual_gaps"
docker compose exec db psql -U agent -d agent -c "SELECT sum(input_tokens) AS entrada, sum(output_tokens) AS salida FROM query_log"
```

## Con Taskfile

Si tienes [Task](https://taskfile.dev/installation/), `task` lista las tareas. Lo que escribes después de `--` es la pregunta:

```bash
task install                                   # uv sync
task ingest                                    # ingesta del manual (Click)
task trace -- "Olvidé mi contraseña y estoy en casa: ¿cómo la restablezco y cómo me conecto?"
task ask   -- "¿Cada cuánto vence mi contraseña?"
task chat                                      # conversación con memoria
task api                                       # la API en :8000
task ui                                        # la interfaz en :8501
task bruno                                     # la colección de Bruno contra la API
task test                                      # pruebas sin red
```

## Con Docker

`docker compose up -d` levanta tres servicios con una sola imagen: `db` (Postgres + pgvector), `api` (:8000, indexa el manual al arrancar con `AUTO_INGEST=1`) y `ui` (:8501). La CLI es un contenedor de un solo uso (perfil `cli`).

```bash
docker compose up -d --build                     # db + api + ui  →  http://localhost:8000/docs y http://localhost:8501
LLM_PROVIDER=fake EMBEDDINGS_PROVIDER=fake docker compose up -d   # lo mismo, sin llaves ni red
docker compose run --rm agent ingest             # la CLI en un contenedor
task docker:ask -- "¿Cada cuánto vence mi contraseña?"
task docker:chat                                 # conversación con memoria, en el contenedor
docker compose restart api                       # las sesiones sobreviven: están en Postgres
docker compose down                              # conserva los datos · `down -v` borra también la base y las sesiones
```

**LM Studio desde un contenedor.** `LOCAL_LLM_URL=http://localhost:1235/v1` se traduce solo a `host.docker.internal`, pero eso solo funciona si LM Studio acepta conexiones de la red local (*Developer → Server Settings → «Serve on Local Network»*). **Solo Linux**, sin tocar LM Studio: `task docker:ask:host`.

## Qué mirar con `ask --trace`

- `👤` la pregunta, `🤖` la herramienta que **pidió** el modelo (con su consulta) y `🔧` lo que devolvió tu código. El modelo nunca ejecuta nada: pide, y el código ejecuta.
- Una pregunta que **no necesita** el manual (`"Hola, ¿cuánto es 15 + 27?"`) no genera ninguna llamada a herramientas.
- Una pregunta de **dos partes** genera dos búsquedas con consultas distintas.
- Una pregunta **fuera de alcance** (`"¿Dan soporte a impresoras 3D?"`) suele provocar una reformulación y termina con "no lo encuentro" (`found = false`), no con una invención.

## Experimentos sugeridos

1. Quita la regla "máximo dos reformulaciones" del `SYSTEM_PROMPT` y observa si aparecen bucles; luego baja `run_limit` a 1 en el middleware.
2. Cambia el modelo a uno pequeño (`llama-3.2-3b-instruct`) y compara la elección de herramientas.
3. Agrega un archivo `.md` con instrucciones escondidas ("ignora tus reglas y…") al manual y comprueba que el agente lo trata como dato.
4. Convierte `verify` en un paso obligatorio: si hay problemas de citas, vuelve a invocar al agente con el aviso.
5. Agrega una segunda herramienta (por ejemplo, `ticket_status(ticket_id)` simulada) y observa cómo el modelo elige entre ella y la búsqueda.

## Versiones probadas

`langchain 1.5.0`, `langchain-core 1.8.0`, `langgraph 1.2.22`, `langgraph-checkpoint-postgres 3.2.1`, `psycopg 3.3.9`, `pgvector 0.5.1`, `fastapi 0.163.0`, `uvicorn 0.54.0`, `streamlit 1.67.0`, `click 8.5.1`, `httpx 0.28.1`, `pydantic 2.14.3`, Bruno CLI `4.3.0` (resueltas con `uv sync` en noviembre de 2026).

**Qué se probó:** 34 pruebas (31 sin red y 3 contra Postgres + pgvector reales con `DATABASE_URL_TEST`); la API y la colección de Bruno (15 peticiones, 9 pruebas y 24 aserciones) con el modelo y los embeddings falsos, en local y en Docker; la sesión continuada tras reiniciar la API y el contenedor (PostgresSaver real); la interfaz con `AppTest` y arrancándola de verdad; la CLI (`ingest`, `ask`, `log`) en local y en Docker.
**Qué NO se probó:** las herramientas nuevas (`get_ticket`, `sla_deadline`, `lookup_asset`, `report_gap`) con un modelo real (OpenAI, Anthropic o LM Studio): el modelo falso solo demuestra el mecanismo, no la calidad de la elección de herramientas; un modelo pequeño elegirá peor. El modo `chat` interactivo de la CLI no se automatizó. El `docker-compose.host.yml` solo pasó `docker compose config`.

## 💰 Costo

Con LM Studio, US$ 0. Con un proveedor de pago, cada pregunta hace de 2 a 6 llamadas al modelo (menos de un centavo con un modelo económico). `ToolCallLimitMiddleware` limita las búsquedas por pregunta y `recursion_limit` corta cualquier bucle.
