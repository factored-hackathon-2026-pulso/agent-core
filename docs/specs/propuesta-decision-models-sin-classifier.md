# Propuesta: `match-cargo` y `elegir-especialista` no pueden usar el proveedor `classifier`

- Estado: **propuesta, pregunta para el equipo de agentes** (2026-10-05). No cambia código ni modelos.
- Contexto: al reemplazar los dobles de `serve` (ver `TEMAS-ABIERTOS-PENDIENTES.md` §13) la pieza `--classifier` ya
  carga artefactos reales (`agent_core.composition.artifacts:classifier_provider`). Falta saber qué modelos de
  decisión pueden usarlos de verdad.

## Lo que verifiqué

Dos modelos de decisión declaran `providers: [{provider: classifier, config: {artifact: sintetico}}]`:
`match-cargo@2.0.0` y `elegir-especialista@1.0.0` (fixtures `registry-e2e`, `registry-realflow`, `registry-demo`).
En la demo los resuelve un `ScriptedProvider`; con un `ClassifierProvider` real fallarían siempre, por tres razones:

1. **La entrada no trae `text`.** `ClassifierProvider.predict` exige `inputs["text"]` como string
   (`agent_core/decision/providers/classifier.py:101`). `handle_decide` arma la entrada con una clave por cada ruta de
   `input_view`, con la ruta como nombre (`agent_core/interpreter/projection.py:20`): `slots.descripcion_cargo`,
   `facts.candidatas.value`, `slots.problema`, `facts.directorio.value.entries`. Nunca `text`, y los slots llegan
   envueltos como texto no confiable (D8).
2. **No es clasificar un texto.** `match-cargo` decide entre `unica`, `ninguna` y `varias` comparando la descripción del
   cliente con una lista de cargos candidatos, y además devuelve cuál (`transaction`). `elegir-especialista` elige entre
   las opciones que el directorio devuelve en tiempo de ejecución (`choices_from`). El artefacto `tfidf-logreg-v1`
   tiene clases fijas por campo y un solo vector de texto.
3. **Sin etiquetas para entrenarlo.** Ni el dataset del reto ni la muestra E0 tienen textos de reclamo con variedad
   (5 descripciones distintas en 67 095 reclamos, 42 plantillas de transcripción) ni etiquetas por turno; y el README de
   E0 prohíbe usar `labels` para entrenar.

## Qué proveedores sí encajan hoy

| Proveedor | Para estos modelos | Límite |
|---|---|---|
| `jev` (`choice`) | `elegir-especialista`: elige una opción entre las del directorio. `match` (`unica`/`ninguna`/`varias`) también es un enum | JEV no extrae valores libres (`understand.py:111`): `transaction` no puede salir de JEV; habría que derivarlo con una tool o una regla sobre los candidatos |
| `llm_structured` | Puede devolver `transaction` | Sin `p_raw` (`llm_structured.py`, "solo baseline en evaluación"): todo queda bajo umbral y siempre cae a `low_confidence` |
| `rule` | Casos triviales (por ejemplo, un solo candidato) | No interpreta texto |
| `classifier` | Ninguno de los dos, tal como están definidos | Ver arriba |

## Propuesta

1. **`elegir-especialista`**: pasarlo a `jev` con una pregunta `choice` sobre las opciones del directorio, y calibrarlo con
   el mismo procedimiento que `understand-turno` (`testing/calibration/`). Los umbrales por valor dependen de qué
   especialistas haya, así que el conjunto de calibración tiene que cubrir el directorio real.
2. **`match-cargo`**: separar en dos pasos. `match` por `jev` (enum cerrado, calibrable) y la elección del `transaction`
   con una regla determinista sobre `facts.candidatas` (por ejemplo, si hay una sola candidata, esa), no con un modelo.
3. **`classifier`** queda disponible como proveedor para tareas de **un solo texto con clases fijas**. Hoy no hay ninguna
   en los agentes de demo; si aparece una, necesita sus propios datos etiquetados.

## Preguntas abiertas (no las resuelvo yo)

- ¿Los dueños de los agentes aceptan cambiar el proveedor de estos dos modelos?
- ¿`match-cargo` puede separarse en `match` y `transaction`, o el flujo `disputa-cargo` depende de que sea un solo `decide`?
- ¿Quién etiqueta el conjunto de calibración del directorio real de especialistas?
