# Plan: pruebas end-to-end reales antes de producción

Fecha: 2026-10-03 · Estado: **preparación construida y verificada hasta donde no hacen falta tus keys**. Cómo ejecutarla: [`runbook-e2e.md`](runbook-e2e.md). Quedan sin probar con modelos reales (necesitan tus keys): JEV y el LLM en los tres agentes.

Tres pruebas separadas, cada una con su agente, su identidad y su conjunto de métricas:

| # | Prueba | Agente (release) | Principal | Estado hoy |
|---|---|---|---|---|
| A | Hablar con un agente que resuelve un problema | `recepcion` → `disputas` / `consultas` (`registry-realflow`) | `customer` | **Existe** y corrió de punta a punta el 2026-10-02 (informe `docs/informes/2026-10-02-flujo-real-y-replay-audit.md`) |
| B | Hablar con un agente que asesora a un cliente | `copiloto-asesor` | `advisor` + `X-On-Behalf-Of` | **No existe la release.** Motor listo (T1 `input_view` ya está en M0 y en `LLMAgentPort`); faltan agente, flow, prompt, tools de handoff/transcript y guías internas |
| C | Hablar con el agente constructor | `constructor-chat` | `builder` (staff) | **No existe la release.** Están `write_draft`, `BuilderToolExecutor` y topes del registry; faltan los dos agentes y sus flows (fase 7 de write-draft) |

## 0. Hallazgos que condicionan el plan

