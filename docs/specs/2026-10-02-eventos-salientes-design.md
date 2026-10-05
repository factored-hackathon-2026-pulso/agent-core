# Contrato de eventos salientes (v1)

- Estado: **borrador implementado, pendiente de revisión** (2026-10-02)
- Paquete: `agent_core.outbound` (fuera de M0: no cambia `SCHEMA_VERSION` ni `contracts/VERSION`)
- Gobierna: ADR 0013 (el núcleo define el contrato; la entrega es de la unidad 4), ADR 0008 (sin PII), ADR 0002
- Plan y decisiones: `docs/superpowers/plans/2026-10-02-registry-para-servicios-y-eventos.md` §3.B

## 1. Qué es

Un sobre estable (`OutboundEvent`) con una **lista cerrada** de tipos públicos, para que otros servicios sepan qué pasa en el motor sin leer la cadena de auditoría. Este paquete solo **define y proyecta**: no entrega, no encola y no lee el outbox ni la cadena. El transporte (webhook, cola, tabla), los reintentos y la suscripción son de la unidad 4.

## 2. Tipos públicos v1

| `type` | Origen | `data` |
|---|---|---|
| `run.started` | evento `run_started` | `agent`, `mode`, `principal_type`, `locale`, `subject_kind` |
| `run.closed` | evento `run_closed` | `outcome`, `closed_by` |
| `run.transferred` | evento `run_transferred` | `transfer_id`, `to_agent`, `to_release_id`, `to_run_id` |
| `handoff.created` | `OutboxMessage` de tipo `handoff_created` | `handoff_ref`, `target_queue`, `priority`, `reason_code`, `language` |
| `handoff.resolved` | evento `handoff_resolved` | `handoff_ref`, `handoff_quality`, `reader_type` |
| `release.published` · `release.promoted` · `release.revoked` | evento `published`, `promoted`, `revoked` de `reg_events` | `release_id`, `proposal_id`, `agent_id`, `alias`, `before` |

Fuera de la v1: eventos de turno, decisión, acción, seguridad y medición.

**Registry.** Los `release.*` salen con `source = "registry"`, sin `run_id` y con `event_id = reg-<seq>` (la posición en `reg_events`, que pone quien lee). El proyector vive en `agent_core.registry.outbound` (`project_registry_event`). No llevan actor, motivo, origen ni hash del candidato; `agent_id`, `alias` y `before` son opcionales y aditivos (2026-10-05): `RegistryEvent` los guarda desde esa fecha y un evento anterior los proyecta como `null`. `published`: `alias = staging` y `before` la base de `staging`; `promoted`: el alias promovido y la release a la que apuntaba; `revoked`: solo `agent_id` (la revocación no toca un alias).

**Excluido a propósito (decisión del usuario, 2026-10-02):** `reportable_attrs` (de `run_started` y `handoff_created`), `reason` de `run_transferred` y `resolution_code` de `handoff_resolved`. También quedan fuera `notes`, `origin`, `packet_fp`, `directory`, `directory_hash` y `candidates`.

## 3. Sobre

`spec_version` (entero, 1), `event_id`, `type`, `occurred_at`, `source` (`engine` o `registry`), `run_id`, `session_id`, `turn_id`, `release_id`, `data`.

- **Sin campo `subject`** (ni referencia del cliente): solo `subject_kind` dentro de `run.started`.
- `event_id` es el del evento de la cadena que lo origina (para `handoff.created`, el `message_id` del outbox). Es la clave de deduplicación.
- `occurred_at` sale del `ts` del evento (`Clock`); nunca la hora del sistema.
- `data` es un modelo cerrado por tipo (`extra = forbid`), construido campo a campo: nunca un volcado del payload de la cadena, para que un campo nuevo allí no se filtre solo.

## 4. Garantías que promete el contrato

- Entrega **al menos una vez**: el consumidor deduplica por `event_id`.
- Orden garantizado solo **dentro de un run**; sin orden global entre runs.
- Sin promesa de latencia ni de transporte.
- Proyección **determinista**: mismos eventos, mismos bytes (`canonical_bytes`).

## 5. Compatibilidad

Los consumidores ignoran campos y tipos desconocidos. Un campo opcional o un tipo nuevo es un cambio compatible (sube la parte menor de `catalog.json.version`); quitar o cambiar un campo exige un tipo nuevo o `spec_version` 2. Una copia congelada de los esquemas v1 (`tests/outbound/frozen_v1/`) hace fallar la prueba si un campo obligatorio desaparece o cambia de tipo.

## 6. Publicación

`uv run agentcore contracts` genera `contracts/events/OutboundEvent.json` (unión discriminada por `type`) y `contracts/events/catalog.json` (`spec_version`, tipos públicos y su modelo). `--check` los verifica en CI.

## 7. Abiertos

- ~~Agente y alias en `release.*`~~: hecho el 2026-10-05 (campos opcionales en `RegistryEvent` y `ReleaseData`).
- Quién consume esto y desde dónde (unidad 4): outbox transaccional o relay con cursor sobre la cadena.
- `contracts/VERSION` no cambia con este contrato (no toca M0); `catalog.json` lleva su propia versión. Confirmar que ADR 0002 ("un solo semver") lo admite.
