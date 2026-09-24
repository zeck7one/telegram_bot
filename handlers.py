import base64
import threading
import time

from telebot import types

import config
from config import LOFYPAY_API_KEY, ACCESS_LINK, ADMIN_CHAT_ID
from lofypay import (
    gerar_pix, consultar_status_pix, simular_pagamento_pix,
    gerar_qrcode_imagem, gerar_link_acesso, poll_pagamento,
    conceder_assinatura_se_necessario,
)
from storage import (
    token_info, marcar_token_usado, registrar_transacao,
    buscar_transacao, buscar_ultima_transacao_pendente,
    atualizar_status_transacao, resumo_vendas,
)
from styles import card, status_emoji, status_label
from vip import VIP_TIERS

TEXTO_HELP_COMUM = '''
Comandos disponíveis:
/start - Inicia o bot
/help - Mostra esta mensagem de ajuda
/verificar_pagamento [idTransaction] - Verifica o status do seu pagamento (usa sua cobrança mais recente se você não informar o ID)
'''

TEXTO_HELP_ADMIN_EXTRA = '''
Comandos administrativos:
/pix <valor> [nome] - Gera uma cobrança PIX avulsa
/statuspix <idTransaction> - Consulta o status de qualquer cobrança
/simularpix <idTransaction> [status] - Simula pagamento no sandbox (só sk_test_)
/relatorio - Mostra total de vendas confirmadas
/postarvip <canal_id> <texto> - Posta no canal com botões VIP (aceita foto/vídeo via reply)
'''


def _is_admin(user_id) -> bool:
    return bool(ADMIN_CHAT_ID) and str(user_id) == str(ADMIN_CHAT_ID)


# === Teclados: PIX comum (/pix) ===

def teclado_pix(id_transaction: str, sandbox: bool, is_admin: bool = False) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔄 Verificar status", callback_data=f"check:{id_transaction}"),
    )
    if sandbox and is_admin:
        markup.add(
            types.InlineKeyboardButton("✅ Simular pago", callback_data=f"sim:{id_transaction}:paid"),
            types.InlineKeyboardButton("❌ Simular falha", callback_data=f"sim:{id_transaction}:failed"),
        )
    return markup


