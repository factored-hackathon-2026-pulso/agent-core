"""Dobles en memoria de los puertos de M0 (índice §4)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from testing.fakes.registry_dir import registry_from_directory

__all__ = ["registry_from_directory"]


def __getattr__(name: str) -> object:
    """Carga perezosa: importar los dobles de puertos no arrastra `agent_core.flows`."""
    if name == "registry_from_directory":
        from testing.fakes.registry_dir import registry_from_directory

        return registry_from_directory
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
