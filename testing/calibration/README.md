# Calibración sintética de `understand-turno`

Conjunto etiquetado escrito a mano, salidas grabadas de JEV y el artefacto de calibración que sale de ahí.
Es **calibración de demostración**: los mensajes son sintéticos y más limpios que los de un cliente real.
El artefacto lo declara en `limitations`. Hay que recalibrar con turnos reales.

## Piezas

| Archivo | Qué es |
|---|---|
| `understand_turno_phrases.py`, `understand_turno.py` | Frases por comando y contexto; partición `dev` / `test` estratificada y sin frases repetidas entre ambas |
| `collect_jev.py` | Llama a JEV (necesita `AGENTCORE_JEV_API_KEY`) y graba la salida cruda en `recorded/*.jsonl`; reanudable |
| `calibrate_understand_turno.py` | Calibra **sin red** sobre lo grabado, escribe `recorded/<run_id>.json` y el informe, y mide en `test` |
| `recorded/` | Grabación, artefacto `cal-49e0da2c384f3dec` e informe del modelo tal como está hoy |
| `variants/understand-turno-strict.yaml` | Experimento: mismos criterios salvo `start_flow` y `out_of_scope` (no es el modelo de ningún agente) |
| `recorded/strict/` | Grabación, artefacto e informe de la variante |
| `holdout_check.py`, `recorded/holdout/` | Prueba fuera de muestra (ver abajo) |

Reproducir sin red: `uv run python -m testing.calibration.calibrate_understand_turno --raw
testing/calibration/recorded/understand_turno_raw.jsonl --out-dir testing/calibration/recorded`. Una prueba
(`tests/m05/test_understand_turno_calibration.py`) comprueba que el artefacto versionado sale exactamente de la
grabación.

## Resultados (español, conjunto cerrado `test`, n = 166)

| | Exactitud sin umbral | Cobertura | Precisión de lo aceptado | Cobertura de `start_flow` |
|---|---|---|---|---|
| Modelo actual | 92,8 % | 83,1 % | 97,8 % | 35,7 % |
| Variante estricta | 94,6 % | 97,6 % | 96,9 % | 100 % |

Con 13 a 37 casos por comando, los porcentajes por valor tienen mucha incertidumbre.

## Por qué hay una variante

El modelo actual marca como `start_flow`, con confianza de 0,98 a 1,0, trámites bancarios que no son
disputas ("abrir una cuenta de ahorros", "pedir un préstamo"). Una calibración no corrige un error seguro de sí
mismo: solo sube el umbral de `start_flow` a 0,99, y por eso casi ninguno pasa. La variante dice en el criterio
que esos trámites no son de este asistente.

**Cuidado con la lectura:** el criterio estricto lo escribí después de ver los errores del conjunto completo
(incluido `test`), y nombra justo frases de ese conjunto. La mejora en `test` por sí sola puede ser
sobreajuste. Por eso `holdout_check.py` usa mensajes nuevos, sobre temas que el criterio no nombra
(tarjeta nueva, tasas, constancias, límites, transferencias, divisas…):

| Prueba fuera de muestra | Trámites bancarios ajenos marcados `start_flow` | Aciertos |
|---|---|---|
| Modelo actual | 22 de 23 | 15 de 38 |
| Variante estricta | 0 de 24 | 41 de 41 |

(Las filas con timeout de JEV no cuentan; son pocas.) La mejora generaliza a temas no nombrados, pero son 42
mensajes escritos por la misma persona: confirma la dirección, no mide el tamaño del efecto real.

## Límites conocidos

- `interrupt` no es calibrable: su enum tiene un solo valor, JEV responde siempre `fraude` con 1,0. La decisión
  real la toma `command=interrupt`. En la variante, `interrupt` tuvo cobertura 100 % pero solo 71,4 % de precisión
  en `test` (n = 14): hay que mirarlo antes de adoptarla.
- Algunas etiquetas propias son discutibles (affirm contra continue, clarify contra continue con nodo activo,
  deny contra cancel). No se corrigieron después de ver las salidas de JEV.
- Portugués: 121 ejemplos en `dev`, por debajo del mínimo (200), así que `calibrate` copia la calibración del
  español.
- **El `run_id` no cubre la configuración del proveedor.** `calibrate` lo calcula con el modelo, el split, el
  método, los *nombres* de los proveedores, los objetivos y los mínimos (`calibrate.py`), pero no con su `config`
  (prompt, criterios, modelo de JEV). Por eso el artefacto de la variante estricta y el del modelo actual tienen
  el mismo `run_id` (`cal-49e0da2c384f3dec`) con umbrales distintos. Cambiar un criterio y recalibrar pisaría el
  artefacto anterior sin que el nombre lo delate, y el replay no podría distinguirlos. Se resuelve en el núcleo
  (incluir un hash de la `config` en el `run_id`), no aquí; es una decisión del equipo.
- Si el `run_id` cambia, los modelos que lo referencian (`thresholds_from`) deben actualizarse con una versión
  nueva del modelo.
