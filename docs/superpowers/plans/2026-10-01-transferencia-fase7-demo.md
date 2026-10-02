# Transferencia entre agentes, fase 7 — demo (recepción y dos especialistas) y cableado: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que la demo de la spec (§1) corra de punta a punta: en una sesión, el cliente escribe a `recepcion`, que lo transfiere a `disputas`, y `disputas` responde **en el mismo turno**. Después, el linaje de la sesión muestra los dos runs con su release exacta y el `transfer_id`, y las dos cadenas quedan enlazadas por hash. La sesión además se graba y se reproduce (`agentcore record` / `agentcore replay`).

**Architecture:**
- Un registro de autoría nuevo, `tests/fixtures/registry-transfer-demo`, con tres agentes: `recepcion`, `disputas` (derivado de `atencion`) y `consultas` (sobre `obtener_pqr`). Cada uno tiene **una release propia con alias `prod`**. También trae la tool `directory/list@1.0.0` como archivo y un artefacto de calibración hecho a mano con el umbral comodín `"*"`.
- La raíz de composición cablea el directorio:
  - `EngineDeps.directory` opcional: si está, `build_engine` envuelve `deps.tools` con `DirectoryToolExecutor`.
  - `agentcore serve` construye `RegistryDirectory` sobre el registry de Postgres.
- La base impone "a lo sumo un run abierto por sesión" con un índice único parcial. El doble en memoria lo imita.
- El replay aprende a reproducir una sesión que transfiere:
  - `RecordedIds` recoge `to_run_id` y `transfer_id`.
  - El fixture lleva las cadenas de los runs destino (`linked`).
  - El runner conoce varias releases.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, FastAPI `TestClient`, Postgres 16 (solo pruebas de integración, **sin ejecutar en este entorno**), `uv`.

**Spec:** `docs/specs/2026-09-30-transferencia-entre-agentes-design.md` (§1, §4, §5, §11 fase 7, §12.1, §12.8, §12.10, §12.14, §12.16) y ADR 0021. Leer también:
- `docs/specs/motor/m04-ciclo-del-turno.md` §5 (transferencia) y §11;
- `docs/specs/motor/m11-auditoria-transcript-replay.md` §3.5 y decisiones 9, 13, 14 y 20;
- `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` #19;
- el plan de las fases 1 a 6, `docs/superpowers/plans/2026-09-30-transferencia-entre-agentes.md`;
- el libro `.superpowers/sdd/2026-09-30-transferencia-entre-agentes/progress.md`.

## Global Constraints

- Todo el código va en inglés: identificadores, comentarios y docstrings. Los mensajes de error que ya están en español siguen en español. Los textos de plantillas, los YAML de datos y los documentos de `docs/` y `README.md` van en español.
- Nunca `datetime.now()`, `time.time()`, `uuid4()`, `random` ni `secrets`: instantes del `Clock` e ids del `IdSource`.
- Dinero y cifras con `Decimal`. JSON de entrada con `agent_core.domain.loads`; canonización con `canonical_bytes`.
- Fixtures y pruebas **solo con datos sintéticos**. Los ejemplos de las fichas de enrutamiento son frases inventadas.
- Nada en vista `full` sale a modelos, logs ni eventos.
- Fronteras de `.importlinter`: `composition` puede importar todo y nadie la importa (salvo `cli`). `audit` no importa `turn` ni `interpreter`.
- **No se toca M0** (`agent_core/domain`): esta fase no cambia contratos ni `SCHEMA_VERSION` (sigue en 1.2.0). Si una tarea parece necesitarlo, se detiene y pregunta.
- **No se toca `calibrate`** y el Abierto 1 de la spec sigue abierto (proveedor de `choices_from` y cómo se genera `"*"`).
- **No se resuelven otros Abiertos:**
  - vuelta a recepción;
  - especialistas con `step_up`;
  - tope por sesión (`max_transfers_per_session` sigue en 1);
  - evaluación §7 y REL-T1;
  - spans OTel;
  - hash del directorio en el linaje (§12.9);
  - atomicidad de M3 (§12.11).
- **Sin docker:** las pruebas de `tests/integration` y la variante `postgres` de `tests/contracts` se omiten. Toda afirmación sobre Postgres queda marcada **"sin verificar"** en el commit, en la spec y en el informe de la tarea.
- **Los seis fixtures de `tests/fixtures/runs/` no cambian ni un byte.** `tests/composition/test_replay_fixtures.py::test_el_fixture_committeado_esta_vigente` lo vigila: si falla, el cambio rompió el comportamiento del motor de la demo `disputa-cargo` y se corrige el código, no el fixture.
- Cada tarea termina con estos comandos en verde:
  - `uv run pytest <carpetas tocadas>`
  - `uv run lint-imports`
  - `uv run mypy`
  - `uv run ruff check .`
  - `uv run agentcore contracts --check`
