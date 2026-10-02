# ADR 0021 — Transferencia entre agentes: recepción, directorio y especialistas

- Estado: propuesto; implementado en las ramas `feat/transferencia-entre-agentes` (fases 1–6) y `feat/transferencia-demo` (fase 7), sin spans OTel; pendiente de aprobación del usuario
- Unidad: 1 · Motor de decisión (con dependencias de la unidad 2, registry, y del diseño de evaluación del ADR 0020)
- Spec: `docs/specs/2026-09-30-transferencia-entre-agentes-design.md`
- Amplía: ADR 0004 (nodos), ADR 0006 (principal y sesión), ADR 0013 (escalamiento), ADR 0017 y 0018 (registry y gate), ADR 0020 (evaluación por agente)

## Contexto
- Hoy un agente de cara al cliente resuelve todos los problemas con flows dentro de **una sola release**. Para escalar, se quiere atender con **agentes especialistas**, cada uno con su release, su versionado y su evaluación, sin un "súper flow" que contenga toda la atención.
- El cliente no elige con quién habla: escribe y le responden. Alguien tiene que entender qué necesita y llevarlo al especialista correcto sin que lo note.
- Publicar o cambiar un especialista **no debe obligar** a republicar a quien enruta.
- El motor tiene dos reglas que lo impiden hoy: una sesión es un solo run (`UnitOfWork.find_run_by_session`, `410 run_closed` al cerrarse) y un run nunca cambia de release (base del replay y del linaje).
- La única salida de un run hacia otro actor es escalar a una persona (ADR 0013); no existe traspaso entre agentes, y `subflow` no está habilitado.
- Debe quedar trazado, de forma verificable, que un especialista fue llamado por una recepción concreta.

## Decisión
1. **Una sesión encadena runs.** Una sesión de conversación puede tener varios runs en orden; solo uno está abierto a la vez. Cada run conserva su release fija. Un run cerrado por transferencia no cierra la sesión.
2. **Agente de recepción.** Es un agente de cara al cliente que conversa hasta entender qué necesita la persona y la transfiere al especialista adecuado. No resuelve problemas de negocio.
3. **Directorio de agentes.** Cada especialista publica en su `Agent` una ficha de enrutamiento (`routing`): qué resuelve, ejemplos, a quién atiende y la etiqueta de directorio. El directorio es el conjunto de agentes con release activa en `prod` que llevan esa etiqueta. Publicar un especialista con la etiqueta lo agrega **sin tocar la release de recepción**. El directorio tiene un hash de versión.
4. **Descubrir es una tool; transferir es un nodo.** Recepción lee el directorio con una tool de lectura que el servidor filtra por elegibilidad del principal. Elige con un `decide` cuyas opciones se arman en runtime desde el directorio (como el campo `flow` de Understand). Transfiere con un **nodo `transfer`**: crear un run es trabajo del motor (sesión, principal, presupuestos, auditoría), no de una tool.
5. **Referencia por alias.** El destino se resuelve por `agent_id@prod` en el momento de transferir. Cada run guarda su `release_id` exacto y recepción guarda el hash del directorio que usó, así que la sesión completa sigue siendo reproducible.
6. **Contrato de entrada del especialista.** El especialista declara `Agent.accepts` (el esquema de lo que acepta en el paquete). Se valida al publicar recepción, en runtime al transferir (si no cumple, la transferencia se rechaza y el flow sigue por `next["rejected"]`: el flow decide qué hacer, no necesariamente escalar) y al publicar el especialista (no puede romper a una recepción publicada; si lo hace, `yardstick_loosened`, ADR 0020).
7. **Paquete de transferencia mínimo.** Motivo, el mensaje que disparó la transferencia (como `untrusted_text`) y los slots que el destino acepta. Los hechos de tools **no viajan**: el especialista vuelve a leer con sus tools y sus permisos.
8. **Se conservan principal, subject y nivel de autenticación.** El destino debe aceptarlos (`invocable_by`, `subject_kinds`, `min_auth_level`); el `principal_mismatch` de la sesión se mantiene.
9. **Trazabilidad verificable.** Cada transferencia tiene un `transfer_id`. Recepción emite `run_transferred` y el especialista arranca con `run_started.origin` y `transfer_received`. El `run_started` del especialista guarda el hash del `turn_completed` del turno de origen que transfirió (cubre `run_transferred` y `run_closed` por encadenamiento): las dos cadenas quedan enlazadas y alterar una rompe el enlace. Spans OTel enlazados y linaje por sesión.
10. **Evaluación por agente, sin depender del destino.** En la suite de recepción, el escenario termina en `transfer`: afirma a quién transfirió y con qué paquete. Un especialista puede partir de un `transfer_packet` en su escenario. La ficha de enrutamiento es parte de la vara del especialista: al publicarlo, su gate corre la suite de enrutamiento de recepción `prod` contra el directorio candidato.
11. **Alcance de la demo: solo la ida** (recepción → especialista). La vuelta a recepción (`on_out_of_scope: transfer`) y el tope de transferencias por sesión quedan diseñados, no construidos.

## Alternativas
| Alternativa | Por qué se descarta |
|---|---|
| **Un agente con muchos flows** (el modelo actual) | Todo cambio republica y reevalúa la atención completa; dos equipos chocan al publicar (`proposal_stale`); no escala. |
| **Recepción con un flow por especialista, elegido por Understand** | Cada especialista nuevo obliga a republicar recepción. |
| **Enrutador fuera del motor** (la app elige el agente) | Solo ve el primer mensaje, no conversa para aclarar, no queda en la auditoría y duplica Understand. |
| **Tool que inicia el run destino** | Crear un run fuera de M4 salta la sesión, los presupuestos y la cadena de auditoría; una tool de escritura además exige `confirm`. |
| **Destino por versión exacta en la release de recepción** | Reproducible, pero cada publicación de un especialista obliga a republicar recepción. |
| **Release compuesta** (recepción incluye las releases de los especialistas) | Rompe "un run, una release fija" o arrastra el mismo acoplamiento de publicación. |
| **Pasar los hechos de tools en el paquete** | Mezcla datos leídos con permisos y vistas de otra release; el especialista no podría verificar su origen. |
| **Enrutamiento solo como métrica `monitor`** | Una ficha mal escrita robaría conversaciones de otro especialista sin que ningún gate lo detecte. |

## Consecuencias
- **Cambio de interfaz para todos los módulos:** M0 gana el nodo `transfer`, el outcome `transferred`, los eventos `run_transferred`, `transfer_received` y `transfer_rejected` (sin `directory_read`: leer el directorio es un `tool_called` normal), `RunStartedPayload.origin`, `Agent.routing` y `Agent.accepts`. `SCHEMA_VERSION` pasó a 1.2.0 y `contracts/` está regenerado.
- **M4 cambia de fondo:** la sesión deja de ser un run. `find_run_by_session` pasa a devolver el run **abierto** de la sesión y la transferencia ocurre dentro del mismo turno.
- El registry expone el directorio y suma chequeos de compatibilidad de `accepts` y de enrutamiento al gate.
- La tool `directory/list` vive en `composition` (`DirectoryToolExecutor`); queda implementada pero sin cablear en la raíz de composición.
- **Riesgo aceptado:** la calidad del enrutamiento depende de fichas que publican otros equipos. Se compensa con la suite de enrutamiento compartida en el gate de cada especialista.
- **Riesgo aceptado:** los presupuestos son por run; una cadena de transferencias podría gastar varias veces el presupuesto. En la demo solo hay ida (una transferencia por sesión); el tope por sesión queda diseñado.
- **Pendiente de decisión:** ver "Abiertos" de la spec.
