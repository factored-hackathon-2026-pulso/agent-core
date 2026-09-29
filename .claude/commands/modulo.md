---
description: Implementa un módulo del motor a partir de su spec (p. ej. /modulo M3)
argument-hint: <M0..M12>
---

Implementa el módulo $ARGUMENTS del motor.

1. Lee `docs/specs/motor/00-indice.md` y el spec del módulo $ARGUMENTS en `docs/specs/motor/` (el archivo que empieza con su número). Lee también cada ADR que el spec cite en su cabecera.
2. Comprueba que los módulos de los que depende (columna "Usa" del índice) ya existen. Si falta alguno, detente y dímelo; no lo implementes tú.
3. Revisa la sección "Abiertos" del spec. Si alguno bloquea la implementación, pregúntame antes de seguir.
4. Muéstrame un plan corto: archivos que vas a crear, interfaz pública y cómo vas a cubrir cada prueba `T-Mx-NN`.
5. Escribe primero todas las pruebas del spec en `tests/` del módulo, usando los dobles de `testing/fakes/`, y confirma que fallan.
6. Implementa hasta que pasen. No modifiques otros módulos ni tipos de M0 sin preguntarme.
7. Corre `uv run pytest` sobre el módulo, `uv run lint-imports`, `uv run mypy agent_core` y `uv run ruff check .`.
8. Termina con la "Definición de terminado" del spec marcada punto por punto, y la lista de decisiones que tomaste que el spec no cubría.
