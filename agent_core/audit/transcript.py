"""Transcript (M11 §3.3): escritura por turno con huella con clave. Nunca razonamiento intermedio."""

from agent_core.domain import RejectedDraft, TranscriptEntry, TranscriptRef
from agent_core.ports import KeyProvider, TranscriptStore
from agent_core.views import fingerprint


class TranscriptWriteError(Exception):
    """El transcript store falló: se falla el turno (decisión 4). Sin el mensaje del store ni el texto."""

    def __init__(self, run_id: str, turn_id: str, cause: str) -> None:
        super().__init__(f"transcript no disponible (run={run_id}, turn={turn_id}, causa={cause})")
        self.run_id = run_id
        self.turn_id = turn_id


class TurnRecorder:
    def __init__(self, store: TranscriptStore, keys: KeyProvider) -> None:
        self._store = store
        self._keys = keys

    def record_turn(self, run_id: str, turn_id: str, user_msg_model: str, final_model: str,
                    rejected: list[RejectedDraft]) -> list[TranscriptRef]:
        """Orden de las referencias: `[user, *rejected, final]`. Los textos ya vienen en vista `model`."""
        planned: list[tuple[str, str, str | None]] = [
            ("user", user_msg_model, None),
            *[("rejected_draft", d.text_model, d.reason) for d in rejected],
            ("assistant", final_model, None),
        ]
        fingerprints = [fingerprint(text, self._keys) for _, text, _ in planned]  # antes de escribir
        refs: list[TranscriptRef] = []
        for (role, text, reason), fp in zip(planned, fingerprints, strict=True):
            entry = TranscriptEntry.model_validate(
                {"run_id": run_id, "turn_id": turn_id, "role": role, "text_model": text, "reason": reason})
            try:
                entry_id = self._store.append(entry)
            except Exception as exc:  # cualquier falla del store: el turno falla y se reintenta
                raise TranscriptWriteError(run_id, turn_id, type(exc).__name__) from None
            refs.append(TranscriptRef(entry_id=entry_id, fingerprint=fp))
        return refs