def teclado_status(id_transaction: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Atualizar", callback_data=f"check:{id_transaction}"))
    return markup


def teclado_relatorio() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Atualizar relatório", callback_data="refresh_relatorio"))
    return markup


def texto_relatorio() -> str:
    resumo = resumo_vendas()
    return card(
        "Relatório de vendas",
        {
            "Vendas confirmadas": resumo["quantidade"],
            "Total arrecadado": f"R$ {resumo['total']:.2f}",
        },
        emoji="📊",
    )


# === Teclados: funil VIP ===

def teclado_post_vip() -> types.InlineKeyboardMarkup:
    """Botões pra colocar embaixo de posts de produto no canal — um por plano,
    já com o valor. São botões de URL (deep link) e não callback: um bot não
    consegue mandar PIX/QR code dentro de um post de canal (mensagem única,
    vista por todo mundo), então o clique abre o privado do bot e já dispara
    a compra daquele plano específico (start=vip_<tier_id>)."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for tier_id, tier in VIP_TIERS.items():
        markup.add(types.InlineKeyboardButton(
            f"💎 {tier['nome']} — R$ {tier['preco']:.2f}",
            url=f"https://t.me/{config.BOT_USERNAME}?start=vip_{tier_id}",
        ))
    return markup


def teclado_vip_planos() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    for tier_id, tier in VIP_TIERS.items():
        markup.add(types.InlineKeyboardButton(
            f"{tier['nome']} — R$ {tier['preco']:.2f}",
            callback_data=f"vip_tier:{tier_id}",
        ))
    return markup


def teclado_vip_pix(id_transaction: str, sandbox: bool, is_admin: bool = False) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("💳 Verificar pagamento", callback_data=f"vip_check:{id_transaction}"),
        types.InlineKeyboardButton("🔓 Liberar acesso VIP", callback_data=f"vip_release:{id_transaction}"),
    )
    if sandbox and is_admin:
        markup.add(
            types.InlineKeyboardButton("✅ Simular pago", callback_data=f"sim:{id_transaction}:paid"),
            types.InlineKeyboardButton("❌ Simular falha", callback_data=f"sim:{id_transaction}:failed"),
        )
    return markup


def texto_menu_vip() -> str:
    linhas = {tier["nome"]: f"R$ {tier['preco']:.2f}" for tier in VIP_TIERS.values()}
    return card("Planos VIP", linhas, emoji="💎") + "\n\nEscolha um plano abaixo pra gerar o PIX:"


def registrar_handlers(bot):
    """Registra todos os comandos na instância do bot passada."""

    @bot.message_handler(commands=['start'])
    def start(messagem):
        try:
            args = messagem.text.split(maxsplit=1)

            if len(args) == 2 and args[1].startswith("acesso_"):
                id_transaction = args[1][len("acesso_"):]
                registro = token_info(id_transaction)

                if not registro:
                    bot.send_message(messagem.chat.id, "❌ Link inválido.")
                    return
                if registro["usado"]:
                    bot.send_message(messagem.chat.id, "⚠️ Esse link já foi utilizado e não é mais válido.")
                    return

                if not marcar_token_usado(id_transaction):
                    bot.send_message(messagem.chat.id, "⚠️ Esse link já foi utilizado e não é mais válido.")
                    return

                bot.send_message(messagem.chat.id, f"🔓 Aqui está seu acesso:\n{ACCESS_LINK}")
                return

            if len(args) == 2 and args[1] == "vip":
                bot.send_message(
                    messagem.chat.id, texto_menu_vip(),
                    parse_mode="HTML", reply_markup=teclado_vip_planos(),
                )
                return

            if len(args) == 2 and args[1].startswith("vip_"):
                tier_id = args[1][len("vip_"):]
                ok, erro = iniciar_compra_vip(messagem.chat.id, tier_id)
                if not ok:
                    bot.send_message(messagem.chat.id, erro)
                return

            bot.send_message(messagem.chat.id, "Olá! Eu sou o Zeck, seu bot. Use /help para ver os comandos disponíveis.")
        except Exception as e:
            print(f"Erro no /start (chat_id={messagem.chat.id}): {e}")
            bot.send_message(messagem.chat.id, "❌ Ocorreu um erro ao iniciar. Tente novamente em instantes.")

    @bot.message_handler(commands=['help'])
    def help_cmd(messagem):
        texto = TEXTO_HELP_COMUM
        if _is_admin(messagem.from_user.id):
            texto += "\n" + TEXTO_HELP_ADMIN_EXTRA
        bot.send_message(messagem.chat.id, texto)

    @bot.message_handler(commands=['verificar_pagamento'])
    def verificar_pagamento(messagem):
        chat_id = messagem.chat.id
        try:
            args = messagem.text.split(maxsplit=1)

            if len(args) == 2:
                id_transaction = args[1].strip()
                transacao = buscar_transacao(id_transaction)
                # Nunca deixa um usuário consultar a cobrança de outra pessoa:
                # a transação tem que pertencer ao chat_id de quem está pedindo.
                if not transacao or transacao["chat_id"] != chat_id:
                    bot.send_message(chat_id, "❌ Não encontramos essa cobrança na sua conta.")
                    return
            else:
                transacao = buscar_ultima_transacao_pendente(chat_id)
                if not transacao:
                    bot.send_message(
                        chat_id,
                        "Você não tem nenhuma cobrança pendente no momento.\n"
                        "Gere um PIX ou escolha um plano VIP pra começar."
                    )
                    return
                id_transaction = transacao["id_transaction"]

            try:
                resultado = consultar_status_pix(id_transaction)
                status = resultado.get("status", resultado.get("error", "desconhecido"))
            except Exception as e:
                print(f"Erro ao consultar status ({id_transaction}): {e}")
                bot.send_message(chat_id, "❌ Não foi possível verificar o pagamento neste momento. Tente novamente em alguns instantes.")
                return

            atualizar_status_transacao(id_transaction, status)

            if status == "PAID_OUT":
                conceder_assinatura_se_necessario(bot, id_transaction, chat_id)
                plano = transacao.get("produto_id")
                canal_id = VIP_TIERS.get(plano, {}).get("canal_id") if plano else None
                link = gerar_link_acesso(bot, id_transaction, canal_id=canal_id)

                texto = card(
                    "Pagamento confirmado",
                    {"ID": f"<code>{id_transaction}</code>"},
                    emoji="✅",
                )
                if link:
                    texto += f"\n\n🔓 Aqui está seu acesso:\n{link}"
                bot.send_message(chat_id, texto, parse_mode="HTML")

            elif status in ("EXPIRED", "FAILED", "REFUNDED", "CANCELLED"):
                bot.send_message(
                    chat_id,
                    f"{status_emoji(status)} Essa cobrança não foi concluída ({status_label(status)}).\n"
                    "Gere um novo PIX pra tentar de novo."
                )
            else:
                bot.send_message(
                    chat_id,
                    "⏳ Ainda não identificamos o pagamento.\n\n"
                    "Se você acabou de realizar o PIX, aguarde alguns instantes e use "
                    "/verificar_pagamento novamente."
                )
        except Exception as e:
            print(f"Erro em /verificar_pagamento (chat_id={chat_id}): {e}")
            bot.send_message(chat_id, "❌ Não foi possível verificar o pagamento neste momento. Tente novamente em alguns instantes.")

    @bot.message_handler(commands=['pix'])
    def pix(messagem):
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        try:
            args = messagem.text.split(maxsplit=2)
            if len(args) < 2:
                bot.send_message(messagem.chat.id, "Formato: /pix <valor> [nome do pagador]\nExemplo: /pix 25.90 Pedro Santos")
                return

            valor = float(args[1].replace(',', '.'))
            nome = args[2] if len(args) == 3 else None

            resultado = gerar_pix(
                amount=valor,
                name=nome,
                external_reference=f"TG-{messagem.chat.id}-{int(time.time())}"
            )

            if resultado.get("status") not in ("success", "OK"):
                print(f"Erro ao gerar PIX (chat_id={messagem.chat.id}): {resultado}")
                bot.send_message(messagem.chat.id, "❌ Não foi possível gerar o PIX agora. Tente novamente em instantes.")
                return

            id_transaction = resultado["idTransaction"]
            codigo = resultado["paymentCode"]
            qr_base64 = resultado.get("paymentCodeBase64")
            sandbox = LOFYPAY_API_KEY.startswith("sk_test_")

            registrar_transacao(id_transaction, messagem.chat.id, valor, nome)

            texto = card(
                "PIX gerado",
                {
                    "Valor": f"R$ {valor:.2f}",
                    "Nome": nome or "-",
                    "Status": f"{status_emoji('pending')} {status_label('pending')}",
                    "ID": f"<code>{id_transaction}</code>",
                },
                emoji="💠",
            )
            texto += f"\n\n📋 Código copia-e-cola:\n<code>{codigo}</code>"

            bot.send_message(
                messagem.chat.id,
                texto,
                parse_mode="HTML",
                reply_markup=teclado_pix(id_transaction, sandbox, is_admin=True),
            )

            if qr_base64:
                bot.send_photo(messagem.chat.id, base64.b64decode(qr_base64), caption="📱 Ou escaneie o QR Code")
            else:
                bot.send_photo(messagem.chat.id, gerar_qrcode_imagem(codigo), caption="📱 Ou escaneie o QR Code")

            threading.Thread(
                target=poll_pagamento,
                args=(bot, messagem.chat.id, id_transaction),
                daemon=True
            ).start()

        except ValueError:
            bot.send_message(messagem.chat.id, "Valor inválido. Use, por exemplo: /pix 25.90")
        except Exception as e:
            print(f"Erro no /pix (chat_id={messagem.chat.id}): {e}")
            bot.send_message(messagem.chat.id, "❌ Não foi possível completar o comando agora. Tente novamente em instantes.")

    @bot.message_handler(commands=['simularpix'])
    def simularpix(messagem):
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        try:
            args = messagem.text.split()
            if len(args) not in (2, 3):
                bot.send_message(messagem.chat.id, "Formato: /simularpix <idTransaction> [status]\nstatus pode ser: paid, failed, expired, pending (padrão: paid)")
                return

            id_transaction = args[1]
            status = args[2] if len(args) == 3 else "paid"
            _executar_simulacao(bot, messagem.chat.id, id_transaction, status)

        except Exception as e:
            print(f"Erro no /simularpix (chat_id={messagem.chat.id}): {e}")
            bot.send_message(messagem.chat.id, "❌ Não foi possível completar o comando agora. Tente novamente em instantes.")

    @bot.message_handler(commands=['statuspix'])
    def statuspix(messagem):
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        try:
            args = messagem.text.split()
            if len(args) != 2:
                bot.send_message(messagem.chat.id, "Formato: /statuspix <idTransaction>")
                return

            id_transaction = args[1]
            resultado = consultar_status_pix(id_transaction)
            status = resultado.get("status", resultado.get("error", "desconhecido"))

            texto = card(
                "Status da transação",
                {
                    "ID": f"<code>{id_transaction}</code>",
                    "Status": f"{status_emoji(status)} {status_label(status)}",
                },
                emoji="🧾",
            )
            bot.send_message(
                messagem.chat.id, texto, parse_mode="HTML",
                reply_markup=teclado_status(id_transaction),
            )
        except Exception as e:
            print(f"Erro no /statuspix (chat_id={messagem.chat.id}): {e}")
            bot.send_message(messagem.chat.id, "❌ Não foi possível completar o comando agora. Tente novamente em instantes.")

    @bot.message_handler(commands=['relatorio'])
    def relatorio(messagem):
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        bot.send_message(
            messagem.chat.id,
            texto_relatorio(),
            parse_mode="HTML",
            reply_markup=teclado_relatorio(),
        )

    @bot.message_handler(commands=['postarvip'])
    def postarvip(messagem):
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        try:
            args = messagem.text.split(maxsplit=2)
            if len(args) < 2:
                bot.send_message(
                    messagem.chat.id,
                    "Formato:\n"
                    "• Só texto: /postarvip <chat_id_do_canal> <texto>\n"
                    "• Com foto/vídeo: mande a foto ou vídeo pro bot, depois dê Reply "
                    "nela com /postarvip <chat_id_do_canal> [legenda opcional]"
                )
                return

            canal_id = int(args[1])
            legenda = args[2] if len(args) == 3 else None
            origem = messagem.reply_to_message

            if origem and origem.photo:
                file_id = origem.photo[-1].file_id
                bot.send_photo(canal_id, file_id, caption=legenda, reply_markup=teclado_post_vip())
            elif origem and origem.video:
                file_id = origem.video.file_id
                bot.send_video(canal_id, file_id, caption=legenda, reply_markup=teclado_post_vip())
            else:
                if not legenda:
                    bot.send_message(messagem.chat.id, "Sem foto/vídeo (reply) e sem texto — nada pra postar.")
                    return
                bot.send_message(canal_id, legenda, reply_markup=teclado_post_vip())

            bot.send_message(messagem.chat.id, "✅ Postado no canal com os botões VIP.")
        except ValueError:
            bot.send_message(messagem.chat.id, "chat_id inválido — precisa ser o ID numérico do canal (ex: -1001234567890).")
        except Exception as e:
            print(f"Erro no /postarvip (chat_id={messagem.chat.id}): {e}")
            bot.send_message(messagem.chat.id, "❌ Não foi possível completar o comando agora. Tente novamente em instantes.")

    # === Botões (callbacks) — PIX comum ===

    @bot.callback_query_handler(func=lambda call: call.data.startswith("check:"))
    def cb_check_status(call):
        id_transaction = call.data.split(":", 1)[1]
        resultado = consultar_status_pix(id_transaction)
        status = resultado.get("status", resultado.get("error", "desconhecido"))

        texto = card(
            "Status da transação",
            {
                "ID": f"<code>{id_transaction}</code>",
                "Status": f"{status_emoji(status)} {status_label(status)}",
            },
            emoji="🧾",
        )
        try:
            bot.edit_message_text(
                texto, call.message.chat.id, call.message.message_id,
                parse_mode="HTML", reply_markup=teclado_status(id_transaction),
            )
        except Exception:
            pass  # ignora "message not modified" quando status não mudou
        bot.answer_callback_query(call.id, "Status atualizado ✅")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("sim:"))
    def cb_simular(call):
        if not _is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "❌ Comando restrito.", show_alert=True)
            return

        _, id_transaction, status = call.data.split(":", 2)

        if not LOFYPAY_API_KEY.startswith("sk_test_"):
            bot.answer_callback_query(call.id, "⚠️ Só funciona em sandbox (sk_test_)", show_alert=True)
            return

        resp = simular_pagamento_pix(id_transaction, status)
        if resp.status_code == 200:
            bot.answer_callback_query(call.id, f"✅ Simulação enviada: {status}")
        else:
            print(f"Erro ao simular pagamento ({id_transaction}): {resp.status_code} {resp.text}")
            bot.answer_callback_query(call.id, "❌ Erro ao simular", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data == "refresh_relatorio")
    def cb_refresh_relatorio(call):
        try:
            bot.edit_message_text(
                texto_relatorio(), call.message.chat.id, call.message.message_id,
                parse_mode="HTML", reply_markup=teclado_relatorio(),
            )
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Relatório atualizado ✅")

    def iniciar_compra_vip(chat_id, tier_id):
        """Gera o PIX de um plano VIP e manda o card + QR code pro chat_id.
        Usado tanto pelo botão direto do post no canal (/start vip_<tier_id>)
        quanto pelo menu de planos no privado (callback vip_tier:<tier_id>).
        Retorna (ok: bool, mensagem_erro: str | None)."""
        tier = VIP_TIERS.get(tier_id)

        if not tier:
            return False, "❌ Plano inválido."
        if not tier.get("canal_id"):
            return False, f"⚠️ O plano {tier['nome']} ainda não tem canal_id configurado em vip.py."

        resultado = gerar_pix(
            amount=tier["preco"],
            external_reference=f"VIP-{tier_id}-{chat_id}-{int(time.time())}",
        )

        if resultado.get("status") not in ("success", "OK"):
            print(f"Erro ao gerar PIX VIP (chat_id={chat_id}, tier={tier_id}): {resultado}")
            return False, "❌ Não foi possível gerar o PIX agora. Tente novamente em instantes."

        id_transaction = resultado["idTransaction"]
        codigo = resultado["paymentCode"]
        qr_base64 = resultado.get("paymentCodeBase64")
        sandbox = LOFYPAY_API_KEY.startswith("sk_test_")

        registrar_transacao(id_transaction, chat_id, tier["preco"], produto_id=tier_id, dias=tier.get("dias"))
        is_admin = _is_admin(chat_id)  # em chat privado, chat_id == telegram_id de quem está comprando

        texto = card(
            f"PIX gerado — {tier['nome']}",
            {
                "Valor": f"R$ {tier['preco']:.2f}",
                "Status": f"{status_emoji('pending')} {status_label('pending')}",
                "ID": f"<code>{id_transaction}</code>",
            },
            emoji="💠",
        )
        texto += f"\n\n📋 Código copia-e-cola:\n<code>{codigo}</code>"

        bot.send_message(chat_id, texto, parse_mode="HTML", reply_markup=teclado_vip_pix(id_transaction, sandbox, is_admin=is_admin))

        if qr_base64:
            bot.send_photo(chat_id, base64.b64decode(qr_base64), caption="📱 Ou escaneie o QR Code")
        else:
            bot.send_photo(chat_id, gerar_qrcode_imagem(codigo), caption="📱 Ou escaneie o QR Code")

        threading.Thread(
            target=poll_pagamento,
            args=(bot, chat_id, id_transaction),
            kwargs={"canal_id": tier["canal_id"]},
            daemon=True,
        ).start()
        return True, None

    # === Botões (callbacks) — funil VIP ===

    @bot.callback_query_handler(func=lambda call: call.data.startswith("vip_tier:"))
    def cb_vip_tier(call):
        tier_id = call.data.split(":", 1)[1]
        ok, erro = iniciar_compra_vip(call.message.chat.id, tier_id)
        bot.answer_callback_query(call.id, erro, show_alert=True) if not ok else bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("vip_check:"))
    def cb_vip_check(call):
        id_transaction = call.data.split(":", 1)[1]
        resultado = consultar_status_pix(id_transaction)
        status = resultado.get("status", resultado.get("error", "desconhecido"))
        if status:
            atualizar_status_transacao(id_transaction, status)
        bot.answer_callback_query(call.id, f"{status_emoji(status)} {status_label(status)}", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("vip_release:"))
    def cb_vip_release(call):
        id_transaction = call.data.split(":", 1)[1]

        # Consulta ao vivo, nunca confia só no status salvo — é o que garante
        # que o link do canal VIP só sai se o pagamento estiver confirmado de fato.
        resultado = consultar_status_pix(id_transaction)
        status = resultado.get("status", resultado.get("error", "desconhecido"))
        atualizar_status_transacao(id_transaction, status)

        if status != "PAID_OUT":
            bot.answer_callback_query(
                call.id,
                f"{status_emoji(status)} Pagamento ainda não confirmado ({status_label(status)}). Tente de novo em instantes.",
                show_alert=True,
            )
            return

        # Concede os dias do plano (idempotente: se o polling em background já
        # confirmou esse mesmo pagamento antes, essa chamada não soma dias de novo).
        conceder_assinatura_se_necessario(bot, id_transaction, call.message.chat.id)

        transacao = buscar_transacao(id_transaction)
        tier_id = transacao.get("produto_id") if transacao else None
        tier = VIP_TIERS.get(tier_id) if tier_id else None
        canal_id = tier.get("canal_id") if tier else None

        link = gerar_link_acesso(bot, id_transaction, canal_id=canal_id)
        if not link:
            bot.answer_callback_query(call.id, "⚠️ Nenhum canal configurado pra esse plano.", show_alert=True)
            return

        bot.send_message(call.message.chat.id, f"🔓 Aqui está seu acesso VIP:\n{link}")
        bot.answer_callback_query(call.id, "Acesso liberado ✅")

    @bot.message_handler(func=lambda m: True)
    def fallback(messagem):
        texto = TEXTO_HELP_COMUM
        if _is_admin(messagem.from_user.id):
            texto += "\n" + TEXTO_HELP_ADMIN_EXTRA
        bot.reply_to(messagem, texto)


def _executar_simulacao(bot, chat_id, id_transaction, status):
    if not LOFYPAY_API_KEY.startswith("sk_test_"):
        bot.send_message(chat_id, "⚠️ Esse comando só funciona com uma chave sk_test_ (sandbox). Com sk_live_ a API recusa (403).")
        return

    resp = simular_pagamento_pix(id_transaction, status)

    if resp.status_code == 403:
        bot.send_message(chat_id, "❌ 403: essa rota só aceita chaves sk_test_.")
        return

    if resp.status_code == 200:
        bot.send_message(
            chat_id,
            f"{status_emoji(status)} Simulação enviada!\nID: <code>{id_transaction}</code>\nStatus: {status_label(status)}",
            parse_mode="HTML",
        )
    else:
        print(f"Erro ao simular ({id_transaction}): {resp.status_code} {resp.text}")
        bot.send_message(chat_id, "❌ Não foi possível simular o pagamento agora.")