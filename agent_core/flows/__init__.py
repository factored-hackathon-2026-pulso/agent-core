"""M1 — esquema de flows y validación estática (docs/specs/motor/m01-validacion-estatica.md).

Interfaz pública (§2, fase 1). Otros módulos importan solo de aquí, nunca de los submódulos.
"""

from agent_core.flows.claims import derive_claims
from agent_core.flows.jsonlogic import JSONLOGIC_OPS, jsonlogic_problems
from agent_core.flows.paths import Path, parse_path, template_vars, value_paths
from agent_core.flows.pin import PinnedRelease, pin_release
from agent_core.flows.registry import AuthoringRegistry, ReleaseDecl, load_registry
from agent_core.flows.schema import parse_flow
from agent_core.flows.validate import validate_flow, validate_registry
from agent_core.flows.view import RegistryView, release_view
from agent_core.flows.violations import FlowSchemaError, Violation
from agent_core.flows.yaml_loader import load_yaml

__all__ = [
    "JSONLOGIC_OPS",
    "AuthoringRegistry",
    "FlowSchemaError",
    "Path",
    "PinnedRelease",
    "RegistryView",
    "ReleaseDecl",
    "Violation",
    "derive_claims",
    "jsonlogic_problems",
    "load_registry",
    "load_yaml",
    "parse_flow",
    "parse_path",
    "pin_release",
    "release_view",
    "template_vars",
    "validate_flow",
    "validate_registry",
    "value_paths",
]
