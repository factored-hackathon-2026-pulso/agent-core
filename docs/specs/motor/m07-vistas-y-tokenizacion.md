# M7 — Vistas de datos y tokenización

- Estado: borrador · Fase 3
- Paquete: `agent_core.views`
- Origen: spec general §8.1, §8.1.1, §4.8 (renderer), §12 (fugas), §13.6
- ADRs: 0008 (vistas, `untrusted_text`, huellas con clave), 0003 (huellas del transcript)
- Usa: M0 · Lo usan: M2, M5, M8, M10, M11
- Contrato compartido con la unidad 3 (clasificación de campos y `tokenize`/`render`)

## 1. Propósito y límites

Produce las tres vistas de todo dato de cliente (`full`, `model`, `audit`), mantiene el `token_map` del run, renderiza tokens solo para lectores autorizados y calcula las huellas con clave.

**No hace:** decidir quién está autorizado (lo decide la política de la unidad 3 vía `AuthzPort`; M7 la consulta), ni guardar el transcript (M11).

## 2. Interfaz pública

```python
FieldClass = Literal["pii_direct", "pii_quasi", "financial", "untrusted_text", "public"]
class FieldClassifier:
    def classify(self, path: str) -> FieldClass          # sin clasificar → pii_direct
class TokenVault:                                          # uno por run
    def tokenize(self, value: str, field: str, cls: FieldClass) -> str
    def resolve(self, token: str) -> str | None
    def exists(self, token: str) -> bool
    def seal(self) -> EncryptedBlob ; @classmethod open(blob, key) -> TokenVault
class Views:  full: Any; model: Any; audit: Any
class ViewService:
    def project(self, data_full: Any, source: str, untrusted_fields: list[str], vault: TokenVault) -> Views
    def render(self, text_model_view: str, vault: TokenVault, reader: Principal, purpose: str) -> str
    def find_clear_pii(self, text: str, facts_full: dict) -> list[str]     # para M8, check 4
def fingerprint(data: Any, keys: KeyProvider) -> Fingerprint              # {alg, kid, value}
def verify_fingerprint(data: Any, fp: Fingerprint, keys: KeyProvider) -> bool
```

## 3. Comportamiento

### 3.1 Clasificación

Catálogo por defecto del §8.1 (`pii_direct`, `pii_quasi`, `untrusted_text`), reemplazable por la `FieldClassification` que publique el equipo de datos. Los campos se identifican por ruta `tabla.campo`; los resultados de tools declaran su tabla de origen.

### 3.2 Vista `model`

| Clase | Transformación |
|---|---|
| `pii_direct` | token estable del run: el mismo valor da el mismo token durante todo el run |
| `pii_quasi` | propuesta: `date_of_birth` → rango de edad de 10 años; `postal_code` → se elimina; `latitude`/`longitude` → se eliminan |
| `untrusted_text` | PII interna tokenizada con un detector de patrones (documento, teléfono, email, cuenta) + envoltura `<datos_no_confiables fuente="tabla.campo">…</datos_no_confiables>`; se escapan cierres falsos de la etiqueta dentro del texto |
| `financial`, `public` | pasan |

**Formato del token (propuesta):** `⟦<clase_corta>:<n>⟧`, p. ej. `⟦doc:1⟧`, `⟦tx:3⟧`. Delimitadores poco comunes para que M6 y M8 los detecten con una regex y el modelo no los confunda con texto normal.

### 3.3 Vista `audit`

Enmascarada (`pii_direct` → `***` + últimos 2–4 caracteres cuando aplique, `pii_quasi` como en `model`, `untrusted_text` → largo + huella), **sin tokens reversibles**, más `fingerprint(full)`.

### 3.4 `token_map`

