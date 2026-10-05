"""Transcript (M11 §3.3): escritura por turno con huella con clave. Nunca razonamiento intermedio."""

from typing import Literal

from pydantic import Field

from agent_core.domain import OnBehalfOf, Principal, RejectedDraft, TranscriptEntry, TranscriptRef
from agent_core.domain.base import Model
from agent_core.ports import IdSource, KeyProvider, TranscriptStore, UnitOfWorkFactory
from agent_core.views import TokenVault, ViewService, fingerprint

TRANSCRIPT_PURPOSE = "transcript_read"


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
        entries = [TranscriptEntry.model_validate(
            {"run_id": run_id, "turn_id": turn_id, "role": role, "text_model": text, "reason": reason})
            for role, text, reason in planned]
        try:  # todo el turno de una vez: atómico, y reescribirlo en un reintento no duplica
            entry_ids = self._store.write_turn(run_id, turn_id, entries)
        except Exception as exc:  # cualquier falla del store: el turno falla y se reintenta
            raise TranscriptWriteError(run_id, turn_id, type(exc).__name__) from None
        return [TranscriptRef(entry_id=entry_id, fingerprint=fp)
                for entry_id, fp in zip(entry_ids, fingerprints, strict=True)]


class RunNotFound(LookupError):
    """El run no existe (o el lector no puede saberlo: M9 decide qué código HTTP devuelve)."""


class RenderedEntry(Model):
    turn_id: str
    role: Literal["user", "assistant", "rejected_draft"]
    text: str = Field(repr=False)
    reason: str | None = None
    unknown_tokens: list[str] = Field(default_factory=list)


class TranscriptReader:
    """Autoriza por campo (política de la unidad 3 vía `AuthzPort`, dentro de `ViewService.render`)."""

    def __init__(self, store: TranscriptStore, uow_factory: UnitOfWorkFactory, views: ViewService,
                 keys: KeyProvider, ids: IdSource) -> None:
        self._store = store
        self._uow_factory = uow_factory
        self._views = views
        self._keys = keys
        self._ids = ids

    def read_rendered(self, run_id: str, reader: Principal,
                      on_behalf_of: OnBehalfOf | None) -> list[RenderedEntry]:
        with self._uow_factory() as uow:
            state = uow.load_run(run_id)
        if state is None:
            raise RunNotFound(run_id)
        vault = (TokenVault.open(state.token_map, run_id, self._keys, self._ids)
                 if state.token_map is not None else TokenVault(run_id, self._keys, self._ids))
        rendered: list[RenderedEntry] = []
        for entry in self._store.read(run_id):
            out = self._views.render(entry.text_model, vault, reader, TRANSCRIPT_PURPOSE, on_behalf_of)
            rendered.append(RenderedEntry(turn_id=entry.turn_id, role=entry.role, text=out.text,
                                          reason=entry.reason, unknown_tokens=out.unknown_tokens))
        return rendered
