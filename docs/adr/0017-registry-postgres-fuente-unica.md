# ADR 0017 — Registry: Postgres como única fuente de verdad

- Estado: aceptado (2026-09-29)
- Unidad: 2 · Entidades, registro y versionado
- Spec: `docs/specs/2026-09-29-registry-design.md`
- Enmienda: `docs/00-descomposicion-y-repos.md` (repo `agent-registry` con CI propio)

## Contexto
- La descomposición aprobada (2026-09-28) planteaba `agent-registry` como un repo git de contenido versionado, validado en CI, donde la auto-mejora abre PRs.
- Ese modelo supone que quien cambia el contenido revisa PRs. Aquí, personas no técnicas construyen y modifican agentes conversando con un agente constructor, y aprueban y publican desde la plataforma. Un agente constructor autónomo y un detector automático también proponen cambios.
- Git y Postgres juntos serían dos fuentes de verdad: publicar sería una escritura en dos sistemas, con fallas parciales y la pregunta de cuál gana ante una divergencia.
- El ADR 0001 fija el stack en Postgres, sin infraestructura extra, y el núcleo debe congelarse el 02/10.
- El motor exige releases inmutables con referencias exactas (M0 §2.2), y la plataforma debe mostrar qué versión de cada entidad corrió en una traza.
- Las entidades y las páginas de conocimiento son texto pequeño. No hay hoy datos que justifiquen un almacén de objetos.

## Decisión
1. **Postgres es la única fuente de verdad** del registry, en un esquema propio `registry`: versiones de entidad, releases, alias, propuestas, evaluaciones, aprobaciones y eventos de auditoría.
2. **Inmutabilidad reforzada en la base de datos:** las versiones y releases solo admiten `INSERT`. El rol de la aplicación no tiene `UPDATE` ni `DELETE`, y hay *triggers* como segunda barrera. Lo único mutable es el estado de una release (`active`/`revoked`), los alias por promoción con bitácora, y el estado de las propuestas.
3. **Contenido detrás de un puerto `BlobStore`** direccionado por hash. Se implementa sobre Postgres; pasar a S3 es un cambio de adaptador si aparecen adjuntos binarios, corpus grandes o datasets de evaluación pesados.
4. **YAML como formato de importación y exportación.** El loader de M1 sirve para sembrar la demo y las pruebas, y una exportación determinista sirve para inspección; ninguno es fuente de verdad.
5. **Un export a git de solo lectura es opcional**: un derivado desechable, que nunca se lee de vuelta y no cuenta como fuente de verdad.
6. **El registry es un módulo de `agent-core`** (`agent_core.registry`) con frontera de servicio: el motor solo depende de `RegistryPort`, y las funciones de validación de M1 se reutilizan desde el paquete. Se puede extraer a un servicio más adelante sin cambiar M0 ni el motor.

## Alternativas
| Alternativa | Por qué se descarta |
|---|---|
| **Git como fuente y Postgres como índice** | Reintroduce dos fuentes de verdad, con sincronización y modos de falla nuevos. |
| **Git como respaldo interno de cada publicación** | Con la aprobación en la plataforma, sus beneficios (PR, diff, CODEOWNERS) los ve un técnico, no el usuario; los cubren tablas de aprobaciones y un diff estructural calculado por el servicio. Una publicación pasa a ser una escritura en dos sistemas. |
| **Solo git, sin base de datos** | La revocación, el estado de alias y las consultas de linaje por traza quedan limitadas; la publicación no es transaccional. |
| **Servicio separado con su propia base** | Suma red, autenticación entre servicios y un segundo despliegue en una semana con el núcleo por congelar. La frontera de servicio se conserva para extraerlo después. |
| **Postgres + S3 desde el inicio** | No hay datos que justifiquen un almacén de objetos hoy. `BlobStore` deja la puerta abierta sin decidirlo ahora. |
| **Registro OCI, lakeFS o Dolt** | Resuelven el versionado, pero añaden una plataforma completa para lo que una tabla de versiones inmutables ya cubre. |
| **Event sourcing puro** | Da linaje perfecto, pero es ingeniería de más para el plazo. |

## Consecuencias
- El repo `agent-registry` y su CI dejan de ser el modelo. La validación G0 pasa al flujo de publicación del servicio.
- La auto-mejora escribe *propuestas* por la API en vez de abrir PRs (ADR 0018).
- Se pierde un respaldo legible por cualquier técnico. Se mitiga con backups de Postgres, la exportación YAML determinista y, si se activa, el export a git de solo lectura.
- `EntityKind` de M0 gana `knowledge_snapshot`, y `Release` gana un campo opcional `knowledge_snapshot` (aditivo, regenera `contracts/`).
- Hay que endurecer el esquema y los límites de tamaño, porque el contenido lo escriben agentes y no solo personas.