1. **PR #27** (`feat/aws-scale-fase0-1`, S3 blobs + relay + pool + sweep): CI verde (`check`, `image`), sin review, mergeable sin determinar. Es la única PR abierta; `main` está en `d7f8b8d` (PR #26, gateway HTTP). Las demás ramas locales (`feat/jev-via-gateway`, `feat/solicitudes-n01-n11`, `feat/http-llm-gateway`) ya están integradas o son worktrees.
2. **Todo lo opt-in de #27 (S3, relay SNS, pool) debe quedar APAGADO en la prueba E2E local** y probarse aparte; si no, mezclas dos fuentes de falla. Verifica que con las variables vacías el comportamiento es idéntico a `main`.
3. **Tools, `AuthzPort`, transcript, calibración, clasificador y `FieldClassifier` siguen siendo dobles sintéticos** (`testing/serve_demo.py`, `testing/realflow_demo.py`); `serve` solo arranca con `AGENTCORE_ALLOW_DEMO=1`. **Esto es el bloqueante real de producción**: ninguna prueba E2E "real" valida datos reales mientras no existan las piezas de la unidad 3, 6 y 7 (TEMAS #13). El E2E prueba el motor, el gateway, JEV y el registry; no los datos de clientes.
4. **Replay `audit` de runs reales da `diverged`** por 3 causas de diseño de M11 (reloj por turno, `result_fp`, borrador con citas). No es bloqueante para probar, sí para decir "auditoría reproducible" en producción.
5. **No hay vistas SQL de métricas** (TEMAS #11, pendiente). Hoy las métricas salen de: Phoenix (trazas `agentcore.*`: latencia, costo por llamada), la API de exportación `/v1/export/*` y consultas SQL ad hoc sobre los eventos. Hay que construirlas (fase 4 abajo).
6. **Transferencia por `serve` todavía no transfiere**: el proveedor real de "elegir especialista" está abierto; los flows de especialistas vuelven a preguntar el slot `problema`. Se prueba con `registry-realflow` + clasificador sintético, o se resuelve (fase 1).
7. **`agentcore migrate` ya existe** (README) — el informe del 10-02 decía que no. Úsalo, no `apply_schema`.
8. **Copiloto**: el modo (b) "escuchar la conversación" **no está soportado** (turno de observación, C2-2). La prueba B cubre solo el modo (a): pedidos del asesor.
9. **Constructor**: solo `constructor-chat`; `constructor-task` no debe probarse como autónomo (falta detector de señales y tope de costo, C3-4/C3-6).
10. `consultas` (lectura de PQR por idempotencia) nunca se probó a fondo.

## 1. Entorno común (una sola vez)

Todo local, un solo comando levanta el stack:

- **Postgres 16** con `docker compose up -d postgres` (Docker 25 está instalado). Base `agentcore`, credencial de dev del compose.
- **llm-gateway** (repo hermano, Go): `go run ./cmd/llm-gateway` en `:8080` con alias `openrouter`. Pasa también JEV (`/v1/jev`).
- **agent-core**: `uv sync --locked`, `uv run agentcore migrate --dsn ...`, importar registry, `uv run agentcore serve --registry-api ...`.
- **Phoenix** (`docker run -p 6006:6006 arizephoenix/phoenix`) con `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006`.
- **Credenciales de prueba**: `python -m testing.demo_identities` emite JWS de `customer`, `advisor` con delegación, `supervisor`, `admin` y bot.

### Lo que TÚ tienes que configurar (nadie más puede)

| Qué | Dónde | Nota |
|---|---|---|
| `OPENROUTER_API_KEY` (o la de tu proveedor) y `LLM_ENDPOINTS` | entorno del **llm-gateway** | Nunca en agent-core |
| `AGENTCORE_JEV_API_KEY` | entorno de agent-core (o del gateway si usas `/v1/jev`) | Acceso anticipado; sin ella Understand cae bajo umbral |
| `AGENTCORE_LLM_GATEWAY_URL` + `AGENTCORE_LLM_GATEWAY_TOKEN` | agent-core | Van juntas o ninguna (sin ellas toda generación cae a plantilla) |
| `AGENTCORE_KEYS_FINGERPRINT`, `AGENTCORE_KEYS_TOKEN_MAP` | agent-core | `kid:base64` de 32 bytes: `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"` |
| `AGENTCORE_REGISTRY_DSN` (+ `AGENTCORE_EVAL_DSN`) | agent-core | Para el DSN del compose local |
| `AGENTCORE_ALLOW_DEMO=1` | solo local | Habilita los dobles. **Jamás en producción** |
| Docker Desktop corriendo | tu máquina | `docker info` hoy solo mostró el cliente: confirma que el daemon está arriba |
| Phoenix (opcional pero recomendado) | tu máquina | Para latencia/costo |

Hoy no hay `.env` en el repo (solo `.env.example`): hay que crearlo.

## 2. Fases de trabajo

### Fase 0 — Línea base y PR (bloquea todo)
1. Decidir el destino de PR #27 antes de probar: o se mergea y se prueba sobre `main`, o se prueba en su rama. **Recomendado: probar sobre la rama de #27 rebasada con `main`, con S3/relay/pool apagados, y mergear solo si pasa.** Así el E2E valida lo que irá a `main`.
2. Correr en la rama: `uv run pytest`, `uv run lint-imports`, `uv run mypy`, `uv run ruff check .`, `uv run agentcore contracts --check`. (Ya hay un `full_tests.log` verde del 10-03 10:32 a nivel de carpeta padre, pero es de antes de la fusión `1fbccde`: repetir.)
3. `agentcore migrate` sobre una base vacía y luego otra vez (idempotente).

### Fase 1 — Prueba A: agente que resuelve un problema (el más listo)
Ya existe. Trabajo: convertirlo en un guion repetible y ampliar cobertura.
- Script `scripts/e2e/up.ps1` (levanta Postgres, gateway, migra, importa `registry-realflow`, emite credenciales, arranca `serve`).
- Escenarios (de casos-de-uso §1.4): disputa resuelta, cargo no encontrado, escalamiento por monto, step-up, interrupción de fraude, IDOR, injection, portugués, `consultas` con radicado (la brecha 6 del informe), FAQ con cita.
- Cerrar la transferencia por `serve`: cablear un proveedor real para "elegir especialista" o dejar el clasificador sintético explícito en el reporte.
- Cliente: `uv run python -m testing.chat --agent recepcion` (ya existe).

### Fase 2 — Prueba B: copiloto del asesor (a construir)
Construcción en `tests/fixtures/registry-copiloto/` (mismo formato que `registry-realflow`):
- Agente `copiloto-asesor` (`invocable_by: [advisor]`, `subject_kinds: [customer]`, presupuestos holgados para sesión larga, C2-5).
- Flow `asistir`: `collect(pedido)` → `agent` (con `input_view: slots.pedido`) → `respond(advisor_view)` → bucle.
- Prompt del agente + plantilla de abstención.
- Tools de lectura: movimientos, productos, PQR (dobles sintéticos) + `obtener_handoff` y `leer_transcript` (envolviendo M10 `get` y M11, C2-4).
- Snapshot de conocimiento `guias-internas@1` (páginas `internal`: guion de retención, ofertas).
- Escenarios: pedido con cifra validada por M8, recomendación citada, continuidad de handoff, delegación ajena (`403 delegation_mismatch`), cliente fuera de delegación, injection por texto del cliente.
- Cliente: ampliar `testing/chat.py` con `--advisor` para enviar `X-On-Behalf-Of`.
- **Fuera de alcance, documentado**: modo escucha (b).

### Fase 3 — Prueba C: agente constructor (a construir)
- Release `constructor-chat` (modo `conversational`, `invocable_by: [builder]`, `subject_kinds: []`): flow `objetivo → agent → create_proposal → put_draft → validate → freeze → evaluate → respond`, usando `BuilderToolExecutor` (ya existe) y credencial de servicio con rol `constructor`.
- Requiere `serve --registry-api` y `--eval-dsn`.
- Escenarios (casos-de-uso §3.4): cambio de prompt, flow nuevo, validación fallida, gate fallido, escalada de privilegios ("apruébala y publícala" → no hay tool), datos de clientes (AG-02), crear agente desde cero (C3-7).
- Flujo humano completo: tú, como supervisor, apruebas y publicas la propuesta con el CLI/API del registry y vuelves a correr la prueba A contra la release nueva (cierra el círculo constructor → producción).
- Riesgos: salida única del borrador completo (C3-5): empezar con cambios pequeños. Replay del bucle del agente en M11 pendiente.

### Fase 4 — Métricas, trazas y depuración
- Phoenix: latencia y costo por turno/llamada (`invoke_agent`, `chat {modelo}`, `execute_tool`, `agentcore.transfer`).
- Vistas SQL de negocio (a construir, TEMAS #11): contención, resolución segura, `abandoned`, aclaraciones por run, modo degradado, costo por run, tasa de abstención/plantilla de respaldo, `validation_failed` del constructor. Un archivo `scripts/e2e/metrics.sql` + un comando para imprimirlas tras cada prueba.
- Depuración: `agentcore replay` (modo `fixture` sobre caminos grabados; `audit` solo informativo), `check_chain` de integridad, `GET /v1/export/runs` y `/events`, `trace_id` de la respuesta → Phoenix.
- Grabar cada escenario bueno con `agentcore record` como fixture de regresión.

### Fase 5 — Lista de salida a producción
Bloqueantes **no** cubiertos por el E2E local (decisión tuya o de otro dueño):
1. Reemplazar dobles por `ToolExecutor`, `AuthzPort`, `TranscriptStore` persistente, calibración con datos etiquetados, `FieldClassifier` real y servicio de identidad con `grant_active` (unidades 3, 6, 7).
2. `AGENTCORE_ALLOW_DEMO` fuera; `serve` debe arrancar sin dobles.
3. Claves reales rotadas y secretos en el gestor del despliegue (infra, Terraform `staging` y `prod`, sin despliegue automatizado).
4. Resolver divergencias de replay `audit` (M11) si se promete auditoría reproducible.
5. Probar aparte S3/relay SNS/pool de #27 en AWS (`staging`).
6. Gate de evaluación: confirmar que un agente sin `eval_suite` no llega a `validated` (abierto §13.14 de la spec de evaluación).
7. Retención y topes del constructor autónomo (TEMAS #16).

## 3. Entregables que dejaré listos al aprobar el plan

- `scripts/e2e/up.ps1` / `down.ps1` (stack local), `.env.e2e.example`.
- `tests/fixtures/registry-copiloto/` y `registry-constructor/` + un registry combinado `registry-e2e/` para importar los tres agentes de una vez.
- `testing/chat.py` con `--advisor` y `--builder`.
- `scripts/e2e/metrics.sql` y `scripts/e2e/report.py` (resumen por prueba).
- `docs/runbook-e2e.md`: cómo ejecutar cada prueba, qué esperar y cómo depurar.
- Pruebas automáticas (sin red) de las nuevas releases: `agentcore validate` y replay `fixture`.

## 4. Orden y esfuerzo estimado

1. Fase 0 + entorno (medio día) → 2. Fase 1 (medio día) → 3. Fase 4 base: Phoenix + SQL (medio día) → 4. Fase 2 (1 día) → 5. Fase 3 (1–1.5 días, la más riesgosa) → 6. Fase 5 como lista, no como trabajo.

## 5. Decisiones que necesito de ti

1. ¿Probamos sobre la rama de PR #27 (recomendado) o primero la mergeas a `main`?
2. ¿Qué proveedor/modelo real usarás (OpenRouter + Gemini Flash-Lite como en el informe, u otro)? ¿Tienes ya la key de JEV?
3. ¿Aceptas que el copiloto cubra solo el modo (a) y el constructor solo `constructor-chat`?
4. ¿Los datos siguen siendo sintéticos en esta ronda? (Las reglas del repo prohíben datos reales y las credenciales del diccionario de datos.)
