# ADR 0008 — Vistas de datos y tokenización reversible por run

- Estado: aceptado (2026-09-28). Enmendado el 2026-09-28 por el hallazgo R6 de la revisión externa (`untrusted_text`, incorporado en la auto-revisión #9) y por el tema #4 de la auto-revisión (huellas con clave).
- Unidad: 1 · Motor de decisión (contrato compartido con la unidad 3)
- Origen: hallazgo C7 de la revisión externa

## Contexto
- El ejecutor devolvía `result` y `masked_result`, pero no estaba definido qué vista iba a los hechos, al LLM, a la auditoría y al validador.
- El reto prohíbe incluir *"private customer records… in external model requests"*.
- El workflow de disputas necesita referirse a transacciones y cuentas concretas.
- **(R6)** Campos de texto libre del dataset (descripciones y resoluciones de quejas, transcripciones de llamadas, comentarios de encuestas) pueden contener instrucciones inyectadas y PII no estructurada. La spec ya los trataba como clase propia, pero este ADR no la listaba (auto-revisión #9).
- **(Auto-revisión #4)** La vista `audit` guardaba un `sha256` sin clave de `full`. Como los campos no PII del resultado quedan visibles en `audit`, un lector del log solo tiene que probar valores de los campos enmascarados (documento, teléfono, fecha) para recuperarlos. Además, sin serialización canónica, el mismo resultado podía producir hashes distintos.

## Decisión
- **Clasificación de campos:** la publica el equipo de datos como `FieldClassification`, con las clases `pii_direct`, `pii_quasi`, `financial`, `untrusted_text` y `public`. El núcleo trae un catálogo por defecto derivado del diccionario de datos. **Un campo sin clasificar se trata como `pii_direct`.**
- **`untrusted_text` (R6):** texto libre de origen externo al sistema. Por defecto: `complaints.description`, `complaints.resolution`, `call_transcripts.full_text`, `call_transcripts.customer_text`, `call_transcripts.agent_text`, `satisfaction_surveys.open_comments`. Las tools lo declaran en `untrusted_fields[]`.
- **Tres vistas:**

  | Vista | Contenido | Destinos |
  |---|---|---|
  | `full` | Resultado completo | Hechos, `rule`, `verify` y argumentos de acciones; nunca sale del núcleo, salvo por el renderer |
  | `model` | `pii_direct` → token estable del run; `pii_quasi` → generalizado o eliminado; `untrusted_text` → tokenizado y envuelto en `<datos_no_confiables fuente="…">…</datos_no_confiables>`; el resto pasa | JEV, LLM y classifier |
  | `audit` | Enmascarada, sin tokens reversibles, + huella con clave de `full` | Log de auditoría, trazas y handoff por defecto |

- **Validador:** corre sobre la vista `model`. Un identificador en claro en la salida es un fallo (fuga o alucinación).
- **Renderer:** es determinista y, después del validador, reemplaza tokens por valores solo si la política autoriza al destinatario para ese campo. Si no, enmascara.
- **`token_map`:** vive cifrado en el estado del run y nunca sale del núcleo.
- **Huellas con clave (auto-revisión #4):**
  - `{alg: HMAC-SHA256, kid, value}` con `value = HMAC-SHA256(k[kid], JCS(full))` (JCS: RFC 8785).
  - La clave vive en un gestor de secretos, fuera del log y de la base del núcleo; rota por `kid` y las claves anteriores se conservan solo para verificar. Solo el servicio de verificación de evidencia la usa.
  - El mismo esquema se aplica a las huellas de entradas del transcript (ADR 0003).

## Alternativas
- **Redacción irreversible:** es más simple, pero el bot no podría referirse a cuentas ni transacciones concretas.
- **Sin enmascarar, confiando en el contrato del proveedor:** se descarta porque incumple la regla del reto.
- **(R6) Tratar el texto libre como `public`:** se descarta porque pasaría instrucciones inyectadas al modelo sin delimitar.
- **(#4) Clave por subject con borrado criptográfico:** permite inutilizar las huellas de un cliente, pero exige un almacén de claves por subject. Queda como **diseño de producción**.
- **(#4) No guardar huella de `full`:** se descarta porque la evidencia pasaría a depender solo de los logs del backend.

## Consecuencias
- Cuesta ~150 LOC más el catálogo de campos.
- Las salidas del modelo que contienen tokens (p. ej. un `transaction_id` elegido por `decide`) se resuelven contra el `token_map`, y un token desconocido invalida la salida.
- La métrica de fugas es: valores `pii_direct` en claro en requests a proveedores externos, con objetivo 0 y cota superior reportada. Se prueba capturando los requests en el gateway y en el adaptador de JEV.
- **(R6)** La delimitación no impide la injection por sí sola; se combina con el detector de injection y el modo degradado de la spec (§4.5).
- **(#4)** Verificar evidencia requiere reconstruir el resultado desde el backend, canonizarlo con JCS y comparar el HMAC con el `kid` registrado. Una filtración de la clave reabre el ataque por diccionario; se mitiga con acceso restringido y rotación.
