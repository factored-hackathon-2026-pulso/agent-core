# ADR 0018 — Propuestas de cambio y gate de publicación

- Estado: aceptado (2026-09-29). **Enmendado por el ADR 0020** (2026-09-30): los puntos 4 (gate con una sola métrica principal; historia) y 9 (`eval_suite`) se reemplazan por métricas declaradas por agente, N métricas `gate` y doble vara. La enmienda de entrega (2026-09-30), punto 2 (métrica principal = tasa de pase), queda reemplazada por el gate con doble vara del ADR 0020, integrado en el registry el 2026-10-01
- Unidad: 2 · Entidades, registro y versionado (contrato con la unidad 6)
- Spec: `docs/specs/2026-09-29-registry-design.md`
- Relacionados: ADR 0007 (escrituras y `verify`), ADR 0009 (políticas protegidas), ADR 0015 (conocimiento), ADR 0017 (Postgres como fuente de verdad)

## Contexto
- Quienes más usarán el registry son un agente constructor, manual o autónomo, y un detector automático de casos. Construyen, iteran y evalúan agentes, flows, modelos de decisión y demás entidades.
- Las personas no técnicas aprueban y publican desde la plataforma, no en un PR.
- Un agente autónomo que puede publicar es el riesgo principal: un error suyo llegaría a producción sin freno.
- Cambiar una entidad puede empeorar la calidad o romper un invariante (fugas de PII, afirmaciones de éxito sin verificar) sin que el flow deje de validar estáticamente.
- El motor exige releases inmutables con referencias exactas, y la plataforma debe mostrar qué versión corrió en cada traza.

## Decisión
1. **Toda modificación es una propuesta** con un único ciclo: `draft → validated → candidate → evaluated → approved → published`, con `rejected`, `abandoned` y `stale` como estados laterales. Es igual para el constructor conversacional, el detector y las personas; solo cambia el campo `origen`.
2. **Congelar produce una candidata identificada por hash.** Las evaluaciones y aprobaciones quedan atadas a ese hash; cualquier edición crea otra candidata e invalida las anteriores.
3. **La unidad publicable es la entidad versionada, y una release agrupa las versiones exactas.** Cada entidad tiene semver y su propia evaluación. La traza guarda solo el `release_id`, y la plataforma lo expande a la lista de versiones, sus docs y el diff frente a la release anterior.
4. **Gate de evaluación:**
   - ningún guardarraíl de seguridad o invariante empeora (tolerancia cero);
   - (original; reemplazado por el ADR 0020) la métrica principal es mayor o igual que la vigente, dentro del margen de ruido declarado por la suite. Hoy: cada métrica `gate` del agente, por separado y con su propio margen de ruido;
   - sin release base se compara contra un piso mínimo;
   - ambas mediciones sobre la misma suite congelada.
5. **Sin excepción manual del gate.** Un gate fallido devuelve la propuesta a `draft`; ni un aprobador puede publicar sobre él.
6. **Solo aprobación humana manual.** El constructor y el detector pueden avanzar hasta `evaluated`, pero nunca aprueban, publican, promueven ni revocan. Se verifica por rol en el servidor, no por el prompt del agente. La aprobación automática y la autoaprobación por el mismo humano quedan como temas abiertos.
7. **Publicar es una transacción.** Publicar inserta las versiones, crea la release y apunta `staging`. Promover a `prod` es un paso aparte. Revocar es inmediato y sin gate; volver a una release anterior no necesita gate nuevo.
8. **Propuesta obsoleta:** si la release base cambió, la propuesta pasa a `stale` y hay que reconstruirla y reevaluarla contra la nueva base.
9. **El registry define lo que exige de la evaluación** (`EvalPort` y `eval_suite`), porque la unidad 6 aún no tiene spec.
10. **Presupuestos por propuesta** (borradores, evaluaciones, costo) para cortar bucles del agente autónomo.

