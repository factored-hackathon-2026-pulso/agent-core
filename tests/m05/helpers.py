"""Constructores sintéticos para las pruebas de M5 (sin datos reales)."""

from collections.abc import Sequence

from agent_core.decision.calibration.artifact import CalibrationArtifact, IsotonicMap, Target
from agent_core.domain import CalibrationRef, DecisionModelDef, JsonValue, ProviderSpec
from agent_core.views import TokenVault
from testing.fakes.ids import FakeIds
from testing.fakes.keys import FakeKeyProvider

SPLIT_HASH = "b" * 64
RUN_ID = "run-1"

OUTPUT_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["command"],
    "properties": {
        "command": {"type": "string", "enum": ["affirm", "deny", "cancel", "start_flow"]},
        "flow": {"type": "string"},
        "slots": {"type": "object", "additionalProperties": True},
    },
}


def model_def(providers: Sequence[str] = ("classifier",), calibrated: Sequence[str] = ("command",),
              calibration_method: str = "isotonic", thresholds_from: str | None = "cal-1",
              calibration_run: str | None = "cal-1", input_view: Sequence[str] = ()) -> DecisionModelDef:
    """`DecisionModelDef` sintético: `command` enum cerrado y `slots` libre."""
    return DecisionModelDef.model_validate({
        "id": "understand", "version": "1.0.0", "output_schema": OUTPUT_SCHEMA,
        "calibrated_fields": list(calibrated), "input_view": list(input_view),
        "providers": [ProviderSpec(provider=name) for name in providers],  # type: ignore[arg-type]
        "calibration": CalibrationRef(method=calibration_method, run=calibration_run),  # type: ignore[arg-type]
        "thresholds_from": thresholds_from,
    })


def artifact(*, run_id: str = "cal-1", method: str = "isotonic",
             thresholds: dict[tuple[str, str, str, str], float] | None = None,
             calibrators: dict[tuple[str, str, str], IsotonicMap] | None = None,
             target: dict[str, Target] | None = None) -> CalibrationArtifact:
    """Artefacto mínimo: por defecto sin calibradores ni umbrales."""
    return CalibrationArtifact(
        run_id=run_id, split_hash=SPLIT_HASH, method=method,  # type: ignore[arg-type]
        calibrators=calibrators or {}, thresholds=thresholds or {},
        target=target or {"command": Target(metric="precision", value=0.9)},
    )


def identity_map() -> IsotonicMap:
    """Calibrador identidad (`apply(p) == p`)."""
    return IsotonicMap(xs=[0.0, 1.0], ys=[0.0, 1.0])


def vault(ids: FakeIds | None = None) -> TokenVault:
    return TokenVault(RUN_ID, FakeKeyProvider.default(), ids or FakeIds())
