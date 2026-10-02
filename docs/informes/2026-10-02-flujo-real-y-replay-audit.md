# Flujo real de punta a punta y replay `audit` sobre runs reales

- Fecha: 2026-10-02 · Rama: `feat/real-flow-e2e` · Módulos: M4, M5, M8, M11 (`testing/`, sin cambios en el motor)
- Datos: solo sintéticos (cargos inventados). Proveedores reales: JEV (`jev-1.13.0`) y OpenRouter (Gemini 3.1 Flash-Lite).
- La conversación la ejecutó el agente con las claves del usuario cargadas en el shell; las claves no se leyeron ni se registraron.

## 1. Estado

| Punto | Estado |
|---|---|
| Recepción → `disputas` → radicación con JEV y LLM reales | **Funciona** (§2). |
| Cadenas de hash y enlaces de transferencia sobre Postgres real | **Íntegros**: 6 cadenas, 3 enlaces (§3). |
| Replay `audit` de un run real | **`diverged`** por tres causas de diseño de M11 (§4). Las demás divergencias eran del arnés y están resueltas. |
| Sesiones con transferencia en `audit`; `replay <run_id>` desde el almacén | **Sin soporte** (§5). |
| Camino `consultas` | **Sin probar a fondo** (§6). |

## 2. Qué se probó

`agentcore serve` con Postgres, JEV y gateway reales, y dobles etiquetados solo para tools, authz, transcript, calibración y clasificador (`testing/realflow_demo.py`). Conversación:

recepción pregunta → "no reconozco un cargo en la Tienda Aurora" (JEV `continue` 0.91) → el clasificador sintético elige `disputas` (0.9) → "el de 120 dólares" (empareja `tx-1001`) → confirma → step-up simulado → radica → verifica → el LLM redacta (`generated`, validador ok, 1 llamada, 0.000102 USD) → `resolved`.

Sin coincidencia ("el de 999 dólares") responde que no encontró el cargo y sigue abierto.

### Lo que hizo falta (y conviene saber)

- **JEV exige `questions` por campo.** Sin `instructions`/`criteria` devuelve una distribución plana (`start_flow` 0.34, `deny` 0.30) y todo queda bajo umbral. `criteria` debe cubrir **exactamente** el enum, y el enum de `flow` es distinto en cada agente: con un modelo compartido solo se pueden dar criterios de `command` e `interrupt`.
- **`start_flow` frente a `continue` depende de `current_node`.** Con criterios que lo usan, recepción dio `continue` 0.91; sin ellos, `start_flow` 0.60 y se perdía el texto. Sigue fluctuando (0.57–0.91): es trabajo de calibración (unidad 6).
- **El validador `numbers` de M8 rechazó el primer prompt** (2 llamadas, plantilla de respaldo). Con "no incluyas números, cifras, fechas ni identificadores" pasó. El respaldo funcionó como está diseñado.
- **`transaction_id` tokenizado no llega a la tool.** Los nodos `tool` no destokenizan sus argumentos (solo el nodo `agent`). El catálogo sintético lo declara `public`; un clasificador real que devuelva el token rompería `seleccionar`. A decidir en el diseño de la unidad 6.
- **`serve` en demo solo verifica credenciales del staff.** Para clientes hay que pasar `--identity-keys` (`python -m testing.demo_identities --public-keys <archivo>`).
- **No existe `agentcore migrate`:** el esquema (`apply_schema` y `apply_registry_schema`) se aplicó con una llamada directa. Bloquea el despliegue (ADR 0003 de infra).

## 3. Integridad

`check_chain` sobre los 6 runs del almacén y `verify_transfer_link` sobre los 3 runs destino: 0 problemas.

## 4. Replay `audit` de un run real (`disputas`, 45 eventos)

Tras dar al arnés el `KeyProvider` y el `FieldClassifier` del despliegue, y tomar el `client_turn_id` y el tipo de sujeto de la cadena, el motor reproduce los 45 eventos con el mismo tipo en cada posición, las mismas decisiones, nodos y ramas. El veredicto es `diverged` por:

| # | Divergencia | Causa | Decisión pendiente (M11) |
|---|---|---|---|
| 1 | `expiry_evaluated.now` (`seq` 5) y otros instantes | El replay fija **un instante por turno** (el `ts` de `turn_started`); el reloj real se lee varias veces por turno y `ts` es el momento de volcado, no el de la lectura. | Grabar las lecturas de reloj, o garantizar una por turno. |
| 2 | `tool_called.result_fp` (×3) | `RecordedToolExecutor` entrega el `result` de vista `audit` como `result_full` y M3 lo re-proyecta y re-huella (riesgo 2 del spec). | Un proyector grabado que devuelva `(result, result_fp)` por `call_id`. |
| 3 | `response_emitted`: `generated` → `template`, 2 llamadas, falla `format`, otro `transcript_fp` | El transcript guarda el texto renderizado; el borrador del LLM lleva citas que no se recuperan (riesgo 6). | Guardar el borrador con sus citas, o su huella y su texto. |

## 5. Lo que `audit` no soporta hoy

- `agentcore replay <run_id> --mode audit` exige un archivo fixture; el camino por `run_id` del `Replayer` arma entradas sin `op` (`KeyError: 'op'`) y no sigue la sesión (pendiente #24 de M11).
- Sesiones con transferencia: `directory/list` se sirve desde la vista `audit` (directorio y hash en `***`), así que `transfer` se rechaza con `not_in_directory`.

## 6. No cubierto

- `consultas`: la tool `obtener_pqr` está registrada como lectura posterior a la radicación (por clave de idempotencia); con un radicado dado no se comprobó. La recepción llegó a enrutar bien.
- Comparación Haiku 4.5 frente a Gemini en el flujo completo (solo humo de 3 llamadas: Gemini 0.000327 USD, p50 1984 ms; Haiku 0.001908 USD, p50 2781 ms).

## 7. Cómo reproducirlo (local)

1. `docker compose up -d postgres` (o un Postgres 16 propio) y una base vacía; aplicar los esquemas con `apply_schema` y `apply_registry_schema`.
2. Importar el registry: `AGENTCORE_ALLOW_DEMO=1 AGENTCORE_REGISTRY_DSN=… AGENTCORE_CREDENTIAL=<admin de testing.demo_identities> uv run agentcore registry --verifier testing.registry_demo:demo_verifier import tests/fixtures/registry-realflow`.
3. Claves de demo (`AGENTCORE_KEYS_FINGERPRINT`, `AGENTCORE_KEYS_TOKEN_MAP`: `kid:base64` de 32 bytes), `AGENTCORE_JEV_API_KEY`, `LLM_ENDPOINTS` y la variable de la key de OpenRouter, en el shell.
4. `uv run python -m testing.demo_identities --public-keys identity-keys.json`.
5. `AGENTCORE_ALLOW_DEMO=1 uv run agentcore serve --identity-keys identity-keys.json --agents recepcion,disputas,consultas --tools testing.realflow_demo:tools --classifier testing.realflow_demo:classifier_provider --field-classifier testing.realflow_demo:field_classifier --calibration testing.realflow_demo:calibration`.
6. `uv run python -m testing.chat --agent recepcion`.

Cualquier cambio en una entidad del registry exige reconstruir la base: las versiones son inmutables por hash.
