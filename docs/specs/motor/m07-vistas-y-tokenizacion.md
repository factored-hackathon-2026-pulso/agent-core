# M7 — Vistas de datos y tokenización

- Estado: rev. 5 (2026-10-05) · Fase 3
- Paquete: `agent_core.views`
- Origen: spec general §8.1, §8.1.1, §4.8 (renderer), §12 (fugas), §13.6
- ADRs: 0008 (vistas, `untrusted_text`, huellas con clave), 0003 (huellas del transcript)
- Usa: M0 · Lo usan: M2, M5, M8, M10, M11
- Contrato compartido con la unidad 3 (clasificación de campos y `tokenize`/`render`)

## 1. Propósito y límites

Produce las tres vistas de todo dato de cliente (`full`, `model`, `audit`), mantiene el `token_map` del run, renderiza tokens solo para lectores autorizados y calcula las huellas con clave.

**No hace:** decidir quién está autorizado (lo decide la política de la unidad 3 vía `AuthzPort`; M7 la consulta), guardar el transcript (M11), ni limpiar datos (el ETL entrega el dato limpio; M7 proyecta por lector y deja `full` intacto para `rule`/`verify`). Las reglas de clasificación y de generalización son **datos** del catálogo; M7 solo trae los mecanismos.

## 2. Interfaz pública

```python
FieldClass = Literal["pii_direct", "pii_quasi", "financial", "untrusted_text", "public"]
class QuasiRule:  op: Literal["drop", "age_bucket"] = "drop"; width: PositiveInt = 10
class FieldRule:  field_class: FieldClass; tag: str = "pii"; quasi: QuasiRule = QuasiRule()   # quasi solo en pii_quasi
DEFAULT_CATALOG: Mapping[str, FieldRule]                   # §8.1 de la spec general, nada más
class FieldClassifier:
    def __init__(self, catalog: Mapping[str, FieldRule] = DEFAULT_CATALOG)
    def lookup(self, path: str) -> FieldRule | None         # ruta exacta `tabla.campo` y luego nombre del campo
    def rule(self, path: str) -> FieldRule                  # sin clasificar → pii_direct, tag "pii"
    def classify(self, path: str) -> FieldClass
TOKEN_PATTERN: str                                         # ⟦tag:n⟧, para M6 y M8
class TokenEntry:  token: str; tag: str; field: str; value: str
class TokenVault:                                          # uno por run
    def __init__(self, run_id: str, keys: KeyProvider, ids: IdSource)
    def tokenize(self, value: str, field: str, tag: str) -> str
    def resolve(self, token: str) -> str | None
    def exists(self, token: str) -> bool
    def lookup(self, token: str) -> TokenEntry | None
    def seal(self) -> EncryptedBlob
    @classmethod
    def open(cls, blob: EncryptedBlob, run_id: str, keys: KeyProvider, ids: IdSource) -> TokenVault  # TokenMapError
class Views:     full: JsonValue; model: JsonValue; audit: JsonValue; fingerprint: Fingerprint   # fingerprint(full)
class Rendered:  text: str; unknown_tokens: list[str]
class ViewService:
    def __init__(self, keys: KeyProvider, authz: AuthzPort, clock: Clock, classifier: FieldClassifier | None = None)
    def project(self, data_full: JsonValue, source: str, untrusted_fields: list[str], vault: TokenVault) -> Views
    def tokenize_text(self, text: str, vault: TokenVault) -> str        # texto libre del usuario → vista model
    def render(self, text_model_view: str, vault: TokenVault, reader: Principal, purpose: str,
               on_behalf_of: OnBehalfOf | None = None) -> Rendered
    def find_clear_pii(self, text: str, facts_full: Mapping[str, JsonValue]) -> list[str]  # M8, check 4
    def find_tokenized_echo(self, text: str, vault: TokenVault) -> list[str]  # M8 `suggest` (ADR 0026): tokens, nunca valores
def fingerprint(data: Any, keys: KeyProvider) -> Fingerprint              # {alg, kid, value}
def verify_fingerprint(data: Any, fp: Fingerprint, keys: KeyProvider) -> bool
```

