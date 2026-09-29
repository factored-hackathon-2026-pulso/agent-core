# ADR 0004 — Flows deterministas con comprensión tipada (estilo CALM)

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por los hallazgos A1, I5, U1, U2 y U10 de la revisión externa.
- Unidad: 1 · Motor de decisión
- Spec: `docs/specs/2026-09-28-motor-de-decision-design.md`

## Contexto
- El reto exige un ciclo Understand → Decide → Act → Verify → Escalate auditable, permisos fuera del prompt y reproducibilidad para comparar versiones.
- Los agentes deben ser datos versionados que puedan editar personas, el constructor y la auto-mejora.

## Decisión
- **Modelo de ejecución:** un grafo dirigido de nodos tipados, con subflows y manejadores globales, interpretado por un motor propio.
- **Rol de los modelos:** el LLM y JEV solo emiten comandos o valores tipados. La ruta la decide el grafo.
- **Catálogo de nodos cerrado:** decide, rule, collect, tool, confirm, verify, respond, agent, escalate, subflow y end.
- **Válvula de flexibilidad:** el nodo `agent` es un ReAct acotado, limitado a tools de lectura y a N pasos.
- **Reglas:** se expresan en un subconjunto de JSON Logic, sobre hechos verificados.

### Enmiendas (revisión externa)
- **Corte MVP (A1).** La demo implementa un solo flow activo. La pila de flows, `subflow` y el nodo `agent` quedan como diseño de producción.
- **Intenciones múltiples (U1).**
  - Cada flow declara `priority`.
  - Understand devuelve `flow` (calibrado) y `additional_flows` (sin calibrar).
  - Arranca el de mayor prioridad y el resto va a `pending_intents`: primero por prioridad y, en empate, por orden de mención.
  - Una intención pendiente solo arranca con `affirm`.
- **Interrupciones declaradas (U2).**
  - La release declara `interrupts [{id, priority, action}]`.
  - Understand las emite como comando calibrado **por recall**, con una `policy` protegida opcional como segunda señal: basta cualquiera de las dos.
  - El motor no conoce conceptos como "fraude".
- **Validación en dos niveles (I5, U10).**
  - G0 valida el flow aislado.
  - El gate de release (unidad 2) valida:
    - un solo dueño por intención;
    - `tools_allowed`;
    - aprobaciones de políticas;
    - interrupciones;
    - aciclicidad de subflows.
  - En runtime, un tope de profundidad actúa como defensa adicional.

## Alternativas
| Alternativa | Por qué se descarta |
|---|---|
| **LangGraph como runtime** | Su valor principal (checkpointing durable, interrupciones) apenas se necesita, porque un turno es una request. Lo que sí importa (grafo como dato, diff por nodo, replay) habría que construirlo encima de todas formas. |
| **Agente ReAct libre con guardrails** | Rutas no reproducibles, difíciles de auditar y de comparar entre versiones. |
| **Árbol de decisión** | No representa ciclos de aclaración ni reintentos. |
| **Statechart completo** | Paralelismo e historia son sobre-ingeniería para este alcance. |

## Consecuencias
- Hay que mantener un intérprete propio. La estimación original de 600–900 líneas era optimista: con las correcciones de la revisión son **1.500–2.000 LOC** con pruebas. Se mitiga con el corte MVP, la validación estática y el replay en CI.
- Los flows son rígidos ante lo imprevisto. Se mitiga con la abstención y, en producción, con el nodo `agent`.
- Si en producción aparecen procesos de días, se añade un motor durable (Temporal) debajo del intérprete, sin cambiar el modelo de flows.

## Fuentes
- https://rasa.com/docs/learn/concepts/calm/
- https://rasa.com/docs/pro/customize/command-generator/
- https://www.langchain.com/resources/langgraph-vs-temporal
- https://zylos.ai/research/2026-04-24-durable-execution-agent-runtimes/
