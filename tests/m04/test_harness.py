from tests.m04.harness import World


def test_el_mundo_construye_el_motor_y_un_run_abierto() -> None:
    w = World()
    state = w.open_run()
    assert state.status == "open" and state.active_flow is None
    assert w.engine is not None and w.saved().state_version == 1
