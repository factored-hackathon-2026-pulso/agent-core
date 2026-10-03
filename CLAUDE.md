# agent-core

Núcleo de agentes: motor de decisión que ejecuta agentes descritos como datos versionados (ciclo Understand → Decide → Act → Verify → Escalate). Agnóstico al negocio: aquí no hay conceptos bancarios.

## Dónde está el diseño

- `docs/specs/motor/00-indice.md` — **empieza aquí**: mapa de módulos M0–M12, dependencias, puertos, dueños del estado, eventos y fases.
- `docs/specs/motor/mXX-*.md` — spec de cada módulo: interfaz, comportamiento, invariantes, pruebas `T-Mx-NN` y definición de terminado.
- `docs/adr/` — decisiones de arquitectura. Cada spec de módulo cita los ADRs que lo gobiernan; léelos antes de implementar.
- `docs/specs/2026-09-28-motor-de-decision-design.md` — spec general (visión integrada).
- `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` y la sección "Abiertos" de cada módulo — decisiones pendientes.

## Stack (ADR 0001)

Python 3.12, FastAPI, Pydantic v2, Postgres 16, `uv`, `docker-compose`. Sin colas, Redis ni vector DB.

## Comandos

- Pruebas de un módulo: `uv run pytest tests/mXX`
- Suites de contrato de los puertos: `uv run pytest tests/contracts`
- Todas las pruebas: `uv run pytest`
- Fronteras entre módulos: `uv run lint-imports` (config en `.importlinter`)
- Tipos: `uv run mypy` (strict; archivos en `pyproject.toml`)
- Lint: `uv run ruff check .`
- Contratos: `uv run agentcore contracts` (regenera) · `uv run agentcore contracts --check` (CI)
- Postgres local (M3, M4, M9, M11, registry): `docker compose up -d postgres`

## Estructura

```
agent_core/
  domain/  ports/            M0: tipos, nodos, eventos, errores, JCS, puertos
  adapters/                  SystemClock, SystemIds (únicos que leen hora o aleatoriedad) y adaptadores reales
  flows/                     M1   interpreter/   M2   actions/   M3
  turn/                      M4   decision/      M5   guards/    M6
  views/                     M7   response/      M8   api/       M9
  handoff/                   M10  audit/         M11  knowledge/ M12
  registry/                  unidad 2 (spec 2026-09-29-registry-design.md)
  composition/               raíz de composición: motor, evaluador, servicio y CLI del registry
  adapters/llm/              HttpLLMGateway (cliente del servicio llm-gateway, ADR 0024) y LLMAgentPort (unidad 5)
testing/fakes/               dobles en memoria de cada puerto
tests/mXX/                   pruebas unitarias por módulo (sin red ni Postgres)
tests/contracts/             suites de contrato de los puertos
tests/integration/           pruebas con Postgres (M3, M4, M9, M11 y registry)
contracts/                   JSON Schema y OpenAPI generados (no editar a mano)
```

## Reglas duras

1. **Fronteras:** un módulo solo importa `agent_core.domain`, `agent_core.ports` y la interfaz pública (`__init__.py`) de los módulos permitidos en `.importlinter`. Nunca importes internos de otro módulo. Si necesitas algo que no está en una interfaz, detente y pregunta.
2. **Tiempo e IDs:** nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`; todo instante sale del `Clock` y todo ID o secreto del `IdSource` inyectados (lo verifica `ruff`).
3. **Determinismo:** con los mismos puertos y el mismo `Clock`, el motor produce los mismos eventos (base del replay).
4. **Dinero y cifras:** `Decimal`, nunca `float`. Todo JSON entra con `agent_core.domain.loads` y toda canonización usa `canonical_bytes` (M0).
5. **Datos:** fixtures y pruebas solo con datos sintéticos. Nunca copies datos reales del dataset ni las credenciales de AWS del diccionario de datos a este repo ni a un request a un modelo.
6. **PII:** nada en vista `full` sale a modelos, logs o eventos; usa las vistas de M7.
7. **Contratos:** si cambias un tipo de M0, regenera `contracts/` (`uv run agentcore contracts`) y avisa: es un cambio de interfaz para todos los módulos.

## Cómo trabajar un módulo

1. Lee el spec del módulo, el índice y los ADRs que cita.
2. Planea antes de escribir código; muestra el plan.
3. Escribe primero las pruebas `T-Mx-NN` del spec y confirma que fallan.
4. Implementa hasta que pasen, sin tocar otros módulos.
5. Corre `pytest tests/mXX`, `lint-imports`, `mypy` y `ruff`.
6. Marca punto por punto la "Definición de terminado" del spec.

**Si el spec es ambiguo, contradice un ADR o toca un "Abierto": detente y pregunta. No lo resuelvas por tu cuenta.** Si durante la implementación cambia el comportamiento acordado, actualiza el spec del módulo en el mismo cambio.
