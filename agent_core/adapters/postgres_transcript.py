"""`TranscriptStore` sobre Postgres (M11, unidad 7). Mismo contrato que `InMemoryTranscript`
(`tests/contracts/test_transcript_contract.py` corre contra los dos).

Cada operación usa la conexión de `PostgresStore.reading()` en autocommit: una entrada es una sentencia, así
que la falla de una escritura no deja medias filas. Una falla del store sube como excepción de psycopg y
`TurnRecorder` la convierte en `TranscriptWriteError` sin mensaje (el texto no se pierde en silencio).

`write_turn` es la vía del motor (m11 decisión 4): borra e inserta las entradas del turno en una sola
transacción, así que un reintento del turno no duplica ni deja restos. `append` sigue siendo una escritura
suelta, no idempotente."""

from agent_core.adapters.postgres_uow import PostgresStore
from agent_core.domain import TranscriptEntry

_ENTRY_ID_WIDTH = 10


class PgTranscriptStore:
    def __init__(self, store: PostgresStore) -> None:
        self._store = store

    def append(self, entry: TranscriptEntry) -> str:
        with self._store.reading() as conn:
            row = conn.execute(
                "INSERT INTO transcript_entries (run_id, turn_id, role, text_model, reason) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING seq",
                (entry.run_id, entry.turn_id, entry.role, entry.text_model, entry.reason)).fetchone()
        assert row is not None
        return _entry_id(row[0])

    def write_turn(self, run_id: str, turn_id: str, entries: list[TranscriptEntry]) -> list[str]:
        if any(e.run_id != run_id or e.turn_id != turn_id for e in entries):
            raise ValueError("todas las entradas deben ser del run y el turno indicados")
        ids: list[str] = []
        with self._store.reading() as conn, conn.transaction():  # borrar e insertar: todo o nada
            conn.execute("DELETE FROM transcript_entries WHERE run_id = %s AND turn_id = %s",
                         (run_id, turn_id))
            for entry in entries:
                row = conn.execute(
                    "INSERT INTO transcript_entries (run_id, turn_id, role, text_model, reason) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING seq",
                    (run_id, turn_id, entry.role, entry.text_model, entry.reason)).fetchone()
                assert row is not None
                ids.append(_entry_id(row[0]))
        return ids

    def read(self, run_id: str) -> list[TranscriptEntry]:
        with self._store.reading() as conn:
            rows = conn.execute(
                "SELECT run_id, turn_id, role, text_model, reason FROM transcript_entries "
                "WHERE run_id = %s ORDER BY seq", (run_id,)).fetchall()
        return [_entry(row) for row in rows]

    def recent_turns(self, run_id: str, n: int) -> list[TranscriptEntry]:
        if n <= 0:
            return []
        with self._store.reading() as conn:
            rows = conn.execute(
                "SELECT run_id, turn_id, role, text_model, reason FROM ("
                "SELECT seq, run_id, turn_id, role, text_model, reason FROM transcript_entries "
                "WHERE run_id = %s ORDER BY seq DESC LIMIT %s) last ORDER BY seq", (run_id, n)).fetchall()
        return [_entry(row) for row in rows]

    def delete_run(self, run_id: str) -> None:
        """Supresión (unidad 7). No toca el log de auditoría: T-M11-08."""
        with self._store.reading() as conn:
            conn.execute("DELETE FROM transcript_entries WHERE run_id = %s", (run_id,))


def _entry_id(seq: int) -> str:
    return f"entry-{seq:0{_ENTRY_ID_WIDTH}d}"


def _entry(row: tuple[str, str, str, str, str | None]) -> TranscriptEntry:
    return TranscriptEntry.model_validate(
        {"run_id": row[0], "turn_id": row[1], "role": row[2], "text_model": row[3], "reason": row[4]})
