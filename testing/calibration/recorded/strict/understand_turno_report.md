# Calibración de `understand-turno` (sintética)

- Artefacto: `cal-66d5b362c5386147` · método `isotonic` · `split_hash` `52329d6510ef…`
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
| es | command | 166 | 97.6 % | 96.9 % | 94.6 % |
| es | flow | 28 | 96.4 % | 96.3 % | 96.4 % |
| es | interrupt | 14 | 100.0 % | 100.0 % | 100.0 % |
| pt | command | 53 | 98.1 % | 96.2 % | 94.3 % |
| pt | flow | 8 | 100.0 % | 100.0 % | 100.0 % |
| pt | interrupt | 4 | 100.0 % | 100.0 % | 100.0 % |

## `command` por valor verdadero (es, `test`)

| Valor | n | Cobertura | Precisión |
|---|---|---|---|
| affirm | 13 | 100.0 % | 100.0 % |
| cancel | 13 | 92.3 % | 100.0 % |
| clarify | 16 | 87.5 % | 92.9 % |
| continue | 37 | 100.0 % | 100.0 % |
| deny | 15 | 93.3 % | 100.0 % |
| handoff | 13 | 100.0 % | 100.0 % |
| interrupt | 14 | 100.0 % | 71.4 % |
| out_of_scope | 17 | 100.0 % | 100.0 % |
| start_flow | 28 | 100.0 % | 100.0 % |

## Umbrales (`command`, es)

| Valor | Umbral |
|---|---|
| affirm | 0.583 |
| cancel | 0.872 |
| clarify | 0.583 |
| continue | 0.872 |
| deny | 0.583 |
| handoff | 0.583 |
| interrupt | 0.583 |
| out_of_scope | 0.872 |
| start_flow | 0.583 |

Sin umbral (quedan en 1.0: nunca pasan): ninguno
