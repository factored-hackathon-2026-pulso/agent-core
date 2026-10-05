"""Frases SINTÉTICAS para calibrar `understand-turno` (escritas a mano; sin datos reales ni texto de e0).

Cada fila es `(texto, contexto)`. Contextos del turno (ver `understand_turno.CONTEXTS`):
- `none`: no hay trámite en curso (`current_node` vacío).
- `ask`: el agente acaba de pedir un dato del trámite (`current_node` con valor, sin confirmación).
- `confirm`: el agente pidió confirmar una acción (`confirm_pending`).

Las filas de `continue` y de `ask` llevan además el agente (`disputas`, `consultas`, `recepcion`), porque la
pregunta que se acaba de hacer, y por tanto lo que cuenta como respuesta, cambia con él.

Sin nombres, documentos, tarjetas ni cuentas. Los montos y comercios son genéricos. Variantes regionales (MX,
CO, AR con voseo) y registros (formal, coloquial, fragmentado, impaciente) mezclados a propósito."""

Row = tuple[str, str]

# --- start_flow: ctx none, con el flujo que corresponde ------------------------------------------------------
START_FLOW_ES: dict[str, list[str]] = {
    "disputa-cargo": [
        "no reconozco un cargo en mi tarjeta", "me cobraron algo que yo no compré",
        "hay un cargo de $85.000 que no es mío", "quiero disputar un cargo", "aparece un cobro raro en mi estado de cuenta",
        "necesito desconocer una compra que no hice", "me llegó un cargo de una tienda en línea que no reconozco",
        "hola, vi un movimiento en mi tarjeta que no fui yo", "quiero reclamar un cobro que no entiendo",
        "me hicieron un cargo duplicado", "me cobraron dos veces lo mismo", "cobro indebido en mi tarjeta de crédito",
        "no sé de qué es un cargo de $1.250 pesos y quiero reportarlo", "me cobraron una comisión que no corresponde",
        "buenas, tengo un problema con un cargo que no hice", "quiero impugnar un consumo de mi tarjeta",
        "hay una compra en mi cuenta que yo no hice", "me aparece un cobro de un comercio que nunca visité",
        "¿me pueden ayudar? no reconozco un movimiento", "cargo no reconocido",
        "disputar cobro", "me debitaron un monto que no autoricé", "necesito reportar un cobro que no es mío",
        "che, me cobraron algo que no compré, ¿me ayudás?", "vos, tengo un cargo de $4500 que no reconozco",
        "me cobraron una suscripción que nunca contraté", "quiero que me devuelvan un cobro que no corresponde",
        "ayer vi en la app un cargo que no recuerdo haber hecho", "tengo una duda con un cobro de mi tarjeta, creo que está mal",
        "me cobraron de más en una compra", "me cargaron un seguro que no pedí",
        "el banco me cobró una cuota de manejo que ya me habían quitado", "necesito aclarar un cobro de $320 que no entiendo",
        "reporto un cargo sospechoso de una tienda que no conozco", "no fui yo quien hizo esa compra de ayer",
        "me llegó el estado de cuenta con un cobro que no reconozco", "quiero levantar una queja por un cobro indebido",
        "necesito ayuda con un cargo extraño", "hay un movimiento en mi cuenta que no entiendo, ¿lo pueden revisar?",
        "tengo un cobro que no corresponde y quiero que lo corrijan",
    ],
    "consulta-pqr": [
        "¿cómo va mi reclamo?", "quiero saber el estado de mi PQR", "¿ya tienen respuesta de mi queja?",
        "consultar el estado de mi solicitud", "hace dos semanas puse un reclamo y no me han dicho nada",
        "necesito saber en qué quedó mi caso", "estado de mi reclamo", "¿me pueden decir cómo va mi radicado?",
        "radiqué una queja y quiero saber si ya la revisaron", "hola, quería ver cómo va el reclamo que hice",
        "quiero seguimiento de mi PQR", "¿en qué estado está la disputa que abrí?", "ya pasó el plazo y no sé nada de mi reclamo",
        "consulta de radicado", "vos, ¿me podés decir cómo va mi reclamo?", "che, ¿ya resolvieron mi queja?",
        "quisiera saber si ya hay respuesta a la solicitud que presenté", "necesito revisar un reclamo que ya radiqué",
        "mi caso sigue abierto y quiero saber por qué", "¿se resolvió mi queja del cobro?", "dame el estado de mi reclamo por favor",
        "tengo un número de radicado y quiero saber qué pasó", "pasaron los días y mi PQR sigue sin respuesta",
        "buenas tardes, consulto por el estado de un reclamo anterior", "quería averiguar cómo avanza mi trámite de reclamación",
        "¿me confirmas si mi reclamo ya fue aceptado?", "revisar mi queja", "¿cuánto falta para que respondan mi reclamo?",
        "estado de mi caso", "necesito saber si mi reclamo prosperó", "abrí un reclamo la semana pasada, ¿cómo va?",
        "me prometieron respuesta en 15 días y ya pasaron, ¿qué pasó?", "quiero consultar una PQR",
        "¿hay novedades de mi reclamo?", "consulto por mi reclamación, ¿ya la revisaron?",
        "hola, ¿pueden decirme qué pasó con mi queja?", "buenas, ¿en qué va el reclamo que presenté?",
        "me interesa saber el resultado de mi reclamo", "¿ya salió el resultado de mi solicitud?",
        "quiero ver si mi reclamo ya tiene una decisión",
    ],
}
START_FLOW_PT: dict[str, list[str]] = {
    "disputa-cargo": [
        "não reconheço uma cobrança no meu cartão", "me cobraram algo que eu não comprei", "quero contestar uma compra",
        "tem uma cobrança de R$ 85 que não é minha", "apareceu um lançamento estranho na minha fatura",
        "fui cobrado duas vezes pela mesma compra", "cobrança indevida no cartão de crédito",
        "preciso reportar uma compra que eu não fiz", "oi, vi um movimento no cartão que não fui eu",
        "me cobraram uma assinatura que nunca contratei", "quero reclamar de uma tarifa que não deveria existir",
        "contestar cobrança", "tem um débito na minha conta que eu não autorizei",
    ],
    "consulta-pqr": [
        "como está minha reclamação?", "quero saber o estado da minha solicitação", "já tem resposta da minha queixa?",
        "faz duas semanas que abri uma reclamação e ninguém me disse nada", "preciso saber como ficou meu caso",
        "consultar o andamento do meu protocolo", "status da minha reclamação", "oi, queria ver como vai a reclamação que fiz",
        "já passou o prazo e não tenho notícia da minha solicitação", "acompanhar minha reclamação",
        "o que aconteceu com a queixa que registrei?", "pode me dizer como está meu protocolo?",
        "minha reclamação foi aceita?",
    ],
}

