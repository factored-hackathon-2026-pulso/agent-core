# ADR 0001 — Stack del núcleo

- Estado: aceptado (2026-09-28)
- Unidad: 0 · Fundamentos

## Contexto
El núcleo (`agent-core`) corre en contenedores portátiles. El cuello de botella de latencia es el LLM, no el runtime. El equipo (DS, DE, SE) domina Python, y los contratos deben poder exportarse a JSON Schema y OpenAPI para otros equipos.

## Decisión
- **Lenguaje y frameworks:** Python 3.12, FastAPI y Pydantic v2.
- **Persistencia:** Postgres 16 para el estado de sesión, el índice del registro y el log de auditoría.
- **Herramientas:** `uv` para paquetes y `docker-compose` para un entorno reproducible con un solo comando.
- **Forma de despliegue:** el núcleo es un servicio HTTP, no una librería embebida.
- **Infraestructura adicional:** no se agregan colas, Redis ni vector DB hasta que un subsistema lo justifique con su propio ADR.

## Alternativas descartadas
- **TypeScript:** ecosistema de eval y DS más pobre. Queda para las apps.
- **Go:** ninguna persona del equipo lo domina y no cabe en el plazo.
- **Databricks de punta a punta:** genera lock-in y arranques en frío incompatibles con la latencia de un chat.

## Consecuencias
- Un solo proceso Python limita el throughput. Se acepta para la demo y se escala horizontalmente porque el servicio no guarda estado en memoria: todo el estado vive en Postgres.
