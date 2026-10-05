# Lista de verificación de salida a producción de `agentcore serve`

- Fecha: 2026-10-05 · Alcance: `agent-core` y su imagen. La plataforma, la infra, el tool-service y el llm-gateway
  tienen sus propias listas; aquí solo aparecen como dependencias.
- Estado según `docs/serve-readiness.md` y la corrida en vivo del 2026-10-05 (datos sintéticos, sin clientes reales).
- Cada punto tiene un **criterio de hecho** que se puede comprobar, un dueño y un esfuerzo (S ≤ 1 día, M ≤ 1 semana,
  L > 1 semana). Estado: ✅ hecho · 🟡 parcial · ⬜ pendiente · ⛔ bloqueado por otro equipo.

## Niveles de salida

| Nivel | Qué permite | Qué exige |
|---|---|---|
| **A. Staging** | Equipo interno, datos sintéticos | Todos los puntos de A |
| **B. Piloto** | Pocos clientes reales, con supervisión humana | A + B |
| **C. General** | Todos los clientes | A + B + C |

Hoy: **A casi completo; B no; C no.**

## A. Staging

| # | Requisito | Criterio de hecho | Dueño | Esf. | Estado |
|---|---|---|---|---|---|
| A1 | `serve` en modo producción con las siete piezas reales | `serve mode=production` en el log; ninguna ruta `testing.` | agent-core | — | ✅ |
| A2 | Configuración solo por entorno, falla rápido | arranque sin variables sale con código 2 y nombra cada una | agent-core | — | ✅ |
| A3 | Migraciones automáticas y seguras | 6 instancias a la vez aplican el esquema una vez | agent-core | — | ✅ |
| A4 | Sondas | `/healthz` 200 con la base caída; `/readyz` 503 hasta que vuelva y 200 después | agent-core | — | ✅ |
| A5 | Arranque con la base caída | el proceso levanta y se recupera solo | agent-core | — | ✅ (#74) |
| A6 | Concurrencia dimensionada | 20 simultáneos sin errores, `AGENTCORE_DB_POOL_MAX` ≥ 2 × tope | agent-core / infra | — | ✅ (#75) |
| A7 | Imagen reproducible | amd64 y arm64, no root, por digest, `HEALTHCHECK` sano | agent-core | — | ✅ (arm64 sin probar en vivo) |
| A8 | `AGENTCORE_ALLOW_DOUBLES` prohibido en despliegues | la guarda estática de infra lo rechaza como a `ALLOW_DEMO` | infra | S | ⬜ |
| A9 | `sweep` y `relay` programados | `agentcore sweep --once` cada pocos minutos; `relay` como servicio con `AGENTCORE_EVENTS_TOPIC_ARN` | infra | S | ⬜ |
| A10 | Pool de Postgres dimensionado | `max_connections` ≥ instancias × 2 pools × `AGENTCORE_DB_POOL_MAX` (con margen) | infra | S | ⬜ |
| A11 | Claves de Terraform en uso | claves de identidad, staff y de huella/tokens generadas por infra; rotación probada una vez | infra | S | 🟡 |
| A12 | Imagen arm64 probada en vivo (si el host es Graviton) | el compose de referencia sube en arm64 y pasa el ciclo de disputas | agent-core | S | ⬜ |
| A13 | CI funcionando | los jobs de `ruff`, `mypy`, `lint-imports`, `contracts --check` y las suites corren en cada PR | infra / equipo | S | ⛔ (facturación) |

## B. Piloto (clientes reales, pocos, con supervisión)

| # | Requisito | Criterio de hecho | Dueño | Esf. | Estado |
|---|---|---|---|---|---|
| B1 | Calibración real | artefacto exportado por datos, con su `split_hash` y métricas; los umbrales por campo salen de él | datos | L | ⛔ |
| B2 | Clasificador real | artefacto `tfidf-logreg-v1` entrenado, versionado por hash de datos; sustituye al sintético | datos | L | ⛔ |
| B3 | Concesiones de campo reales | `AGENTCORE_AUTHZ_FIELD_GRANTS_FILE` aprobado por gobierno de datos; la prueba local deja de usar `--grant-synthetic-fields` | gobierno de datos | M | ⛔ |
| B4 | Catálogo de clasificación de campos real | el de data-pipeline más el overlay del motor, revisado; todo campo de la salida de las tools clasificado | datos / agent-core | M | 🟡 |
| B5 | `grant_active` contra la plataforma real | una delegación revocada deja de valer en ≤ el TTL de la caché (5 s) en una prueba en vivo | plataforma / agent-core | S | ⛔ |
| B6 | Evaluación estable | cada suite con `repetitions` ≥ 3 y margen de ruido; una candidata idéntica a la base pasa el gate en 5 corridas seguidas | motor de mejora | M | ⬜ |
| B7 | Suites base revisadas por el negocio | los 104 casos de Codex validados o reemplazados por casos con política real; los casos sin aserto (`expect: {}`) con su criterio | producto / motor de mejora | M | ⬜ |
| B8 | Copiloto utilizable | la suite `copiloto-asesor-suite` evalúa (sin `failed_infra`) con el modelo elegido y el prompt corregido (`kind` en el paso final) | motor de mejora | M | ⬜ |
| B9 | `consultas` sin defectos conocidos | los casos `negative-*-transferencia-*` pasan o se reescriben; consulta de un radicado probada contra datos reales | agent-core / producto | S | 🟡 (#76 arregla el flujo; falta reevaluar) |
| B10 | Errores de infraestructura como 503 | base caída o pool agotado responde 503 con `Retry-After`, no 500 `internal_error` | agent-core | S | ⬜ |
| B11 | Prueba de seguridad | inyección de prompts (los rulesets y los casos `protected-*`), aislamiento entre clientes con 20 simultáneos, ninguna PII en logs, eventos ni llamadas al modelo; informe firmado | seguridad / agent-core | M | ⬜ |
| B12 | Supervisión humana del piloto | todo `escalated` llega a una cola atendida; un humano revisa una muestra de runs resueltos a diario | operaciones | M | ⬜ |
| B13 | Presupuesto y límites | `AGENTCORE_DAILY_BUDGET_USD`, `AGENTCORE_RATE_*` y `AGENTCORE_MAX_INFLIGHT` fijados con los valores del piloto, no los de demo | agent-core / infra | S | ⬜ |
| B14 | Observabilidad conectada | trazas OTLP llegan a Langfuse desde el contenedor; alertas sobre `/readyz` 503, `escalated` y costo | infra | M | 🟡 |
| B15 | Plan de reversa | `registry` permite promover la release anterior a `prod`; ensayado en staging con un run abierto | agent-core / operaciones | S | ⬜ |
| B16 | Respaldo y restauración de Postgres | restauración ensayada; el log de auditoría encadenado verificado (`check_chain`) tras restaurar | infra | M | ⬜ |

## C. General

| # | Requisito | Criterio de hecho | Dueño | Esf. | Estado |
|---|---|---|---|---|---|
| C1 | Jueces cableados | las métricas `judge` se miden en `evaluate` (mimo-v2.6-pro); sus notas aparecen en el reporte | agent-core | M | ⬜ |
| C2 | Replay de auditoría reproducible | el replay `audit` de un run real da `match`, o su exclusión está aprobada por auditoría con las tres causas conocidas (reloj, `result_fp`, borrador con citas) | agent-core | L | ⬜ |
| C3 | Replay por `run_id` y sesiones con transferencia | `agentcore replay <run_id> --mode audit` funciona desde el almacén | agent-core | M | ⬜ |
| C4 | Emparejamiento de cargos robusto | `match-cargo` no depende de las últimas 50 transacciones ni de que la frase mencione el comercio o el monto; decisión de producto documentada | producto / agent-core | M | ⬜ |
| C5 | Prueba de carga sostenida | la carga esperada de producción durante ≥ 1 h sin errores, con p95 acordado; el turno de 11 s (JEV + clasificador + LLM) revisado contra el objetivo | agent-core / infra | M | ⬜ |
| C6 | Caos repetido en el entorno real | los cinco escenarios (Postgres reiniciado y caído, `serve` reiniciado a mitad de un run, arranque con la base caída, variables faltantes) pasan en el EC2 real | infra / agent-core | S | ⬜ |
| C7 | Separar la evaluación de producción | el transcript de evaluación no se mezcla con el de producción; la base `agent_eval` con su rol propio | agent-core | S | ⬜ |
| C8 | Procedimiento de rotación de claves | rotación de las claves de identidad y de huella/tokens sin reiniciar, ensayada y documentada con responsables | infra / seguridad | S | 🟡 |
| C9 | Plazo de `/readyz` acotado | con una dependencia caída responde en menos de 500 ms (hoy ~1,2 s) | agent-core | S | ⬜ |
| C10 | Contrato con el motor de mejora cerrado | los `source` de las tools acordados en `ASK_tool-alignment.md`; el motor nunca aprueba, publica ni promueve (probado en `serve`) | motor de mejora / agent-core | S | 🟡 |

## Orden sugerido

1. **Esta semana (S, sin bloqueos):** A8, A9, A10, A12, B10, B13, B15, C7, C9.
2. **Mientras llegan los datos:** B6, B7, B8, B9 (reevaluar con #76) y B11; cada uno desbloquea al siguiente.
3. **Bloqueantes de piloto, fuera de este equipo:** B1, B2, B3, B5 (datos, gobierno de datos y plataforma). Sin
   ellos no hay piloto, aunque lo demás esté listo.
4. **Después del piloto:** C1 a C6.

## Cómo se cierra un punto

Un punto pasa a ✅ cuando su criterio se ejecutó (no solo se leyó) y el resultado quedó en un PR o en
`docs/serve-readiness.md`. Las puertas locales (`ruff`, `mypy`, `lint-imports`, `contracts --check` y las pruebas
afectadas) valen mientras la CI de GitHub no corra (A13).