# --- continue: ctx ask, por agente ---------------------------------------------------------------------------
CONTINUE_ES: dict[str, list[str]] = {
    "disputas": [
        "es un cargo de $85.000 de una tienda en línea", "el cobro del 3 de marzo", "fueron $1.250 en una gasolinera",
        "no reconozco el de 120 mil pesos", "sí, un cargo de una app de comida", "creo que fue en un supermercado, el viernes",
        "el de ayer por $430", "es de una tienda de ropa que no conozco", "de Netflix o algo así, pero yo no tengo eso",
        "fueron dos cargos iguales de $60", "el que dice MERCADO CENTRAL en el estado de cuenta",
        "un cargo de $2.300 el día 12", "no recuerdo el comercio pero fue por $99", "el cobro de la cuota de manejo",
        "es un cargo en dólares, 35 USD", "uno de $15.000 de hace una semana", "el más grande, el de $540",
        "fue en una farmacia, que yo no fui", "no sé el nombre, solo que salió ayer en la app",
        "el cargo de la suscripción de música", "me cobraron $78 en una tienda de electrónica",
        "fue el cargo del 20 de abril, de $3.400", "un cobro de una aerolínea que no usé",
        "el de la tienda virtual, 250 mil pesos", "ese cargo de un casino en línea, yo no juego",
        "es uno de $9.99 de una aplicación", "el cobro duplicado del restaurante", "el del lunes por la noche, $180",
        "mmm, el de una tienda de mascotas", "es el de $64.900, de un envío internacional",
        "che, es el de $8000 de ayer, no sé de qué es", "el cargo que apareció esta mañana, de una plataforma de streaming",
        "fue en una estación de servicio, pero estaba en otra ciudad", "se trata de un débito automático de $340 que no autoricé",
        "uno de una tienda departamental, el 5 de este mes", "el cobro que tiene fecha 14 de febrero",
    ],
    "consultas": [
        "el radicado es 4821937", "mi número es 20250318", "es el 77412", "RAD-2025-0098213", "creo que es el 5530021",
        "el número de mi reclamo es 1048832", "te paso el radicado: 90021346", "es 3341, lo tengo en el correo",
        "el que me llegó por mensaje, 6120945", "radicado 2025-45012", "el de la queja del cobro, el 8830127",
        "sería el 215904", "es el 99120, de marzo", "el número es 730118 y lo radiqué por la app",
        "4412099", "tengo anotado el 560032", "uno, cero, cuatro, ocho, dos, tres, uno", "el radicado que empieza con 55",
        "me dieron el 3099871 cuando llamé", "es el reclamo 120044", "el 81230, del cargo duplicado",
        "el número está en el correo: 7741209", "el último que radiqué, 9004412", "mi folio es 33120478",
        "aquí lo tengo: 2468013", "el de la disputa, 1357924", "pues el número es 600128, creo",
        "radicado 11003 de la semana pasada", "es el 5190077", "el reclamo número 889102",
    ],
    "recepcion": [
        "no reconozco un cargo de mi tarjeta", "quiero saber cómo va mi reclamo", "tengo un problema con la app",
        "me cobraron de más", "no puedo entrar a mi cuenta", "quiero reclamar por un cobro", "es sobre un cargo del fin de semana",
        "necesito ayuda con un cobro duplicado", "es por una queja que hice antes", "me bloquearon la tarjeta y no sé por qué",
        "es sobre un cobro que no entiendo", "quiero disputar una compra", "consulta sobre mi reclamo de marzo",
        "el problema es con un cargo que no hice", "me llegó un cobro de una suscripción", "es por mi estado de cuenta, hay algo raro",
        "hay un movimiento en mi cuenta que no es mío", "vengo por un reclamo pendiente", "necesito revisar un cobro",
        "mi tarjeta tiene un cargo de $200 que no reconozco", "estoy esperando respuesta de una queja", "es por una devolución que no llega",
    ],
}
CONTINUE_PT: dict[str, list[str]] = {
    "disputas": [
        "é uma cobrança de R$ 85 de uma loja online", "a cobrança do dia 3 de março", "foram R$ 12 em um posto de gasolina",
        "não reconheço a de 120 reais", "foi num supermercado, na sexta", "a de ontem, de R$ 43", "de uma loja de roupas que não conheço",
        "duas cobranças iguais de R$ 60", "uma cobrança de R$ 230 no dia 12", "a da assinatura de streaming",
        "foi uma cobrança em dólar, 35 USD", "a mais alta, a de R$ 540",
    ],
    "consultas": [
        "o protocolo é 4821937", "meu número é 20250318", "é o 77412", "RAD-2025-0098213", "acho que é o 5530021",
        "o número da minha reclamação é 1048832", "te passo o protocolo: 90021346", "é o 3341, está no e-mail",
        "protocolo 2025-45012", "seria o 215904", "o número é 730118 e registrei pelo app", "4412099",
    ],
    "recepcion": [
        "não reconheço uma cobrança do meu cartão", "quero saber como está minha reclamação", "tenho um problema com o app",
        "me cobraram a mais", "não consigo entrar na minha conta", "é sobre uma cobrança duplicada",
        "é por uma reclamação que fiz antes", "quero contestar uma compra", "é sobre um débito que não entendo",
        "o problema é com uma cobrança que não fiz", "aguardo resposta de uma queixa", "tem algo estranho na minha fatura",
    ],
}

