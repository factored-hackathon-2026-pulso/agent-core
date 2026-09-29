# Núcleo de agentes — descomposición y mapa de repos (aprobado 2026-09-28)

## Calendario
- Entrega del proyecto: 2026-10-05.
- Núcleo congelado (asumido): jueves 2026-10-02 en la noche. Diseño hasta el 29/09 y construcción del 30/09 al 02/10.
- Del 03 al 05/10: auto-mejora, integración, eval, slides y video. Recomendación: la auto-mejora de la demo cubre un solo tipo de propuesta de punta a punta.

## Principio aprobado
Cada sistema recibe entradas y produce salidas por contratos explícitos, y no conoce los internos de los demás. Esta sesión se concentra en `agent-core` y `agent-registry`; los demás sistemas son trabajo de otras personas y aquí solo se definen sus interfaces con el núcleo.

## Unidades de diseño (en orden)
| # | Unidad | Subsistemas | Profundidad |
|---|---|---|---|
| 0 | Fundamentos transversales (stack, repos, convenciones de trazas) | parte de 10 | ADR cortos |
| 1 | Motor de decisión | 1 orquestación + 2 JEV | spec completa |
| 2 | Entidades, registro y versionado | 3 + 9 | spec completa |
| 3 | Tools y gobernanza | 4 + 5 | spec completa |
| 4 | Trazas de auditoría y eventos | 10 | spec completa |
| 5 | LLM gateway | 6 | spec corta + ADR |
| 6 | Evaluación y métrica por entidad | 11 | spec completa |
| 7 | Memoria y conocimiento (interfaces de consumo) | 7 + 8 | ADR + interfaz |

El constructor para personas no técnicas es un cliente del registro y queda fuera del diseño del núcleo.

## Repos del núcleo
- **agent-core** (código): motor de decisión, ejecutor de tools con punto de control de políticas, LLM gateway, trazas y harness de eval, más un SDK delgado.
  - Expone: API de runtime, API de registro, API de eval y el esquema de eventos.
- **agent-registry** (contenido versionado): agentes, grafos, configuraciones JEV, definiciones de tools, prompts y plantillas ES/PT, y suites de eval.
  - Se valida en CI contra los esquemas de `agent-core`.
  - Es el repo donde la auto-mejora abre sus PRs.

> **Enmienda 2026-09-29 (ADR 0017, ADR 0018):** `agent-registry` deja de ser un repo git con CI propio. El registry es el módulo `agent_core.registry`, con Postgres como única fuente de verdad, propuestas de cambio aprobadas en la plataforma y un gate de evaluación al publicar. El YAML queda como formato de importación y exportación. Detalle en `docs/specs/2026-09-29-registry-design.md`.

## Interfaces externas (solo contrato)
- **Datos/ETL:** read-models por cliente y un catálogo de clasificación de campos.
- **Identidad:** sesión de prueba con claims firmados.
- **Conocimiento:** `leer` y `buscar`.
- **Auto-mejora:** lee eventos y evals, y escribe PRs en `agent-registry`.
- **Apps** (chat, consola, constructor): consumen la API de runtime y la API de registro.
- **Repo de entrega público:** `factored-hackathon-2026`. Pendiente definir cómo agrega los demás repos (submódulos o copia).

## Riesgos registrados
- Alcance frente a tiempo.
- El chain-of-thought no sirve como artefacto de auditoría: se trazan decisiones estructuradas.
- Agnóstico al negocio no significa "sin invariantes": el núcleo impone los mecanismos (confirmación, read-back, customer_id tomado de la sesión, separación de crédito).
- El enforcement de políticas ocurre en runtime, no en la ETL.
- JEV no está verificado (acceso anticipado, sin datos en PT).
- El diccionario de datos trae credenciales de AWS: nunca deben ir al repo público ni a requests a modelos externos.
