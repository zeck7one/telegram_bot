import base64
import io
import time

import qrcode
import requests

import config
from config import LOFYPAY_API_KEY, LOFYPAY_BASE_URL, ACCESS_LINK, PRIVATE_GROUP_ID
from storage import (
    registrar_token, atualizar_status_transacao, buscar_transacao,
    conceder_pagamento_uma_vez, registrar_usuario, criar_ou_renovar_assinatura,
    obter_estado_link, salvar_link_convite_se_necessario,
)
from styles import card, status_emoji, status_label

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {LOFYPAY_API_KEY}",
}

# Sentinela devolvida por gerar_link_acesso quando a transação já teve seu
# convite de canal consumido antes — diferente de None (que significa "sem
# canal configurado pra essa transação"). Quem chama trata os dois casos
# com mensagens diferentes pro usuário.
LINK_JA_UTILIZADO = "__LINK_JA_UTILIZADO__"


def gerar_pix(amount, name=None, document=None, email=None, external_reference=None):
    """Cria uma cobrança PIX e retorna o JSON da resposta."""
    payload = {"amount": amount, "method": "pix", "client": {}}
    if name:
        payload["client"]["name"] = name
    if document:
        payload["client"]["document"] = document
    if email:
        payload["client"]["email"] = email
    if external_reference:
        payload["external_reference"] = external_reference

    try:
        resp = requests.post(f"{LOFYPAY_BASE_URL}/gateway", headers=HEADERS, json=payload, timeout=15)
        dados = resp.json()
    except (requests.RequestException, ValueError) as e:
        # Rede caiu, timeout ou resposta que não é JSON — devolve um erro padrão
        # pros chamadores mostrarem a mensagem amigável em vez de estourar exceção.
        print(f"Erro ao chamar a LofyPay (gerar_pix): {e}")
        return {"status": "error"}
    return dados if isinstance(dados, dict) else {"status": "error"}


def consultar_status_pix(id_transaction):
    """Consulta o status de uma transação PIX pelo idTransaction."""
    resp = requests.post(
        f"{LOFYPAY_BASE_URL}/status",
        headers=HEADERS,
        json={"idtransaction": id_transaction},
        timeout=15,
    )
    return resp.json()


def simular_pagamento_pix(id_transaction, status="paid"):
    """Simula o pagamento de uma transação no sandbox (só funciona com sk_test_)."""
    return requests.post(
        f"{LOFYPAY_BASE_URL}/sandbox/pay",
        headers=HEADERS,
        json={"api-key": LOFYPAY_API_KEY, "idTransaction": id_transaction, "status": status},
        timeout=15,
    )


def gerar_qrcode_imagem(codigo_copia_e_cola):
    """Gera a imagem do QR Code a partir do código copia e cola do PIX."""
    img = qrcode.make(codigo_copia_e_cola)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def gerar_link_acesso(bot, id_transaction, canal_id=None, permitir_criar=True):
    """Retorna o link a ser enviado após confirmação do pagamento.
    - canal_id (parâmetro): canal específico daquele produto/plano (ex: um canal VIP
      diferente por tier). Tem prioridade sobre o PRIVATE_GROUP_ID fixo do .env.
    - Sem canal_id, cai no PRIVATE_GROUP_ID: convite de uso único nativo do Telegram
      (member_limit=1 invalida o link sozinho depois de 1 entrada; o bot também
      revoga explicitamente assim que detecta a entrada, ver on_chat_member_update
      em handlers.py — reforço caso o member_limit falhe por algum motivo).
    - Cada id_transaction só gera UM convite, guardado em storage: chamar de novo
      (usuário batendo /verificar_pagamento ou "Liberar acesso VIP" repetidas vezes
      pra uma transação já aprovada) devolve sempre o mesmo link, nunca um novo.
      Se esse convite já foi usado, devolve LINK_JA_UTILIZADO em vez de gerar outro.
    - permitir_criar=False: usado quando o chamador sabe que essa transação já tinha
      sido processada antes (conceder_pagamento_uma_vez não é a 1ª chamada) — cobre
      inclusive transações antigas, confirmadas antes desse controle de convite único
      existir, que por isso não têm nenhum invite_link registrado. Sem essa trava,
      um id_transaction de uma compra antiga já entregue voltaria a gerar convite
      novo simplesmente por não ter histórico salvo. Com permitir_criar=False e
      nenhum convite já registrado, devolve LINK_JA_UTILIZADO sem criar nada.
    - ACCESS_LINK + BOT_USERNAME configurados (sem canal): deep link do bot que só
      libera o link real na 1ª vez que for clicado.
    - Só ACCESS_LINK: devolve o link cru, sem controle de uso único.
    """
    canal_alvo = canal_id or PRIVATE_GROUP_ID

    if canal_alvo:
        estado = obter_estado_link(id_transaction)
        if estado:
            # Já existe convite pra essa transação — nunca gera um segundo.
            return LINK_JA_UTILIZADO if estado["usado"] else estado["invite_link"]

        if not permitir_criar:
            # Transação já tinha sido processada antes (ou é histórico anterior
            # a esse controle) e nunca teve um convite rastreado — trata como
            # já consumida em vez de abrir uma brecha pra gerar um convite novo.
            return LINK_JA_UTILIZADO

        try:
            invite = bot.create_chat_invite_link(
                chat_id=canal_alvo,
                member_limit=1,
                expire_date=int(time.time()) + 3600
            )
        except Exception as e:
            print(f"Erro ao gerar convite: {e}")
            return ACCESS_LINK

        if salvar_link_convite_se_necessario(id_transaction, canal_alvo, invite.invite_link):
            return invite.invite_link

        # Perdemos a corrida: outra chamada concorrente (polling + clique manual
        # ao mesmo tempo) já salvou um convite antes da nossa. Revoga o convite
        # que acabamos de criar (não seria usado por ninguém, mas fica sobrando
        # válido se não revogarmos) e devolve o que já está registrado.
        try:
            bot.revoke_chat_invite_link(canal_alvo, invite.invite_link)
        except Exception:
            pass
        estado = obter_estado_link(id_transaction)
        if not estado:
            return ACCESS_LINK
        return LINK_JA_UTILIZADO if estado["usado"] else estado["invite_link"]

    if ACCESS_LINK and config.BOT_USERNAME:
        registrar_token(id_transaction)
        return f"https://t.me/{config.BOT_USERNAME}?start=acesso_{id_transaction}"

    return ACCESS_LINK