## Alternativas
| Alternativa | Por qué se descarta |
|---|---|
| **Puntaje compuesto único** | Un promedio puede esconder una regresión de seguridad detrás de una mejora en otra métrica. |
| **Sin regresión en todas las métricas** | Muy rígido con datos ruidosos: bloquea mejoras legítimas y frena la iteración. |
| **Que el agente publique en no-producción sin aprobación** | Deja llegar propuestas no revisadas a un entorno que puede servir a usuarios; se prefiere un aprobador humano en cada publicación mientras la aprobación automática no esté especificada. |
| **Autonomía configurable por tipo de entidad** | Es una spec mayor, con más superficie para errores y sin necesidad demostrada para el MVP. |
| **Solo releases completas** | Se pierde la evaluación y el historial por entidad, que el constructor necesita para iterar y para el gate. |
| **Fijar cada entidad en el run, sin release** | Rompe el invariante de M0 (release inmutable fijada al iniciar el run) y obligaría a reabrir M2, M4 y M9. |
| **Excepción manual del gate para aprobadores** | Un humano con prisa es justo el hueco que el gate quiere cerrar. |

## Consecuencias
- La auto-mejora deja de abrir PRs y escribe propuestas por la API del registry, con `origen = auto_detect`.
- Cada agente necesita una `eval_suite` con los umbrales (margen de ruido y piso) de sus métricas `gate` y `guardrail` (ADR 0020; la versión original pedía además una métrica principal única). Si falta, no se puede evaluar ni publicar por el gate.
- La unidad 6 debe implementar `EvalPort` (`run(request: EvalRequest) → reporte`; la forma original era `run(suite, release)`).
- Un aprobador sin rol de dueño no puede aprobar el cambio de una política protegida (ADR 0009); las páginas de conocimiento solo las aprueba un humano.
- Queda abierto el salto semver, los topes del agente autónomo y la autoaprobación.

## Enmienda 2026-09-30 (alcance de entrega)
Acordada al recortar el registry a la entrega del 05/10 (spec rev. 2, §0 y §16). Reemplaza lo que contradiga a las secciones anteriores:

1. **Evaluación por agente, no por entidad.** Una mejora puede venir de un prompt, del modelo de decisión, de un flow o de varias entidades a la vez. La `eval_suite` se liga al agente y consiste en escenarios corridos con el motor real, el LLM real y acciones contra un **sandbox** aislado (`SandboxPort`, lo implementa la unidad 3; `LocalSandbox` como respaldo). Las suites por entidad pasan a fase 2.
2. **Métrica principal determinista:** la proporción de corridas cuyo resultado cumple el `expect` del escenario, calificada desde los eventos del motor. Un juez LLM es opcional e informativo, y no entra al veredicto. (**Reemplazado** por el ADR 0020: ya no hay métrica principal única, sino N métricas `gate` declaradas por el agente, evaluadas por separado con doble vara; la corrida sigue calificándose desde los eventos y el juez sigue siendo informativo. El ADR 0020 prevalece en lo que contradiga este punto.)
3. **El registry implementa el evaluador determinista** (`ScenarioEvaluator`). Los escenarios de negocio de la demo y el juez los hace otra persona del equipo.
4. **Una persona puede recorrer el ciclo completo** y aprobar su propia propuesta: los roles `constructor` y `aprobador` se acumulan. La barrera dura es el actor: un principal no humano (el constructor o el detector, que autentican como `builder` con credencial propia; un principal es humano solo con `attrs.actor = "human"` firmado) nunca aprueba, publica, promueve ni revoca, aunque tenga el rol. Esto cierra el tema abierto de la autoaprobación.
5. **Ciclo simplificado:** `validated` se absorbe en `freeze`, `reject` devuelve a `draft`, y `stale` se detecta al publicar (la propuesta vuelve a `draft` con la base nueva). Los estados `stale` y `abandoned` como estados propios pasan a fase 2.
6. **La candidata se evalúa en memoria** (`SnapshotRegistry`). Las versiones no publicadas nunca entran en las tablas publicadas, así que el motor no puede verlas por construcción.
7. **Salto semver:** lo fija quien propone; `validate` exige que sea mayor que el de la base.
8. Políticas protegidas, aprobación humana de páginas, presupuestos del agente autónomo y escáner de secretos quedan como diseño de fase 2 (spec §16).
