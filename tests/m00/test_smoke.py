import agent_core
import agent_core.domain
import agent_core.ports


def test_packages_import() -> None:
    assert agent_core.__doc__
    assert agent_core.domain.__doc__
    assert agent_core.ports.__doc__
