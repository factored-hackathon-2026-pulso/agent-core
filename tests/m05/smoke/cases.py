"""Casos SINTÉTICOS de la prueba de humo: mensajes inventados para un catálogo de prueba neutro.

Catálogo: flows `track_order`, `cancel_subscription`, `change_address`. Etiquetas: `command` (y `flow` con
`start_flow`). Contexto (`ctx`): "confirm" = el agente acaba de pedir confirmación; "ask" = el agente pidió un
dato del trámite en curso; "none" = sin trámite. Sin datos reales, nombres, documentos ni cuentas."""

from dataclasses import dataclass
from typing import Literal

from agent_core.domain import JsonValue
from tests.m05.smoke.metrics import word_count

Ctx = Literal["none", "confirm", "ask"]
FLOWS = ("track_order", "cancel_subscription", "change_address")
COMMANDS = ("start_flow", "continue", "affirm", "deny", "clarify", "cancel", "handoff", "out_of_scope")

_CONFIRM_TURN = {"es": "¿Confirmas que quieres continuar con este trámite?",
                 "pt": "Você confirma que quer continuar com este procedimento?"}
_ASK_TURN = {"es": "¿Cuál es el número de tu pedido?", "pt": "Qual é o número do seu pedido?"}


@dataclass(frozen=True)
class Case:
    id: str
    lang: str            # "es", "pt" o "mix" (portuñol)
    text: str
    command: str
    flow: str | None
    ctx: Ctx

    @property
    def words(self) -> int:
        return word_count(self.text)

    def inputs(self) -> dict[str, JsonValue]:
        """Entrada de Understand en vista `model`: `{text, recent_turns, current_node, confirm_pending}`."""
        lang = self.lang if self.lang in _ASK_TURN else "es"  # portuñol: el agente habla en español
        turns: list[JsonValue] = {"none": [], "confirm": [_CONFIRM_TURN[lang]], "ask": [_ASK_TURN[lang]]}[
            self.ctx]
        node = {"none": None, "confirm": "confirm", "ask": "ask_order"}[self.ctx]
        return {"text": self.text, "recent_turns": turns, "current_node": node,
                "confirm_pending": self.ctx == "confirm"}


# (texto, command, flow, ctx)
_Row = tuple[str, str, str | None, Ctx]

_ES: list[_Row] = [
    # start_flow · track_order
    ("¿Dónde está mi pedido?", "start_flow", "track_order", "none"),
    ("quiero saber cuándo llega el paquete que compré", "start_flow", "track_order", "none"),
    ("rastrear pedido", "start_flow", "track_order", "none"),
    ("Mi compra no ha llegado y ya pasó una semana, ¿me pueden ayudar a seguirla?", "start_flow",
     "track_order", "none"),
    ("seguimiento del envío", "start_flow", "track_order", "none"),
    ("hola, necesito ver el estado de mi pedido por favor", "start_flow", "track_order", "none"),
    # start_flow · cancel_subscription
    ("Quiero cancelar mi suscripción", "start_flow", "cancel_subscription", "none"),
    ("dar de baja la suscripción", "start_flow", "cancel_subscription", "none"),
    ("ya no quiero seguir pagando el plan mensual, ¿cómo lo cancelo?", "start_flow", "cancel_subscription",
     "none"),
    ("cancelar suscripción", "start_flow", "cancel_subscription", "none"),
    ("necesito darme de baja del servicio, no lo uso más", "start_flow", "cancel_subscription", "none"),
    ("por favor cancélenme el plan", "start_flow", "cancel_subscription", "none"),
    # start_flow · change_address
    ("Necesito cambiar mi dirección de entrega", "start_flow", "change_address", "none"),
    ("me mudé y quiero actualizar la dirección", "start_flow", "change_address", "none"),
    ("cambiar dirección", "start_flow", "change_address", "none"),
    ("La dirección que puse está mal, ¿la puedo corregir?", "start_flow", "change_address", "none"),
    ("actualizar domicilio de envío", "start_flow", "change_address", "none"),
    ("quiero que el pedido llegue a otra dirección distinta", "start_flow", "change_address", "none"),
    # affirm
    ("Sí", "affirm", None, "confirm"), ("sí, adelante", "affirm", None, "confirm"),
    ("claro que sí, confirmo", "affirm", None, "confirm"), ("dale", "affirm", None, "confirm"),
    ("correcto, hazlo", "affirm", None, "confirm"),
    # deny
    ("No", "deny", None, "confirm"), ("no gracias", "deny", None, "confirm"),
    ("mejor no, prefiero mantenerlo", "deny", None, "confirm"), ("no quiero eso", "deny", None, "confirm"),
    ("negativo", "deny", None, "confirm"),
    # cancel (abandonar el trámite en curso)
    ("Mejor déjalo, ya no quiero seguir con esto", "cancel", None, "ask"),
    ("cancela todo esto", "cancel", None, "ask"), ("olvídalo", "cancel", None, "ask"),
    ("quiero salir de este proceso", "cancel", None, "ask"),
    ("déjalo así, no continúes", "cancel", None, "ask"),
    # clarify
    ("eh… no sé", "clarify", None, "none"), ("es que tengo un problema con lo otro", "clarify", None, "none"),
    ("¿me pueden ayudar con eso de ayer?", "clarify", None, "none"),
    ("no entiendo qué tengo que hacer", "clarify", None, "none"),
    ("sobre lo que hablamos", "clarify", None, "none"),
    # out_of_scope
    ("¿Quién ganó el partido anoche?", "out_of_scope", None, "none"),
    ("Cuéntame un chiste", "out_of_scope", None, "none"),
    ("¿Qué tiempo hará mañana en Bogotá?", "out_of_scope", None, "none"),
    ("recomiéndame una película", "out_of_scope", None, "none"),
    # handoff
    ("Quiero hablar con una persona", "handoff", None, "none"),
    ("pásame con un agente humano", "handoff", None, "none"),
    ("necesito un asesor de verdad, no un robot", "handoff", None, "none"),
    ("operador", "handoff", None, "none"),
    # continue (aporta el dato pedido)
    ("es el pedido número 12", "continue", None, "ask"), ("el que compré el lunes", "continue", None, "ask"),
    ("el número 7 creo", "continue", None, "ask"), ("el paquete grande", "continue", None, "ask"),
]

