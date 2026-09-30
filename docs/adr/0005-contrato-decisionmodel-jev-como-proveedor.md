# ADR 0005 — Contrato DecisionModel; JEV como proveedor

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por los hallazgos I2 e I6 de la revisión externa, y por el tema #5 de la auto-revisión (JEV fuera de la detección de idioma). Enmendado el 2026-09-29 por el resultado de la prueba de humo de JEV: dos llamadas por turno en Understand, versión de JEV fijada y JEV descartado como segunda opinión de idioma (ver "Enmienda 2026-09-29").
- Unidad: 1 · Motor de decisión

## Contexto
- JEV (TypeSafe, un "System One model") devuelve valores tipados con probabilidades calibradas, a 70–500 ms y con costo mínimo, según el proveedor.
- El equipo tiene API key.
- Está en acceso anticipado. No se ha verificado qué idiomas soporta ni dónde procesa los datos, y sus benchmarks son del propio proveedor.
- El reto exige comparar un componente aprendido contra un baseline.

## Decisión
- **Contrato:** toda decisión tipada del sistema pasa por el contrato `DecisionModel`, que incluye `output_schema`, `input_view` y una cadena de proveedores con calibración y umbrales propios.
- **Proveedores disponibles:** `jev`, `classifier`, `llm_structured` y `rule`.
- **Papel de JEV:** es el proveedor preferido, pero nunca una dependencia directa del flow.
- **Calibración:** siempre propia, calculada por proveedor e idioma. Los umbrales salen de corridas de calibración y no se editan a mano.
- **Campos calibrados (enmienda I2).**
  - La salida es un struct cerrado.
  - `calibrated_fields` declara qué campos (enums) tienen probabilidad y umbral. Solo esos campos pueden ramificar un flow.
  - Los campos no calibrados nunca se ejecutan directamente:
    - los slots entran como `claimed`;
    - las intenciones adicionales van a pendientes;
    - los tokens elegidos solo alimentan tools `compute`.
- **Tabla de umbrales (enmienda I6).**
  - `thresholds_from` apunta a una corrida de calibración que produce una tabla con clave `(campo calibrado, valor, proveedor, idioma) → umbral`, además del hash del split.
  - Una combinación ausente tiene umbral 1.0, así que nunca se toma y cae en `low_confidence`.
- **Understand es un DecisionModel más, con una llamada a JEV por turno para los campos calibrados** (enmendado el 2026-09-29: los slots se extraen en una segunda llamada, ver "Enmienda 2026-09-29").
  - Campos calibrados: `command`, `flow` e `interrupt`.
  - Campos sin calibrar: `slots` y `additional_flows`.
  - `affirm` y `deny` alimentan los resultados del nodo `confirm`.
- **Detección de idioma (auto-revisión #5):** no es un `DecisionModel` ni usa JEV. La resuelve un detector local en las guardas, antes de Understand (ADR 0012), para no añadir una llamada a modelo por turno. La prueba de humo lo confirmó: JEV no fue claramente mejor en mensajes cortos y el detector local cuesta ~1 ms frente a ~360 ms.

## Alternativas (I2)
- **Dos modelos por turno (clasificador de comando + extractor de slots):** separación más limpia, pero duplica la latencia y el costo por turno.

## Consecuencias
- Cambiar de proveedor es un cambio de datos, no de código.
- La comparación contra el baseline se obtiene corriendo el mismo modelo con otro proveedor.
- La auto-mejora puede proponer cambios de proveedor o de umbral como tipos de cambio conocidos.
- Hay un costo de calibración y etiquetado, que absorbe el científico de datos.

## Enmienda 2026-09-29 — Resultado de la prueba de humo y llamadas por turno

- **Qué hace JEV:** solo preguntas tipadas (`choice`, `noul`, `score`) sobre la vista `model`. No extrae valores libres (montos, fechas, identificadores). En Understand responde `command`, `flow` e `interrupt` (`choice`).
- **Slots por `llm_structured`, en una segunda llamada.** Se reemplaza "una sola llamada por turno" por: **una llamada a JEV por turno** (campos calibrados) **más, solo cuando `command = start_flow`, una llamada a `llm_structured`** para los slots, que salen como `claimed`, sin umbral. Con cualquier otro comando el turno sigue teniendo una sola llamada.
  - Costo: la latencia del turno con `start_flow` suma la del LLM a los ~375 ms (p50) de JEV. El límite `max_model_calls_per_turn` de la release debe admitir 2 (los agentes de prueba lo fijan en 3).
  - Alternativa descartada: JEV para los campos calibrados y el LLM siempre. Duplicaría costo y latencia en turnos que no inician flow.
- **Versión fijada:** el `DecisionModelDef` usa el ID versionado (`jev-1.13.0`), no el alias `jev-latest`. La calibración es por versión: un cambio de versión exige recalibrar (`model_version` queda en `decision_made`).
- **Idioma:** JEV queda descartado como segunda opinión de `undetermined` (no cumplió el criterio de la verificación de abajo).
- **Estado de implementación:** el proveedor y el transporte de JEV están hechos; la segunda llamada a `llm_structured` para slots está implementada en `UnderstandService` (M5, vía `UnderstandContext.slots_model_ref`). Falta en M4 resolver ese modelo desde la release (m04 §14).
- **Resultados:** `docs/informes/2026-09-29-m5-jev-humo.md`. Con `jev-1.13.0` y 110 mensajes sintéticos: `command` 96% (ES) y 94% (PT), `flow` 100%, p50/p95 de 375/453 ms, sin errores ni 429. Con salvedades de muestra y de calibración (aún faltan datos etiquetados reales).

## Verificación (2026-09-29: ejecutada; ver la enmienda de arriba)
- Correr una prueba de humo con 50 casos en ES y 50 en PT sobre JEV, midiendo precisión, latencia y cobertura de idioma.
- **(#5)** En la misma prueba, correr lingua-py sobre los mismos casos y agregar un subconjunto de mensajes de 1 a 3 palabras y de portuñol. Medir para ambos: precisión de idioma por largo, tasa de indeterminados y latencia p50/p95 desde nuestro entorno (no la publicada por el proveedor). Si JEV resulta claramente mejor en mensajes cortos, se evalúa como segunda opinión solo en turnos `undetermined`.

## Fuentes
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