- Cada tarea actualiza en el mismo commit el spec del módulo que toca.
- Commits con estas líneas al final (bash; `git commit -F <archivo>` es opcional):
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Jnvu6ofA2xGYztmAgtpKMs
  ```
  Si la tarea la implementa otro modelo, va su nombre en `Co-Authored-By`, como en la regla R7 del libro de las fases 1 a 6.
- Rama `feat/transferencia-demo` (sale de `feat/transferencia-entre-agentes`). No se cambia de rama, no se hace push y no se abre un PR.

## Decisiones

### Del usuario (fijas, no se reabren)

| # | Decisión |
|---|---|
| U1 | Los agentes de la demo viven en un registro nuevo, `tests/fixtures/registry-transfer-demo`; `tests/fixtures/registry-demo` no se toca. Son tres: **`recepcion`**, **`disputas`** y **`consultas`**. Cada uno tiene alias `prod`, y `directory/list@1.0.0` es un archivo de tool del registro. Los flows son:<br>• `recepcion`: `collect` → `directory/list` → `decide choices_from` → "te comunico con…" → `transfer`. `rejected` escala; `none` y `low_confidence` piden aclarar y vuelven al `collect`.<br>• `disputas`: el flow `disputa-cargo`, más ficha, contrato `accepts` y `understand`.<br>• `consultas`: un flow de consulta sobre `obtener_pqr`, con ficha y `accepts`. |
| U2 | El umbral `"*"` de la demo sale de un **artefacto de calibración hecho a mano** dentro del fixture. El Abierto 1 sigue abierto y `calibrate` no cambia. |
| U3 | `EngineDeps.directory: AgentDirectory \| None = None`. Si está presente, `build_turn_engine` envuelve `deps.tools` con `DirectoryToolExecutor`, que usa el `authz` y los `ids` de `deps`; si falta, nada cambia. `DirectoryToolExecutor` y `DIRECTORY_TOOL` se exportan desde `agent_core.composition`, y el harness deja de importar el módulo interno. `serve` construye `RegistryDirectory(store, registry, releases)` con `releases = PostgresRegistry.release` (R3). |
| U4 | Índice único parcial `runs_one_open_per_session` en `schema.sql`. El doble en memoria rechaza un segundo run abierto en una sesión. La prueba de Postgres se escribe pero queda **sin verificar**. Además:<br>• se corrige el error que hoy se reporta, un `VersionConflict` con un mensaje sobre la cadena de auditoría;<br>• se corrige el comentario viejo de `schema.sql`;<br>• se corrige el docstring O(1) del doble. |

### Que fija este plan (derivadas de lo que hay en el código; revisar antes de ejecutar)

| # | Decisión | Por qué |
|---|---|---|
| F1 | "Una release con alias `prod` para los tres" se implementa como **tres releases**: `recepcion-demo`, `disputas-demo` y `consultas-demo`, una por agente. | `RegistryService.import_seed` rechaza una release con más de un agente ("una release por agente en esta entrega", `registry/service.py:541`). Así lo pide además ADR 0021 D3: publicar un especialista no toca la release de recepción. También deja que el linaje muestre dos releases distintas. |
| F2 | El artefacto hecho a mano es `calibrations/cal-transfer-demo.json` dentro del fixture. Lleva **todos** los umbrales que usan los modelos de decisión del fixture: los de `demo_calibration()`, más `flow` de `consulta-pqr` y `recepcion`, más `("choice", "*", "classifier", "es") = 0.8`. Los tres modelos del fixture declaran `thresholds_from: cal-transfer-demo`. | Así funciona el código: `DecisionService._calibrate` busca el artefacto por `DecisionModelDef.thresholds_from` en un `CalibrationSource` (`get(run_id)`). Las claves son `(campo, valor, proveedor, idioma)`; si falta el valor, `_passes` prueba `(campo, "*", proveedor, idioma)`. `DirectoryCalibrationSource(dir)` lee `<dir>/<run_id>.json` en el formato de `CalibrationArtifact.to_json`. `load_registry` solo recorre las carpetas de `DIRS`, así que `calibrations/` no molesta a M1 ni a `registry import`. Con un solo artefacto, el fixture no depende de `testing.engine_world.demo_calibration()`. |
| F3 | El modelo de enrutamiento `elegir-especialista@1.0.0` usa el proveedor `classifier`, que en la demo es guionado (`ScriptedProvider`), con `calibration: {method: none}`. | Es el único proveedor guionable de los mundos de prueba, como `match-cargo`. El proveedor real queda en el Abierto 1 (ver Pregunta 4). |
| F4 | El catálogo de la demo (`testing/engine_world.CATALOG`, que usan `EngineWorld`, el replay, `serve` en demo y el harness del registry) clasifica `public` estas rutas: `directory.choices`, `directory.entries.agent_id` y `directory.entries.release_id`. | Spec §3.2 y §12.14. Rutas con prefijo `directory.`: no cambian ninguna ruta de los fixtures existentes. Qué hacer con `summary` y `examples` es la Pregunta 3. |
| F5 | Al **grabar**, `EngineWorld` arma `RecordingToolExecutor(DirectoryToolExecutor(fake))` y pasa `deps.directory = None`. Sin grabar, usa `deps.directory = InMemoryDirectory(registry)` y deja que `build_engine` envuelva. | El replay sirve **toda** tool desde lo grabado (`RecordedToolExecutor`). Si la grabación no ve la llamada a `directory/list`, el fixture no tiene su resultado `full` y el replay diverge. El modo sin grabar es el que prueba U3 de punta a punta. |
| F6 | El orden de las escrituras de runs en `PostgresUnitOfWork._apply` pasa a ser **primero los que no quedan abiertos**: se ordena de forma estable por `status == "open"`. Un `UniqueViolation` del índice nuevo se traduce a `VersionConflict("la sesión ya tiene un run abierto (runs_one_open_per_session)")`, distinguido por `exc.diag.constraint_name`. | Un índice único parcial no se puede diferir y Postgres lo comprueba en cada sentencia. Hoy el orden ya es "origen y después destino" (orden de inserción del dict, m04 §5 paso 5), pero depende de un detalle. Ordenar por estado lo vuelve una propiedad de la UoW. La comprobación del doble sobre el estado final equivale a la de Postgres solo si los cierres van primero. |
| F7 | El replay de una sesión que transfiere usa un fixture con `linked`: los eventos de los runs destino, en orden de creación. Los puertos grabados se arman con `events + linked`; la comparación es `events + linked` contra lo que produce el motor; cada cadena enlazada se verifica con `check_chain`. **`run_started.payload.origin.from_event_hash` no se compara.** | Arreglar solo `RecordedIds` no alcanza: el destino corre en el mismo turno y llama a Understand, así que necesita sus `decision_made` grabados, que están en la cadena del destino. `from_event_hash` es un hash de cadena, y el replay no reproduce hashes: `ts` y `duration_ms` cambian, y `compare.normalize` ya ignora `hash` y `prev_hash` por eso. El enlace se verifica sobre lo grabado con `verify_transfer_link`. **Pendiente de la Pregunta 1** (cambia el formato del fixture de M11). |
| F8 | `check_fixture` deja pasar las hojas que son exactamente un sha256 hex en minúsculas (`[0-9a-f]{64}`). | El resultado `full` de `directory/list` lleva `hash`, y casi cualquier sha256 tiene seis dígitos seguidos, así que `--catalog` rechazaría el fixture. La regla de M11 (decisión 13) ya excluye los `events` por la misma razón. **Pendiente de la Pregunta 2.** |
| F9 | REL-T1 **no** se construye (fase 8). Una prueba del fixture comprueba lo mismo a mano para este registro: los slots del paquete de `recepcion` cumplen el `accepts` de cada especialista del directorio. | Sin REL-T1, nada impide publicar una combinación rota. La prueba evita que el fixture la tenga, y M4 la rechaza en runtime (`accepts_mismatch`). |
| F10 | `agentcore validate` de este registro no necesita reglas nuevas. En M1 no hay reglas `REL-*` construidas; las comprobaciones de release son la clausura (G0-02) y las reglas por flow, agente y release que ya corre `validate_registry`. | Lectura de `agent_core/flows/validate.py`. |

---

## Preguntas para el usuario (antes de ejecutar las tareas marcadas)

1. **(Tareas 5 y 6) ¿Se amplía el formato del fixture de M11 con `linked` para grabar y reproducir la sesión con transferencia?** El cambio:
   - agrega un campo opcional a `Fixture`;
   - `dump_fixture` lo omite cuando está vacío, así que los seis fixtures no cambian;
   - el `Replayer` arma los puertos y compara sobre `events + linked`;
   - `run_started.origin.from_event_hash` sale de la comparación (F7).

   Es replay de sesión, el pendiente §12.10. **Si la respuesta es no:** la tarea 5 se reduce al arreglo de `RecordedIds` con su prueba unitaria; la tarea 6 se reduce al runner con varias releases; la demo queda probada en proceso y por HTTP (tarea 4), sin fixture grabado; y el README dice que el replay de la sesión sigue pendiente. Conectar `verify_transfer_link` a la salida de `agentcore replay` (con un veredicto nuevo) **no** entra en ninguno de los dos casos.
2. **(Tarea 5) ¿Se exime en `check_fixture` un valor que es exactamente un sha256 hex (F8)?** Las alternativas son:
   - (b) no usar `--catalog` con el fixture de transferencia;
   - (c) agregar al catálogo los dígitos de ese hash concreto. Es frágil: cambia si cambian los ids de release.

   Se recomienda (a), la exención.
3. **(Tarea 4) Clasificación de `summary` y `examples` de las entradas del directorio.** Hoy quedan `pii_direct` y llegan **tokenizados** al modelo de enrutamiento. Con el proveedor guionado no importa; con uno real, el modelo no vería las fichas. Opciones:
   - (a) `public`: es texto de autoría del registro, no del cliente;
   - (b) `untrusted_text`: llega envuelto;
   - (c) dejarlos como están y que el modelo elija solo por `agent_id`.

   Además: ¿estas reglas del directorio van también en `DEFAULT_CATALOG` de M7? Toda instalación las tendría sin acordarse, pero sería un cambio de M7. ¿O solo en el catálogo de la demo, como fija F4? El plan implementa F4 sin `summary` ni `examples` hasta la respuesta.
4. **(README y serve) Proveedor del modelo de enrutamiento en `serve` (Abierto 1).** En `serve` el doble `classifier` es un `ScriptedProvider` vacío y `jev` es el JEV real. Con eso, `agentcore serve` en modo demo **no transfiere**: la elección queda en `low_confidence` y recepción pide aclarar. La demo ejecutable es la de proceso y HTTP en memoria (tarea 4) y el replay (tarea 6). ¿Se acepta así para esta fase, o se decide ya el proveedor (JEV `choice` o `llm_structured`)?
5. **Suites de evaluación de los tres agentes.** §11 dice "con sus suites". Una suite de recepción necesita la evaluación §7 (`transferred_to`, que no abra el destino), que es fase 8. ¿Se dejan **todas** las suites para la fase 8? El plan no crea ninguna. ¿O se agregan ya suites simples para `disputas` y `consultas`?
6. **(Tarea 3) Error del índice.** ¿Basta un `VersionConflict` con mensaje propio (F6, que el motor ya trata como conflicto de concurrencia)? ¿O se quiere un error nuevo? Un error nuevo sería un cambio de M0, fuera de esta fase.
7. **Evaluación desde `serve` (`EngineScenarioHarness`):** queda sin directorio en esta fase, así que un escenario de recepción no puede leer `directory/list`. ¿Se confirma que va con la fase 8?
8. **F1:** ¿se confirma que "una release con `prod` para los tres" son tres releases, una por agente?

---

## Review Focus

1. **Escritura del origen y del destino en el mismo commit con el índice.**
   - Un commit que guarda primero el destino abierto y después cierra el origen debe aplicarse sin `UniqueViolation`.
   - Un segundo run abierto en la sesión debe fallar con `VersionConflict("la sesión ya tiene un run abierto …")` y no aplicar nada.
   - Pruebas: `check_open_target_saved_before_closing_origin_still_commits` y `check_a_session_holds_at_most_one_open_run` (tarea 3). La variante Postgres está **sin verificar**.
2. **Los seis fixtures de `disputa-cargo` siguen iguales byte a byte** tras cambiar `EngineWorld`, `Driver`, el runner, `dump_fixture` y el catálogo. Prueba: `test_el_fixture_committeado_esta_vigente` (tareas 4 a 6).
3. **El replay de la sesión con transferencia da `match` sin excepciones de más:** lo único que se excluye de la comparación es `run_started.origin.from_event_hash`. Pruebas: `test_the_transfer_session_replays_as_match` (tarea 6) y `test_from_event_hash_is_not_compared_but_the_rest_of_origin_is` (tarea 5).
4. **Sin el umbral comodín no hay transferencia.** Si al artefacto le falta `("choice", "*", …)`, la elección queda en `low_confidence`, recepción pide aclarar y la sesión tiene un solo run. Prueba: `test_without_the_wildcard_threshold_reception_asks_again` (tarea 4).
5. **Sin PII en eventos:** el texto del cliente no aparece en ningún evento de las dos cadenas, y las únicas rutas del directorio en claro para el modelo son las de F4. Prueba: `test_no_event_carries_the_customer_text` (tarea 4).

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `agent_core/composition/engine.py` | `EngineDeps.directory`; `build_engine` envuelve `tools` |
| `agent_core/composition/__init__.py` | exporta `DIRECTORY_TOOL` y `DirectoryToolExecutor` |
| `agent_core/composition/serve_ports.py`, `serve.py` | `ServePorts.directory`; `RegistryDirectory` en `resolve_ports`; `build_api_deps` lo pasa |
| `tests/fixtures/registry-transfer-demo/**` (nuevo) | agentes, flows, tools (incluida `directory/list`), modelos de decisión, plantillas, tres releases y `calibrations/cal-transfer-demo.json` |
| `agent_core/adapters/sql/schema.sql` | índice `runs_one_open_per_session`; comentario de `run_seq` |
| `agent_core/adapters/postgres_uow.py` | orden de escrituras en `_apply`; traducción del `UniqueViolation` |
| `testing/fakes/storage.py` | rechazo de un segundo run abierto; docstring de complejidad |
| `testing/fakes/registry_dir.py` | `registry_from_releases(root, release_ids=None)` |
| `testing/engine_world.py` | catálogo (F4); `EngineWorld` con varias releases, agente de entrada, directorio y calibraciones; `Driver.agent`; `transfer_world()`; `routes()` |
| `agent_core/audit/replay/ports.py` | `RecordedIds` recoge `to_run_id` y `transfer_id` |
| `agent_core/audit/replay/fixture.py`, `recording.py`, `runner.py`, `compare.py`, `synthetic.py` | `Fixture.linked`; `build_fixture(linked=…)`; `Replayer` sobre `events + linked`; exclusión de `from_event_hash`; exención sha256 |
| `testing/replay/runner.py`, `scenarios.py`, `__init__.py` | runner con varias releases, `IdKind.transfer` grabado y umbrales sintetizados por `thresholds_from`; escenario `transferencia`; registro por escenario |
| `tests/fixtures/runs-transfer/transferencia.yaml` (nuevo, generado) | la sesión grabada |
| `README.md`, spec de transferencia, m04, m11, `TEMAS-ABIERTOS-PENDIENTES.md` #19, ADR 0021 | documentación |

---

### Task 1: Composición — `EngineDeps.directory`, exportes y `RegistryDirectory` en `serve`

**Modelo recomendado:** sonnet.

**Files:**
- Modify: `agent_core/composition/engine.py`, `agent_core/composition/__init__.py`, `agent_core/composition/serve_ports.py`, `agent_core/composition/serve.py`
- Modify (imports): `tests/m04/harness.py:7`, `tests/composition/test_directory_tool.py:7`
- Test: `tests/composition/test_engine_directory.py` (nuevo), `tests/composition/test_serve_ports.py`, `tests/composition/test_serve_app.py`
- Docs: spec de transferencia §4, párrafo "Cableado"

**Interfaces:**
- Produce:
  - `EngineDeps.directory: AgentDirectory | None = None` (último campo antes de `config`; o después, si el orden de los dataclass con valores por defecto lo exige);
  - `from agent_core.composition import DIRECTORY_TOOL, DirectoryToolExecutor`;
  - `ServePorts.directory: AgentDirectory | None = None`.
- Consume: `agent_core.registry.RegistryDirectory(store, registry, releases)` (R3) y `PostgresRegistry.release`.

- [ ] **Step 1: Write the failing tests**

`tests/composition/test_engine_directory.py`:

```python
"""U3 (ADR 0021, phase 7): with `EngineDeps.directory` the engine serves `directory/list`; without it, nothing changes."""

from dataclasses import replace

from agent_core.composition import DIRECTORY_TOOL, DirectoryToolExecutor, build_turn_engine
from agent_core.domain import EntityRef
from testing.engine_world import EngineWorld
from testing.fakes.directory import InMemoryDirectory

TOOL = EntityRef(id="directory/list", version="1.0.0")


def _runtime_tools(engine: object) -> object:
    return engine._runtimes._tools  # type: ignore[attr-defined]  # composition detail, as EngineWorld does


def test_without_a_directory_the_tools_are_the_given_ones() -> None:
    world = EngineWorld()
    assert _runtime_tools(build_turn_engine(world.deps)) is world.deps.tools


def test_with_a_directory_the_engine_serves_directory_list_and_delegates_the_rest() -> None:
    world = EngineWorld()
    deps = replace(world.deps, directory=InMemoryDirectory(world.registry))
    tools = _runtime_tools(build_turn_engine(deps))
    assert isinstance(tools, DirectoryToolExecutor)
    assert tools.definition(TOOL) == DIRECTORY_TOOL
    assert tools.definition(EntityRef(id="obtener_pqr", version="1.0.0")).id == "obtener_pqr"
```

In `tests/composition/test_serve_ports.py` add:

```python
def test_serve_wires_the_registry_directory() -> None:
    from agent_core.registry import RegistryDirectory

    ports = _resolve(AGENTCORE_ALLOW_DEMO="1")
    assert isinstance(ports.directory, RegistryDirectory)
    assert "directory" not in ports.doubles  # a real piece over the registry, not a demo double
```

In `tests/composition/test_serve_app.py`, `make_ports` passes `directory=d.directory` (the field exists on `EngineDeps` after Step 3). Add:

```python
def test_build_api_deps_passes_the_directory_to_the_engine() -> None:
    from dataclasses import replace

    from agent_core.composition import DirectoryToolExecutor
    from agent_core.composition.serve import build_api_deps
    from testing.fakes.directory import InMemoryDirectory

    world = EngineWorld()
    issuer = TestIdentityIssuer(world.clock)
    ports = replace(make_ports(world, issuer), directory=InMemoryDirectory(world.registry))
    deps = build_api_deps(ports)
    assert isinstance(deps.turns._runtimes._tools, DirectoryToolExecutor)  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/composition/test_engine_directory.py tests/composition/test_serve_ports.py tests/composition/test_serve_app.py -q`
Expected: FAIL. `ImportError: cannot import name 'DIRECTORY_TOOL' from 'agent_core.composition'`, and `TypeError`/`AttributeError` for `directory`.

- [ ] **Step 3: Implement**

`agent_core/composition/engine.py`:

```python
from agent_core.composition.directory import DirectoryToolExecutor
from agent_core.ports import AgentDirectory  # with the other port imports

@dataclass(frozen=True)
class EngineDeps:
    ...
    knowledge: KnowledgeSource | None = None
    directory: AgentDirectory | None = None  # ADR 0021: with it the engine serves `directory/list`
    config: EngineConfig = field(default_factory=EngineConfig)


def _tools(deps: EngineDeps) -> ToolExecutor:
    """`deps.tools`, wrapped to serve `directory/list` when a directory is wired (ADR 0021, spec §4)."""
    if deps.directory is None:
        return deps.tools
    return DirectoryToolExecutor(deps.tools, deps.directory, deps.authz, deps.ids)
```

`build_engine` passes `tools=_tools(deps)` to `EngineRuntimeFactory`; nothing else uses `deps.tools`.

`agent_core/composition/__init__.py`: import `DIRECTORY_TOOL` and `DirectoryToolExecutor` from `agent_core.composition.directory` and add them to `__all__`, keeping it sorted as it is now.

`agent_core/composition/serve_ports.py`:
- add `from agent_core.ports import AgentDirectory` and `from agent_core.registry import RegistryDirectory` (next to `PgRegistryStore, PostgresRegistry`);
- add `directory: AgentDirectory | None = None` to `ServePorts`, after `registry_api`;
- in `resolve_ports`, add to the `ServePorts(...)` call:

```python
        directory=RegistryDirectory(registry_store, pg_registry, pg_registry.release),
```

`agent_core/composition/serve.py`, `build_api_deps`: add `directory=ports.directory` to the `EngineDeps(...)` call. Leave `build_registry_service_for_serve` without a directory (Pregunta 7).

Replace `from agent_core.composition.directory import DIRECTORY_TOOL, DirectoryToolExecutor` with `from agent_core.composition import DIRECTORY_TOOL, DirectoryToolExecutor` in `tests/m04/harness.py` and `tests/composition/test_directory_tool.py`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/composition tests/m04 -q`, then `uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: PASS. The six replay fixtures do not change (no directory there).

- [ ] **Step 5: Update the spec and commit**

In the transfer spec §4, replace the "**Cableado:**" paragraph with this content (Spanish):
- `EngineDeps.directory` opcional; con él, `build_engine` envuelve las tools con `DirectoryToolExecutor` (autorización e ids de `deps`);
- `agentcore serve` construye `RegistryDirectory(store, PostgresRegistry, PostgresRegistry.release)`;
- `DirectoryToolExecutor` y `DIRECTORY_TOOL` se exportan desde `agent_core.composition`;
- la evaluación del registry (`EngineScenarioHarness`) aún no tiene directorio (fase 8).

In §12, item 16: "**Cerrado (fase 7)**".

```bash
git add agent_core/composition tests/composition tests/m04/harness.py docs/specs/2026-09-30-transferencia-entre-agentes-design.md
git commit -F msg.txt   # feat(composition): wire the agent directory into the engine and serve
```

---

### Task 2: Registro de la demo — `tests/fixtures/registry-transfer-demo`

**Modelo recomendado:** sonnet.

**Files:**
- Create: everything under `tests/fixtures/registry-transfer-demo/` (listed below)
- Test: `tests/composition/test_transfer_demo_registry.py` (nuevo)

**Interfaces:**
- Produce:
  - agents `recepcion@1.0.0`, `disputas@1.0.0` and `consultas@1.0.0`;
  - flows `recepcion@1.0.0`, `disputa-cargo@1.0.0` (copy) and `consulta-pqr@1.0.0`;
  - tool `directory/list@1.0.0`;
  - decision model `elegir-especialista@1.0.0`;
  - releases `recepcion-demo`, `disputas-demo` and `consultas-demo`;
  - artifact `calibrations/cal-transfer-demo.json`.
- Directory label: `atencion-cliente`.

- [ ] **Step 1: Write the failing tests**

`tests/composition/test_transfer_demo_registry.py`:

```python
"""The phase 7 demo registry (ADR 0021): valid for M1, importable into the registry, coherent contracts."""

from pathlib import Path

import pytest

from agent_core.cli import main
from agent_core.composition import DIRECTORY_TOOL
from agent_core.decision.calibration.artifact import CalibrationArtifact, DirectoryCalibrationSource
from agent_core.domain import Agent, CollectNode, EntityKind, Flow, ToolDef, TransferNode, packet_problem
from agent_core.flows import load_registry
from agent_core.registry.memory import InMemoryRegistryStore
from agent_core.registry.service import RegistryService
from testing.fakes.clock import FakeClock
from testing.fakes.ids import FakeIds
from tests.registry.helpers import admin
from tests.registry.service_world import FakeEvaluator

ROOT = Path("tests/fixtures/registry-transfer-demo")
DIRECTORY = "atencion-cliente"
SPECIALISTS = ["consultas", "disputas"]  # codepoint order, as the directory lists them


def _registry():  # type: ignore[no-untyped-def]
    reg, violations = load_registry(ROOT)
    assert violations == []
    return reg


def test_agentcore_validate_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(ROOT)]) == 0, capsys.readouterr().out


def test_the_directory_tool_file_is_the_definition_composition_serves() -> None:
    assert _registry().get_exact(EntityKind.tool, "directory/list", "1.0.0") == DIRECTORY_TOOL


def test_the_specialists_carry_a_card_of_the_directory_and_a_contract() -> None:
    reg = _registry()
    for agent_id in SPECIALISTS:
        agent = reg.get_exact(EntityKind.agent, agent_id, "1.0.0")
        assert isinstance(agent, Agent) and agent.routing is not None and agent.accepts is not None
        assert agent.routing.directory == DIRECTORY and agent.understand is not None
    reception = reg.get_exact(EntityKind.agent, "recepcion", "1.0.0")
    assert isinstance(reception, Agent) and reception.routing is None and reception.accepts is None


def test_the_reception_packet_fits_every_specialist_contract() -> None:
    """REL-T1 by hand for this registry (F9): REL-T1 itself is phase 8."""
    reg = _registry()
    flow = reg.get_exact(EntityKind.flow, "recepcion", "1.0.0")
    assert isinstance(flow, Flow)
    [transfer] = [n for n in flow.nodes if isinstance(n, TransferNode)]
    collected = {n.config.slot for n in flow.nodes if isinstance(n, CollectNode)}
    assert set(transfer.config.packet.slots) <= collected
    sample = {name: "texto sintético" for name in transfer.config.packet.slots}  # a collect without validator: string
    for agent_id in SPECIALISTS:
        agent = reg.get_exact(EntityKind.agent, agent_id, "1.0.0")
        assert isinstance(agent, Agent) and agent.accepts is not None
        assert packet_problem(agent.accepts, sample) is None, agent_id


def test_the_hand_made_calibration_has_the_wildcard_and_is_canonical() -> None:
    source = DirectoryCalibrationSource(ROOT / "calibrations")
    artifact = source.get("cal-transfer-demo")
    assert artifact is not None
    assert artifact.thresholds[("choice", "*", "classifier", "es")] == 0.8
    text = (ROOT / "calibrations" / "cal-transfer-demo.json").read_text(encoding="utf-8")
    assert CalibrationArtifact.from_json(text).to_json() == text.rstrip("\n")


def test_every_decision_model_reads_the_hand_made_artifact() -> None:
    from agent_core.domain import DecisionModelDef

    models = [m for m in _registry().all(EntityKind.decision_model) if isinstance(m, DecisionModelDef)]
    assert {m.thresholds_from for m in models} == {"cal-transfer-demo"}


def test_registry_import_publishes_one_release_per_agent() -> None:
    store = InMemoryRegistryStore()
    service = RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds())
    details = service.import_seed(admin(), ROOT)
    assert len(details) == 3
    with store.transaction() as tx:
        assert sorted(agent for agent, _ in tx.aliases_named("prod")) == ["consultas", "disputas", "recepcion"]


def test_the_registry_directory_lists_the_two_specialists() -> None:
    from agent_core.registry import PostgresRegistry, RegistryDirectory

    store = InMemoryRegistryStore()
    RegistryService(store, FakeEvaluator(), FakeClock(), FakeIds()).import_seed(admin(), ROOT)
    runtime = PostgresRegistry(store, FakeClock())  # type: ignore[arg-type]  # works over any RegistryStore
    members = RegistryDirectory(store, runtime, runtime.release).members(DIRECTORY)
    assert [agent.id for _, agent in members] == SPECIALISTS
```

The last test assumes `PostgresRegistry` only uses the `RegistryStore` protocol. If it calls something only `PgRegistryStore` has, **remove that test** and say so in the report. `InMemoryDirectory` covers the same path in Task 4. Do not change `PostgresRegistry`. If `tx.aliases_named` is not on the in-memory `_Tx`, read the aliases with `tx.get_alias(agent, "prod")` instead.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/composition/test_transfer_demo_registry.py -q`
Expected: FAIL. The registry folder does not exist (`violations` empty but no entities, or `main(["validate", …])` returns non-zero).

- [ ] **Step 3: Create the registry**

Copy these files from `tests/fixtures/registry-demo` **unchanged** into the same relative paths:
- `flows/disputa-cargo@1.0.0.yaml`
- `injection_rulesets/injection-rules@1.0.0.yaml`
- `language_detection/lang-es-pt@1.0.0.yaml`
- `model_profiles/perfil-generacion@1.0.0.yaml`
- `policies/escalamiento-disputa-monto@1.0.0.yaml`
- `prompts/p/resumen_radicado@1.0.0.yaml`
- all of `templates/t/*.yaml`
- all five files of `tools/*.yaml`

Copy `decision_models/understand-turno@1.0.0.yaml` and `decision_models/match-cargo@2.0.0.yaml` with **one change**: `thresholds_from: cal-transfer-demo` (F2).

`agents/disputas@1.0.0.yaml` is `atencion@1.0.0.yaml` with `id: disputas` plus:

```yaml
routing:
  directory: atencion-cliente
  summary: "Disputa un cargo de la tarjeta que la persona no reconoce."
  examples: ["no reconozco un cargo", "me cobraron algo que no compré"]
accepts:
  slots:
    problema: {type: string, required: true}
```

`agents/consultas@1.0.0.yaml`:

```yaml
id: consultas
version: 1.0.0
mode: conversational
entry_flow: consulta-pqr@1
invocable_by: [customer]
min_auth_level: session
subject_kinds: [customer]
supported_locales: [es, pt]
default_locale: es
tools_allowed: [obtener_pqr@1]
budgets: {max_nodes_per_turn: 40, max_model_calls_per_turn: 3, max_tokens_per_run: 20000, max_cost_per_run: 0.50, max_wall_ms_per_turn: 8000}
templates:
  clarify: t/aclarar
  abstain: t/abstencion
  handoff: t/traspaso
  pending_ack: t/acuse
  pending_offer: t/oferta
  unsupported_language: t/idioma_no_soportado
  input_too_large: t/mensaje_largo
understand: understand-turno@1
max_clarifications: 2
on_clarify_exhausted: escalate
default_target_queue: general
routing:
  directory: atencion-cliente
  summary: "Consulta el estado de una PQR ya radicada."
  examples: ["¿cómo va mi reclamo?", "quiero saber el estado de mi PQR"]
accepts:
  slots:
    problema: {type: string, required: true}
```

`agents/recepcion@1.0.0.yaml`: same as `consultas`, but with `id: recepcion`, `entry_flow: recepcion@1` and `tools_allowed: [directory/list@1]`, and **without** `routing` or `accepts`. Reception is in no directory and receives no transfers.

`flows/recepcion@1.0.0.yaml` (spec §5.1). The `locale` argument is a literal (spec §12.17, still open):

```yaml
id: recepcion
version: 1.0.0
priority: 50
nodes:
  - {id: entender, type: collect, config: {slot: problema, prompt_ref: t/pedir_problema, max_attempts: 2},
     next: {ok: listar, max_attempts: esc_sin_datos}}
  - {id: listar, type: tool, config: {tool: directory/list@1, args: {directory: atencion-cliente, locale: es}, save_as: directorio},
     next: {ok: elegir, error: esc_directorio, timeout: esc_directorio, denied: esc_directorio}}
  - {id: elegir, type: decide, config: {model: elegir-especialista@1, branch_on: choice, save_as: ruta,
       choices_from: facts.directorio.value.choices, input_view: [slots.problema, facts.directorio.value.entries]},
     next: {chosen: avisar, none: aclarar, low_confidence: aclarar}}
  - {id: avisar, type: respond, config: {template_ref: t/te_comunico}, next: {next: transferir}}
  - {id: transferir, type: transfer, config: {target_from: decisions.ruta.choice, directory_from: directorio,
       packet: {reason: routed, slots: [problema]}},
     next: {rejected: esc_rechazo}}
  - {id: aclarar, type: respond, config: {template_ref: t/aclarar_problema, await: true}, next: {next: entender}}
  - {id: esc_sin_datos, type: escalate, config: {reason_code: low_confidence}}
  - {id: esc_directorio, type: escalate, config: {reason_code: tool_failure}}
  - {id: esc_rechazo, type: escalate, config: {reason_code: "policy:transfer_rejected"}}
```

`flows/consulta-pqr@1.0.0.yaml`:

```yaml
id: consulta-pqr
version: 1.0.0
priority: 50
nodes:
  - {id: pedir_radicado, type: collect, config: {slot: radicado, prompt_ref: t/pedir_radicado, max_attempts: 2},
     next: {ok: consultar, max_attempts: esc_sin_datos}}
  - {id: consultar, type: tool, config: {tool: obtener_pqr@1, args: {radicado: slots.radicado}, save_as: pqr},
     next: {ok: responder, error: esc_tool, timeout: esc_tool, denied: esc_tool}}
  - {id: responder, type: respond, config: {template_ref: t/estado_pqr}, next: {next: fin}}
  - {id: fin, type: end, config: {outcome: resolved}}
  - {id: esc_sin_datos, type: escalate, config: {reason_code: low_confidence}}
  - {id: esc_tool, type: escalate, config: {reason_code: tool_failure}}
```

`tools/directory/list@1.0.0.yaml`. It must equal `DIRECTORY_TOOL` field by field, description included:

```yaml
id: directory/list
version: 1.0.0
risk_class: read
min_auth_level: anonymous
idempotent: true
source: directory
description: "Lists the specialists of a directory the current person can be transferred to."
args_schema:
  type: object
  properties:
    directory: {type: string}
    locale: {type: string}
  required: [directory, locale]
```

`decision_models/elegir-especialista@1.0.0.yaml` (F3):

```yaml
id: elegir-especialista
version: 1.0.0
output_schema:
  type: object
  additionalProperties: false
  properties:
    choice: {type: string}
calibrated_fields: [choice]
input_view: [slots.problema, facts.directorio.value.entries]
providers: [{provider: classifier, config: {artifact: sintetico}}]
calibration: {method: none}
thresholds_from: cal-transfer-demo
```

New templates, each with both locales (G0-12):

| Archivo | `es` | `pt` |
|---|---|---|
| `templates/t/pedir_problema@1.0.0.yaml` | "¿En qué te puedo ayudar?" | "Em que posso ajudar?" |
| `templates/t/te_comunico@1.0.0.yaml` | "Te comunico con un especialista." | "Vou te transferir para um especialista." |
| `templates/t/aclarar_problema@1.0.0.yaml` | "No entendí bien qué necesitas. ¿Me lo cuentas con otras palabras?" | "Não entendi bem o que você precisa. Pode explicar com outras palavras?" |
| `templates/t/pedir_radicado@1.0.0.yaml` | "¿Cuál es el número de radicado de tu PQR?" | "Qual é o número de protocolo da sua solicitação?" |
| `templates/t/estado_pqr@1.0.0.yaml` | "Ya consulté tu PQR. Si necesitas algo más, dímelo." | "Já consultei sua solicitação. Se precisar de algo mais, me avise." |

Releases (F1). Each file follows the format of `registry-demo/releases/demo.yaml` and carries the same `interrupts`, `language_detection` and `injection_ruleset`:

```yaml
# releases/recepcion-demo.yaml
id: recepcion-demo
agents:
  - {agent: "recepcion@^1", aliases: [prod]}
flows: ["recepcion@^1"]
interrupts:
  - {id: fraude, priority: 100, action: {type: escalate, target_queue: fraude, priority: critical}}
language_detection: "lang-es-pt@1"
injection_ruleset: "injection-rules@1"
```

- `releases/disputas-demo.yaml`: `disputas@^1` with `flows: ["disputa-cargo@^1"]`.
- `releases/consultas-demo.yaml`: `consultas@^1` with `flows: ["consulta-pqr@^1"]`.

`calibrations/cal-transfer-demo.json` (F2). Generate it once with the canonical serializer, so the file is `to_json()` byte for byte:

```bash
uv run python - <<'EOF'
from pathlib import Path
from agent_core.decision.calibration.artifact import CalibrationArtifact, Target

jev = {("command", c, "jev", "es"): 0.5
       for c in ("continue", "affirm", "deny", "start_flow", "out_of_scope", "interrupt")}
jev |= {("flow", f, "jev", "es"): 0.5 for f in ("disputa-cargo", "consulta-pqr", "recepcion")}
jev[("interrupt", "fraude", "jev", "es")] = 0.5
other = {("match", "unica", "classifier", "es"): 0.5, ("choice", "*", "classifier", "es"): 0.8}
art = CalibrationArtifact(
    run_id="cal-transfer-demo", split_hash="c" * 64, method="none", calibrators={},
    thresholds={**jev, **other}, target={"command": Target(metric="precision", value=0.9)},
    limitations=["artefacto hecho a mano para la demo de transferencia (spec §12.1): no sale de una calibración"])
out = Path("tests/fixtures/registry-transfer-demo/calibrations/cal-transfer-demo.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(art.to_json() + "\n", encoding="utf-8")
EOF
```

Notes:
- This is a one-off generator, not code in the repo. The test checks that the file is canonical.
- Only `es`: the demo is in Spanish. In `pt`, every runtime choice stays below threshold, the safe value.

- [ ] **Step 4: Run the tests and the CLI**

Run: `uv run pytest tests/composition/test_transfer_demo_registry.py -q` and `uv run agentcore validate tests/fixtures/registry-transfer-demo`
Expected: PASS, and `validate` exits 0.

If M1 reports a violation, fix the **fixture**, not M1. For example, a rule may require `input_view` on the decision model to cover the node's `input_view`. If the fix would need an M1 change, stop and ask.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/registry-transfer-demo tests/composition/test_transfer_demo_registry.py
git commit -F msg.txt   # test(transfer): demo registry with reception and two specialists
```

---

### Task 3: UoW — a lo sumo un run abierto por sesión

**Modelo recomendado:** opus.

**Files:**
- Modify: `agent_core/adapters/sql/schema.sql`, `agent_core/adapters/postgres_uow.py`, `testing/fakes/storage.py`
- Test: `tests/contracts/test_uow_contract.py` (three new checks in `CHECKS`), `tests/integration/test_m4_postgres.py` (one index test)
- Docs: `docs/specs/motor/m04-ciclo-del-turno.md` (§5 step 5, line 138; invariants, line 157), transfer spec §5.3 and §12.8

**Interfaces:**
- Produce:
  - index `runs_one_open_per_session`;
  - on `commit()`, a second open run in a session raises `VersionConflict("la sesión ya tiene un run abierto (runs_one_open_per_session)")` and applies nothing, in both backends.

- [ ] **Step 1: Write the failing contract checks**

In `tests/contracts/test_uow_contract.py`, next to `check_session_prefers_the_open_run_and_lists_all`:

```python
_TRANSFERRED = {"status": "closed", "outcome": "transferred", "closed_at": NOW, "inactive_after": None}


def check_a_session_holds_at_most_one_open_run(b: Backend) -> None:
    _seed(b.factory, run_id="run-a")  # open, session-0001
    with b.factory() as uow:
        uow.save_run(run_state(run_id="run-b"), 0)  # a second open run in the same session
        with pytest.raises(VersionConflict, match="run abierto"):
            uow.commit()
    with b.factory() as uow:
        assert uow.load_run("run-b") is None  # nothing applied
        found = uow.find_run_by_session("session-0001")
        assert found is not None and found.run_id == "run-a"


def check_closing_the_origin_and_opening_the_target_commit_together(b: Backend) -> None:
    _seed(b.factory, run_id="run-a")
    with b.factory() as uow:
        origin = uow.load_run("run-a")
        assert origin is not None
        uow.save_run(origin.model_copy(update=_TRANSFERRED), origin.state_version)
        uow.save_run(run_state(run_id="run-b"), 0)
        uow.commit()
    with b.factory() as uow:
        found = uow.find_run_by_session("session-0001")
        assert found is not None and found.run_id == "run-b"


def check_open_target_saved_before_closing_origin_still_commits(b: Backend) -> None:
    """The index is checked per statement in Postgres: the UoW applies closing writes first (F6)."""
    _seed(b.factory, run_id="run-a")
    with b.factory() as uow:
        origin = uow.load_run("run-a")
        assert origin is not None
        uow.save_run(run_state(run_id="run-b"), 0)  # target first
        uow.save_run(origin.model_copy(update=_TRANSFERRED), origin.state_version)  # then the origin closes
        uow.commit()
    with b.factory() as uow:
        assert [r.run_id for r in uow.list_runs_by_session("session-0001")] == ["run-a", "run-b"]
```

Add the three checks to `CHECKS`.

In `tests/integration/test_m4_postgres.py` add, following the fixtures of that file:

```python
def test_the_schema_has_the_one_open_run_per_session_index(...) -> None:  # use the file's connection fixture
    row = conn.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'runs_one_open_per_session'").fetchone()
    assert row is not None and "WHERE" in row[0] and "status" in row[0]
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/contracts/test_uow_contract.py -q`
Expected: `check_a_session_holds_at_most_one_open_run[memory]` FAILS (no `VersionConflict`). The other two pass in memory. The `postgres` variants are skipped (no docker).

- [ ] **Step 3: Implement**

`agent_core/adapters/sql/schema.sql`. Replace the comment of `run_seq`:

```sql
    run_seq        bigserial   NOT NULL,          -- creation order (the runs of a session, in order)
```

After `runs_session_idx`, add:

```sql
-- At most one open run per session (ADR 0021 D1). A transfer closes the origin and opens the target in one
-- commit; the UoW applies closing writes first, because a partial unique index cannot be deferred.
-- On a database that already holds two open runs of one session this statement fails: recreate it.
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_open_per_session ON runs (session_id)
    WHERE status = 'open' AND session_id IS NOT NULL;
```

`agent_core/adapters/postgres_uow.py`:

```python
_ONE_OPEN_RUN = "runs_one_open_per_session"
ONE_OPEN_RUN_MESSAGE = "la sesión ya tiene un run abierto (runs_one_open_per_session)"

    def commit(self) -> None:
        self._check_open()
        try:
            with self._conn.transaction():  # una sola transacción: se aplica completa o no se aplica
                self._apply()
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == _ONE_OPEN_RUN:
                raise VersionConflict(ONE_OPEN_RUN_MESSAGE) from None
            # Otro escritor encadenó el mismo `seq` (o repitió el `event_id`) sin pasar por `save_run`: es una
            # carrera de versión, no un error de base. Se aplica nada y quien llama reintenta.
            raise VersionConflict("la cadena de auditoría cambió: otro escritor commiteó primero") from None
        finally:
            self._done = True

    def _apply(self) -> None:
        conn = self._conn
        # Closing writes first: `runs_one_open_per_session` is checked per statement (F6).
        ordered = sorted(self._base_versions.items(), key=lambda item: self._runs[item[0]].status == "open")
        for run_id, base in ordered:
            ...  # unchanged body
```

`testing/fakes/storage.py`:
- In `_apply`, after the version check loop and **before** any write, call `self._check_one_open_run_per_session(store)`.
- Import the message from the adapter **only if** `.importlinter` allows `testing` to import `agent_core.adapters`. Otherwise duplicate the string literal and add a test that both are equal.

```python
    def _check_one_open_run_per_session(self, store: InMemoryStore) -> None:
        """Mirror of the partial unique index `runs_one_open_per_session` on the final state of the commit."""
        touched = {s.session_id for s in self._runs.values() if s.session_id is not None and s.status == "open"}
        for session_id in touched:
            ids = set(store.sessions.get(session_id, []))
            ids |= {rid for rid, s in self._runs.items() if s.session_id == session_id}
            open_runs = [rid for rid in ids if (self._runs.get(rid) or store.runs[rid]).status == "open"]
            if len(open_runs) > 1:
                raise VersionConflict(ONE_OPEN_RUN_MESSAGE)
```

Fix the module docstring of `testing/fakes/storage.py` (line 9): `load_run` is O(1); `find_run_by_session` and `list_runs_by_session` are linear in the runs of the session.

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. If a test outside the contract fails because it seeds two **open** runs in the default `session-0001`, give the second run its own `session_id`; it was relying on a state the base now forbids. List every such test in the report. Today a reading of the code finds none: `tests/m09/test_session_lineage.py:113` seeds a closed run and an open one, which is valid.

Then `uv run lint-imports && uv run mypy && uv run ruff check .`

- [ ] **Step 5: Docs and commit**

- m04 §5 step 5 (line 138): replace "Así, si un día se agrega … decisión pendiente" with:
  - la base lo impone con `runs_one_open_per_session`;
  - la UoW aplica primero las escrituras que cierran;
  - una violación es `VersionConflict` con mensaje propio;
  - **la ruta de Postgres está sin verificar** (sin docker).
- m04 line 157: lo garantizan el motor y la base.
- Transfer spec §5.3, first bullet: la base lo impone (índice parcial; Postgres sin verificar).
- Transfer spec §12.8: **Cerrado (fase 7)**, with the same note.

```bash
git add agent_core/adapters testing/fakes/storage.py tests/contracts tests/integration/test_m4_postgres.py docs/specs
git commit -F msg.txt   # feat(uow): at most one open run per session, enforced by the store (postgres path unverified)
```

The commit body must say: "Postgres: index and UniqueViolation mapping not executed here (no docker); run `docker compose up -d postgres && uv run pytest tests/integration tests/contracts`."

---

### Task 4: El mundo de la demo, el catálogo y la demo en proceso y por HTTP

**Modelo recomendado:** opus. `EngineWorld` y `Driver` graban los seis fixtures committeados: cualquier cambio de comportamiento los rompe.

**Files:**
- Modify: `testing/fakes/registry_dir.py`, `testing/engine_world.py`
- Modify: `tests/composition/test_serve_app.py` (`make_ports` passes `directory=d.directory`; done in Task 1)
- Test: `tests/composition/test_transfer_demo.py` (nuevo)

**Interfaces:**
- Produce:
  - `registry_from_releases(root: Path, release_ids: Iterable[str] | None = None) -> InMemoryRegistry`;
  - `EngineWorld(..., releases: tuple[str, ...] = (RELEASE_ID,), agent: str = "atencion", directory: bool = False, calibrations: Mapping[str, CalibrationArtifact] | None = None)`;
  - `EngineWorld.directory: InMemoryDirectory | None`;
  - `EngineWorld.routes(choice: str, p: float = 0.9)`;
  - `Driver.agent: str = "atencion"`;
  - `transfer_world(**over: Any) -> EngineWorld`;
  - `TRANSFER_DEMO`, `TRANSFER_RELEASES`, `TRANSFER_CALIBRATION` and `transfer_calibration()`.

- [ ] **Step 1: Write the failing tests**

`tests/composition/test_transfer_demo.py`:

```python
"""Phase 7 demo (ADR 0021, spec §1): reception transfers to `disputas`, which answers in the same turn."""

from dataclasses import replace

from fastapi.testclient import TestClient

from agent_core.api.app import create_app
from agent_core.api.schemas import session_lineage
from agent_core.audit import verify_transfer_link
from agent_core.composition.serve import build_api_deps
from agent_core.domain import Outcome, directory_hash, dumps
from testing.engine_world import transfer_calibration, transfer_world
from testing.fakes.identity import TestIdentityIssuer
from tests.composition.test_serve_app import make_ports

TEXT = "no reconozco un cargo de ciento veinte dólares en una tienda"
ASK = "¿En qué te puedo ayudar?"
HANDOVER = "Te comunico con un especialista."
DISPUTAS_FIRST = "¿Qué cargo quieres disputar?"


def _script_transfer(w, to: str = "disputas", flow: str = "disputa-cargo") -> None:  # type: ignore[no-untyped-def]
    w.understands("continue")              # reception: the text answers its `collect`
    w.routes(to)                           # elegir-especialista picks `to` (classifier, p=0.9 >= "*" 0.8)
    w.understands("start_flow", flow=flow)  # the specialist starts its flow with the same text (P4)


def test_reception_transfers_and_disputas_answers_in_the_same_turn() -> None:
    w = transfer_world()
    started = w.start()
    assert [m.text for m in started.first_turn.messages] == [ASK]
    _script_transfer(w)
    result = w.turn(TEXT)

    assert result.agent is not None and result.agent.id == "disputas"
    assert [m.text for m in result.messages] == [HANDOVER, DISPUTAS_FIRST]
    with w.store.uow() as uow:
        origin, target = uow.list_runs_by_session(started.session_id)
    assert (origin.agent.id, origin.release, origin.outcome) == ("recepcion", "recepcion-demo", Outcome.transferred)
    assert (target.agent.id, target.release, target.status) == ("disputas", "disputas-demo", "open")
    assert result.run_id == target.run_id
    assert verify_transfer_link(target, w.audit) == []

    moved = next(e for e in w.audit.read(origin.run_id) if e.type == "run_transferred")
    assert moved.payload.directory == "atencion-cliente"
    assert moved.payload.candidates == ["consultas", "disputas"]
    assert moved.payload.directory_hash == directory_hash(
        [("consultas", "consultas-demo"), ("disputas", "disputas-demo")])

    lineage = session_lineage(started.session_id, [origin, target], "trace-demo")
    assert [r["release"] for r in lineage["runs"]] == ["recepcion-demo", "disputas-demo"]
    assert lineage["runs"][1]["origin"]["transfer_id"] == moved.payload.transfer_id


def test_the_router_passed_by_the_hand_made_wildcard_threshold() -> None:
    w = transfer_world()
    w.start()
    _script_transfer(w)
    result = w.turn(TEXT)
    with w.store.uow() as uow:
        origin = uow.list_runs_by_session(result.session_id)[0]  # adjust if TurnResult has no session_id
    routed = [e for e in w.audit.read(origin.run_id)
              if e.type == "decision_made" and e.payload.model.id == "elegir-especialista"]
    assert len(routed) == 1 and routed[0].payload.above_threshold == {"choice": True}


def test_without_the_wildcard_threshold_reception_asks_again() -> None:
    art = transfer_calibration()
    no_wildcard = art.model_copy(update={"thresholds": {k: v for k, v in art.thresholds.items() if k[1] != "*"}})
    w = transfer_world(calibrations={art.run_id: no_wildcard})
    started = w.start()
    w.understands("continue")
    w.routes("disputas")
    result = w.turn(TEXT)
    assert [m.text for m in result.messages] == ["No entendí bien qué necesitas. ¿Me lo cuentas con otras palabras?"]
    with w.store.uow() as uow:
        assert len(uow.list_runs_by_session(started.session_id)) == 1


def test_reception_can_also_transfer_to_consultas() -> None:
    w = transfer_world()
    w.start()
    _script_transfer(w, to="consultas", flow="consulta-pqr")
    result = w.turn("quiero saber cómo va mi reclamo de la semana pasada")
    assert result.agent is not None and result.agent.id == "consultas"
    assert [m.text for m in result.messages] == [HANDOVER, "¿Cuál es el número de radicado de tu PQR?"]


def test_no_event_carries_the_customer_text() -> None:
    w = transfer_world()
    started = w.start()
    _script_transfer(w)
    w.turn(TEXT)
    with w.store.uow() as uow:
        runs = uow.list_runs_by_session(started.session_id)
    for run in runs:
        assert TEXT not in dumps(w.audit.read(run.run_id)).decode("utf-8")


def test_the_directory_ids_reach_the_router_in_clear() -> None:
    """F4: `choices`, `agent_id` and `release_id` are public; without the rules they would be tokens."""
    w = transfer_world()
    w.start()
    _script_transfer(w)
    w.turn(TEXT)
    model_inputs = w.classifier.calls[-1]  # adjust to how ScriptedProvider records its inputs
    entries = model_inputs["facts.directorio.value.entries"]
    assert [e["agent_id"] for e in entries] == ["consultas", "disputas"]
    assert [e["release_id"] for e in entries] == ["consultas-demo", "disputas-demo"]


def test_the_transfer_demo_goes_through_http() -> None:
    world = transfer_world()
    issuer = TestIdentityIssuer(world.clock)
    ports = make_ports(world, issuer)
    assert ports.directory is not None  # deps.directory of the non-recording world (F5)
    client = TestClient(create_app(build_api_deps(ports)), raise_server_exceptions=False)
    bearer = {"Authorization": f"Bearer {issuer.customer()}"}
    created = client.post("/v1/runs", json={"agent": "recepcion"}, headers={**bearer, "Idempotency-Key": "k-tr"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert [m["text"] for m in body["first_turn"]["messages"]] == [ASK]
    _script_transfer(world)
    turn = client.post(f"/v1/sessions/{body['session_id']}/turns", headers=bearer,
                       json={"text": TEXT, "channel": "web", "client_turn_id": "c-1"})
    assert turn.status_code == 200, turn.text
    assert [m["text"] for m in turn.json()["messages"]] == [HANDOVER, DISPUTAS_FIRST]
    assert turn.json()["run_id"] != body["run_id"]
    lineage = client.get(f"/v1/sessions/{body['session_id']}/lineage", headers=bearer)
    assert lineage.status_code == 200, lineage.text
    assert [r["release"] for r in lineage.json()["runs"]] == ["recepcion-demo", "disputas-demo"]
```

Notes for the implementer:
- Adjust the two lines marked "adjust" to the real API: how `TurnResult` exposes the session, and how `ScriptedProvider` records its calls. If it records none, add a `calls` list to `ScriptedProvider` (it is a test double in `testing/fakes`).
- Keep the assertions themselves.
- If `TestIdentityIssuer.customer()` yields a principal whose subject or level is not eligible, use the issuer option that yields a `customer` with `subject.kind == "customer"`. Do not loosen `SyntheticAuthz`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/composition/test_transfer_demo.py -q`
Expected: FAIL with `ImportError: cannot import name 'transfer_world'`.

- [ ] **Step 3: Implement**

`testing/fakes/registry_dir.py`:

```python
def registry_from_releases(root: Path, release_ids: Iterable[str] | None = None) -> InMemoryRegistry:
    """Like `registry_from_directory` for several releases (one per agent, as `registry import` requires).
    `None` pins every release of the directory."""
    reg, violations = load_registry(root)
    _raise_if_invalid([*violations, *validate_registry(reg)])  # the current error text, moved to a helper
    memory = InMemoryRegistry()
    for release_id in (list(release_ids) if release_ids is not None else [d.id for d in reg.releases()]):
        pinned = pin_release(reg, release_id)
        memory.add(*pinned.entities)
        for agent_id, aliases in pinned.aliases.items():
            for alias in aliases:
                memory.add_release(pinned.release, agent_id, alias=alias)
    return memory


def registry_from_directory(root: Path, release_id: str) -> InMemoryRegistry:
    return registry_from_releases(root, [release_id])
```

`testing/engine_world.py`:

1. Catalog (F4):

```python
CATALOG = {
    **DEFAULT_CATALOG,
    "amount": FieldRule(field_class="financial"),
    "currency": FieldRule(field_class="public"),
    "status": FieldRule(field_class="public"),
    "id": FieldRule(field_class="public"),
    # Directory option ids (ADR 0021, spec §3.2): not PII; tokenized, `decide_choice` could not choose.
    "directory.choices": FieldRule(field_class="public"),
    "directory.entries.agent_id": FieldRule(field_class="public"),
    "directory.entries.release_id": FieldRule(field_class="public"),
}
```

2. Constants and the hand-made artifact:

```python
TRANSFER_DEMO = Path(__file__).parents[1] / "tests" / "fixtures" / "registry-transfer-demo"
TRANSFER_RELEASES = ("recepcion-demo", "disputas-demo", "consultas-demo")
TRANSFER_CALIBRATION = "cal-transfer-demo"


def transfer_calibration() -> CalibrationArtifact:
    """The hand-made artifact of the transfer demo (U2): its `"*"` threshold lets a runtime choice pass."""
    artifact = DirectoryCalibrationSource(TRANSFER_DEMO / "calibrations").get(TRANSFER_CALIBRATION)
    assert artifact is not None, "falta calibrations/cal-transfer-demo.json en el registro de la demo"
    return artifact
```

3. `Driver` gets the field `agent: str = "atencion"`, declared after `engine` and before the fields with factories. `start` uses `AgentSelector(id=self.agent, alias="prod")`. The recorded ops do **not** change: the agent is not written into the op.

4. `EngineWorld.__init__`:
- Add the keyword arguments `releases: tuple[str, ...] = (RELEASE_ID,)`, `agent: str = "atencion"`, `directory: bool = False` and `calibrations: Mapping[str, CalibrationArtifact] | None = None`.
- Then:

```python
        self.registry = registry_from_releases(registry_root, releases)
        self._release_ids = releases
        self.release: Release = self.registry.resolve_release(
            AgentSelector(id=agent, alias="prod"), principal_at("step_up"))
        ...
        self.authz = SyntheticAuthz()
        self.directory = InMemoryDirectory(self.registry) if directory else None
        inner: ToolExecutor = self.tools
        if self.directory is not None and record:
            # F5: the recording must see `directory/list` to replay it (replay serves every tool from the record).
            inner = DirectoryToolExecutor(self.tools, self.directory, self.authz, self.ids)
        self.recording_tools = RecordingToolExecutor(inner) if record else None
        ...
        self.deps = EngineDeps(
            ..., tools=self.recording_tools or inner, ...,
            calibrations=InMemoryCalibrationSource(calibrations or {"cal-demo": demo_calibration()}),
            authz=self.authz, ...,
            directory=None if record else self.directory)
        self.driver = Driver(self.engine, agent=agent)
```

Use `self.authz` for the `authz` field as well; the value is the same `SyntheticAuthz`.

- `_release(release_id)` returns `self.registry.resolve_release_by_id(release_id)`; drop the assertion.
- `_register_tools` and `script_tool` work over the **union** of the tools of every release in `self._release_ids`. Build `self._tool_versions: dict[str, str]` once and **skip `directory/list`**: `DirectoryToolExecutor` serves it.

5. Router script and factory:

```python
    def routes(self, choice: str, p: float = 0.9) -> None:
        """`elegir-especialista` (classifier) picks `choice` among the directory options."""
        self.classifier.push(RawPrediction(value={"choice": choice}, p_raw={"choice": p}, tokens=10))


def transfer_world(**over: Any) -> EngineWorld:
    """Reception and two specialists over `registry-transfer-demo` (ADR 0021, phase 7)."""
    options: dict[str, Any] = {"registry_root": TRANSFER_DEMO, "releases": TRANSFER_RELEASES, "agent": "recepcion",
                               "directory": True, "calibrations": {TRANSFER_CALIBRATION: transfer_calibration()}}
    return EngineWorld(**(options | over))
```

`testing` may import `agent_core.composition` (it already imports `EngineDeps`). Import `DirectoryToolExecutor` from `agent_core.composition` (Task 1). Import `InMemoryDirectory` from `testing.fakes.directory`.

- [ ] **Step 4: Run the tests, the six fixtures included**

Run: `uv run pytest tests/composition tests/m11 tests/m04 -q`
Expected: PASS. `test_el_fixture_committeado_esta_vigente` must stay green **without** re-recording: the defaults reproduce today's world exactly, and the new catalog rules only match `directory.*` paths.

Then `uv run lint-imports && uv run mypy && uv run ruff check .`

- [ ] **Step 5: Docs and commit**

Transfer spec:
- §12.14: "el catálogo de la demo (`testing/engine_world.CATALOG`) clasifica `directory.choices`, `directory.entries.agent_id` y `directory.entries.release_id` como `public`; los catálogos de producción deben hacer lo mismo" (still the deployer's job).
- §11: phase 7 row "en curso".

```bash
git add testing tests/composition docs/specs/2026-09-30-transferencia-entre-agentes-design.md
git commit -F msg.txt   # test(transfer): demo world with reception and two specialists, in process and over HTTP
```

---

### Task 5: Replay (M11) — ids de la transferencia, fixture con cadenas enlazadas y comparación

**Modelo recomendado:** opus. **Requiere la Pregunta 1** (y la 2 para Step 3d). Con un "no" a la Pregunta 1, solo se hacen Step 1a, Step 3a y Step 5.

**Files:**
- Modify: `agent_core/audit/replay/ports.py`, `fixture.py`, `recording.py`, `runner.py`, `compare.py`, `synthetic.py`
- Test: `tests/m11/test_replay_transfer.py` (nuevo)
- Docs: `docs/specs/motor/m11-auditoria-transcript-replay.md` (decisions 9, 13, 14 and 20 and §3.5; a new decision "Sesión con transferencia")

**Interfaces:**
- Produce:
  - `RecordedIds.from_events` collects `IdKind.run` from `payload.to_run_id` and `IdKind.transfer` from `payload.transfer_id`;
  - `Fixture.linked: list[AnyEvent] = []`, the last field;
  - `build_fixture(..., linked: Sequence[EngineEvent] = ())`;
  - `Replayer.replay` over `events + linked`, with each linked chain checked;
  - `normalize` drops `payload.origin.from_event_hash` of `run_started`;
  - `check_fixture` lets 64-hex leaves through.

- [ ] **Step 1: Write the failing tests**

`tests/m11/test_replay_transfer.py`. Build the events with the M0 models: a `run_started`, a `run_transferred` and a `transfer_rejected` of the origin, and a `run_started` with `origin` of the target. The helpers in `tests/m00/samples.py` (`make_event`) show the envelope.

```python
"""Replay of a session that transferred (ADR 0021, phase 7; M11 decisions 9 and 14)."""

from agent_core.audit import ReplayReport, dump_fixture, load_fixture
from agent_core.audit.replay.compare import first_divergence, normalize
from agent_core.audit.replay.ports import RecordedIds
from agent_core.audit.replay.synthetic import SyntheticCatalog, check_fixture
from agent_core.ports import IdKind


def test_recorded_ids_hand_out_the_target_run_and_the_transfer_ids() -> None:  # 1a
    ids = RecordedIds.from_events([ORIGIN_STARTED, REJECTED, TRANSFERRED])
    assert [ids.new_id(IdKind.run), ids.new_id(IdKind.run)] == ["run-a", "run-b"]
    assert [ids.new_id(IdKind.transfer), ids.new_id(IdKind.transfer)] == ["transfer-0001", "transfer-0002"]


def test_a_fixture_without_linked_chains_dumps_as_before() -> None:  # 1b
    text = dump_fixture(FIXTURE_WITHOUT_LINKED)
    assert "linked" not in text and load_fixture(text).linked == []


def test_a_fixture_with_linked_chains_round_trips() -> None:  # 1b
    assert load_fixture(dump_fixture(FIXTURE_WITH_LINKED)).linked == FIXTURE_WITH_LINKED.linked


def test_from_event_hash_is_not_compared_but_the_rest_of_origin_is() -> None:  # 1c
    other_hash = TARGET_STARTED.model_copy(update={"payload": TARGET_STARTED.payload.model_copy(update={
        "origin": TARGET_STARTED.payload.origin.model_copy(update={"from_event_hash": "f" * 64})})})
    assert first_divergence([TARGET_STARTED], [other_hash]) is None
    other_transfer = ...  # same, changing origin.transfer_id
    assert first_divergence([TARGET_STARTED], [other_transfer]) is not None


def test_a_broken_linked_chain_is_chain_broken() -> None:  # 1d
    report = Replayer(_NeverRuns(), FakeClock()).replay(FIXTURE_WITH_TAMPERED_LINKED, "fixture")
    assert report.verdict == "chain_broken"


def test_a_sha256_hex_leaf_in_full_is_not_a_number() -> None:  # 1e (Pregunta 2)
    check_fixture(fixture_with_full({"hash": "1234567" + "a" * 57}), SyntheticCatalog(
        email_domains=["example.test"], numbers=[], values=[]))  # does not raise
```

Write the elided constants (`ORIGIN_STARTED`, `FIXTURE_*`, `_NeverRuns`, …) in the file, **with synthetic data only**. Chain them with `testing`'s `AuditLog`, or with the chain helpers already used in `tests/m11`, so that `check_chain` passes on the untampered ones.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/m11/test_replay_transfer.py -q`
Expected: FAIL. `run-replay-0001` instead of `run-b`; `Fixture` has no `linked`; the hash divergence is reported; the hex leaf is rejected.

- [ ] **Step 3: Implement**

(a) `ports.py`, `RecordedIds.from_events`. Add two pairs to the payload loop:

```python
            for name, kind in (("call_id", IdKind.call), ("readback_call_id", IdKind.call),
                               ("decision_id", IdKind.decision), ("action_id", IdKind.action),
                               ("handoff_ref", IdKind.handoff),
                               ("to_run_id", IdKind.run),            # ADR 0021: the target the engine opens
                               ("transfer_id", IdKind.transfer)):    # run_transferred / transfer_rejected / received
                add(kind, getattr(payload, name, None))
```

(b) `fixture.py`:
- add the last field `linked: list[AnyEvent] = Field(default_factory=list)`, documented as "chains of the runs this run transferred to (same session, creation order)";
- `dump_fixture` builds `data = to_jsonable(fixture)` and, if `not fixture.linked`, does `data.pop("linked")` before `yaml.dump`. Fixtures without a transfer keep their committed bytes.

In `recording.py`: `build_fixture(..., linked: Sequence[EngineEvent] = ())` passes `linked=cast("list[AnyEvent]", list(linked))`.

(c) `runner.py`, `Replayer.replay`:
- `_resolve` also returns `linked`: from the `Fixture`, or `[]` for a `run_id` (session replay from the audit store stays pending);
- after the origin chain check, check each linked run in creation order:

```python
        for linked_run, chain_events in _by_run(linked):  # [(run_id, events)] in first-appearance order
            check = check_chain(linked_run, chain_events)
            if not check.ok:
                return self._report(mode, run_id, release, start, "chain_broken", chain_broken_at=check.broken_at)
        recorded = [*events, *linked]
        ports = build_ports(recorded, mode, full=full, drafts=drafts, definitions=self._definitions)
        case = ReplayCase(run_id, release, mode, inputs, recorded)
        ...
        found = first_divergence(recorded, produced)
```

The `EngineRunner` docstring states the new contract: it returns the events of every run of the session, in creation order.

`compare.py`, `normalize`:

```python
    if data.get("type") == "run_started" and isinstance(payload, dict):
        origin = payload.get("origin")
        if isinstance(origin, dict):
            # A chain hash (ADR 0021 P2): replay does not reproduce hashes (`ts`, `duration_ms`), the same reason
            # the envelope `hash` is ignored. The link itself is verified on the recorded chains.
            origin.pop("from_event_hash", None)
```

(d) `synthetic.py` (Pregunta 2):

```python
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
...
        if strict and text not in catalog.values:
            bad.append(path)
        elif _SHA256_HEX.fullmatch(text):
            continue  # a content hash (e.g. the directory hash, ADR 0021), as in `events`: not personal data
        elif any(m.group(1) not in catalog.email_domains for m in _EMAIL.finditer(text)):
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/m11 tests/composition/test_replay_fixtures.py tests/composition/test_replay_scenarios.py -q`
Expected: PASS. The six fixtures replay as `match` and are still current.

Then `uv run lint-imports && uv run mypy && uv run ruff check .`

- [ ] **Step 5: Docs and commit**

In m11, decision 9: `to_run_id` and `transfer_id` are recorded ids.

Add a new decision, "Sesión con transferencia (ADR 0021, fase 7)":
- `Fixture.linked`;
- recorded ports over `events + linked`;
- comparison over both;
- `from_event_hash` is not compared;
- each linked chain is checked with `check_chain`;
- `verify_transfer_link` is not yet part of `agentcore replay` (pending);
- the `run_id` replay (`audit`) does not follow the session (pending).

Decision 13: the sha256-hex exemption.

Transfer spec §12.10: partly closed. Replay of a session with a transfer works in `fixture` mode; the two pending points above remain.

```bash
git add agent_core/audit tests/m11 docs/specs
git commit -F msg.txt   # feat(m11): replay a session that transferred (linked chains, transfer ids)
```

---

### Task 6: Runner del replay con varias releases, escenario `transferencia` y fixture grabado

**Modelo recomendado:** opus. **Requiere la Pregunta 1.** Con un "no", solo se hace Step 3a, para que el runner no esté atado a una release ni a `atencion`, más sus pruebas.

**Files:**
- Modify: `testing/replay/runner.py`, `testing/replay/scenarios.py`, `testing/replay/__init__.py`
- Modify: `tests/composition/test_replay_fixtures.py` (scenario list), `tests/composition/test_replay_scenarios.py` (registry per scenario)
- Create (generated): `tests/fixtures/runs-transfer/transferencia.yaml`
- Test: `tests/composition/test_replay_transfer_fixture.py` (nuevo)

**Interfaces:**
- Produce:
  - `RecordedEngineRunner(registry_root)` over **every** release of the directory;
  - the release comes from `case.release` and the entry agent from the recorded `run_started`;
  - `IdKind.transfer` is in `_RECORDED_KINDS`;
  - synthesized thresholds are registered under every `thresholds_from` of the registry;
  - the runner returns the events of every run of the session;
  - `SCENARIOS["transferencia"]` and `SCENARIO_REGISTRY: dict[str, Path]`;
  - `record_scenario(name, registry_root: Path | None = None)`.

- [ ] **Step 1: Write the failing tests**

`tests/composition/test_replay_transfer_fixture.py`:

```python
"""Phase 7: the recorded transfer session replays as `match`, is current, and its chains are linked."""

from pathlib import Path

import pytest

from agent_core.audit import dump_fixture, load_fixture_file, verify_transfer_link
from agent_core.cli import main
from agent_core.domain import RunStarted
from testing.builders import run_state
from testing.replay import record_scenario

RUN = Path("tests/fixtures/runs-transfer/transferencia.yaml")
REGISTRY = "tests/fixtures/registry-transfer-demo"
CATALOG = "tests/fixtures/catalogo-datos-prueba.yaml"


def test_the_transfer_session_replays_as_match(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", str(RUN), "--mode", "fixture", "--registry", REGISTRY, "--catalog", CATALOG])
    assert code == 0 and capsys.readouterr().out.startswith("match")


def test_the_committed_transfer_fixture_is_current() -> None:
    assert RUN.read_text(encoding="utf-8") == dump_fixture(record_scenario("transferencia", Path(REGISTRY))), (
        "transferencia.yaml quedó desactualizado: regrábalo con `agentcore record transferencia`")


class _Chains:
    """`AuditSink.read` over the recorded chains (all `verify_transfer_link` needs)."""

    def __init__(self, events: list) -> None:  # type: ignore[type-arg]
        self._events = events

    def read(self, run_id: str) -> list:  # type: ignore[type-arg]
        return [e for e in self._events if e.run_id == run_id]


def test_the_recorded_chains_are_linked_by_hash() -> None:
    fixture = load_fixture_file(RUN)
    assert fixture.linked, "the transfer fixture records the target chain"
    started = fixture.linked[0]
    assert isinstance(started, RunStarted) and started.payload.origin is not None
    target = run_state(run_id=started.run_id, session_id=started.session_id, origin=started.payload.origin)
    assert verify_transfer_link(target, _Chains([*fixture.events, *fixture.linked])) == []  # type: ignore[arg-type]
```

In `tests/composition/test_replay_fixtures.py`:
- `test_estan_los_seis_caminos_del_flow_de_demo` asserts `sorted(SCENARIOS) == sorted([*CAMINOS, "transferencia"])`;
- the glob of `tests/fixtures/runs/` stays the six paths: the new fixture lives in `runs-transfer/`.

In `tests/composition/test_replay_scenarios.py`:
- `record_scenario(name)` keeps its default;
- the runner is `build_engine_runner(SCENARIO_REGISTRY.get(name, REGISTRY_DEMO))`.

- [ ] **Step 2: Run them and confirm they fail**

Run: `uv run pytest tests/composition/test_replay_transfer_fixture.py tests/composition/test_replay_fixtures.py tests/composition/test_replay_scenarios.py -q`
Expected: FAIL. The fixture file is missing, `transferencia` is not in `SCENARIOS`, and the runner only knows `demo`.

- [ ] **Step 3: Implement**

(a) `testing/replay/runner.py`:

```python
_RECORDED_KINDS = frozenset({IdKind.event, IdKind.run, IdKind.session, IdKind.turn, IdKind.call,
                             IdKind.decision, IdKind.action, IdKind.handoff, IdKind.transfer})


class RecordedEngineRunner:
    def __init__(self, registry_root: Path) -> None:
        self._registry: InMemoryRegistry = registry_from_releases(registry_root)
        authoring, _ = load_registry(registry_root)
        self._threshold_ids = sorted({m.thresholds_from for m in authoring.all(EntityKind.decision_model)
                                      if isinstance(m, DecisionModelDef) and m.thresholds_from})

    def run(self, case: ReplayCase, ports: RecordedPorts) -> list[EngineEvent]:
        try:
            self._registry.resolve_release_by_id(case.release)
        except KeyError:
            raise ValueError(f"el registro no tiene la release {case.release}") from None
        started = case.recorded[0]
        if not isinstance(started, RunStarted):
            raise ValueError("el fixture no empieza con run_started")
        decisions = decisions_of(list(case.recorded))
        synthesized = synthesized_thresholds(decisions, "replay")
        calibrations = InMemoryCalibrationSource(
            {name: synthesized.model_copy(update={"run_id": name}) for name in ["cal-demo", *self._threshold_ids]})
        store = InMemoryStore()
        audit = InMemoryAuditSink(store)
        deps = EngineDeps(
            clock=ports.clock, ids=_ReplayIds(ports.ids), keys=FakeKeyProvider.default(),
            uow_factory=store.uow, audit=audit, registry=self._registry,
            releases=self._registry.resolve_release_by_id, tools=ports.tools, gateway=ports.llm,
            providers=dict(providers_from(decisions)), calibrations=calibrations,
            transcript=InMemoryTranscript(), authz=SyntheticAuthz(), classifier=FieldClassifier(CATALOG))
        driver = Driver(build_turn_engine(deps), agent=started.payload.agent.id)
        # One instant per turn: the target of a transfer repeats the turn id of the origin.
        turn_ids = list(dict.fromkeys(e.turn_id for e in ports.readings.events_of("turn_started")))
        for index, op in enumerate(case.inputs):
            ports.clock.enter_turn(None if op["op"] == "start" else turn_ids[index])
            driver.apply(op)
        assert driver.run_id is not None
        if driver.session_id is None:
            return audit.read(driver.run_id)
        with store.uow() as uow:
            run_ids = [r.run_id for r in uow.list_runs_by_session(driver.session_id)]
        return [event for rid in run_ids for event in audit.read(rid)]
```

The runner passes no `directory`: `directory/list` is replayed from the record (F5).

Note: before this change `turn_ids[index]` indexed by op. The six existing fixtures have one `turn_started` per op, so `dict.fromkeys` changes nothing for them. Check that they stay `match`.

(b) `testing/replay/scenarios.py`:

```python
def transferencia(w: EngineWorld) -> None:
    """Recepción lee el directorio, elige `disputas` y le transfiere; `disputas` responde en el mismo turno."""
    w.start()
    w.understands("continue")
    w.routes("disputas")
    w.understands("start_flow", flow="disputa-cargo")
    w.turn(TEXTO)


SCENARIOS["transferencia"] = transferencia  # or in the dict literal
SCENARIO_REGISTRY: dict[str, Path] = {"transferencia": TRANSFER_DEMO}
_WORLDS: dict[str, Callable[..., EngineWorld]] = {"transferencia": transfer_world}


def record_scenario(name: str, registry_root: Path | None = None) -> Fixture:
    root = registry_root or SCENARIO_REGISTRY.get(name, REGISTRY_DEMO)
    world = _WORLDS.get(name, EngineWorld)(registry_root=root, record=True)
    SCENARIOS[name](world)
    assert world.driver.run_id is not None and world.recording_tools and world.recording_llm
    origin = world.driver.run_id
    linked: list[EngineEvent] = []
    if world.driver.session_id is not None:
        with world.store.uow() as uow:
            later = [r.run_id for r in uow.list_runs_by_session(world.driver.session_id) if r.run_id != origin]
        linked = [e for rid in later for e in world.audit.read(rid)]
    return build_fixture(name, origin, world.release.id, world.audit.read(origin), world.driver.ops,
                         world.recording_tools, world.recording_llm, linked=linked)
```

`testing/replay/__init__.py`: export `SCENARIO_REGISTRY`.

(c) Record the fixture:

```bash
mkdir -p tests/fixtures/runs-transfer
uv run agentcore record transferencia --out tests/fixtures/runs-transfer/transferencia.yaml \
  --registry tests/fixtures/registry-transfer-demo --catalog tests/fixtures/catalogo-datos-prueba.yaml
```

Read the generated YAML before committing. It must contain:
- no real data;
- the `full` of `directory/list` with `choices: [consultas, disputas]`;
- `linked` starting with the target's `run_started` (with `origin`).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/composition tests/m11 -q`, then `uv run lint-imports && uv run mypy && uv run ruff check .`
Expected: PASS, the six old fixtures included.

If the transfer replay diverges, read `first_divergence`:
- A divergence **only** at an id → there is an id order that `RecordedIds` does not reproduce (M11 decision 9 risk). Report it with the event seq; **do not** add more exclusions to `compare`.
- Any other divergence → stop and report.

- [ ] **Step 5: Commit**

```bash
git add testing/replay tests/composition tests/fixtures/runs-transfer
git commit -F msg.txt   # test(transfer): record and replay the demo transfer session
```

---

### Task 7: README y reconciliación de documentos

**Modelo recomendado:** sonnet.

**Files:**
- Modify: `README.md`, `docs/specs/2026-09-30-transferencia-entre-agentes-design.md`, `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` (#19 and the state header), `docs/adr/0021-transferencia-entre-agentes.md` (state line)

- [ ] **Step 1: README, a new subsection after the `disputa-cargo` demo (Spanish)**

````markdown
Demo de transferencia entre agentes (ADR 0021; sin red ni Postgres). Recepción entiende el problema, lee el directorio
`atencion-cliente`, elige al especialista y le transfiere la conversación; el especialista responde en el mismo turno.

```bash
# validar el registro de la demo: recepcion, disputas y consultas, una release por agente
uv run agentcore validate tests/fixtures/registry-transfer-demo

# la sesión completa en proceso y por HTTP (linaje con los dos runs y enlace por hash)
uv run pytest tests/composition/test_transfer_demo.py

# reproducir la sesión grabada (las dos cadenas)
uv run agentcore replay tests/fixtures/runs-transfer/transferencia.yaml --mode fixture \
  --registry tests/fixtures/registry-transfer-demo \
  --catalog tests/fixtures/catalogo-datos-prueba.yaml

# volver a grabarla
uv run agentcore record transferencia --out transferencia.yaml --registry tests/fixtures/registry-transfer-demo
```

Qué tener en cuenta:
- El umbral con que recepción acepta la elección sale de `calibrations/cal-transfer-demo.json`, un artefacto **hecho a mano** con el umbral comodín `"*"`; no es una calibración (spec §12.1). Sin él, recepción pide aclarar y no transfiere.
- El modelo que elige especialista usa un proveedor guionado. Con `agentcore serve` el directorio está cableado (`RegistryDirectory` sobre el registry de Postgres), pero el proveedor real de esa elección sigue abierto, así que la demo por `serve` todavía no transfiere.
- La base impone un run abierto por sesión (índice `runs_one_open_per_session`); la ruta de Postgres no se ha corrido en CI de esta rama.
````

Adjust the second bullet to the answer to Pregunta 4. If Pregunta 1 was "no", replace the replay and record lines with "el replay de una sesión con transferencia está pendiente".

In the "Comandos" table, the `record` row accepts `transferencia` (with `--registry tests/fixtures/registry-transfer-demo`).

- [ ] **Step 2: Transfer spec**

- Header "Estado": implemented, phases 1 to 7.
- §4 "Cableado": closed (already done in Task 1).
- §5.3: the index (Task 3).
- §11, phase 7 row, "hecha". Content:
  - registro `registry-transfer-demo` (tres agentes, una release cada uno);
  - umbral `"*"` hecho a mano;
  - demo en proceso y HTTP;
  - fixture `transferencia`;
  - sin suites (Pregunta 5).
- §12: items 8 and 16 closed; 10 partly closed; 14 done for the demo catalog.
- §10, T-TR-09: the session replay exists in `fixture` mode (link verified on the recorded chains); connecting it to the CLI output is pending.

- [ ] **Step 3: TEMAS #19 and ADR 0021**

TEMAS #19:
- Move to "hecho": "Agentes de la demo (fase 7)", "Cablear `directory/list`" and "Restricción en la base" (Postgres unverified).
- Keep these pending items:
  - REL-T1 and the evaluation;
  - OTel spans;
  - `verify_transfer_link` in the `agentcore replay` output;
  - `run_id` replay of a session;
  - the router provider (Abierto 1);
  - catalog of `summary` and `examples` (Pregunta 3, if still open);
  - suites (Pregunta 5);
  - return to reception, `step_up`, session cap, atomicity of M3, unique `client_turn_id`;
  - the unverified Postgres paths.

ADR 0021 state line: "implementado en las ramas `feat/transferencia-entre-agentes` (fases 1–6) y `feat/transferencia-demo` (fase 7), sin spans OTel".

- [ ] **Step 4: Full verification**

Run:
- `uv run pytest -q`
- `uv run lint-imports`
- `uv run mypy`
- `uv run ruff check .`
- `uv run agentcore contracts --check`
- `uv run agentcore validate tests/fixtures/registry-transfer-demo`
- `uv run agentcore validate tests/fixtures/registry-demo`

Expected: all green. The `integration` tests are skipped; say so in the report.

- [ ] **Step 5: Commit**

```bash
git add README.md docs
git commit -F msg.txt   # docs(transfer): phase 7 demo in the README; spec, TEMAS and ADR reconciled
```

---

## Self-review

- **Cobertura de lo pedido:**

| Pedido | Tarea |
|---|---|
| U1 registro nuevo con tres agentes, `directory/list` como archivo y releases con `prod` | 2 (F1: tres releases) |
| U2 umbral `"*"` de un artefacto hecho a mano; cómo lo carga `DecisionService` | 2 (archivo, F2) y 5 (`transfer_calibration`, prueba sin comodín) |
| U3 `EngineDeps.directory`, exportes, harness, `RegistryDirectory` en `serve` | 1 |
| U4 índice, doble, prueba de integración sin verificar, orden de escritura, `UniqueViolation`, comentario y docstring | 4 (F6) |
| Catálogo de producción con `choices`, `agent_id` y `release_id` en `public` | 5 (F4; Pregunta 3 para `summary` y `examples`) |
| `agentcore validate` pasa (G0-26/27, AG-03, clausura); REL-T1 | 2 (F9, F10: REL-T1 no hace falta, se comprueba a mano) |
| Camino CLI de grabar y reproducir; `RecordedIds` | 6 y 7 (F7; Pregunta 1) |
| README de la demo | 8 |

- **Criterio de éxito de la spec (§1):** `test_reception_transfers_and_disputas_answers_in_the_same_turn` y `test_the_transfer_demo_goes_through_http` (tarea 4) muestran estos puntos:
  - mismo turno;
  - dos runs con releases distintas;
  - `transfer_id` en el linaje;
  - `directory_hash` en `run_transferred`;
  - `verify_transfer_link == []`.
- **Tipos y nombres consistentes:**
  - `transfer_world`, `TRANSFER_DEMO`, `TRANSFER_RELEASES` y `TRANSFER_CALIBRATION` (tarea 4) los usan las tareas 6 y 7.
  - `registry_from_releases` (tarea 4) lo usa el runner (tarea 6).
  - `ONE_OPEN_RUN_MESSAGE` es el mismo texto en los dos backends (tarea 3).
  - `Fixture.linked` y `build_fixture(linked=…)` (tarea 5) los usa `record_scenario` (tarea 6).
- **Sin cambios de M0 ni de `contracts/`:** ninguna tarea toca `agent_core/domain`.
- **Abiertos no resueltos:** 1 (proveedor), 2 a 4, 6, 9, 11, 12, 13 (Postgres), 15, 17 a 19 de la spec quedan como están. Las tareas los nombran donde los rozan, sin decidirlos.
- **Riesgo principal:** que el replay de la sesión diverja por un orden de ids que `RecordedIds` no reproduce. Por eso la tarea 6 prohíbe agregar exclusiones y manda reportar el `seq`.