# --- affirm / deny: ctx confirm -------------------------------------------------------------------------------
AFFIRM_ES: list[str] = [
    "sí", "sí, confirmo", "dale", "ok, adelante", "correcto", "así es", "sí, por favor", "claro que sí", "perfecto, hazlo",
    "confirmado", "de acuerdo", "va, adelante", "sí, procede", "está bien, continúa", "sí señor", "listo, confirmo",
    "ajá, sí", "exacto", "sí, está correcto", "adelante con eso", "sí, radícalo", "yes, sí", "claro, dale", "por supuesto",
    "sí, así está bien", "ok", "sí, estoy de acuerdo", "ándale, sí", "bueno, sí", "sí, confirmo la información",
    "sip", "dale que sí", "sí, procedan", "correcto, gracias", "acepto",
]
DENY_ES: list[str] = [
    "no", "no, gracias", "mejor no", "no confirmo", "no es correcto", "no, no es eso", "no quiero", "negativo",
    "no, espera, no", "todavía no", "no estoy de acuerdo", "no, así no", "no procedas", "prefiero que no", "no, está mal",
    "nel", "no, déjalo así", "no por ahora", "no, ese no es el monto", "nop", "no, no lo hagas", "para nada",
    "no me convence, no", "no, cambia eso primero", "no acepto", "no, la verdad no", "no, no quiero que lo radiques",
    "en absoluto", "no, esa no es la información", "no gracias, mejor lo reviso",
    "no, no estoy seguro", "no, prefiero no continuar con eso", "ni de broma", "tampoco, no", "no, detente",
]
AFFIRM_PT: list[str] = [
    "sim", "sim, confirmo", "pode ser", "ok, pode seguir", "correto", "isso mesmo", "sim, por favor", "claro que sim",
    "perfeito, faça isso", "confirmado", "de acordo", "beleza, pode continuar", "sim, procede", "tá certo, continua",
    "exato", "sim, está correto", "pode registrar", "com certeza",
]
DENY_PT: list[str] = [
    "não", "não, obrigado", "melhor não", "não confirmo", "não está correto", "não, não é isso", "não quero", "negativo",
    "ainda não", "não concordo", "não, assim não", "não faça isso", "prefiro que não", "não, está errado",
    "deixa assim, não", "agora não", "nunca", "de jeito nenhum",
]

