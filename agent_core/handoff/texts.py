"""Plantillas por defecto de M10 (ES y PT): resumen del handoff y mensaje de traspaso.

Son datos, no lógica: solo llevan conteos y el id del flow activo (nunca valores de campos), así el resumen
puede persistirse y mostrarse sin pasar por M7. El mensaje de traspaso no promete tiempos de atención
(ADR 0013: eso lo comunica la plataforma del asesor). Sobrescribirlos desde `agent-registry` es un abierto."""

from collections.abc import Mapping
from types import MappingProxyType

FALLBACK_LOCALE = "es"

_PREFIXES = ("rule", "policy", "interrupt")

_SUMMARY: Mapping[str, Mapping[str, str]] = MappingProxyType({
    "customer_request": {
        "es": (
            "El usuario pidió hablar con una persona. Flow activo: {flow}. Hechos verificados: "
            "{facts}. Acciones: {actions}."
        ),
        "pt": (
            "O usuário pediu para falar com uma pessoa. Fluxo ativo: {flow}. Fatos verificados: "
            "{facts}. Ações: {actions}."
        ),
    },
    "low_confidence": {
        "es": (
            "El agente no logró entender con suficiente confianza la solicitud. Flow activo: "
            "{flow}. Hechos verificados: {facts}. Acciones: {actions}."
        ),
        "pt": (
            "O agente não conseguiu entender o pedido com confiança suficiente. Fluxo ativo: "
            "{flow}. Fatos verificados: {facts}. Ações: {actions}."
        ),
    },
    "budget_exceeded": {
        "es": (
            "Se agotó el presupuesto de la conversación. Flow activo: {flow}. Hechos verificados: "
            "{facts}. Acciones: {actions}."
        ),
        "pt": (
            "O orçamento da conversa se esgotou. Fluxo ativo: {flow}. Fatos verificados: {facts}. "
            "Ações: {actions}."
        ),
    },
    "tool_failure": {
        "es": (
            "Falló un sistema necesario para atender el caso. Flow activo: {flow}. Hechos "
            "verificados: {facts}. Acciones: {actions}."
        ),
        "pt": (
            "Falhou um sistema necessário para atender o caso. Fluxo ativo: {flow}. Fatos "
            "verificados: {facts}. Ações: {actions}."
        ),
    },
    "verification_failed": {
        "es": (
            "No se pudo verificar el resultado de una acción. Flow activo: {flow}. Hechos "
            "verificados: {facts}. Acciones: {actions}."
        ),
        "pt": (
            "Não foi possível verificar o resultado de uma ação. Fluxo ativo: {flow}. Fatos "
            "verificados: {facts}. Ações: {actions}."
        ),
    },
    "validation_failed": {
        "es": (
            "No se pudo validar una respuesta al usuario. Flow activo: {flow}. Hechos verificados: "
            "{facts}. Acciones: {actions}."
        ),
        "pt": (
            "Não foi possível validar uma resposta ao usuário. Fluxo ativo: {flow}. Fatos "
            "verificados: {facts}. Ações: {actions}."
        ),
    },
    "release_revoked": {
        "es": (
            "La versión del agente fue revocada durante la conversación. Flow activo: {flow}. "
            "Hechos verificados: {facts}. Acciones: {actions}."
        ),
        "pt": (
            "A versão do agente foi revogada durante a conversa. Fluxo ativo: {flow}. Fatos "
            "verificados: {facts}. Ações: {actions}."
        ),
    },
    "auth_insufficient": {
        "es": (
            "El usuario no completó la verificación de identidad requerida. Flow activo: {flow}. "
            "Hechos verificados: {facts}. Acciones: {actions}."
        ),
        "pt": (
            "O usuário não concluiu a verificação de identidade exigida. Fluxo ativo: {flow}. "
            "Fatos verificados: {facts}. Ações: {actions}."
        ),
    },
    "rule": {
        "es": (
            "Una regla de negocio exigió atención humana. Flow activo: {flow}. Hechos verificados: "
            "{facts}. Acciones: {actions}."
        ),
        "pt": (
            "Uma regra de negócio exigiu atendimento humano. Fluxo ativo: {flow}. Fatos "
            "verificados: {facts}. Ações: {actions}."
        ),
    },
    "policy": {
        "es": (
            "Una política exigió atención humana. Flow activo: {flow}. Hechos verificados: "
            "{facts}. Acciones: {actions}."
        ),
        "pt": (
            "Uma política exigiu atendimento humano. Fluxo ativo: {flow}. Fatos verificados: "
            "{facts}. Ações: {actions}."
        ),
    },
    "interrupt": {
        "es": (
            "Se activó una interrupción que exige atención humana. Flow activo: {flow}. Hechos "
            "verificados: {facts}. Acciones: {actions}."
        ),
        "pt": (
            "Foi acionada uma interrupção que exige atendimento humano. Fluxo ativo: {flow}. Fatos "
            "verificados: {facts}. Ações: {actions}."
        ),
    },
    "default": {
        "es": (
            "El caso se escaló a atención humana. Flow activo: {flow}. Hechos verificados: "
            "{facts}. Acciones: {actions}."
        ),
        "pt": (
            "O caso foi encaminhado ao atendimento humano. Fluxo ativo: {flow}. Fatos verificados: "
            "{facts}. Ações: {actions}."
        ),
    },
})

_UNCERTAIN: Mapping[str, str] = MappingProxyType({
    "es": " Hay {uncertain} acción(es) incierta(s): verificar antes de reintentar.",
    "pt": " Há {uncertain} ação(ões) incerta(s): verificar antes de tentar novamente.",
})

_NO_FLOW: Mapping[str, str] = MappingProxyType({"es": "ninguno", "pt": "nenhum"})

_HANDOFF_MESSAGE: Mapping[str, str] = MappingProxyType({
    "es": (
        "Te voy a pasar con una persona de nuestro equipo que continuará con tu caso. Ya tiene el "
        "resumen de lo que hablamos."
    ),
    "pt": (
        "Vou transferir você para uma pessoa da nossa equipe que continuará com o seu caso. Ela já "
        "tem o resumo do que conversamos."
    ),
})


def reason_key(reason_code: str) -> str:
    """Clave de plantilla de un `reason_code`: el prefijo (`rule`, `policy`, `interrupt`) o el código base."""
    prefix = reason_code.partition(":")[0]
    if ":" in reason_code and prefix in _PREFIXES:
        return prefix
    return reason_code if reason_code in _SUMMARY else "default"


def _locale(locale: str, table: Mapping[str, object]) -> str:
    return locale if locale in table else FALLBACK_LOCALE


def render_summary(reason_code: str, locale: str, *, flow: str | None, facts: int, actions: int,
                   uncertain: int) -> str:
    """Resumen determinista: solo conteos e id de flow (nunca valores de campos)."""
    by_locale = _SUMMARY[reason_key(reason_code)]
    loc = _locale(locale, by_locale)
    text = by_locale[loc].format(flow=flow or _NO_FLOW[loc], facts=facts, actions=actions)
    if uncertain > 0:
        text += _UNCERTAIN[loc].format(uncertain=uncertain)
    return text


def default_handoff_message(locale: str) -> str:
    """Mensaje final al principal cuando la plantilla del agente no está disponible."""
    return _HANDOFF_MESSAGE[_locale(locale, _HANDOFF_MESSAGE)]
