# ADR 0019 — Agentes internos: copiloto del asesor y agente constructor

- Estado: aceptado como **diseño**; los agentes y sus piezas de motor **no se construyen todavía** (2026-09-30)
- Unidad: 1 · Motor de decisión (con dependencias de la unidad 2, registry)
- Amplía: ADR 0004 (nodo `agent`), ADR 0006 (principales y agentes internos), ADR 0007 (protocolo de escritura)
- Spec del nodo y de las reglas: `docs/specs/motor/m02-interprete.md` §3.7, `docs/specs/motor/m01-validacion-estatica.md` §3.13

## Contexto
- ADR 0006 anticipó agentes internos (copiloto del asesor, constructor de agentes) sobre los mismos nodos y garantías, pero no dijo cómo se abren al modelo ni cómo escribe un agente que nadie confirma turno a turno.
- Hoy el nodo `agent` existe en el esquema (M0) pero G0-01 lo rechaza y M2 no tiene manejador (`agent_core/interpreter/handlers/__init__.py`, `agent_core/flows/schema.py`).
- G0-16 prohíbe nodos que esperan en flows `task`, y G0-05 exige `confirm` antes de toda escritura: un agente `task` no puede escribir. El constructor por señal (p. ej. el detector automático, `origin: auto_detect` del registry) es un agente `task` que necesita escribir propuestas.
- El registry (unidad 2) es solo spec: el constructor propone cambios a borradores, y solo una persona aprueba, publica, promueve o revoca (registry §3 regla 6, §7).
- La revisión de este trabajo encontró además que el spec del registry (§7) llama `agent` al principal del constructor, mientras ADR 0006, M0, M9, la spec general y el código dicen `builder`.

## Decisión

1. **Los agentes internos usan un modelo mixto.** Un nodo `agent` abierto solo para **lectura y cálculo**; toda escritura sigue guionada. La salida del nodo `agent` entra como hechos marcados como generados por el modelo y **nunca** alimenta directamente una escritura, una `rule` ni un `verify` (regla G0-22).
2. **El constructor son dos agentes.** `Agent.mode` es único, así que hay un agente `conversational` (chat con una persona) y uno `task` (activado por una señal) que comparten flows, tools y prompts. El origen se registra en la propuesta (`builder_chat` o `auto_detect`).
3. **Principal del constructor: `builder`.** No se crea un `PrincipalType` nuevo. El registry §7 se corrige a "principal `builder` con rol `constructor`".
4. **El registry decide por su propia credencial, no por la de la persona.** El adaptador de tools del registry autentica con una identidad de servicio que tiene solo el rol `constructor`. El principal del run viaja como actor de auditoría, nunca como fuente de permisos. Una persona con rol aprobador que chatea con el constructor no le presta ese rol.
5. **Clase de riesgo `write_draft` (enmienda a ADR 0007).** Una tool cuyo efecto está confinado a un borrador del registry, que ninguna release publicada lee, se declara `write_draft`. Se ejecuta con `act → verify`, sin `confirm`. Conserva `idempotency_key`, `readback_by`, `verify` obligatorio y el evento de auditoría de cada escritura. El gate humano es la aprobación de la propuesta en el registry.
   - Solo la pueden usar agentes cuyo `invocable_by` sea un subconjunto de `{builder}` (chequeo de agente AG-02).
   - Todo lo que no sea borrador (`write_reversible`, `write_irreversible`, `money_movement`) conserva `confirm → act → verify` sin cambios.
6. **G0-16 no se relaja.** Un flow `task` sigue sin nodos que esperan; escribe con `write_draft` o no escribe.
7. **El copiloto del asesor es solo lectura y cálculo en su primera versión.** Principal `advisor`, run propio sobre el subject de su delegación (un traspaso entre asesores es un run nuevo, ADR 0006). No escribe, así que no usa `write_draft`.
8. **Un `builder` nunca obtiene datos de clientes**: ni subject, ni campos, ni parámetros vinculados. Es una prueba de contrato de `AuthzPort` (`tests/contracts/test_authz_contract.py`), no solo una convención del doble.

## Alternativas
- **Todo guionado, sin `agent` (opción A):** no exige cambios de motor, pero un copiloto abierto se abstendría a menudo y el contenido de un borrador tendría que salir de un `decide` con esquema estricto.
- **`agent` como cuerpo del agente (opción B):** mismo costo de motor que el modelo mixto, sin la regla que separa lo que el modelo lee de lo que se escribe.
- **D1-a, salida estructurada sin escritura del motor:** deja fuera del motor la escritura de propuestas; se descarta porque validar, congelar y evaluar son operaciones del ciclo de la propuesta.
- **D1-b, `await_approval` (ADR 0014) por escritura:** mantiene `confirm → act → verify`, pero duplica la aprobación humana del registry y contradice la autonomía con presupuestos que prevé el registry §7.
- **Crear `PrincipalType.agent`:** se descarta porque `agent` ya es un `EntityKind` y obliga a cambiar M0 y `contracts/` sin ganancia.

## Consecuencias
- **Riesgo aceptado:** `write_draft` debilita `confirm → act → verify` para esa clase. Se compensa con que los borradores no se sirven, con AG-02 y con que la aprobación de la propuesta es humana.
- **Dependencias del registry** (ver `docs/specs/2026-09-29-registry-design.md` §18): borradores reversibles e idempotentes; adaptador de `ToolExecutor` con credencial `constructor`; `forbidden_role` en `ProblemCode`; puerto de escritura de propuestas; validación de refs del nodo `agent` en el gate.
- **Cambios de interfaz al construirse** (avisar a todos los módulos): M0 (`AgentNodeConfig`, `RiskClass.write_draft`, evento `agent_step`), M1 (G0-01, G0-05, G0-07, G0-22, AG-02), M2 (`handle_agent`), M3 (ruta de acciones sin `confirm`), M5/M8, M11 (replay). Requieren regenerar `contracts/`.
- **Pendiente de decisión al construir** (secciones Abiertos de cada spec): topes del constructor autónomo (registry §17.4), `default_target_queue` opcional para agentes que nunca escalan, política de campos del copiloto (`purpose`), quién llena `open_questions` si el copiloto lo usa.
