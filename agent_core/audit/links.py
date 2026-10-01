"""Verifies the hash link a transfer leaves between two chains (ADR 0021 D9, P2).

The link is `RunOrigin.from_event_hash`: the hash of the origin's `turn_completed` for the turn that
transferred (P2). Events appended to the origin afterwards (e.g. a denied read) do not move it, so the hash
is searched for in the chain instead of being expected at the end. Problems are fixed, readable messages:
they never include payload data, hashes or identifiers.
"""

from agent_core.audit.chain import check_chain
from agent_core.domain import RunStarted, RunState, RunTransferred, TurnCompleted
from agent_core.ports import AuditSink


def verify_transfer_link(target: RunState, sink: AuditSink) -> list[str]:
    """Problems found in the link from `target` back to its origin; empty if the link is valid.

    A run that is not the result of a transfer (`origin is None`) has no link: nothing to verify, `[]`.
    Only the link is checked. The integrity of the target chain itself is `AuditLog.verify_chain`.
    """
    origin = target.origin
    if origin is None:
        return []
    problems: list[str] = []
    source_events = sink.read(origin.from_run_id)
    if not source_events:
        return ["la cadena de origen no está disponible"]
    if not check_chain(origin.from_run_id, source_events).ok:
        problems.append("la cadena de origen no verifica")
    at = next((i for i, e in enumerate(source_events) if e.hash == origin.from_event_hash), None)
    if at is None:
        return [*problems, "el hash de origen no está en la cadena de origen"]
    completed = source_events[at]
    if not isinstance(completed, TurnCompleted):
        problems.append("el hash de origen no corresponde a un turn_completed")
    transferred = [
        e
        for e in source_events[: at + 1]
        if isinstance(e, RunTransferred) and e.payload.transfer_id == origin.transfer_id
    ]
    if len(transferred) != 1 or transferred[0].payload.to_run_id != target.run_id:
        problems.append("no hay un run_transferred que apunte a este run")
    event = transferred[0] if len(transferred) == 1 else None
    if event is not None and isinstance(completed, TurnCompleted) and completed.turn_id != event.turn_id:
        problems.append("el turn_completed no es el del turno que transfirió")
    source_started = source_events[0]
    if (
        not isinstance(source_started, RunStarted)
        or source_started.payload.agent != origin.from_agent
        or source_started.release != origin.from_release_id
    ):
        problems.append("el agente o la release de origen no coinciden con el run_started del origen")
    target_events = sink.read(target.run_id)
    first = target_events[0] if target_events else None
    if not isinstance(first, RunStarted):
        problems.append("el run_started del destino no lleva este origen")
        return problems
    if first.run_id != target.run_id or first.payload.origin != origin:
        problems.append("el run_started del destino no lleva este origen")
    if event is not None and (
        first.payload.agent != event.payload.to_agent or first.release != event.payload.to_release_id
    ):
        problems.append("el agente o la release del destino no coinciden con el run_transferred")
    return problems