## 3. Comportamiento

### 3.1 Clasificación

- Catálogo por defecto: exactamente el del §8.1 de la spec general (`pii_direct`, `pii_quasi`, `untrusted_text`), con tags `name` (`first_name`, `last_name`), `doc`, `email`, `tel` (`mobile_phone`, `landline_phone`), `addr`, `prod`, `ip`. Todo `pii_quasi` por defecto → `drop`.
- La `FieldClassification` que publique el equipo de datos se pasa como mapeo a `FieldClassifier` y extiende o reemplaza el catálogo (`{**DEFAULT_CATALOG, **propio}`). Ahí viven las entradas `financial`/`public` y las reglas `age_bucket`.
- Los campos se identifican por ruta `tabla.campo`; los resultados de tools declaran su tabla de origen (`source`). Para un dict anidado la ruta es `tabla.padre.campo`; los elementos de una lista conservan la ruta de la lista. La búsqueda prueba la ruta exacta y después el último segmento.
- Precedencia al proyectar: `pii_direct`/`pii_quasi` explícitos > `untrusted_fields` de la tool > resto del catálogo > sin clasificar (`pii_direct`).
- Un contenedor (dict o lista) sin clasificar se recorre; uno clasificado `pii_direct` se trata entero (un token de su JSON) y uno `pii_quasi` se elimina. Un contenedor `financial`/`public` también se recorre: cada hijo con regla `pii_direct`/`pii_quasi` explícita toma esa regla (la explícita gana) y toda otra hoja hereda la clase del contenedor (pasa, con `⟦`/`⟧` neutralizados). `null` pasa en todas las vistas.
- Un contenedor `untrusted_text` (por `untrusted_fields` o catálogo) también se recorre: cada hijo con regla `pii_direct`/`pii_quasi` explícita toma su clase (la explícita gana) y todo otro string se trata como `untrusted_text` (envuelto); los valores no string (números, booleanos) se resuelven con su propia clase (catálogo; sin clasificar → `pii_direct`) y `null` pasa. Un `.` dentro de una clave se trata como `_` al clasificar (cae a sin clasificar → `pii_direct`); la clave de salida conserva su texto.

### 3.2 Vista `model`

| Clase | Transformación |
|---|---|
| `pii_direct` | token estable del run: el mismo `(campo, valor NFC)` da el mismo token durante todo el run |
| `pii_quasi` | operación de su `QuasiRule`: `drop` elimina el campo; `age_bucket` convierte una fecha `AAAA-MM-DD` en un rango de `width` años (`"30-39"`) con la fecha del `Clock`; una fecha inválida o futura se elimina |
| `untrusted_text` | NFKC, neutralización de `⟦`/`⟧`, escape de toda etiqueta `<datos_no_confiables` o `</datos_no_confiables` (sin distinguir mayúsculas) como `&lt;…`, PII interna tokenizada con el detector de patrones (documento, teléfono, email, cuenta) y envoltura `<datos_no_confiables fuente="tabla.campo">…</datos_no_confiables>` |
| `financial`, `public` | pasan, con `⟦`/`⟧` neutralizados en textos y claves para que nadie falsifique un token |

**Texto libre del usuario (`tokenize_text`):** el mensaje del usuario a vista `model` para M6, M5 y el transcript (`TurnRuntime.model_text`, M4). Aplica lo mismo que `untrusted_text` (NFKC, neutralización de `⟦`/`⟧`, escape de etiquetas `<datos_no_confiables`, PII detectada → tokens del vault) pero **sin** la envoltura `<datos_no_confiables>`. Sin PII el texto sale igual (salvo NFKC).

**Formato del token:** `⟦<tag>:<n>⟧`, p. ej. `⟦doc:1⟧`, `⟦tx:3⟧`; `n` es un contador por tag dentro del run. Regex: `⟦([a-z]{1,12}):([1-9][0-9]*)⟧`.

