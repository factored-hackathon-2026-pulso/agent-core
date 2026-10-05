# Calibración de `understand-turno` (sintética)

- Artefacto: `cal-49e0da2c384f3dec` · método `isotonic` · `split_hash` `52329d6510ef…`
- Proveedor `jev`, modelo fijado en la definición. Muestras dev: {'es': 417, 'pt': 121}. Muestras test: {'es': 166, 'pt': 53}.
- Objetivos: `command` precision ≥ 0.95, `flow` precision ≥ 0.95, `interrupt` recall ≥ 0.9

## Límites

- calibrado con mensajes sintéticos escritos para esto (testing/calibration), no con tráfico real: los umbrales son de demostración y hay que recalibrar con turnos reales
- interrupt no es informativo: su enum tiene un solo valor (fraude), JEV responde siempre fraude con p_raw 1.0 y el recall sale 1.0 por construcción; la decisión real la toma command=interrupt
- pt: calibración copiada de es (muestra < mínimo)

## Medición en el conjunto cerrado (`test`)

Aceptado = pasa el umbral calibrado. Precisión = aciertos entre los aceptados. Cobertura = aceptados entre todos.

| Idioma | Campo | n | Cobertura | Precisión | Exactitud sin umbral |
|---|---|---|---|---|---|
| es | command | 166 | 83.1 % | 97.8 % | 92.8 % |
| es | flow | 28 | 92.9 % | 100.0 % | 96.4 % |
| es | interrupt | 14 | 100.0 % | 100.0 % | 100.0 % |
| pt | command | 53 | 88.7 % | 100.0 % | 96.2 % |
| pt | flow | 8 | 100.0 % | 100.0 % | 100.0 % |
| pt | interrupt | 4 | 100.0 % | 100.0 % | 100.0 % |

## `command` por valor verdadero (es, `test`)

| Valor | n | Cobertura | Precisión |
|---|---|---|---|
| affirm | 13 | 100.0 % | 100.0 % |
| cancel | 13 | 100.0 % | 100.0 % |
| clarify | 16 | 56.2 % | 100.0 % |
| continue | 37 | 100.0 % | 97.3 % |
| deny | 15 | 100.0 % | 93.3 % |
| handoff | 13 | 100.0 % | 100.0 % |
| interrupt | 14 | 92.9 % | 100.0 % |
| out_of_scope | 17 | 88.2 % | 100.0 % |
| start_flow | 28 | 35.7 % | 90.0 % |

## Umbrales (`command`, es)

| Valor | Umbral |
|---|---|
| affirm | 0.562 |
| cancel | 0.562 |
| clarify | 0.562 |
| continue | 0.824 |
| deny | 0.250 |
| handoff | 0.562 |
| interrupt | 0.562 |
| out_of_scope | 0.750 |
| start_flow | 0.990 |

Sin umbral (quedan en 1.0: nunca pasan): ninguno
