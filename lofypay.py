import base64
import io
import time

import qrcode
import requests

from config import LOFYPAY_API_KEY, LOFYPAY_BASE_URL, ACCESS_LINK, PRIVATE_GROUP_ID, BOT_USERNAME
from storage import registrar_token, atualizar_status_transacao
from styles import card, status_emoji, status_label

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {LOFYPAY_API_KEY}",
}


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

    resp = requests.post(f"{LOFYPAY_BASE_URL}/gateway", headers=HEADERS, json=payload, timeout=15)
    return resp.json()


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


def gerar_link_acesso(bot, id_transaction):
    """Retorna o link a ser enviado após confirmação do pagamento.
    - PRIVATE_GROUP_ID configurado: convite de uso único nativo do Telegram
      (member_limit=1 invalida o link sozinho depois de 1 entrada).
    - ACCESS_LINK + BOT_USERNAME configurados: deep link do bot que só libera
      o link real na 1ª vez que for clicado.
    - Só ACCESS_LINK: devolve o link cru, sem controle de uso único.
    """
    if PRIVATE_GROUP_ID:
        try:
            invite = bot.create_chat_invite_link(
                chat_id=PRIVATE_GROUP_ID,
                member_limit=1,
                expire_date=int(time.time()) + 3600
            )
            return invite.invite_link
        except Exception as e:
            print(f"Erro ao gerar convite: {e}")
            return ACCESS_LINK

    if ACCESS_LINK and BOT_USERNAME:
        registrar_token(id_transaction)
        return f"https://t.me/{BOT_USERNAME}?start=acesso_{id_transaction}"

    return ACCESS_LINK


def poll_pagamento(bot, chat_id, id_transaction, timeout_seg=600, intervalo=10):
    """Roda em background verificando o status até PAID_OUT, expirar ou timeout."""
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
            atualizar_status_transacao(id_transaction, status)
            link = gerar_link_acesso(bot, id_transaction)

            texto = card(
                "Pagamento confirmado",
                {
                    "ID": f"<code>{id_transaction}</code>",
                    "Status": f"{status_emoji(status)} {status_label(status)}",
                },
                emoji="✅",
            )
            if link:
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
    texto += f"\n\nUse /statuspix {id_transaction} pra checar manualmente — o PIX ainda pode ser pago depois disso."
    bot.send_message(chat_id, texto, parse_mode="HTML")