**Normalización previa (rev. 4):** el detector no tiene una regex por variante: primero normaliza el texto (ya en NFKC) **solo para detectar** y después aplica las mismas reglas. La normalización (`views.detector.fold`): (1) elimina los caracteres de control (`Cc`), de formato (`Cf`: ancho cero U+200B–U+200D, U+2060, U+FEFF, guion blando U+00AD, marcas bidi, etiquetas…), las marcas combinantes (`Mn`/`Me`), los puntos de código sin asignar, de uso privado y sustitutos sueltos (`Cn`/`Co`/`Cs`) y los invisibles que no son `Cf` (rellenos hangul U+115F/U+1160/U+3164/U+FFA0, braille en blanco U+2800, U+FFFC); (2) lleva todo dígito decimal Unicode (`Nd`: árabe-índico, persa, devanagari, etc.) a ASCII (NFKC ya convirtió ancho completo, matemáticos y circulados), todo espacio Unicode (`Zs`) a espacio, U+2028/U+2029 a salto de línea y `。` (U+3002) a `.`; (3) lleva la letra acentuada a su base ASCII (`josé` → `jose`: NFKC compone letra + marca y la regex de email es ASCII) y `ø ł đ ð ı ß æ œ þ` a una letra ASCII; (4) junta corridas de dígitos separadas por un salto de línea o por 4 o más espacios (con a lo sumo un separador `- . , / _ ·` a cada lado del salto, un `(` después de él, y un `)` tras los dígitos), solo si hay grupos de 3 o más dígitos a ambos lados, ninguna corrida es una fecha ISO y la cadena unida suma de 10 a 19 dígitos (un PAN como máximo: un identificador por línea no se funde con el siguiente; así `4111-\n1111-\n1111-\n1111` se une, y un extracto con filas `2026-09-12  Compra  450` o `total 450\n300 pendientes` no); (5) reconstruye `@` y `.` ofuscados: `( @ )`, `(@)`, `[at]`, `(at)`, `{at}`, `[arroba]` (sin distinguir mayúsculas, con hasta 3 espacios alrededor), la palabra `arroba` suelta, `@` con hasta 3 espacios a un lado o a ambos, y `[dot]`/`(punto)`/`[.]`/`(.)`/`{.}` o las palabras `dot`/`punto` sueltas, siempre entre un alfanumérico y una letra. Cada hit se mapea de vuelta a los offsets del texto de entrada: se enmascara el span original completo (con sus separadores y caracteres invisibles) y el valor del token es el normalizado, así que `4111 1111 1111 1111`, `4111` + U+200B + `1111`… y `٤١١١…` dan el mismo token. El texto sin PII no cambia (los caracteres invisibles fuera de un hit se conservan). Costo lineal: todas las clases están acotadas, el solape email/número usa búsqueda binaria, el mapeo de offsets usa `array` de enteros de 8 bytes y hay una prueba con entradas de 1 MB (T-M7-14); el memoria pico es de unos 100–300 MB por MB de texto no ASCII, así que conviene un tope de longitud de mensaje aguas arriba.

