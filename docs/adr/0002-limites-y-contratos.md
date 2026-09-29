# ADR 0002 — Límites entre sistemas y publicación de contratos

- Estado: aceptado (2026-09-28)
- Unidad: 0 · Fundamentos

## Contexto
El principio acordado es que cada sistema recibe entradas y produce salidas, y no conoce los internos de los demás. El núcleo debe ser trivial de consumir y agnóstico a la capa de negocio.

## Decisión
- **`agent-core` (código):**
  - Publica `contracts/` con el OpenAPI de sus APIs y los JSON Schemas de entidades y eventos, versionados con **semver**.
  - Las APIs viven bajo `/v1`.
  - Un cambio incompatible sube la versión mayor.
- **Consumidores:** dependen solo de `contracts/` o del SDK delgado, nunca de módulos internos.
- **`agent-registry` (contenido):**
  - Solo contiene datos declarativos (YAML/Markdown), sin código ejecutable.
  - Fija la versión de esquemas de `agent-core` que usa.
  - CI valida todo su contenido contra esa versión.
- **Contratos externos:** el núcleo solo conoce `IdentityClaims`, `ReadModel` (vía conectores de tools), `FieldClassification`, `KnowledgeSource` y los eventos que exporta.
- **Separación de sistemas:** apps, datos/ETL, conocimiento y auto-mejora son sistemas distintos, con dueños distintos.

## Consecuencias
- Hay un costo de versionar contratos, que se acepta.
- El núcleo puede desplegarse en paralelo en varias versiones sin tocar a sus consumidores.
- La auto-mejora interactúa solo mediante eventos (lectura) y PRs a `agent-registry` (escritura).
