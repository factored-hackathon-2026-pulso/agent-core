# ADR 0027: el proveedor `rule` compara en la vista `full` y devuelve solo la vista `model`

- Estado: **propuesto, implementado en la rama `feat/rule-compares-on-full-view`; pendiente de aprobación de seguridad**
- Fecha: 2026-10-05
- Relacionado: M5 §3.1 ("el puerto recibe solo la vista `model`"), M7 (detector de PII), ADR 0008

## Contexto

El detector de PII (M7, `MIN_DIGITS = 6`) tokeniza toda cifra de 6 o más dígitos del texto libre del cliente, también un monto. Es una decisión de diseño documentada ("prefiere tokenizar de más, p. ej. un monto de 6+ dígitos, a dejar pasar un identificador") y no se toca aquí.

Consecuencia observada en un e2e real: `match-cargo` (proveedor `rule`) compara el texto del cliente con `merchant_name` y `amount` de las transacciones, pero en la vista `model` el monto ya es un token. En el dataset publicado el monto tiene 6 o más dígitos aun escrito sin centavos en el **98,7 %** de las transacciones en COP y el **70,1 %** en ARS (38,1 % de las de USD si se escriben los centavos). Si el comercio se repite, la regla da `varias` y el cliente recibe "No encontré ese cargo".

## Decisión

El proveedor `rule` (código local, determinista, sin red ni modelo) puede, **por opt-in explícito del modelo de decisión**, **comparar** contra la vista `full` de sus entradas:

```yaml
providers:
  - provider: rule
    config:
      compare_on: full     # ausente o "model": comportamiento anterior
      cases: [...]
```

Invariantes:

1. Solo el proveedor `rule` con `compare_on: full` recibe la vista `full` (`ProviderSpec.compares_on_full_view`). JEV, `classifier` y `llm_structured` no la reciben nunca, aunque su configuración la pida o implementen `predict_full`.
2. La vista `full` decide **qué filas** de una lista cumplen `narrow`. Los valores de `value_from` y los `count` salen de la lista en vista `model`, en las mismas posiciones: la salida de la regla no puede llevar un dato en claro. Si las dos listas no miden lo mismo, no queda ninguna fila (falla cerrada).
3. `handle_decide` solo calcula y pasa la vista `full` si algún proveedor del modelo la pide; el resto de modelos y de implementaciones de `DecisionPort` no cambian.
4. Los eventos de auditoría no cambian (`decision_made` ya guarda solo valores y probabilidades).

Además, `rule` lee las cifras con separadores de miles (`344.456,72`, `1,586,612.76`, `1.038.345`); antes partía esos números.

## Consecuencias

- El texto en claro del cliente llega a código local del propio motor, y solo cuando el modelo de decisión lo pide. No sale del proceso.
- Los modelos de decisión los pueden escribir builders. Con `compare_on: full` un builder puede hacer que una regla *compare* contra datos en claro, no que los *devuelva* (invariantes 2 y 3). Riesgo residual: un canal lateral por comparación (sí/no según un dato en claro), el mismo que ya existe en el nodo `rule` de políticas (`resolve_path` en vista `full`).
- **Pendiente (no implementado):** que la validación de propuestas del registry marque para revisión humana todo modelo con `compare_on: full`.

## Alternativas descartadas

- Aflojar el detector con contexto monetario: debilita un control de PII.
- Pasar el texto del cliente como argumento de tool a un servicio: saca el texto crudo del motor.
- Solo mejorar el mensaje cuando hay varias coincidencias: no identifica el cargo.

## Pruebas

`tests/m05/test_rule_full_view.py` (incluye el defecto real, con el detector de PII verdadero) y `tests/m02/test_decide.py` (el handler pasa la vista `full` solo con el opt-in).
