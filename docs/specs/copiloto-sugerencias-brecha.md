# Brecha del agente `copiloto-sugerencias` (ADR 0026)

Fecha: 2026-10-05 · Estado: **análisis; no hay cambio de núcleo implementado**. Fuentes: ADR 0026 (propuesto), `support-platform/docs/platform/api/slice-15b-copilot-suggestions.md`, plataforma ADR 0005 y el código de la plataforma (`application/ai/suggestions.py`, `infrastructure/ai/http_runtime.py`, `domain/ai/suggestion.py`), leídos sin modificarlos.

## 0. Qué hay hecho y qué no

| Pieza | Estado |
|---|---|
| `input` de un run `task` con lista de turnos (`list` slot, commit 601ca11) | Hecho en el núcleo |
| Agente como datos: `tests/fixtures/copiloto-sugerencias/agents/copiloto-sugerencias@1.0.0.yaml` | Hecho **sin flow** (entra por `sugerir@1`, que no existe) |
| Casos sintéticos con entrada, salida esperada y valores que no deben filtrarse: `.../casos/sinteticos.yaml` | Hecho; **no es todavía un `eval_suite`** (B3, B4) |
| Pruebas `tests/composition/test_copiloto_sugerencias_data.py` | Hecho: entrada, contrato de salida (con control negativo) y PII sobre M7 real |
| Salida `suggestions` en el motor, nodo `suggest`, flow, prompt, modelo de decisión, política de escalamiento, `eval_suite` ejecutable | **No hecho**: B1 a B5 |

Lo que **no** se probó: ningún modelo produjo estas sugerencias, y el agente no se ejecuta en el motor. Lo "esperado" de cada caso es una especificación, no una medición.

## 1. Brechas del núcleo (no implementadas)

### B1. La salida: `Suggestion` y `RunResult.suggestions` (M0, M9, M11)
Hoy `RunResult` tiene `output` pero **no** `suggestions`; `end.output_map` no puede recibir salida de un nodo `agent` (G0-22, a propósito). Falta:
- M0: tipo `Suggestion`, unión discriminada por `type` (`reply`, `tool`, `action`, `escalate`) con los campos del ADR 0026 §2, y `RunResult.suggestions: list[Suggestion] = []`. `SCHEMA_VERSION` menor; `uv run agentcore contracts`.
- M9: que `POST /v1/runs` y `GET /v1/runs/{id}` lo publiquen (la plataforma ya lee `suggestions` de la respuesta de `POST /v1/runs`).
- M11/replay: una sugerencia debe quedar registrada. **Cambio mínimo propuesto:** un evento `suggestions_produced` con **contadores y tipos** (nunca texto), para que el evaluador y las métricas lo vean, y guardar la lista en el estado del run (como `output`) para que `GET` y el replay la devuelvan. Estado y evento son parte de lo que M11 ya persiste; no hace falta almacén nuevo.

**Cambio mínimo:** solo `RunResult` (no `TurnResult.suggestions`: el ADR lo pide "para uso conversacional futuro"; nada lo necesita hoy).

### B2. El nodo `suggest` (M1, M2, M8) — Abierto 1 del ADR
Sin nodo no hay forma de que una lista generada llegue a `RunResult` sin violar G0-22. **Es el Abierto 1 (¿nodo nuevo o modo de `respond`?) y no se decidió aquí.** Para dimensionarlo, el cambio mínimo de cada opción:
- *Nodo `suggest`:* `SuggestConfig {prompt_ref, reads: [facts…], tools_allowed, actions_allowed, escalate_from: <hecho de una rule>|None, max_items}`; reglas de gate en M1 (listas declaradas ⊆ `tools_allowed` del agente y de solo lectura; `escalate_from` apunta a un hecho producido por una `rule`; G0-22 sin excepción); manejador en M2; M8 valida cifras de `reply` contra hechos citados, y `tool`/`args` contra el `args_schema`.
- *Modo de `respond`:* menos nodos, pero `respond` hoy entrega texto al usuario y su salida estructurada tocaría M8 y el contrato del turno.

El `escalate` solo puede venir de una `rule`: el modelo no lo crea. Eso es independiente de la opción.

### B3. El `eval_suite` no puede alimentar un run `task`
`Step` (`registry/suite.py`) solo tiene `start | turn | confirm` y `EngineScenarioHarness.run` construye `RunInput` **sin `input`**. Un agente con `input_schema` con slots requeridos no se puede evaluar. **Cambio mínimo:** `Step.input: dict[str, JsonValue] | None = None` (solo en `start`), que el harness pasa a `RunInput.input`, y que se **omita del volcado cuando es `None`** (igual que `AcceptedSlot`) para no cambiar el hash de las suites ya publicadas.