_PT: list[_Row] = [
    ("Onde está o meu pedido?", "start_flow", "track_order", "none"),
    ("quero saber quando chega a encomenda que comprei", "start_flow", "track_order", "none"),
    ("rastrear pedido", "start_flow", "track_order", "none"),
    ("Minha compra não chegou e já faz uma semana, podem me ajudar a acompanhar?", "start_flow",
     "track_order", "none"),
    ("acompanhamento da entrega", "start_flow", "track_order", "none"),
    ("oi, preciso ver o status do meu pedido por favor", "start_flow", "track_order", "none"),
    ("Quero cancelar minha assinatura", "start_flow", "cancel_subscription", "none"),
    ("encerrar a assinatura", "start_flow", "cancel_subscription", "none"),
    ("não quero mais pagar o plano mensal, como faço para cancelar?", "start_flow", "cancel_subscription",
     "none"),
    ("cancelar assinatura", "start_flow", "cancel_subscription", "none"),
    ("preciso sair do serviço, não uso mais", "start_flow", "cancel_subscription", "none"),
    ("por favor cancelem meu plano", "start_flow", "cancel_subscription", "none"),
    ("Preciso mudar meu endereço de entrega", "start_flow", "change_address", "none"),
    ("me mudei e quero atualizar o endereço", "start_flow", "change_address", "none"),
    ("mudar endereço", "start_flow", "change_address", "none"),
    ("O endereço que coloquei está errado, posso corrigir?", "start_flow", "change_address", "none"),
    ("atualizar endereço de entrega", "start_flow", "change_address", "none"),
    ("quero que o pedido chegue em outro endereço diferente", "start_flow", "change_address", "none"),
    ("Sim", "affirm", None, "confirm"), ("sim, pode seguir", "affirm", None, "confirm"),
    ("claro que sim, confirmo", "affirm", None, "confirm"), ("beleza", "affirm", None, "confirm"),
    ("correto, faça isso", "affirm", None, "confirm"),
    ("Não", "deny", None, "confirm"), ("não, obrigado", "deny", None, "confirm"),
    ("melhor não, prefiro manter", "deny", None, "confirm"), ("não quero isso", "deny", None, "confirm"),
    ("negativo", "deny", None, "confirm"),
    ("Deixa pra lá, não quero mais continuar com isso", "cancel", None, "ask"),
    ("cancela tudo isso", "cancel", None, "ask"), ("esquece", "cancel", None, "ask"),
    ("quero sair deste processo", "cancel", None, "ask"),
    ("deixa assim, não continue", "cancel", None, "ask"),
    ("hmm… não sei", "clarify", None, "none"), ("é que tenho um problema com aquilo outro", "clarify", None,
                                                 "none"),
    ("podem me ajudar com aquilo de ontem?", "clarify", None, "none"),
    ("não entendo o que preciso fazer", "clarify", None, "none"),
    ("sobre o que falamos", "clarify", None, "none"),
    ("Quem ganhou o jogo ontem à noite?", "out_of_scope", None, "none"),
    ("Conta uma piada", "out_of_scope", None, "none"),
    ("Como vai estar o tempo amanhã em São Paulo?", "out_of_scope", None, "none"),
    ("me indica um filme", "out_of_scope", None, "none"),
    ("Quero falar com uma pessoa", "handoff", None, "none"),
    ("me passa para um atendente humano", "handoff", None, "none"),
    ("preciso de um atendente de verdade, não de um robô", "handoff", None, "none"),
    ("operador", "handoff", None, "none"),
    ("é o pedido número 12", "continue", None, "ask"), ("o que comprei na segunda", "continue", None, "ask"),
    ("o número 7 acho", "continue", None, "ask"), ("o pacote grande", "continue", None, "ask"),
]

# Portuñol: mezcla de ES y PT en el mismo mensaje (no cuenta como es ni pt).
_MIX: list[_Row] = [
    ("quero cancelar mi suscripción", "start_flow", "cancel_subscription", "none"),
    ("necesito mudar mi dirección de entrega", "start_flow", "change_address", "none"),
    ("onde está mi pedido?", "start_flow", "track_order", "none"),
    ("no quiero mais continuar con esto", "cancel", None, "ask"),
    ("sim, adelante", "affirm", None, "confirm"),
    ("no, obrigado", "deny", None, "confirm"),
    ("quero hablar con una persona", "handoff", None, "none"),
    ("me mudé y quero actualizar el endereço", "start_flow", "change_address", "none"),
    ("cuéntame uma piada", "out_of_scope", None, "none"),
    ("el pedido é o de segunda", "continue", None, "ask"),
]


def _build(rows: list[_Row], lang: str) -> list[Case]:
    return [Case(f"{lang}-{index:02d}", lang, text, command, flow, ctx)
            for index, (text, command, flow, ctx) in enumerate(rows, start=1)]


def all_cases() -> list[Case]:
    return _build(_ES, "es") + _build(_PT, "pt") + _build(_MIX, "mix")
