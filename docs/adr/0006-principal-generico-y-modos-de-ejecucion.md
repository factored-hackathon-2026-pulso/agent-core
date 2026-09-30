# ADR 0006 — Principal genérico, delegación y modos de ejecución

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por el hallazgo C1 de la revisión externa y por el tema #2 de la auto-revisión (principal de la sesión), y por la revisión de M0 (delegación atada al asesor, `grantee`).
- Unidad: 1 · Motor de decisión

## Contexto
- Además de los agentes de cara al cliente, habrá agentes internos: el copiloto del asesor, el constructor de agentes y el constructor de tools.
- El núcleo no puede quedar atado al cliente ni al dominio de quejas.
- La revisión externa (C1) mostró que validar solo `principal.type ∈ invocable_by` permite que un principal envíe el `subject.ref` de otro cliente (IDOR). Tampoco estaba definido de dónde sale el `customer_id` cuando el asesor actúa sobre un cliente.
- El reto exige: *"Enforce access to each customer's records and action permissions in the service or tool layer"*, y aclara que *"a national ID or customer number alone does not prove identity"*.
- **(Auto-revisión #2)** Nada impedía que un turno de una sesión llegara con un principal distinto al que abrió el run. Con un `customer`, el chequeo de subject lo atrapa; con un `advisor`, otro asesor con delegación vigente sobre el mismo subject podía continuar la sesión de un colega.

## Decisión
- **Quién invoca:** un `Principal {type: customer|advisor|service|builder, id, roles, scopes, attrs}`.
- **Delegación:** un claim `on_behalf_of {subject, grant_ref, grantee, scopes, exp}` **firmado por el emisor de asignaciones** (p. ej. la cola al asignar un handoff), nunca por la app ni por el cliente.
  - **(Revisión de M0)** `grantee: {type: advisor, id}` ata la delegación al asesor al que se asignó. Si `grantee` no coincide con el principal que la presenta → `403 delegation_mismatch` y `access_denied`. Sin esto, un asesor podía abrir un run nuevo con la delegación de otro; `principal_mismatch` solo protege runs existentes.
- **Autorización del subject:** `authorize_subject(principal, on_behalf_of?, subject)` vive en la capa de políticas (unidad 3). Se evalúa al iniciar el run y **otra vez en cada llamada a tool**.
- **Vinculación de parámetros** (`bind_params`). Los parámetros sensibles salen solo de estas fuentes, nunca del body ni del modelo:

  | Principal | Subject permitido | Origen de los parámetros |
  |---|---|---|
  | `customer` | El suyo, derivado del servidor | `principal.id` |
  | `advisor` | `on_behalf_of.subject`, con grant vigente | `on_behalf_of.subject.ref` |
  | `service` | Según scopes | Subject autorizado por scope |
  | `builder` | Entidades del registro; nunca datos de clientes | `principal.id` para sus borradores |

- **Principal de la sesión (auto-revisión #2):** un run pertenece al principal que lo inició. Cada turno exige que `(principal.type, principal.id)` coincida con el snapshot del run; si no, `403 principal_mismatch` y `access_denied`, antes de cargar el estado.
- **Modos de ejecución:** hay dos sobre el mismo motor:
  - `conversational`: por turnos.
  - `task`: una corrida con entrada estructurada.
- **Sobre qué se ejecuta:** un run actúa sobre un `subject {kind, ref}` genérico y autorizado. El agente declara qué `subject_kinds` acepta.
- **A quién se escala:** `HandoffPacket.target_queue` es configurable.
- **Agentes internos:** usan los mismos nodos y las mismas garantías. En particular, no existe una tool de publicación: publicar es un gate humano externo.

## Alternativas (C1)
- **Subject implícito:** el cliente no envía subject y el asesor solo entra por `handoff_ref`. Es más simple, pero no generaliza a agentes internos. En la demo equivale a la tabla de arriba.
- **Autorización en la app:** se descarta porque saca el permiso de la capa de servicio y contradice el reto.
- **(#2) Permitir que cualquier principal autorizado sobre el subject continúe la sesión:** se descarta porque mezcla auditorías de personas distintas en un mismo run. El traspaso entre asesores se modela como un run nuevo sobre el mismo subject.

## Consecuencias
- La unidad 3 expone `authorize_subject` y `bind_params`, y `execute` vuelve a autorizar internamente.
- El sistema de colas (fuera del núcleo) debe firmar delegaciones. Para la demo, lo hace un emisor de prueba etiquetado como tal.
- Los intentos denegados se registran como `access_denied` y alimentan la métrica de accesos no autorizados.
- El motor no contiene ningún concepto bancario.
- **(#2)** Un principal que renueva su token conserva `type` e `id`, así que sigue en el mismo run. Un traspaso entre asesores abre un run nuevo sobre el mismo subject.

## Enmienda 2026-09-30 (ADR 0019)
- **Agentes internos:** el principal del constructor es `builder` (no existe `PrincipalType.agent`; la línea del registry que lo decía se corrige). El constructor son dos agentes, uno `conversational` y uno `task`.
- **Identidad hacia el registry:** el adaptador de tools del registry decide por su propia credencial con rol `constructor`; el principal del run solo viaja como actor de auditoría.
- **`builder` sin datos de clientes:** además de la tabla, es una prueba de contrato de `AuthzPort` (subject, campos y parámetros vinculados).
- **Copiloto:** principal `advisor`, run propio sobre el subject de la delegación, solo lectura y cálculo en su primera versión.
