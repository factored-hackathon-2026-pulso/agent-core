# ADR 0011 — Hechos derivados (tools compute) y validación numérica por locale

- Estado: aceptado (2026-09-28)
- Unidad: 1 · Motor de decisión (con la unidad 3)
- Origen: hallazgo U8 de la revisión externa

## Contexto
- El validador exigía coincidencia literal de cifras.
- Las conversiones MXN/COP/ARS/USD (tabla `daily_exchange_rates`), las sumas, los días de SLA y los formatos `1.234,56` frente a `1,234.56` habrían producido falsos rechazos y escalamientos innecesarios, o habrían empujado al LLM a calcular sin fuente.

## Decisión
- **Clase de tool `compute`:** funciones puras y deterministas, como `convertir_moneda`, `sumar`, `dias_entre` y `formatear`.
  - Producen **hechos derivados** cuya procedencia incluye los `fact_id` de entrada.
  - Reutilizan el nodo `tool`, así que el catálogo de nodos no cambia.
- **Validador numérico:**
  - Parsea números, montos, porcentajes y fechas según el locale del run (es-CO, es-MX, es-AR, pt).
  - Compara **numéricamente** contra la vista `model` de los hechos citados, con igualdad exacta a la precisión de la moneda.
  - Una cifra calculada sin su hecho `compute` es un fallo.

## Alternativas
- **Nodo nuevo `compute`:** se descarta porque es un cambio mayor del catálogo sin beneficio sobre la clase de tool.
- **Dejar que el LLM calcule, con un validador tolerante:** se descarta porque deja pasar cifras sin fuente.

## Consecuencias
- Hay que mantener 3–4 funciones `compute` y un parser por locale.
- Cada cifra que ve el cliente tiene procedencia auditable.
- La tasa de falsos rechazos del validador se mide en la unidad 6.