**Detector:** email (también las variantes ofuscadas de arriba); secuencias de 6 o más dígitos con separadores sueltos (de 1 a 3 caracteres entre espacio, tab, punto, coma, `/`, `_`, `·`, paréntesis o guion ASCII/Unicode U+2010–U+2015, p. ej. `300  123  4567`, `(300) 123 4567`, `4111/1111/1111/1111`, `1.023.456.789`, `1023 – 456789`), con límites solo contra otros dígitos (detecta `CC1023456789`). Con `+` o 10 dígitos separados por espacio, tab, paréntesis o guion → `tel` (campo `mobile_phone`); 12 o más → `prod` (`product_number`); el resto → `doc` (`document_number`). Las fechas `AAAA-MM-DD` y las fechas con separador débil (`/`, `_`, `·`: `d/m/aa`, `d/m/aaaa`, `aaaa/mm/dd`, con o sin espacios) se ignoran y no se fusionan con sus vecinos (`15/09/2026 - 20/09/2026`). Las listas o rangos de `d/m` y `m/aaaa` (`15/09 - 20/09`, `09/2026, 10/2026`) se ignoran. Un número con separadores débiles o paréntesis solo es PII si tiene 10 o más dígitos o (sin paréntesis) 3 o más grupos de 3 o más dígitos con 8 o más en total; si no, cada tramo se evalúa aparte: `ley 1581/2012`, `09/2026`, `(601) 234` o `file_2026_09_15` no se tokenizan. Esto deja sin cubrir un documento de 6 a 9 dígitos con solo `/`, `_`, `·` o paréntesis y menos de 3 grupos de 3+ dígitos (p. ej. `123/456`, `123_456`), y PAN partidos con `/` en pares `d/mmmm` que imiten listas de fechas parciales (`41/1111 11/1111 11/1111`). Un número pegado a un email por un separador se recorta donde empieza el email (no se descarta); los dígitos dentro de la parte local de un email son parte del email. La coma como separador hace que listas como `1, 2, 3, 4, 5, 6` se tokenicen (falso positivo conservador). Es conservador a propósito: un monto de 6 o más dígitos en texto libre se tokeniza. Los nombres propios en texto libre no se detectan (límite conocido).

**Límites conocidos del detector (no cubiertos):** nombres propios; documentos de 6 a 9 dígitos partidos por un salto de línea o por 4+ espacios (`1234\n5678`: la cadena unida exige 10 o más dígitos para no fundir extractos); PAN con un separador más 4+ espacios (`4111-    1111`), con el separador solo en su línea, o con un número de 1 a 2 dígitos sueltos tras una fecha (`1023/45/67 89`); cadenas unidas de más de 19 dígitos; números separados por más de 3 caracteres (salvo saltos de línea y 4+ espacios entre grupos de 3+ dígitos), por puntuación no listada (`|`, `:`, `*`, `'`, `~`, `#`, `−` U+2212, `•`, `∙`, `⋅`) o con un grupo de 1–2 dígitos al otro lado del salto de línea, o con más de un separador junto al salto; punto espaciado (`dominio . com`), `@`/`punto` con 4+ espacios y puntos de otros alfabetos; `@` como `at` suelto (`usuario at dominio.com`), `%40`, `[a t]` o con salto de línea; punto como `,`, `-` o `·`; dígitos escritos con letras (`cuatro uno uno uno`) o intercalando letras (`4a1b1c1`); homóglifos de letras en correos (cirílico por latino) y letras latinas raras sin descomposición NFD fuera de la tabla (`ə`, `ƈ`); dígitos sin categoría `Nd` que NFKC no convierte (`❶`–`❾` U+2776–2792, `⓿`, `⓵`–`⓽`, etíopes, ideográficos); número pegado al local-part de un correo cuando los dígitos sueltos suman menos de 6; PII partida en varios mensajes; texto codificado (base64, URL, entidades HTML); la etiqueta falsa `<datos_no_confiables` con invisibles dentro no se escapa (anterior a rev. 4). Falsos positivos conservadores nuevos: `1_000_000_000`, `id_12345_67890`, una fecha seguida de un monto de 3+ dígitos (`12/09/2026   450.000`), listas de importes en líneas consecutivas que suman 10 a 19 dígitos, `equipo @ oficina.co` (se toma como correo).

**Claves de dict:** son nombres de esquema; solo se neutralizan `⟦`/`⟧`. Un identificador usado como clave no se tokeniza (límite conocido). Traspaso: la validación de resultados de tools aguas arriba (M5 / unidad 3) debe rechazar claves de dict con forma de dígitos o email; M7 solo neutraliza `⟦`/`⟧` en las claves.

### 3.3 Vista `audit`

Enmascarada, **sin tokens reversibles**:

- `pii_direct` → `***`, más los últimos 4 caracteres (10 o más) o los últimos 2 (6 a 9) solo para los tags `doc`, `tel` y `prod`; el resto, `***`;
- `pii_quasi` → como en `model`;
- `untrusted_text` → `{"untrusted_text": {"length": n, "fingerprint": {…}}}`;
- `financial`, `public` → pasan.