### B4. El `eval_suite` no puede afirmar sobre `suggestions`
`Expect` solo mira `outcome`, `actions_verified` y `escalated`; las `assertions` miran eventos del catálogo. **Cambio mínimo:** con el evento de B1 en el catálogo de métricas (`suggestions_produced`: `count`, `reply`, `tool`, `action`, `escalate`, todos enteros), las `assertions` existentes bastan (`escalate >= 1`, `count == 0`). Para afirmar contenido (la cifra del borrador, la ausencia de PII) hace falta un campo nuevo `Expect.suggestions` o un juez (`Judge`): decisión pendiente. La verificación de `sensitive_values` (`score_run`) ya recorre los eventos; solo cubrirá la salida si el evento o el estado llevan texto, lo cual **no** debe hacerse.

### B5. Datos que faltan y que dependen de Abiertos
Flow `sugerir`, prompt, modelo de decisión con la rama `sin_sugerencia` (Abierto 5), política(s) de escalamiento versionadas (la plataforma ADR 0005 abierto 3 pide el aporte de supervisores; hoy solo existe `escalamiento-disputa-monto`), `budgets` propios (Abierto 4; en el YAML copian los del `copiloto-asesor`, provisionales), `actions_allowed` (Abierto 6: no hay campo en `Agent`), máximo y orden de sugerencias (Abierto 2), idioma de `motive_draft` (Abierto 3).

### Forma que tendrá el `eval_suite` (borrador; NO cargable hoy, `Step` rechaza `input`)

```yaml
id: copiloto-sugerencias-suite
version: 1.0.0
agent_id: copiloto-sugerencias
repetitions: 3
scenarios:
  - id: escalar-por-fraude
    principal: {id: cust-001}
    steps:
      - op: start
        input: {…}            # B3: el `input` de casos/sinteticos.yaml
    assertions:
      - {event: suggestions_produced, where: [{field: escalate, op: ">=", value: 1}]}   # B4
thresholds: {}
```
Los seis casos de `casos/sinteticos.yaml` son sus escenarios. Faltan, para cumplir el ADR §8: otro cliente fuera de la delegación, portugués, inyección en el texto del cliente y cifras respaldadas con un hecho real (necesitan el flow).

## 2. Discrepancias entre agent-core (ADR 0026) y la plataforma (ADR 0005, slice-15b y su código)

Verificado con una ejecución puntual: las salidas esperadas de los casos pasadas por `_suggestions` y `normalize_suggestions` de la plataforma (código real, sin modificarlo; el agente de agent-core no existe, así que la salida es la esperada, no una medida). Las seis se parsean sin perder tipo ni texto. Lo que se pierde o choca:

