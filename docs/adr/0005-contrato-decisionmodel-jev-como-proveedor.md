# ADR 0005 — Contrato DecisionModel; JEV como proveedor

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por los hallazgos I2 e I6 de la revisión externa, y por el tema #5 de la auto-revisión (JEV fuera de la detección de idioma).
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
- **Understand es un DecisionModel más, con una sola llamada por turno.**
  - Campos calibrados: `command`, `flow` e `interrupt`.
  - Campos sin calibrar: `slots` y `additional_flows`.
  - `affirm` y `deny` alimentan los resultados del nodo `confirm`.
- **Detección de idioma (auto-revisión #5):** no es un `DecisionModel` ni usa JEV. La resuelve un detector local en las guardas, antes de Understand (ADR 0012), para mantener una sola llamada a modelo por turno.

## Alternativas (I2)
- **Dos modelos por turno (clasificador de comando + extractor de slots):** separación más limpia, pero duplica la latencia y el costo por turno.

## Consecuencias
- Cambiar de proveedor es un cambio de datos, no de código.
- La comparación contra el baseline se obtiene corriendo el mismo modelo con otro proveedor.
- La auto-mejora puede proponer cambios de proveedor o de umbral como tipos de cambio conocidos.
- Hay un costo de calibración y etiquetado, que absorbe el científico de datos.

## Verificación pendiente (bloqueante antes del miércoles)
- Correr una prueba de humo con 50 casos en ES y 50 en PT sobre JEV, midiendo precisión, latencia y cobertura de idioma.
- **(#5)** En la misma prueba, correr lingua-py sobre los mismos casos y agregar un subconjunto de mensajes de 1 a 3 palabras y de portuñol. Medir para ambos: precisión de idioma por largo, tasa de indeterminados y latencia p50/p95 desde nuestro entorno (no la publicada por el proveedor). Si JEV resulta claramente mejor en mensajes cortos, se evalúa como segunda opinión solo en turnos `undetermined`.

## Fuentes
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
