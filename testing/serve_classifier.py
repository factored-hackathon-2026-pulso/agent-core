"""Artefacto SINTÉTICO del proveedor `classifier` para ensayar `agentcore serve` (formato `tfidf-logreg-v1`).

No sale de un entrenamiento: pesos hechos a mano sobre un vocabulario de palabras clave para que la decisión
`elegir-especialista` (campo `choice`) tenga un artefacto del formato real. Solo lleva `choice`: el
clasificador emite todos los campos de su artefacto y el esquema de salida de la decisión no admite otros.
El artefacto real lo exporta el equipo de datos; este solo existe para la prueba local y lo declara en
`limitations`."""

from agent_core.domain import canonical_bytes, dumps, sha256_hex

_DISPUTE = ["cargo", "cargos", "disputa", "disputar", "disputo", "reconozco", "fraude", "duplicado",
            "duplicada", "cobraron", "cobro", "cobros", "cobrado", "doble", "veces", "indebido", "indebida",
            "compra",
            "cobrança", "cobranca", "contestação", "contestacao", "reconheço", "reconheco", "cobraram",
            "duas", "vezes", "cobrança", "cobrou"]
_INQUIRY = ["pqr", "estado", "consulta", "consultar", "consultas", "radicado", "caso", "revisar", "saber",
            "cómo", "como", "va", "producto", "productos", "saldo", "saldos", "cuenta", "cuentas",
            "movimiento", "movimientos", "cuánto", "cuanto", "tengo", "límite", "limite", "disponible",
            "solicitação", "solicitacao", "reclamação", "reclamacao", "reclamo", "status", "produtos",
            "conta", "extrato", "quanto", "tenho", "protocolo", "andamento"]
_VOCAB = list(dict.fromkeys([*_DISPUTE, *_INQUIRY]))
_CHOICE = ["consultas", "disputas"]


def synthetic_classifier_json() -> str:
    idf = [1.0] * len(_VOCAB)
    coef_choice = [[2.0 if t in _INQUIRY else 0.0 for t in _VOCAB],
                   [2.0 if t in _DISPUTE else 0.0 for t in _VOCAB]]
    body = {
        "format": "tfidf-logreg-v1",
        "vocab": {token: i for i, token in enumerate(_VOCAB)},
        "idf": idf,
        "classes": {"choice": _CHOICE},
        "coef": {"choice": coef_choice},
        "intercept": {"choice": [0.0, 0.0]},
        "limitations": ["artefacto sintético hecho a mano: palabras clave en español y portugués con pesos "
                        "fijos; no es un modelo entrenado y no se midió con mensajes reales"],
    }
    body["data_hash"] = sha256_hex(canonical_bytes(body))
    return dumps(body)
