# ADR 0023 — Escalado en AWS: blobs en S3, relay del outbox a SNS y pool de conexiones

- Estado: aceptado (2026-10-03)
- Unidad: transversal (composición, adaptadores, registry)
- Enmienda: ADR 0001 (la regla "sin colas ni infraestructura adicional hasta que un subsistema lo justifique con su propio ADR" queda cumplida para lo que sigue; Redis y vector DB siguen fuera)
- Gemelo en infra: `infra/docs/adr/0005-agent-core-escalado-fase-0-1.md`

## Contexto
- El servicio no guarda estado en memoria, así que escala horizontalmente, pero tres cosas lo frenan:
  1. Cada `UnitOfWork` abre su propia conexión a Postgres. Con N tareas el número de conexiones crece sin cota.
  2. Los blobs del registry (`reg_blobs`, `bytea`) viven en la base transaccional: inflan backups, réplicas y el WAL.
  3. El outbox (`handoff_created`) se escribe de forma transaccional, pero **nadie lo entrega**: el contrato de eventos salientes dice "no entrega ni encola" y deja el transporte a la unidad 4.
- `agentcore sweep` no se podía programar en un contenedor: leía `AGENTCORE_DATABASE_URL` (no la variable que usan `serve` y `migrate`) y exigía `--registry <directorio local>`.

## Decisión
1. **Blobs en S3 (opt-in).** `S3BlobStore` implementa el puerto `BlobStore`; la clave es `<prefijo>/<sha256>`. Se activa con `AGENTCORE_BLOB_BUCKET` (opcionales `AGENTCORE_BLOB_PREFIX`, `AGENTCORE_BLOB_KMS_KEY_ARN`). Sin la variable nada cambia.
   - **Migración sin corte:** con el bucket configurado, lo nuevo va a S3 y lo anterior se sigue leyendo de `reg_blobs` (`ReadThroughBlobStore`). `agentcore blobs-backfill` copia lo que falte (idempotente, verifica cada hash antes de subir).
   - **La FK `reg_entity_versions.content_hash → reg_blobs` no puede cumplirse con S3.** `agentcore migrate` la quita (solo con `AGENTCORE_BLOB_BUCKET`, solo la corre el dueño del esquema, nunca el rol de la aplicación). La integridad pasa a ser: el hash se verifica en **cada lectura** y el bucket se opera sin borrado (versionado, política que niega `DeleteObject` a los roles de la aplicación). El blob se escribe antes del insert de la fila; si la transacción revierte queda un objeto huérfano e inofensivo.
2. **Pool de conexiones (opt-in).** `AGENTCORE_DB_POOL_MAX` (>0) usa `psycopg_pool` en el motor, el registry y la base de evaluaciones; el pool se abre al primer uso. Con 0 o sin la variable, una conexión por operación, como antes. Detrás de un RDS Proxy el pool de cada tarea acota lo que le toca.
3. **Relay del outbox.** Puerto `EventPublisher` (síncrono, `PublishError` sin detalle del proveedor), servicio `OutboxRelay` en `agent_core.relay` (depende solo de `domain`, `ports` y `outbound`; lo verifica `.importlinter`) y adaptador `SnsEventPublisher`. Comando `agentcore relay [--once]` con `AGENTCORE_EVENTS_TOPIC_ARN`.
   - **Al menos una vez:** se marca entregado solo después de que SNS confirma. Un corte entre ambos pasos duplica el mensaje; el consumidor deduplica por `event_id` (ya lo exige el contrato de eventos salientes).
   - **Un líder:** en modo servicio, un candado asesor de Postgres deja un solo relay activo; si cae, otro toma el relevo. `SIGTERM` termina la pasada en curso.
   - **Tema SNS estándar, no FIFO.** El orden global no es una garantía del contrato; el consumidor ordena por `occurred_at` y deduplica. Evita acoplar el diseño a límites de FIFO (300 msg/s por grupo) y a las suscripciones que FIFO no admite.
   - **Atributos de mensaje** `event_type`, `event_id`, `source`, `spec_version` para que cada cola suscrita filtre sin abrir el cuerpo.