La huella con clave de `full` va aparte, en `Views.fingerprint` (→ `ToolCalledPayload.result_fp`). `Views.full` está excluido de `model_dump()`/`model_dump_json()` y de `repr`, y `Views`/`Rendered` ocultan el valor de entrada en los errores de validación (`hide_input_in_errors`).

### 3.4 `token_map`

- Vive cifrado en `RunState.token_map` (`EncryptedBlob` de M0): AES-256-GCM con una clave derivada por HKDF-SHA256 de `KeyProvider.key(token_map, kid)`, nonce de 12 bytes tomado de `IdSource.secret_token()` (por eso el replay nunca debe persistir blobs re-sellados y el `kid` debe rotar mucho antes de 2^32 sellados) y AAD `agentcore/token_map/v1|<run_id>` (un blob no abre en otro run). Las huellas usan `KeyProvider.key(fingerprint, kid)`: nunca la misma clave para cifrar y para HMAC. Nunca sale del núcleo ni va a eventos.
- `seal` usa el `kid` vigente; `open` usa el `kid` del blob (tras rotar, los blobs anteriores siguen abriendo y el siguiente `seal` usa la clave nueva). Un blob que no abre (clave, run o contenido) → `TokenMapError`, sin valores en el mensaje.
- **Origen de las vistas (M0 rev. 2):** `ToolResult` trae solo `result_full` y `source`; M7 calcula siempre las vistas `model` y `audit` dentro del núcleo. La unidad 3 aporta la clasificación y `ToolDef.untrusted_fields`, nunca el vault.
- Canonización para huellas: `canonical_bytes` de M0.
- `resolve` de un token inexistente devuelve `None`; quien lo usa decide (M5 invalida la salida, M8 rechaza).

### 3.5 Renderer

Recorre los tokens del texto validado; para cada uno pregunta a la política (`can_read_field(reader, on_behalf_of, campo, purpose)`) si `reader` puede ver ese campo con ese `purpose`. Sí → valor real; no → máscara de `audit`. Un token desconocido se deja como `***` y se devuelve en `Rendered.unknown_tokens` para que quien llama lo registre como anomalía. Los valores insertados no se vuelven a escanear. Es la única vía por la que un valor `full` sale del núcleo.

### 3.6 Huellas con clave (§8.1.1)

- `value = HMAC-SHA256(k[kid], JCS(dato))`; textos (valores y claves) como UTF-8 en NFC.
- `kid` vigente de `KeyProvider`; las claves anteriores solo verifican. `verify_fingerprint` con un `kid` desconocido devuelve `False`.
- En la demo, `EnvKeyProvider` lee un secreto de entorno etiquetado.

### 3.7 PII en claro (`find_clear_pii`, para M8)

Sobre el texto sin tokens (NFKC, y con la normalización previa del detector para los números y para `pattern:email`): cada hoja de `facts_full` cuya clase efectiva sea `pii_direct` (ruta `hecho.campo`) se busca en claro, sin distinguir mayúsculas y con límites de palabra; los identificadores numéricos (6 o más dígitos) se comparan sin separadores. Se añade `"pattern:email"` si aparece cualquier email. Devuelve rutas ordenadas, nunca valores. Las hojas sin clasificar cuentan como `pii_direct`: el catálogo debe clasificar los campos de hechos que se citan en claro.

**Límites:** los valores de menos de 4 caracteres (p. ej. `"Ana"`) y las cadenas de 1 a 3 dígitos nunca se buscan; un needle de varias palabras solo coincide con un único espacio entre ellas; los booleanos o enteros cortos sin clasificar pueden buscar palabras como `"true"`/`"2026"` (falsos positivos conservadores). `find_clear_pii` ve solo `hecho.campo` (sin la tabla de origen), mientras `project` clasifica por `tabla.campo`: las reglas de ruta exacta del catálogo no deben contradecir las reglas por nombre de campo; es responsabilidad del dueño de `FieldClassification` (unidad 3).

