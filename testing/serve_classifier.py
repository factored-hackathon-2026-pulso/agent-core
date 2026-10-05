"""Artefacto SINTÉTICO del proveedor `classifier` para ensayar `agentcore serve` (formato `tfidf-logreg-v1`).

No sale de un entrenamiento: pesos hechos a mano sobre un vocabulario mínimo para que la decisión
`elegir-especialista` (campo `choice`) tenga un artefacto del formato real. Solo lleva `choice`: el
clasificador emite todos los campos de su artefacto y el esquema de salida de la decisión no admite otros.
El artefacto real lo exporta el equipo de datos; este solo existe para la prueba local y lo declara en
`limitations`."""

from agent_core.domain import canonical_bytes, dumps, sha256_hex

_VOCAB = ["cargo", "disputa", "reclamo", "reconozco", "fraude", "duplicado", "pqr", "estado", "consulta",
          "radicado", "caso", "revisar"]
_CHOICE = ["consultas", "disputas"]


def synthetic_classifier_json() -> str:
    idf = [1.0] * len(_VOCAB)
    dispute = {"cargo", "disputa", "reclamo", "reconozco", "fraude", "duplicado"}
    coef_choice = [[2.0 if t in {"pqr", "estado", "consulta", "radicado", "caso", "revisar"} else 0.0
                    for t in _VOCAB],
                   [2.0 if t in dispute else 0.0 for t in _VOCAB]]
    body = {
        "format": "tfidf-logreg-v1",
        "vocab": {token: i for i, token in enumerate(_VOCAB)},
        "idf": idf,
        "classes": {"choice": _CHOICE},
        "coef": {"choice": coef_choice},
        "intercept": {"choice": [0.0, 0.0]},
        "limitations": ["artefacto sintético hecho a mano para ensayar el formato; no es un modelo"],
    }
    body["data_hash"] = sha256_hex(canonical_bytes(body))
    return dumps(body)
