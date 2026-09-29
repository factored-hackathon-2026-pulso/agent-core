"""`GuardService` real satisface `GuardsPort` y su salida alimenta `turn_started`."""

from typing import TYPE_CHECKING

from agent_core.guards import GuardService
from tests.m06.helpers import ES, make_agent, make_release, make_service, make_state

if TYPE_CHECKING:
    from agent_core.turn import GuardsPort

    def _conforms(x: GuardService) -> GuardsPort:
        return x


def test_texto_valido_en_espanol_es_kept_y_su_salida_va_a_turn_started() -> None:
    result, events = make_service().run(
        ES, make_state(locale="es"), make_agent(), make_release(), None, False, turn_id="turn-0001"
    )
    assert result.lang.decision == "kept" and result.size_ok and events == []
    assert result.to_output().lang.locale == "es"