| # | Discrepancia | Efecto | Quién decide |
|---|---|---|---|
| D1 | **Forma del `input`.** La plataforma envía `sla` y `sugerencia_anterior` como objetos y `motivo_llegada: null`. El motor solo acepta escalares y una lista plana, y rechaza un slot no declarado (`slot_not_accepted`, `slot_type_mismatch`): `start_run` respondería 422. Probado en `test_discrepancy_d1_…`. | La plataforma real no podría llamar al agente. | Propuesta del YAML: aplanar en la plataforma (`sla_estado`, `sla_minutos_restantes`, `sugerencia_borrador`, `sugerencia_escalacion_aceptada`) y **omitir** los nulos. Alternativa: slots de tipo objeto/null en el núcleo (cambio de M0). |
| D2 | `input.assistant_session_id` (decisión del 2026-10-04) **no lo envía** la plataforma. | Declarado como slot opcional; sin efecto hasta que lo envíe. Falta aclarar para qué lo usa el agente. | Usuario |
| D3 | `tool.label`: la plataforma y la pantalla lo muestran ("Movimientos"); el ADR 0026 define `tool { tool, args, why }` sin `label`. El parseo lo deja en `""`. | La lista "Herramientas" saldría sin rótulo. | O el agente emite `label`, o la plataforma lo deriva del catálogo. |
| D4 | `args` de `tool` y `action`, y `action.executable`: la plataforma **no los lee** (el contrato de la plataforma no tiene `args`; `executable` queda fijo en no ejecutable en su dominio). | Seguro (nada se ejecuta), pero `args` solo sirve a la validación de agent-core. | Informativo |
| D5 | Máximo y duplicados: la plataforma conserva a lo sumo 8 sugerencias, **un solo `reply` y un solo `escalate`**, y descarta en silencio el resto. El ADR 0026 deja el máximo abierto (Abierto 2) y no prohíbe dos `reply`. | Un segundo borrador se perdería sin aviso. | Abierto 2 |
| D6 | Hechos de escalamiento: el ADR 0026 §5 y la plataforma ADR 0005 §4 citan "tercer contacto en 7 días". `build_input` **no envía** ese dato (solo `sla`, `prioridad`, `motivo_llegada`, `espera…`). | La regla de tercer contacto no puede evaluarse. | Plataforma: añadir el conteo; o quitar la regla |
| D7 | Tiempo: la plataforma espera como máximo 60 s (`DEFAULT_TIMEOUT_SECONDS`) y promete 5-10 s; el presupuesto del agente (provisional) es de 60 000 ms. | Un run que agote su presupuesto agota también el timeout de la plataforma. | Abierto 4 |
| D8 | Catálogo de herramientas: la plataforma dice que `tool` es "una tool del catálogo del copiloto" (Q&A, `copiloto-asesor`: 5 lecturas). El YAML usa 3 (`leer_movimientos`, `leer_productos`, `leer_pqr_cliente`), un subconjunto. | Seguro: *Usar* siempre cae en una tool que el copiloto Q&A tiene. | Informativo |
| D10 | Límites que la plataforma aplica en silencio: `evidence` ≤ 5 de ≤ 300 caracteres, `summary` ≤ 300, `why` y `motive_draft` ≤ 500, `citations` ≤ 10 de ≤ 120, `tool`/`reason_code` ≤ 120, `language` ≤ 8; descarta `tool`/`action` con campos vacíos y trunca el `texto` de cada turno a 1000 (el `input_schema` no limita longitud). | Texto largo del agente se recorta sin aviso. | Abiertos 2 y 3 |
| D9 | ADR 0026 §3 pide "versión menor de `SCHEMA_VERSION`"; en esta rama `SCHEMA_VERSION` ya es 1.4.0 por los slots `list`. | La salida sería 1.5.0 (o se agrupa si 1.4.0 no se publicó aún). | Usuario |

Coinciden: nombres snake_case en el HTTP de agent-core (`reason_code`, `motive_draft`); `suggestions` leído de la respuesta de `POST /v1/runs`; lista vacía como resultado normal (`status: none`); `reason_code` con los prefijos `rule:`/`policy:`/`interrupt:` o de M0 (la plataforma solo exige un texto no vacío ≤ 120: acepta más de lo que agent-core emitirá); `escalate` solo recomienda; `action` no ejecutable.

## 3. PII y texto no confiable (verificado con M7 real)
- Todo slot entra al modelo envuelto como `untrusted_text` (decisión D8 de M7, no la discrepancia D8 de arriba) y la PII detectada en el texto (tarjeta, correo, cédula, celular de los casos sintéticos) se sustituye por tokens del vault: probado en `test_pii_in_the_customer_text_…`, con control negativo (`full` sí la contiene y la comprobación la detecta).
- Un cierre falso `</datos_no_confiables>` dentro de un turno queda escapado.
- **No hace falta** añadir `turnos[*].texto` al catálogo de campos para que el texto del cliente sea no confiable (el ADR 0026 §6 lo pide): ya lo cubre la regla D8 de M7 por ser slot. Sí hará falta cuando una salida de herramienta o del nodo `suggest` lleve texto.
- No se verificó: que el **modelo** no repita PII en su salida. El nodo `suggest` deberá pasar por M8 (`find_clear_pii`); hoy solo se prueba que la salida esperada no la repite y que el chequeo detecta una que sí.

- **Hueco conocido de M7 (no corregido aquí):** el detector no ve un PAN con separadores U+200B, `/` o `_`, dígitos árabe-índicos, un celular con paréntesis ni un correo con `@` separado; esas variantes llegan en claro a la vista `model` (hallazgo del revisor; `test_known_gap_…` fija uno). Los casos sintéticos solo cubren formatos canónicos, así que este cambio **no** demuestra que no haya fugas por formato.
- El caso `escalar-por-fraude` usa `rule:fraude-con-supervisor`, una regla que **no existe**; `escalamiento-disputa-monto` mide monto y no aplica. La política real es parte de B5.
- Reglas del validador de la prueba que el ADR no fija (copiadas de la plataforma o supuestas): `text` ≤ 4000, `language` ∈ locales del agente, `evidence` ≥ 1, `why` no vacío y `ACTIONS_ALLOWED = {radicar_pqr@1}` (Abierto 6).
- `assistant_session_id`: la plataforma podría enviarlo en una rama que no pude leer; D2 vale para su código principal.