### 3.7.1 Eco de un valor tokenizado (`find_tokenized_echo`, para M8; ADR 0026)

`find_clear_pii` solo ve los hechos. La PII que el cliente pega en un mensaje (tarjeta, documento, teléfono, correo) entra por un slot, así que la salida de un nodo `suggest` podría repetirla sin que M8 lo note. `find_tokenized_echo(text, vault)` devuelve los **tokens** del vault cuyo valor en claro aparece en `text` (mismos criterios que `find_clear_pii`: NFKC, mayúsculas, límites de palabra y un número de 6+ dígitos por sus dígitos sin separadores). `TokenVault.entries()` lista las entradas (su `repr` no muestra valores).

- Solo cuentan las entradas que halló el detector de texto libre (`DETECTOR_TAGS`: `email`, `tel`, `prod`, `doc`). El `pii` de un campo sin clasificar puede ser una palabra de negocio (`vencido`, que M7 tokeniza por defecto) y decirla no es una fuga.
- Una cifra de un hecho no se confunde con PII: el detector no corre sobre el texto de salida (marcaría `1342.80`), solo se compara contra lo que ya se ocultó.
- **Hueco conocido (no corregido aquí):** lo que el detector no halla (un PAN con U+200B, `/` o `_` entre grupos, dígitos árabe-índicos, un celular con paréntesis, un correo con `@` separado) nunca llega al vault y por tanto no se detecta como eco.

## 4. Invariantes

- Ningún valor `pii_direct` en claro en la vista `model` ni en `audit`.
- Todo `untrusted_text` en vista `model` va envuelto.
- Ninguna huella de datos de cliente es `sha256` sin clave.
- La misma entrada con claves en otro orden da la misma huella (JCS).

## 5. Fallas

| Falla | Comportamiento |
|---|---|
| Campo sin clasificar | `pii_direct` (puede empobrecer respuestas; riesgo §15) |
| `KeyProvider` sin clave | `ViewsConfigError` al construir `ViewService`; nunca se cae a hash sin clave |
| Token desconocido al renderizar | `***` y el token en `Rendered.unknown_tokens` (anomalía que registra quien llama) |
| `token_map` que no abre | `TokenMapError` |

## 6. Eventos que emite

Ninguno propio. Sus vistas `audit` y huellas van dentro de los eventos de otros módulos.

## 7. Pruebas

| ID | Caso | §13 |
|---|---|---|
| T-M7-01 | Ningún request capturado hacia JEV o el LLM contiene `pii_direct` en claro (`RequestCapture` en `testing/capture.py`; en M7 se prueba con requests construidos desde vistas `model`; M5 y M8 lo conectan a `ScriptedGateway` y al adaptador JEV) | 6 |
| T-M7-02 | `untrusted_text` llega delimitado y un cierre falso dentro del texto se escapa | 6 |
| T-M7-03 | El mismo valor da el mismo token en todo el run; valores distintos, tokens distintos | — |
| T-M7-04 | El renderer muestra al asesor autorizado y enmascara al no autorizado | 6 |
| T-M7-05 | Ninguna huella es `sha256` sin clave | 6 |
| T-M7-06 | Claves en otro orden → misma huella | 6 |
| T-M7-07 | Un registro con `kid` antiguo se verifica después de rotar | 6 |
| T-M7-08 | Con la huella y los campos visibles de `audit`, probar todos los documentos de un rango no recupera el valor sin la clave | 6 |
| T-M7-09 | Campo sin clasificar → tokenizado | — |
| T-M7-10 | `seal`/`open` del `token_map` hace round-trip | — |
| T-M7-11 | `tokenize_text` cambia la PII del texto por tokens del vault, sin envoltura; mismo valor, mismo token; token o etiqueta falsos se neutralizan | 6 |
| T-M7-12 | Catálogo de variantes sintéticas (PAN Luhn de prueba, celular, correo, documento) con separadores `/` `_` `·`, ancho cero, controles, dígitos árabe-índicos/persas/devanagari/ancho completo/matemáticos, paréntesis, `[at]`, `( @ )`: todas enmascaradas en `model` por el span original completo; variantes equivalentes comparten token; `find_clear_pii` ve las mismas variantes | 6 |
| T-M7-13 | Controles negativos: fechas (`d/m/aaaa`, ISO, árabe-índicas), horas, versiones, números de 5 dígitos, `@`/`[at]` sin correo, emojis con ZWJ, texto con acentos: sin tokens y texto intacto; los montos de 6+ dígitos siguen tokenizándose | 6 |
| T-M7-14 | Entradas hostiles de 1 MB (ancho cero, separadores, paréntesis, `[at]`, `@` con espacios, números pegados a emails…) se detectan en tiempo acotado; 1 MB con PII real se tokeniza en tiempo acotado | — |
| T-M7-15 | El hueco histórico (PAN con U+200B entre grupos llegaba en claro) queda cerrado | 6 |