# --- clarify: mensajes ambiguos o incompletos ------------------------------------------------------------------
CLARIFY_ES: list[Row] = [
    ("hola", "none"), ("buenas", "none"), ("necesito ayuda", "none"), ("tengo un problema", "none"),
    ("es que no sé cómo explicarlo", "none"), ("lo de siempre", "none"), ("ayuda con eso de ayer", "none"),
    ("¿pueden revisarme algo?", "none"), ("tengo una duda", "none"), ("me pasó algo con mi cuenta", "none"),
    ("hmm", "none"), ("no sé", "none"), ("eso mismo", "none"), ("quería preguntar algo pero no recuerdo qué", "none"),
    ("el asunto del otro día", "none"), ("¿me ayudan?", "none"), ("qué raro, ¿no?", "none"), ("a ver", "none"),
    ("lo que me dijeron antes", "none"), ("pues eso", "none"),
    ("no sé cuál es", "ask"), ("no me acuerdo", "ask"), ("no tengo ese dato ahora", "ask"), ("¿cuál dijo?", "ask"),
    ("mmm… algo así", "ask"), ("depende", "ask"), ("no entiendo la pregunta", "ask"), ("¿qué necesita exactamente?", "ask"),
    ("puede ser, no estoy seguro", "ask"), ("el otro", "ask"), ("lo mismo de antes", "ask"),
    ("no sé si sí o no", "confirm"), ("¿confirmar qué?", "confirm"), ("mmm, no entendí qué voy a confirmar", "confirm"),
    ("espera, ¿qué es lo que voy a aceptar?", "confirm"), ("a ver, repítelo", "confirm"), ("no sé, déjame pensar", "confirm"),
    ("más o menos", "confirm"), ("quizás", "confirm"), ("pues no sé", "confirm"), ("depende de qué", "confirm"),
]
CLARIFY_PT: list[Row] = [
    ("oi", "none"), ("preciso de ajuda", "none"), ("tenho um problema", "none"), ("é que não sei como explicar", "none"),
    ("aquilo de ontem", "none"), ("pode ver uma coisa pra mim?", "none"), ("tenho uma dúvida", "none"), ("hmm", "none"),
    ("não sei", "ask"), ("não lembro", "ask"), ("qual é mesmo?", "ask"), ("depende", "ask"), ("não entendi a pergunta", "ask"),
    ("o outro", "ask"), ("não sei se sim ou não", "confirm"), ("confirmar o quê?", "confirm"), ("espera, o que vou aceitar?", "confirm"),
    ("talvez", "confirm"), ("mais ou menos", "confirm"),
]

