"""Referencias de autoría → referencias exactas de la release (M1 `release_view`)."""

from agent_core.actions import RefResolver
from agent_core.domain import EntityKind, EntityRef, InvalidRuntimeRef, RefSpec
from agent_core.flows import release_view
from agent_core.interpreter.context import StepContext


def exact_ref(ctx: StepContext, kind: EntityKind, ref: RefSpec) -> EntityRef:
    entity = release_view(ctx.registry, ctx.release).resolve(kind, ref)
    if entity is None:
        raise InvalidRuntimeRef(f"{kind.value} {ref} no está en la release {ctx.release.id}")
    return EntityRef(id=entity.id, version=entity.version)


def make_resolver(ctx: StepContext) -> RefResolver:
    """`RefResolver` de M3: sus referencias son tools (`action.tool`, `readback`) y plantillas."""

    def resolve(ref: RefSpec) -> EntityRef:
        for kind in (EntityKind.tool, EntityKind.template):
            try:
                return exact_ref(ctx, kind, ref)
            except InvalidRuntimeRef:
                continue
        raise InvalidRuntimeRef(f"{ref} no está en la release {ctx.release.id}")

    return resolve