4. **Contrato del sweep.** `agentcore sweep` lee `AGENTCORE_REGISTRY_DSN` (acepta `AGENTCORE_DATABASE_URL` como alias anterior) y, sin `--registry`, lee el `Agent` de cada run del registry de Postgres, igual que `serve`. `--registry <dir>` sigue funcionando.

## Alternativas
| | Por qué no |
|---|---|
| **Blobs en S3 por defecto** | Rompe a quien no tiene bucket y obliga a migrar de golpe. Opt-in con fallback permite desplegar, hacer backfill y después quitar el fallback. |
| **Mantener la FK con una tabla de punteros** | Una fila por blob en Postgres mantiene la FK pero conserva el cuello de botella (escritura sincrónica en la base) y duplica el estado. |
| **pgbouncer o RDS Proxy sin pool en la aplicación** | El proxy acota las conexiones hacia la base, pero cada UoW seguiría pagando el handshake TLS hacia el proxy. Las dos capas son complementarias. |
| **Relay con `LISTEN/NOTIFY`** | Más rápido, pero el relay igual necesita el barrido periódico para ponerse al día; el sondeo cada 2 s cumple sin otro mecanismo. |
| **SNS FIFO** | Ver arriba. |
| **Cursor global sobre `audit_events` para los eventos de la cadena** | Un `bigserial` no es monótono respecto del commit: un cursor puede saltarse filas confirmadas tarde. Por eso los eventos de la cadena no se leen de la tabla (ver "Fuera de alcance"). |

## Consecuencias
- Dependencias nuevas: `boto3` (producción) y `moto` (pruebas). `psycopg[binary,pool]` reemplaza a `psycopg[binary]`.
- `PostgresStore` gana `pool_max`, `pool_min`, `open_pool()`, `close()` y `release()`. `PostgresUoW` recibe un `release` opcional; sin él cierra la conexión como antes.
- `PgRegistryStore` recibe una fábrica de blobs opcional.
- La imagen no cambia de entrypoint: `relay`, `sweep`, `migrate` y `blobs-backfill` son subcomandos de la misma imagen.

## Riesgos y lo no verificado
- **Mensaje venenoso al frente del outbox.** Un mensaje que nunca se puede proyectar o publicar se reintenta en cada pasada y no se marca. No frena a los demás de su lote, pero si hubiera más de `--batch` venenosos consecutivos, el resto no avanzaría. Mitigación: el relay imprime `failed=N` y la infraestructura alarma sobre ese contador. Falta una columna `attempts` y una cuarentena (decisión pendiente).
- Probado con `moto` (S3 y SNS/SQS simulados) y con Postgres 17 real, **no** contra AWS. El comportamiento de `head_object` con permisos mínimos (403 en vez de 404 sin `s3:ListBucket`) es un riesgo conocido: el rol de la tarea debe tener `s3:ListBucket`, o `put` fallará con 403 en vez de tratar el objeto como ausente.
- Backfill: lee `reg_blobs` con un cursor del lado del servidor dentro de una transacción larga; con una base grande conviene correrlo fuera de hora pico.

## Fuera de alcance (pendiente, decisión de otros módulos)
- **Eventos de la cadena** (`run.started`, `run.closed`, `run.transferred`, `handoff.resolved`) y los del registry (`release.*`): el relay de hoy cubre solo lo que ya pasa por el outbox. Entregarlos exige proyectarlos al outbox **en la misma transacción** del turno (cambia M4 y el tipo `OutboxMessage`, hoy limitado a `handoff_created`). Es una decisión de M0/M4: queda como tema abierto.
- **Rate limit en Redis/ElastiCache** y **evaluaciones del registry por SQS con workers propios.** Cada una necesita su ADR (el segundo, además, serializar `EvalRequest` y el sandbox).