# --- cancel: quiere abandonar el trámite en curso --------------------------------------------------------------
CANCEL_ES: list[Row] = [
    ("cancela todo", "ask"), ("mejor lo dejo así", "ask"), ("ya no quiero seguir con esto", "ask"), ("olvídalo", "ask"),
    ("déjalo, no importa", "ask"), ("quiero cancelar el trámite", "ask"), ("no sigas, cancela", "ask"),
    ("ya no quiero hacer el reclamo", "ask"), ("paremos aquí", "ask"), ("me arrepentí, cancela", "ask"),
    ("no continúes con esto", "ask"), ("salgamos de este proceso", "ask"), ("mejor otro día, cancela", "ask"),
    ("no quiero seguir, gracias", "ask"), ("anula el trámite", "ask"), ("ya me cansé, déjalo", "ask"),
    ("qué pereza, cancela", "ask"), ("no, mejor no hago nada, cancela", "ask"), ("detén el proceso", "ask"),
    ("olvida el reclamo, ya lo arreglé", "ask"),
    ("cancela, no quiero radicarlo", "confirm"), ("mejor cancelemos", "confirm"), ("olvídalo, no lo radiques", "confirm"),
    ("ya no quiero, cancela todo", "confirm"), ("paremos, no sigas", "confirm"), ("cancelar", "confirm"),
    ("mejor no sigo con el trámite", "confirm"), ("detén todo, ya lo resolví", "confirm"), ("anula todo", "confirm"),
    ("déjalo así, cancelo", "confirm"),
]
CANCEL_PT: list[Row] = [
    ("cancela tudo", "ask"), ("melhor deixar assim", "ask"), ("não quero mais continuar com isso", "ask"), ("esquece", "ask"),
    ("quero cancelar o procedimento", "ask"), ("não segue, cancela", "ask"), ("já não quero fazer a reclamação", "ask"),
    ("vamos parar por aqui", "ask"), ("me arrependi, cancela", "ask"), ("pare o processo", "ask"),
    ("cancela, não quero registrar", "confirm"), ("melhor cancelar", "confirm"), ("esquece, não registra", "confirm"),
    ("cancelar", "confirm"), ("deixa assim, cancelo", "confirm"),
]

