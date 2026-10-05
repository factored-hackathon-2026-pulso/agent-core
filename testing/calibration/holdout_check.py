"""Verificación fuera de muestra del criterio de `start_flow` / `out_of_scope` de `understand-turno`.

    AGENTCORE_JEV_API_KEY=... uv run python -m testing.calibration.holdout_check \
        --model-def DEF.yaml --out R.jsonl

Mensajes escritos DESPUÉS de ajustar el criterio estricto y a propósito sobre temas que ese criterio no
nombra (tarjetas nuevas, tasas, constancias, límites, transferencias, divisas…), más disputas con otra
redacción. Sirve para saber si la mejora del criterio generaliza o solo memoriza los ejemplos del conjunto.
Sin trámite en curso (`current_node` vacío). Solo texto sintético."""

import argparse
import json
import os
import sys
from pathlib import Path

from agent_core.adapters.system_clock import SystemClock
from agent_core.decision import HttpJevTransport, JevProvider, ProviderError, ProviderTimeout
from agent_core.domain import JsonValue
from testing.calibration.collect_jev import INTERRUPTS, MODEL_DEF, provider_spec, runtime_schema
from testing.calibration.understand_turno import FLOWS

OUT_OF_SCOPE_BANKING = [
    "quiero solicitar una tarjeta de crédito nueva", "¿cuál es la tasa de interés de un CDT a 90 días?",
    "necesito una constancia laboral de mi cuenta de nómina", "quiero subir el límite de retiro de mi cajero",
    "quiero transferir dinero a mi hermano", "¿cómo cambio el celular registrado en mi cuenta?",
    "necesito un certificado de saldo para una visa", "quiero domiciliar el pago de mi luz",
    "¿cuánto cobran por enviar dinero al extranjero?", "quiero cerrar mi cuenta de ahorros",
    "me interesa un crédito hipotecario", "necesito el extracto del mes pasado en PDF",
    "¿cómo activo mi tarjeta nueva?", "quiero cambiar el nombre que aparece en mi tarjeta",
    "¿cuál es el cupo de mi tarjeta?", "quiero pagar mi tarjeta desde otra cuenta",
    "quero aumentar o limite do meu cartão", "preciso de um comprovante de renda do banco",
    "quero pedir uma segunda via do cartão", "como faço para abrir uma conta conjunta?",
    "quiero un seguro para mi auto", "¿me ayudan a programar un pago recurrente?",
    "necesito actualizar mis datos de contacto", "quiero un adelanto de nómina",
    "¿qué requisitos hay para una cuenta empresarial?",
]
OUT_OF_SCOPE_OTHER = [
    "¿puedes recomendarme un libro?", "dime cómo está el tráfico hoy", "¿cuál es tu color favorito?",
    "ayúdame a escribir un correo para mi jefe", "¿cuánto es 15 por 12?",
]
DISPUTES = [
    "aparece una compra que yo no hice en mi tarjeta", "hay un descuento en mi cuenta que no autoricé",
    "me facturaron dos veces el mismo pedido", "necesito objetar un consumo de mi tarjeta",
    "no estoy de acuerdo con un cobro que me hicieron", "quiero que revisen un débito raro de mi cuenta",
    "¿ya respondieron la queja que radiqué hace un mes?", "estoy esperando la respuesta de mi reclamo",
    "quisiera saber en qué estado está mi solicitud de reembolso",
    "me cobraron una comisión por una cuenta que cerré",
    "quero contestar uma compra que não reconheço", "como anda a reclamação que abri semana passada?",
]


PORTUGUESE = {t for t in [*OUT_OF_SCOPE_BANKING, *DISPUTES]
              if t.startswith(("quero", "preciso de um", "como faço", "como anda"))}


def cases() -> list[tuple[str, str]]:
    """(texto, comando esperado): `out_of_scope` fuera de disputas; `start_flow` para disputas y consultas."""
    return ([(t, "out_of_scope") for t in OUT_OF_SCOPE_BANKING + OUT_OF_SCOPE_OTHER]
            + [(t, "start_flow") for t in DISPUTES])


def run(model_def: Path, out: Path) -> int:
    key = os.environ.get("AGENTCORE_JEV_API_KEY", "")
    if not key:
        print("falta AGENTCORE_JEV_API_KEY en el entorno", file=sys.stderr)
        return 2
    provider = JevProvider(HttpJevTransport(lambda: key, SystemClock()))
    spec, schema = provider_spec(model_def), runtime_schema(list(FLOWS), INTERRUPTS)
    rows: list[dict[str, object]] = []
    for text, expected in cases():
        inputs: dict[str, JsonValue] = {"text": text, "recent_turns": [], "current_node": None,
                                        "confirm_pending": False}
        row: dict[str, object] = {"text": text, "expected": expected, "banking": text in OUT_OF_SCOPE_BANKING}
        try:
            raw = provider.predict(spec, inputs, schema, "pt" if text in PORTUGUESE else "es")
        except (ProviderError, ProviderTimeout) as exc:
            row["error"] = type(exc).__name__
        else:
            row.update(predicted=raw.value.get("command"), p=str(raw.p_raw.get("command")))
        rows.append(row)
    lines = (json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    out.write_text("".join(lines), encoding="utf-8")
    ok = [r for r in rows if "error" not in r]
    hit = sum(r["predicted"] == r["expected"] for r in ok)
    bank = [r for r in ok if r["banking"]]
    leaked = [r for r in bank if r["predicted"] == "start_flow"]
    print(f"válidas {len(ok)}/{len(rows)}; aciertos {hit}; "
          f"banca ajena marcada start_flow: {len(leaked)}/{len(bank)}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m testing.calibration.holdout_check")
    parser.add_argument("--model-def", type=Path, default=MODEL_DEF)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.model_def, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