- Vive cifrado en `RunState.token_map` (`EncryptedBlob` de M0; AES-GCM con `KeyProvider.key(token_map, kid)`). Las huellas usan `KeyProvider.key(fingerprint, kid)`: nunca la misma clave para cifrar y para HMAC. Nunca sale del núcleo ni va a eventos.
- **Origen de las vistas (M0 rev. 2):** `ToolResult` trae solo `result_full` y `source`; M7 calcula siempre las vistas `model` y `audit` dentro del núcleo. La unidad 3 aporta la clasificación y `ToolDef.untrusted_fields`, nunca el vault.
- Canonización para huellas: `canonical_bytes` de M0.
- `resolve` de un token inexistente devuelve `None`; quien lo usa decide (M5 invalida la salida, M8 rechaza).

### 3.5 Renderer

Recorre los tokens del texto validado; para cada uno pregunta a la política si `reader` puede ver ese campo con ese `purpose`. Sí → valor real; no → máscara de `audit`. Es la única vía por la que un valor `full` sale del núcleo.

### 3.6 Huellas con clave (§8.1.1)

- `value = HMAC-SHA256(k[kid], JCS(dato))`; textos como UTF-8 en NFC.
- `kid` vigente de `KeyProvider`; las claves anteriores solo verifican.
- En la demo, `EnvKeyProvider` lee un secreto de entorno etiquetado.

## 4. Invariantes

- Ningún valor `pii_direct` en claro en la vista `model` ni en `audit`.
- Todo `untrusted_text` en vista `model` va envuelto.
- Ninguna huella de datos de cliente es `sha256` sin clave.
- La misma entrada con claves en otro orden da la misma huella (JCS).

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Campo sin clasificar | `pii_direct` (puede empobrecer respuestas; riesgo §15) |
| `KeyProvider` sin clave | error de arranque; nunca se cae a hash sin clave |
| Token desconocido al renderizar | se deja la máscara y se registra como anomalía |

## 6. Eventos que emite

Ninguno propio. Sus vistas `audit` y huellas van dentro de los eventos de otros módulos.

## 7. Pruebas

| ID | Caso | §13 |
|---|---|---|
| T-M7-01 | Ningún request capturado hacia JEV o el LLM contiene `pii_direct` en claro (captura en `ScriptedGateway` y en el adaptador JEV) | 6 |
| T-M7-02 | `untrusted_text` llega delimitado y un cierre falso dentro del texto se escapa | 6 |
| T-M7-03 | El mismo valor da el mismo token en todo el run; valores distintos, tokens distintos | — |
| T-M7-04 | El renderer muestra al asesor autorizado y enmascara al no autorizado | 6 |
| T-M7-05 | Ninguna huella es `sha256` sin clave | 6 |
| T-M7-06 | Claves en otro orden → misma huella | 6 |
| T-M7-07 | Un registro con `kid` antiguo se verifica después de rotar | 6 |
| T-M7-08 | Con la huella y los campos visibles de `audit`, probar todos los documentos de un rango no recupera el valor sin la clave | 6 |
| T-M7-09 | Campo sin clasificar → tokenizado | — |
| T-M7-10 | `seal`/`open` del `token_map` hace round-trip | — |

## 8. Evaluación

- **Principal:** fugas de `pii_direct` en claro hacia proveedores externos (objetivo 0, con cota superior sobre los requests capturados).
- **Secundarias:** tokens desconocidos en salidas de modelos, proporción de campos sin clasificar vistos en runtime.

## 9. Puntos de iteración

- Catálogo de campos: dato; lo reemplaza `FieldClassification`.
- Reglas de generalización de `pii_quasi`: tabla configurable.
- Clave por subject con borrado criptográfico (producción): implementación nueva de `KeyProvider` con `kid` por subject.

## 10. Definición de terminado

- Tres vistas, `TokenVault` cifrado, renderer y huellas con T-M7-01…10 en verde.
- Captura de requests en los adaptadores externos activada en pruebas.

## 11. Abiertos

- Formato del token (propuesta en 3.2).
- Reglas de generalización de `pii_quasi` (propuesta en 3.2).