# --- handoff: pide una persona -----------------------------------------------------------------------------------
HANDOFF_ES: list[Row] = [
    ("quiero hablar con una persona", "none"), ("comuníqueme con un asesor", "none"), ("necesito un agente humano", "none"),
    ("pásame con alguien de carne y hueso", "none"), ("no quiero hablar con un robot", "none"), ("asesor", "none"),
    ("¿hay alguien que me atienda?", "none"), ("quiero que me atienda un ejecutivo", "none"), ("transfiéreme con una persona",
                                                                                           "none"),
    ("prefiero hablar con un ser humano", "none"), ("me pueden comunicar con un representante?", "none"),
    ("humano por favor", "none"), ("necesito hablar con el área de reclamos, con una persona", "none"),
    ("che, pasame con alguien, no con el bot", "none"), ("llamen a un supervisor", "none"),
    ("quiero hablar con una persona", "ask"), ("mejor pásame con un asesor", "ask"), ("esto no me sirve, quiero un humano", "ask"),
    ("necesito que me atienda alguien real", "ask"), ("agente por favor", "ask"), ("ya no quiero seguir con el bot, un asesor", "ask"),
    ("comunícame con una persona ahora", "ask"), ("quiero hablar con un supervisor", "ask"), ("operador", "ask"),
    ("prefiero que me atienda una persona", "ask"), ("no entiendo esto, páseme con un asesor", "ask"),
    ("antes de confirmar quiero hablar con una persona", "confirm"), ("no confirmo hasta hablar con un asesor", "confirm"),
    ("pásame con un humano", "confirm"), ("quiero que lo revise una persona primero", "confirm"), ("asesor humano, por favor", "confirm"),
]
HANDOFF_PT: list[Row] = [
    ("quero falar com uma pessoa", "none"), ("me passa para um atendente", "none"), ("preciso de um atendente humano", "none"),
    ("não quero falar com robô", "none"), ("atendente", "none"), ("tem alguém que me atenda?", "none"),
    ("quero falar com uma pessoa", "ask"), ("melhor me passar para um atendente", "ask"), ("isso não me ajuda, quero um humano", "ask"),
    ("preciso de alguém de verdade", "ask"), ("quero falar com um supervisor", "ask"),
    ("antes de confirmar quero falar com uma pessoa", "confirm"), ("me passa para um humano", "confirm"),
    ("quero que uma pessoa revise primeiro", "confirm"),
]

# --- out_of_scope: sin relación con disputas ni reclamos ------------------------------------------------------------
OUT_OF_SCOPE_ES: list[Row] = [
    ("¿cómo va a estar el clima mañana?", "none"), ("cuéntame un chiste", "none"), ("¿quién ganó el partido de anoche?", "none"),
    ("recomiéndame una película", "none"), ("¿cuál es la capital de Francia?", "none"), ("¿qué hora es en Tokio?", "none"),
    ("escríbeme un poema", "none"), ("¿me ayudas con mi tarea de matemáticas?", "none"), ("quiero abrir una cuenta de ahorros", "none"),
    ("¿cuánto está el dólar hoy?", "none"), ("quiero pedir un préstamo", "none"), ("¿a qué hora abre la sucursal?", "none"),
    ("necesito cambiar mi dirección de correo", "none"), ("cómo se prepara una paella", "none"),
    ("¿me recomiendas un restaurante?", "none"), ("quiero contratar un seguro de vida", "none"), ("traduce 'hello' al francés", "none"),
    ("¿qué opinas del nuevo presidente?", "none"), ("dime un dato curioso", "none"), ("quiero cambiar mi PIN", "none"),
    ("¿puedes darme consejos para invertir?", "none"), ("¿cuál es el mejor celular?", "none"), ("hola, ¿cómo estás? ¿qué te gusta hacer?", "none"),
    ("¿hay ofertas de tarjetas con millas?", "none"), ("necesito el horario del cajero", "none"),
    ("cuéntame un chiste", "ask"), ("¿qué tiempo hará hoy?", "ask"), ("¿me recomiendas una serie?", "ask"),
    ("mejor dime cuánto está el dólar", "ask"), ("quiero abrir una cuenta nueva", "ask"), ("¿quién es tu creador?", "ask"),
    ("dime la capital de Italia", "ask"), ("¿me puedes ayudar con un préstamo?", "ask"), ("¿cómo se llama el gerente de la sucursal?", "ask"),
    ("olvida eso, ¿cómo cocino arroz?", "ask"), ("¿qué día es hoy?", "ask"),
]
OUT_OF_SCOPE_PT: list[Row] = [
    ("como vai estar o tempo amanhã?", "none"), ("me conta uma piada", "none"), ("quem ganhou o jogo ontem?", "none"),
    ("me indica um filme", "none"), ("qual é a capital da França?", "none"), ("quero abrir uma conta poupança", "none"),
    ("quanto está o dólar hoje?", "none"), ("quero pedir um empréstimo", "none"), ("a que horas abre a agência?", "none"),
    ("me ajuda com minha lição de matemática?", "none"), ("conta uma piada", "ask"), ("como vai estar o tempo hoje?", "ask"),
    ("quero abrir uma conta nova", "ask"), ("quem é o seu criador?", "ask"), ("qual é a capital da Itália?", "ask"),
]

