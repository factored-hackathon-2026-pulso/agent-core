# ADR 0009 — Políticas de negocio protegidas; elegibilidad de crédito como servicio externo

- Estado: aceptado (2026-09-28)
- Unidad: 1 · Motor de decisión (con la unidad 2)
- Origen: hallazgo C5 de la revisión externa

## Contexto
- Los umbrales de negocio (p. ej. escalar disputas de más de 500 USD) vivían como `rule` inline dentro de los flows de `agent-registry`, que es donde la auto-mejora abre PRs.
- El reto exige que *"the conversational model must not invent eligibility rules or independently approve credit"* y pide *"approved rules or a clearly labeled synthetic policy service"*.
- El crédito no está en la demo, pero la exposición ante la auto-mejora es real desde ya.

## Decisión
1. **Entidad `policy` protegida** en el registro.
   - Formato: `{id, version, owner, expr (JSON Logic sobre hechos), rationale}`.
   - Cambiarla exige la aprobación de su dueño (CODEOWNERS + gate de release en la unidad 2).
   - La cuenta de la auto-mejora puede **proponer** cambios, pero nunca aprobarlos ni fusionarlos.
2. **Los flows referencian políticas; no las incrustan.**
   - Un `rule` usa `policy: id@v` para cualquier decisión de negocio.
   - `expr` inline queda para guardas estructurales: el validador rechaza literales numéricos o de texto que no sean enums declarados.
3. **La elegibilidad de crédito nunca es una `rule` ni una `policy` del núcleo.** Es un servicio externo que se invoca como tool y devuelve un veredicto firmado con:
   - las reglas aplicadas;
   - la explicación;
   - la incertidumbre;
   - la ruta de revisión.

   El modelo de riesgo, la política de elegibilidad y la conversación quedan separados, como exige el reto.

## Alternativas
- **Todo en un servicio de políticas externo (tipo OPA) desde ya:** da más separación, pero agrega otro servicio en la semana de construcción. Queda como forma obligatoria solo para crédito.
- **Dejar las reglas en los flows y confiar en la revisión del PR:** se descarta porque es débil frente a la auto-mejora.

## Consecuencias
- Cada evaluación de política queda en `rule_evaluated` con `policy@v`, entradas y resultado. Es la explicación auditable que pide el reto.
- La unidad 2 implementa la protección (dueños y aprobación) y la unidad 6 mide el acuerdo de cada política con las referencias.
