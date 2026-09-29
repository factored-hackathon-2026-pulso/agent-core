# ADR 0012 — Portugués como idioma de conversación sobre clientes MX/CO/AR, evaluado con datos sintéticos etiquetados

- Estado: aceptado (2026-09-28)
- Unidad: 1 · Motor de decisión (con la unidad 6)
- Origen: hallazgo R1 de la revisión externa. Enmendado el 2026-09-28 por decisión del usuario sobre idioma de respuesta y responsabilidad de los datos PT, y por el tema #5 de la auto-revisión (detector de idioma).

## Contexto
- El reto exige demostrar interacciones en español y portugués y reportar las limitaciones de idioma.
- El dataset es solo en español (*"All text data is in Spanish"*), de clientes de México, Colombia y Argentina; no hay datos de Brasil.
- **(Auto-revisión #5)** La spec decía que el motor "detecta el idioma" sin nombrar el detector ni las reglas para mensajes cortos, ambiguos o mixtos, que son el caso difícil entre ES y PT.

## Decisión
- **Cliente lusófono:** hablantes de portugués dentro de la base de clientes de MX/CO/AR (expatriados, clientes transfronterizos).
  - Cambia el idioma de la conversación.
  - No cambian el país, la moneda, los productos ni la regulación.
  - **Brasil queda fuera de alcance** (BRL, LGPD y regulación propia) y se declara así.
- **Idiomas soportados: español y portugués.**
  - Los agentes responden en el idioma que habla la persona.
  - En cada turno, el motor detecta el idioma y actualiza el `locale` del run si es ES o PT.
  - Plantillas, prompts y el validador (cifras e idioma) usan ese `locale`.
  - Un idioma no soportado recibe una plantilla en el idioma por defecto del agente con los idiomas atendidos.
  - El gate de release exige plantillas y prompts para cada locale soportado.
- **Detector de idioma (auto-revisión #5):**
  - `lingua-py` local, fijado a versión exacta en una entidad versionada de la release. No es una tool de la unidad 3 ni un modelo externo.
  - Candidatos cerrados: los `supported_locales` más una lista corta de no soportados declarada en la release.
  - Reglas: limpieza de dígitos, montos, URLs, emojis y tokens; `short` y `undetermined` conservan el `locale`; histéresis para cambiar de idioma; "no soportado" exige umbral y largo mínimo; el primer turno parte del `lang` del request.
  - Umbrales por corrida de calibración sobre el conjunto ES/PT; si falta, valen 1.0 (nunca se cambia de idioma ni se declara no soportado).
  - El validador de respuesta usa el mismo detector y no rechaza textos `short` o `undetermined`.
  - Cada decisión se registra en `turn_started` con la versión del detector, los dos primeros candidatos y su puntaje.
- **Datos de evaluación y calibración en PT: no son responsabilidad del núcleo.**
  - Otro equipo produce los conjuntos sintéticos en PT: traducciones del conjunto ES con una muestra revisada por un nativo, **etiquetadas como sintéticas**.
  - El núcleo y la unidad 6 solo los consumen.
  - Si la muestra PT no alcanza el mínimo que fije la unidad 6, se usa la calibración ES y se reporta como limitación.

## Alternativas
- **Clientes en Brasil:** se descarta porque todo sería inventado y agregaría regulación que no podemos respaldar.
- **PT solo como capacidad, sin segmento:** cumple el reto, pero deja sin respuesta la pregunta de producto.
- **(#5) JEV como detector:** latencia de 70–500 ms según el proveedor, más la red hacia EE. UU., en serie antes de Understand; idiomas soportados no verificados; requiere respaldo local de todos modos. Se compara en la prueba de humo (ADR 0005) y, si gana claramente en mensajes cortos, se evalúa solo como segunda opinión en turnos `undetermined`.
- **(#5) fastText lid.176:** más rápido y liviano, pero más débil en textos cortos y exige filtrar 176 etiquetas.
- **(#5) Idioma como campo de Understand:** circular, porque los umbrales de Understand dependen del idioma.

## Consecuencias
- Las métricas por idioma separan ES (derivado de datos del reto) y PT (sintético).
- Queda pendiente para producción conseguir datos reales en PT y hacer una revisión nativa amplia.
- **(#5)** Según el reporte del autor (75 idiomas, sin restringir), lingua acierta 43,6 % (ES) y 59,0 % (PT) en palabras sueltas y más de 96 % en oraciones; el error dominante es la confusión mutua ES↔PT. Las reglas `short` y `undetermined` absorben ese caso a costa de no cambiar de idioma con mensajes muy cortos.
- **(#5)** La unidad 6 debe producir la corrida de calibración de idioma con subconjuntos de mensajes de 1 a 3 palabras y portuñol.

## Fuentes (#5)
- https://github.com/pemistahl/lingua-py
- https://raw.githubusercontent.com/pemistahl/lingua-py/main/accuracy-reports/lingua-high-accuracy/Spanish.txt
- https://raw.githubusercontent.com/pemistahl/lingua-py/main/accuracy-reports/lingua-high-accuracy/Portuguese.txt
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