# --- interrupt (fraude): reporta fraude, robo o suplantación ----------------------------------------------------------
INTERRUPT_ES: list[Row] = [
    ("me robaron la tarjeta", "none"), ("creo que me clonaron la tarjeta", "none"), ("alguien usó mis datos para hacer compras", "none"),
    ("me suplantaron la identidad", "none"), ("es un fraude, me vaciaron la cuenta", "none"), ("me hackearon la cuenta", "none"),
    ("perdí la cartera y ya hay compras que no hice", "none"), ("me robaron el celular y entraron a mi app del banco", "none"),
    ("hay compras en otro país y mi tarjeta nunca salió de aquí, creo que es fraude", "none"), ("quiero reportar un robo de tarjeta", "none"),
    ("alguien se hizo pasar por mí en el banco", "none"), ("me llamaron diciendo ser del banco y me sacaron datos, ahora hay cargos", "none"),
    ("urgente, me están vaciando la cuenta ahora mismo", "none"), ("che, me afanaron la tarjeta", "none"), ("clonaron mi tarjeta, bloquéenla ya", "none"),
    ("me robaron la tarjeta", "ask"), ("espera, creo que esto es un fraude, alguien más usó mi tarjeta", "ask"),
    ("es que me robaron y no fui yo", "ask"), ("alguien entró a mi cuenta y hizo transferencias", "ask"),
    ("me suplantaron, esto es un fraude", "ask"), ("me hackearon, hay movimientos que no hice", "ask"),
    ("la tarjeta me la robaron ayer, por eso hay cargos", "ask"), ("sospecho que clonaron mi tarjeta, hay compras en otro país", "ask"),
    ("urgente, están usando mi tarjeta ahora mismo", "ask"), ("me están estafando, alguien tiene mis claves", "ask"),
    ("espera, me acaban de robar la cartera con la tarjeta", "confirm"), ("no confirmes nada, creo que es un fraude", "confirm"),
    ("detente, me robaron la tarjeta", "confirm"), ("esto es un fraude, alguien se hizo pasar por mí", "confirm"),
    ("me clonaron la tarjeta, bloquéala", "confirm"),
]
INTERRUPT_PT: list[Row] = [
    ("roubaram meu cartão", "none"), ("acho que clonaram meu cartão", "none"), ("alguém usou meus dados para fazer compras", "none"),
    ("é fraude, esvaziaram minha conta", "none"), ("hackearam minha conta", "none"), ("alguém se passou por mim no banco", "none"),
    ("urgente, estão esvaziando minha conta agora", "none"), ("roubaram meu cartão", "ask"), ("espera, isso é fraude, outra pessoa usou meu cartão", "ask"),
    ("clonaram meu cartão, tem compras em outro país", "ask"), ("estão usando meu cartão agora mesmo", "ask"),
    ("não confirma nada, acho que é fraude", "confirm"), ("para, roubaram meu cartão", "confirm"),
]
