# Servidor arrancable (`agentcore serve`) — diseño

- Fecha: 2026-09-30
- Estado: **implementado (2026-09-30)**; ver ajustes en §8. Antes: borrador para revisión (resuelve el tema #13 de `docs/specs/TEMAS-ABIERTOS-PENDIENTES.md` salvo las piezas reales de las unidades 3, 6 y 7)
- Gobierna: ADR 0001 (stack), ADR 0016 (gateway), spec del registry §14, m09 §11 "Cableado real"

## 1. Intención

La demo del 02/10 debe correr de punta a punta como servidor HTTP. Hoy `create_app(ApiDeps)` y `build_turn_engine(EngineDeps)` existen, pero ningún proceso los construye con adaptadores concretos y `agentcore` no tiene un comando `serve`.

**Decidido con el usuario (2026-09-30):**
- Son reales: Postgres, el gateway de LLM, JEV por HTTP (`jev-1.13.0`) y la carga de claves de identidad desde configuración.
- Son dobles etiquetados, permitidos solo con `AGENTCORE_ALLOW_DEMO=1`: ejecutor de herramientas, `AuthzPort`, transcript en memoria, calibración y clasificador de demo.
- No se espera a la unidad 3.

**Criterio de éxito:** con `AGENTCORE_ALLOW_DEMO=1`, `agentcore serve` arranca y un turno completo funciona por HTTP; sin la variable y con piezas faltantes, se niega a arrancar y nombra cada una.

**Supuestos a confirmar:** la clave de JEV llega por entorno; las claves públicas de identidad llegan por un archivo de configuración cuyo formato se fija en §3.3.

## 2. Enfoque

Puertos por ruta `modulo:atributo`, el mismo patrón de la CLI del registry (`--verifier`, `--harness`). Cada pieza pendiente de otra unidad es un punto de enchufe: cuando exista la real, solo cambia la ruta y el motor no se toca. Se descartó un `demo_server.py` fijo en `testing/` porque no deja raíz de composición.

## 3. Diseño

### 3.1 Fábrica de composición

`agent_core/composition/serve.py` expone `build_api_deps(ports: ServePorts, *, registry_service=None) -> ApiDeps` (`ServePorts` lo arma `resolve_ports`). Arma `EngineDeps`, llama a `build_engine` y, solo si recibe un `registry_service`, monta el registry como `ApiExtension` (§8). Solo `composition` importa adaptadores concretos (regla de fronteras de `.importlinter`; se añade el contrato si hace falta).

### 3.2 Piezas reales

| Pieza | Fuente |
|---|---|
| Postgres (`UnitOfWorkFactory`, registry, auditoría) | `--dsn` / `AGENTCORE_REGISTRY_DSN` |
| Gateway de LLM | `OpenAICompatGateway` con `load_endpoints`; al arrancar avisa de los alias sin configurar (gateway §5) |
| JEV | `HttpJevTransport`, modelo fijado `jev-1.13.0` (ADR 0005); clave por entorno |
| Claves HMAC/cifrado | `EnvKeyProvider` (`AGENTCORE_KEYS_FINGERPRINT`, `AGENTCORE_KEYS_TOKEN_MAP`); falla cerrado si faltan |
| Identidad | `JwsIdentityVerifier` con claves cargadas del archivo de §3.3 (`--identity-keys`) |

### 3.3 Claves de identidad

Archivo YAML con dos mapas por `kid`: `principal_keys` y `delegation_keys` (claves públicas Ed25519 en base64url). `grant_active` se enchufa por ruta; en la demo es el doble de `testing/`. El formato se documenta en m09 al implementar.

### 3.4 Dobles etiquetados

Solo con `AGENTCORE_ALLOW_DEMO=1`: `FakeToolExecutor` (vía `LazyDemoTools`), `SyntheticAuthz`, `InMemoryTranscript`, calibración y clasificador de demo. Cada uno se elige con una opción de ruta (`--tools`, `--authz`, `--transcript`, `--calibration`, `--classifier`) y tiene como valor de demo la ruta al doble.
- Sin la variable y con alguna pieza sin ruta: código 2 y un mensaje que nombra cada pieza faltante.
- Con la variable: al arrancar se imprime la lista de piezas que son dobles.

### 3.5 Lectores de la API

`build_turn_engine` hoy deja `HandoffService` y `TranscriptReader` dentro. Se exponen (p. ej. devolviendo un objeto `Engine` con `turns`, `handoffs`, `transcripts`) sin cambiar el comportamiento del motor. Es el único cambio a código existente.

### 3.6 Comando y dependencia

Subcomando `serve` en `agent_core/cli.py` con `--host`, `--port` y las rutas anteriores. `uvicorn` se declara en `pyproject.toml` y `uv.lock` se regenera.

## 4. Pruebas

1. Sin `AGENTCORE_ALLOW_DEMO` y con piezas sin ruta, `serve` termina con código 2 y nombra cada pieza.
2. Con `AGENTCORE_ALLOW_DEMO=1`, `build_api_deps` devuelve un `ApiDeps` completo y un turno completo pasa por `TestClient` (M9).
3. El aviso de alias sin configurar sale al arrancar.
4. La lista de dobles se imprime con la variable.
5. `uv run lint-imports`, `uv run mypy`, `uv run ruff check .` y `uv run pytest` siguen en verde; `contracts/` no cambia (no se toca M0).

## 5. Fuera de alcance

`ToolExecutor` real y `AuthzPort` real (unidad 3), `TranscriptStore` persistente (unidad 7), calibración real (P9, unidad 6) y artefactos del `classifier`. Quedan en el tema #13 con su punto de enchufe ya definido.

## 6. Cambios de documentación

- Tema #13 pasa a "Resuelto salvo las piezas reales de las unidades 3, 6 y 7", con enlace a esta spec.
- m09 §11 "Cableado real" se marca resuelto y documenta el formato del archivo de claves.
- Registry §14: `build_api_deps` acepta el `registry_service`; `serve` lo monta cuando tenga evaluador y harness reales (§8).
- `AGENTCORE_ALLOW_DEMO=1` queda documentado como el mecanismo oficial de la demo para todo el núcleo, no solo para el registry.

## 7. Abiertos

- Formato exacto del archivo de claves (§3.3) y nombre de las opciones, a fijar en el plan.
- Si `serve` debe cargar los flows de la demo desde una carpeta o solo desde el registry en Postgres.

## 8. Ajustes en la implementación (2026-09-30)

- `resolve_ports` y `run_serve` reciben el entorno como `Mapping[str, str]`: el gateway lee claves de API por nombres de variable arbitrarios.
- La clave de JEV se lee de `AGENTCORE_JEV_API_KEY` solo al llamar; construir el servidor no la exige.
- Sin `--identity-keys`, en demo el verificador es `testing.registry_demo:demo_verifier` y `identity` figura entre los dobles.
- `serve` **no** monta todavía la API HTTP del registry: `build_api_deps` acepta un `registry_service`, pero falta cablear el evaluador y el harness reales. El CLI `agentcore registry` no cambia.
- **Aviso de alias de LLM (gateway §5), añadido el 2026-09-30:** `--agents a,b` (o `AGENTCORE_SERVE_AGENTS`). Al arrancar, `model_alias_warnings` resuelve la release `prod` de cada agente, lee sus entidades `model_profile` y avisa por cada `endpoint_alias` sin entrada en `LLM_ENDPOINTS` o con la variable de key vacía (nombra la variable, nunca el valor), y por cada agente sin release `prod`. Solo avisa: no bloquea el arranque. Sin `--agents` no revisa nada. Solo se revisa `prod`; `staging` queda fuera.
