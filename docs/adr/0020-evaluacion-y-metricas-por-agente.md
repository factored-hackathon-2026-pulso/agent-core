# ADR 0020 — Evaluación y métricas por agente

- Estado: aceptado (2026-09-30). **Enmienda** los puntos 4 y 9 del ADR 0018 y la regla de datos del registry §7 (esta última solo para fuentes `scripted`; la fuente `dataset` queda desactivada)
- Unidad: 2 · Entidades, registro y versionado (contrato con la unidad 6)
- Spec: `docs/specs/2026-09-30-evaluacion-y-metricas-design.md`
- Relacionados: ADR 0003 (observabilidad), 0008 (vistas), 0017 (registry en Postgres), 0018 (gate de publicación), 0019 (agentes internos)

## Contexto
- El ADR 0018 exige evaluar antes de publicar, con guardarraíles y una sola métrica principal por suite, pero deja las métricas fuera de la definición del agente.
- Cada agente necesita métricas propias (el constructor, por ejemplo, oportunidades detectadas, mejoras propuestas y mejoras promovidas) y las mismas definiciones deben servir al gate y al monitoreo en producción.
- El constructor autónomo debe poder armar una release de punta a punta, incluidas sus evaluaciones y métricas, lo que le permitiría rebajar la vara con que lo miden.
- Se quiere probar con datasets reales a futuro, pero la regla 5 de CLAUDE.md y el registry §7 permiten solo datos sintéticos.

## Decisión
1. **Cada agente declara sus métricas** en `Agent.metrics`. Una misma definición se usa en el gate (sobre los escenarios) y en producción (sobre eventos reales).
2. **DSL declarativo restringido**, con catálogo cerrado de eventos (`engine.*` y `registry.*`), validado por M1 y compilable a SQL. No hay SQL libre en una entidad.
3. **Tres roles:** `guardrail` (tolerancia cero), `gate` (no peor que la base dentro del ruido, y sobre el piso) y `monitor` (no bloquea). Se aplica a todas las métricas `gate` por separado, sin puntaje compuesto; **reemplaza la métrica principal única** del ADR 0018.
4. **La `eval_suite` es una entidad del registry** con semver propio. La release del motor no cambia; la tabla `releases` del registry guarda las suites usadas.
5. **Doble vara.** La candidata debe pasar la suite y las definiciones de la release base sin modificar, y además su propia suite nueva. Aflojar la vara (borrar o cambiar algo existente) se marca `yardstick_loosened`, lo aprueba una persona por separado y solo afecta a propuestas posteriores.
6. **El constructor puede redactar métricas, escenarios y umbrales**, pero los guardarraíles de plataforma (PII, éxito sin `verify`, escrituras sin verificar, citas no aprobadas) son universales y ninguna propuesta puede editarlos. Se verifica por rol en el servidor.
7. **Fuente de escenarios intercambiable:** `scripted` (sintética) habilitada; `dataset` diseñada pero desactivada hasta un ADR aparte firmado por el dueño de los datos. Los datos reales no viven en el repo ni en el registry.
8. **Calificación mixta:** métricas deterministas sobre eventos, y juez LLM opcional con perfil fijo y versionado y margen de ruido propio. Cambiar el perfil o la rúbrica cuenta como cambiar la vara.
9. **Producción:** se calcula, se muestra y se alerta a personas. Sin reversión ni promoción automática; revocar sigue siendo del aprobador.

## Alternativas
| Alternativa | Por qué se descarta |
|---|---|
| **Métrica solo en la `eval_suite`** | Duplica definiciones con la analítica y pueden divergir; el número del gate y el del dashboard dejan de significar lo mismo. |
| **Métricas del agente para monitoreo y otras en la suite para el gate** | El mismo concepto acaba con dos definiciones. |
| **SQL crudo contra vistas permitidas** | Exige validar un parser de SQL y acotar costo; abre riesgo de fuga de PII por uniones; acopla a Postgres y complica exportar a un harness. Un agente autónomo escribiendo SQL de producción es una superficie grande. |
| **Catálogo fijo de métricas parametrizables** | No cubre las métricas específicas de cada agente. |
| **Constructor append-only o sin acceso a evaluación** | Impide que arme una release de punta a punta y que convierta un caso real fallido en un escenario de regresión. |
| **Constructor con acceso libre y aprobación humana sobre el diff** | Depende de que el aprobador detecte un umbral cambiado dentro de un diff grande: el hueco que el ADR 0018 ya rechazó. |
| **Suite dentro de la release** | El motor tendría que ignorar otro tipo de entidad en runtime sin ganar nada. |
| **Servicio de evals con su propia base de datos** | Rompe la fuente única (ADR 0017) y abre el problema de consistencia entre dos stores. |
| **Juez LLM como calificador principal** | La vara depende de otro modelo; cambiar el juez mueve todos los números. |
| **Solo aserciones deterministas** | No mide calidad de lenguaje ni la de las propuestas del constructor. |
| **Puntaje compuesto con pesos** | Ya descartado en el ADR 0018: un promedio esconde una regresión. |
| **Habilitar datasets reales ya, con tokenización** | Exige enmendar la regla 5 y el ADR 0008 con firma del dueño de los datos, que no está decidida. |
| **Reversión automática por métricas de producción** | Contradice que solo un humano promueve o revoca y puede producir oscilaciones. |

## Consecuencias
- **Cambio de interfaz para todos los módulos:** `Agent.metrics` y el catálogo de eventos en M0; subir `SCHEMA_VERSION` y regenerar `contracts/` (regla 7 de CLAUDE.md). El motor ignora `metrics` en runtime.
- M1 gana reglas de validación del DSL.
- El registry gana la entidad `eval_suite`, el nuevo gate, `eval_suite_refs` en `releases` y el elemento de aprobación `yardstick_loosened`.
- El evaluador en memoria (unidad 6) y el compilador a SQL (analítica, tema #11) deben producir el mismo valor sobre los mismos eventos; se verifica con una prueba de contrato.
- Si una métrica obsoleta bloquea un cambio legítimo hacen falta dos propuestas (primero el aflojamiento, luego el cambio).
- **Riesgo aceptado:** en un agente nuevo el `floor` lo fija el propio constructor. Se compensa con la revisión humana obligatoria de la suite como elemento aparte.
- **Abiertos:** conector de datasets reales, catálogo inicial de eventos, si los eventos del registry y del motor comparten tabla, valores por defecto de `floor`, ruido y repeticiones, y gestión del juez LLM. Ver §13 del spec.