def conceder_assinatura_se_necessario(bot, id_transaction, chat_id):
    """Concede os dias de acesso do plano comprado — só na 1ª confirmação dessa
    transação. É seguro chamar isso mais de uma vez pro mesmo id_transaction
    (polling, clique manual em "Verificar pagamento" e reentrega de webhook podem
    confirmar o mesmo pagamento em paralelo): só quem ganha a corrida no banco
    concede a assinatura, as demais chamadas não fazem nada.
    Transações sem plano/dias (ex: /pix avulso) não geram assinatura.
    Retorna True só quando ESSA chamada foi a 1ª confirmação real da transação —
    os chamadores usam isso (permitir_criar em gerar_link_acesso) pra saber se
    ainda podem emitir um convite novo, ou se a transação (mesmo uma antiga, de
    antes desse controle existir) já tinha sido processada antes."""
    if not conceder_pagamento_uma_vez(id_transaction):
        return False

    transacao = buscar_transacao(id_transaction)
    plano = transacao.get("produto_id") if transacao else None
    dias = transacao.get("dias") if transacao else None
    if not plano or not dias:
        return True  # cobrança avulsa (não é um plano VIP com validade), mas é a 1ª confirmação

    try:
        chat = bot.get_chat(chat_id)
        nome = chat.first_name or chat.username
        username = chat.username
    except Exception as e:
        print(f"Erro ao buscar dados do chat {chat_id}: {e}")
        nome = username = None

    registrar_usuario(chat_id, nome, username)
    criar_ou_renovar_assinatura(chat_id, plano, dias)
    return True


def poll_pagamento(bot, chat_id, id_transaction, timeout_seg=600, intervalo=10, canal_id=None):
    """Roda em background verificando o status até PAID_OUT, expirar ou timeout.
    canal_id: passa adiante pra gerar_link_acesso quando o produto/plano tem
    um canal de destino específico (ex: um tier VIP diferente do PRIVATE_GROUP_ID)."""
    decorrido = 0
    while decorrido < timeout_seg:
        time.sleep(intervalo)
        decorrido += intervalo
        try:
            resultado = consultar_status_pix(id_transaction)
        except Exception:
            continue

        status = resultado.get("status")

        if status == "PAID_OUT":
            concedeu_agora = conceder_assinatura_se_necessario(bot, id_transaction, chat_id)
            atualizar_status_transacao(id_transaction, status)
            link = gerar_link_acesso(bot, id_transaction, canal_id=canal_id, permitir_criar=concedeu_agora)

            texto = card(
                "Pagamento confirmado",
                {
                    "ID": f"<code>{id_transaction}</code>",
                    "Status": f"{status_emoji(status)} {status_label(status)}",
                },
                emoji="✅",
            )
            if link == LINK_JA_UTILIZADO:
                texto += "\n\n⚠️ O acesso dessa compra já foi utilizado."
            elif link:
                texto += f"\n\n🔓 Aqui está seu acesso:\n{link}"
            else:
                texto += "\n\n⚠️ Pagamento ok, mas nenhum link de acesso está configurado."

            bot.send_message(chat_id, texto, parse_mode="HTML")
            return

        if status in ("EXPIRED", "FAILED", "REFUNDED"):
            atualizar_status_transacao(id_transaction, status)
            texto = card(
                "PIX não concluído",
                {
                    "ID": f"<code>{id_transaction}</code>",
                    "Status": f"{status_emoji(status)} {status_label(status)}",
                },
                emoji="❌",
            )
            bot.send_message(chat_id, texto, parse_mode="HTML")
            return

    # Timeout: parou de monitorar, mas a transação pode ainda ser paga depois
    # (o gateway continua válido). Marcamos como TIMEOUT pra diferenciar de
    # um "pending" comum no /relatorio e no storage.
    try:
        atualizar_status_transacao(id_transaction, "TIMEOUT")
    except Exception:
        pass  # se o storage não aceitar esse valor, não trava o bot por isso

    texto = card(
        "Monitoramento encerrado",
        {
            "ID": f"<code>{id_transaction}</code>",
            "Status": f"{status_emoji('timeout')} {status_label('timeout')}",
        },
        emoji="⏱",
    )
    texto += (
        "\n\n💳 Já pagou? Clique em <b>Verificar pagamento</b> na mensagem do PIX "
        f"(ou use /verificar_pagamento {id_transaction}) que eu libero seu acesso — "
        "o PIX ainda pode ser pago depois disso."
    )
    bot.send_message(chat_id, texto, parse_mode="HTML")