## 8. Evaluación

- **Principal:** fugas de `pii_direct` en claro hacia proveedores externos (objetivo 0, con cota superior sobre los requests capturados).
- **Secundarias:** tokens desconocidos en salidas de modelos (`Rendered.unknown_tokens`, `resolve → None`), proporción de campos sin clasificar vistos en runtime.

## 9. Puntos de iteración

- Catálogo de campos: dato; lo reemplaza `FieldClassification`.
- Reglas de generalización de `pii_quasi`: dato (`QuasiRule`); una operación nueva es un cambio de M7.
- Detector de patrones de `untrusted_text`: versión propia, reemplazable sin cambiar la interfaz.
- Clave por subject con borrado criptográfico (producción): implementación nueva de `KeyProvider` con `kid` por subject.

## 10. Definición de terminado

- Tres vistas, `TokenVault` cifrado, renderer y huellas con T-M7-01…15 en verde.
- Captura de requests en los adaptadores externos activada en pruebas: `RequestCapture` listo en M7; se activa en `ScriptedGateway` (M8) y en el adaptador JEV (M5).

## 11. Abiertos

Ninguno. Resueltos en rev. 2 (2026-09-29): formato del token (§3.2) y generalización de `pii_quasi` como dato (§3.1, §3.2).

## Cambios

- rev. 5 (2026-10-05): `TokenVault.entries()` y `ViewService.find_tokenized_echo(text, vault)` (§3.7.1), pedidos por el nodo `suggest` (ADR 0026). Aditivo; la refactorización de `find_clear_pii` conserva su comportamiento (mismas pruebas).
- rev. 4 (2026-10-05): el detector normaliza antes de detectar (§3.2): quita controles/formato/marcas/invisibles, dígitos Unicode a ASCII, acentos a su base, junta grupos partidos por saltos de línea o muchos espacios, reconstruye `@`/`.` ofuscados y mapea los hits al span original; separadores nuevos `/`, `_`, `·` y paréntesis con reglas para no tokenizar fechas, leyes ni nombres de archivo; `find_clear_pii` usa la misma normalización también para nombres y direcciones y encuentra un valor fundido con dígitos vecinos; T-M7-12…15. Sin cambios de interfaz pública. Corrige un costo cuadrático al solapar números con emails.
- rev. 3 (2026-09-29): `ViewService.tokenize_text(text, vault)` (T-M7-11), pedido por el cableado del motor: `TurnRuntime.model_text` no tenía API pública en M7 (el detector solo se usaba dentro de `untrusted_text`).
- rev. 2 (2026-09-29): formato `⟦tag:n⟧` adoptado; `pii_quasi` como regla de datos (`QuasiRule`, por defecto `drop`); catálogo por defecto solo §8.1 + override; `TokenVault(run_id, keys, ids)`, `tokenize(value, field, tag)`, `lookup`, `open(blob, run_id, keys, ids)`; `Views.fingerprint`; `render(..., on_behalf_of) -> Rendered`; `find_clear_pii` definido (§3.7); detector definido (§3.2); AES-GCM con HKDF y AAD por run (§3.4).
