import base64
import html
import threading
import time

from telebot import types

import config
from config import (
    LOFYPAY_API_KEY, ACCESS_LINK, ADMIN_CHAT_ID,
    PREVIEW_CHANNEL_ID, PREVIEW_CHANNEL_LINK,
    PREVIEW_BANNER, PREVIEW_MEDIA, PREVIEW_MEDIA_TYPE,
    BANNER_SOURCE_CHANNEL_ID, POST_CHANNEL_ID,
)
from lofypay import (
    gerar_pix, consultar_status_pix, simular_pagamento_pix,
    gerar_qrcode_imagem, gerar_link_acesso, poll_pagamento,
    conceder_assinatura_se_necessario, LINK_JA_UTILIZADO,
)
from storage import (
    token_info, marcar_token_usado, registrar_transacao,
    buscar_transacao, buscar_ultima_transacao_pendente,
    atualizar_status_transacao, resumo_vendas, transacoes_nao_pagas,
    buscar_pix_pendente_recente,
    transacao_por_invite_link, marcar_link_usado,
    usuario_existe, registrar_usuario,
    salvar_midia_previa, obter_midia_previa,
)
from styles import card, status_emoji, status_label, tier_emoji, banner_card
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
/relatorio - Vendas confirmadas + PIX gerados e não pagos (nome/@/ID de quem gerou)
/postar [legenda] - Posta no canal do post_channel_id (.env) com os planos VIP e botões; pra foto/vídeo, dê reply nela com /postar (também aceita /pastar e /postarvip)
/pegarmidia - Reply numa foto/vídeo pra pegar o file_id certo (cola em preview_banner/preview_media no .env)
'''


def _is_admin(user_id) -> bool:
    return bool(ADMIN_CHAT_ID) and str(user_id) == str(ADMIN_CHAT_ID)


def _esta_no_canal(bot, canal_id, user_id) -> bool:
    """Retorna True somente quando o Telegram confirma que o usuário está no canal.

    O bot precisa ser administrador do canal para que get_chat_member() seja
    confiável. Em caso de erro, tratamos como NÃO membro para não liberar a prévia.
    """
    try:
        membro = bot.get_chat_member(canal_id, user_id)
        return membro.status in ("member", "administrator", "creator")
    except Exception as e:
        print(f"Erro ao checar membro do canal de prévias (user_id={user_id}): {e}")
        return False


def _arquivo_ou_id(valor):
    """Mantém file_id/URL como string e transforma caminho local em arquivo aberto."""
    if not valor:
        return None, None
    try:
        import os
        if os.path.isfile(valor):
            return open(valor, "rb"), True
    except Exception as e:
        print(f"Erro ao abrir mídia de prévia '{valor}': {e}")
    return valor, False


def _enviar_menu_vip(bot, chat_id):
    """Manda o menu de planos VIP tentando o banner primeiro — dinâmico do
    canal, depois o fixo do .env — e só cai pro texto puro se as duas
    tentativas de imagem falharem (ex: file_id inválido, URL que o Telegram
    não reconhece como imagem direta). Reaproveitada por _enviar_previa (que
    ainda soma o vídeo) e pelo deep link /start vip, que antes mandava sempre
    texto puro sem nem tentar o banner."""
    legenda = texto_menu_vip()
    markup = teclado_vip_planos()

    banner_dinamico = obter_midia_previa("banner")
    if banner_dinamico:
        try:
            bot.send_photo(
                chat_id, banner_dinamico["file_id"],
                caption=legenda, parse_mode="HTML", reply_markup=markup,
            )
            return
        except Exception as e:
            print(f"Erro ao enviar banner dinâmico (chat_id={chat_id}): {e}")

    if PREVIEW_BANNER:
        banner, abriu = _arquivo_ou_id(PREVIEW_BANNER)
        try:
            bot.send_photo(chat_id, banner, caption=legenda, parse_mode="HTML", reply_markup=markup)
            return
        except Exception as e:
            print(f"Erro ao enviar banner fixo (preview_banner, chat_id={chat_id}): {e}")
        finally:
            if abriu:
                banner.close()

    # Nenhuma imagem deu certo (ou nenhuma configurada) — garante que o
    # usuário ao menos recebe os preços e os botões pra comprar.
    bot.send_message(chat_id, legenda, parse_mode="HTML", reply_markup=markup)


def _enviar_previa(bot, chat_id):
    """Envia banner + preços + mídia de prévia para quem já está no canal.

    O banner segue a cascata de _enviar_menu_vip (dinâmico → fixo → texto).
    O vídeo abaixo segue a mesma ideia: dinâmico do canal → fixo do .env →
    simplesmente não manda nada (não há "texto" equivalente pra vídeo)."""
    _enviar_menu_vip(bot, chat_id)

    video_enviado = False
    video_dinamico = obter_midia_previa("video")
    if video_dinamico:
        try:
            bot.send_video(
                chat_id, video_dinamico["file_id"], supports_streaming=True,
                caption="🎬 <b>Prévia exclusiva</b> — dá uma olhada no que te espera 👀",
                parse_mode="HTML",
            )
            video_enviado = True
        except Exception as e:
            print(f"Erro ao enviar vídeo dinâmico (chat_id={chat_id}): {e}")

    if not video_enviado and PREVIEW_MEDIA:
        media, abriu = _arquivo_ou_id(PREVIEW_MEDIA)
        try:
            if PREVIEW_MEDIA_TYPE == "video":
                bot.send_video(chat_id, media, supports_streaming=True)
            else:
                bot.send_photo(chat_id, media)
        except Exception as e:
            print(f"Erro ao enviar mídia fixa (preview_media, chat_id={chat_id}): {e}")
        finally:
            if abriu:
                media.close()


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


def _quem_gerou(t: dict) -> str:
    """Melhor identificação disponível: nome > @username > chat_id."""
    nome = t.get("nome_usuario") or t.get("nome_cobranca")
    user = f"@{t['username']}" if t.get("username") else None
    partes = [html.escape(str(p)) for p in (nome, user) if p]
    return " ".join(partes) if partes else f"ID {t['chat_id']}"


def texto_relatorio() -> str:
    resumo = resumo_vendas()
    total_np, nao_pagas = transacoes_nao_pagas(limite=15)
    texto = card(
        "Relatório de vendas",
        {
            "Vendas confirmadas": resumo["quantidade"],
            "Total arrecadado": f"R$ {resumo['total']:.2f}",
            "PIX gerados e não pagos": total_np,
        },
        emoji="📊",
    )
    if nao_pagas:
        linhas = []
        for t in nao_pagas:
            plano = f" · {html.escape(t['produto_id'])}" if t.get("produto_id") else ""
            linhas.append(
                f"{status_emoji(t['status'])} {_quem_gerou(t)} — R$ {t['valor']:.2f}{plano} "
                f"(<code>{t['chat_id']}</code>)"
            )
        texto += "\n\n<b>Não pagos (mais recentes):</b>\n" + "\n".join(linhas)
        if total_np > len(nao_pagas):
            texto += f"\n… e mais {total_np - len(nao_pagas)}"
    return texto


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
    if PREVIEW_CHANNEL_LINK:
        markup.add(types.InlineKeyboardButton(
            "📢 Canal de prévias",
            url=PREVIEW_CHANNEL_LINK,
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
    linhas = [
        f"{tier_emoji(tier_id)} <b>{tier['nome']}</b> — R$ {tier['preco']:.2f} ({tier['dias']} dias)"
        for tier_id, tier in VIP_TIERS.items()
    ]
    return banner_card(
        "Planos VIP", linhas, emoji="💎",
        chamada="👉 Escolha um plano abaixo pra gerar o PIX:",
    )


def registrar_handlers(bot):
    """Registra todos os comandos na instância do bot passada."""

    @bot.channel_post_handler(
        content_types=['photo', 'video'],
        func=lambda post: bool(BANNER_SOURCE_CHANNEL_ID) and str(post.chat.id) == str(BANNER_SOURCE_CHANNEL_ID),
    )
    def capturar_midia_banner(post):
        """Toda foto/vídeo postado no canal de mídia (banner_source_channel_id)
        vira automaticamente o banner/vídeo de prévia atual, usado por
        _enviar_previa. Zeck só precisa postar uma arte nova lá pra atualizar
        o que o bot manda — sem tocar no .env nem reiniciar o bot."""
        try:
            if post.photo:
                salvar_midia_previa("banner", post.photo[-1].file_id, post.chat.id, post.message_id)
                print(f"📸 Novo banner de prévia capturado (canal {post.chat.id}, msg {post.message_id})")
            elif post.video:
                salvar_midia_previa("video", post.video.file_id, post.chat.id, post.message_id)
                print(f"🎬 Novo vídeo de prévia capturado (canal {post.chat.id}, msg {post.message_id})")
        except Exception as e:
            print(f"Erro ao capturar mídia de prévia do canal {post.chat.id}: {e}")

    @bot.message_handler(commands=['start'])
    def start(messagem):
        try:
            args = messagem.text.split(maxsplit=1)

            if len(args) == 1:
                # /start puro SEMPRE verifica o canal de prévias.
                # Não usamos usuario_existe() para pular esta etapa: um usuário
                # já cadastrado também precisa estar no canal para receber a prévia.
                if PREVIEW_CHANNEL_ID:
                    if not usuario_existe(messagem.from_user.id):
                        registrar_usuario(
                            messagem.from_user.id,
                            messagem.from_user.first_name or messagem.from_user.username,
                            messagem.from_user.username,
                        )

                    if not _esta_no_canal(bot, PREVIEW_CHANNEL_ID, messagem.from_user.id):
                        markup = types.InlineKeyboardMarkup()
                        if PREVIEW_CHANNEL_LINK:
                            markup.add(types.InlineKeyboardButton(
                                "📢 Entrar no canal de prévias",
                                url=PREVIEW_CHANNEL_LINK,
                            ))
                        bot.send_message(
                            messagem.chat.id,
                            "👋 Para continuar, entre primeiro no nosso canal de prévias."
                            "\n\nDepois de entrar, volte aqui e mande /start novamente.",
                            reply_markup=markup if PREVIEW_CHANNEL_LINK else None,
                        )
                        return

                    # Confirmado pelo Telegram: agora sim libera banner + preços + preview.
                    _enviar_previa(bot, messagem.chat.id)
                    return

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
                _enviar_menu_vip(bot, messagem.chat.id)
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
                concedeu_agora = conceder_assinatura_se_necessario(bot, id_transaction, chat_id)
                plano = transacao.get("produto_id")
                canal_id = VIP_TIERS.get(plano, {}).get("canal_id") if plano else None
                link = gerar_link_acesso(bot, id_transaction, canal_id=canal_id, permitir_criar=concedeu_agora)

                texto = card(
                    "Pagamento confirmado",
                    {"ID": f"<code>{id_transaction}</code>"},
                    emoji="✅",
                )
                if link == LINK_JA_UTILIZADO:
                    # A transação já é válida (PAID_OUT), mas o convite de acesso
                    # dela já foi consumido antes — nunca gera um segundo convite
                    # pra mesma compra, então não repassa nenhum link aqui.
                    texto += "\n\n⚠️ O acesso dessa compra já foi utilizado e não pode ser gerado novamente."
                elif link:
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

    @bot.message_handler(commands=['postar', 'pastar', 'postarvip'])
    def postar(messagem):
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        try:
            if not POST_CHANNEL_ID:
                bot.send_message(
                    messagem.chat.id,
                    "⚠️ Canal de postagem não configurado. Coloque no .env:\n"
                    "post_channel_id=-100xxxxxxxxxx"
                )
                return

            canal_id = int(POST_CHANNEL_ID)
            args = messagem.text.split(maxsplit=1)
            legenda_extra = args[1].strip() if len(args) == 2 else ""
            origem = messagem.reply_to_message

            # Legenda final = legenda opcional do admin (escapada pra não quebrar
            # o HTML) + bloco de planos VIP (mesmo texto do menu do privado).
            partes = []
            if legenda_extra:
                partes.append(html.escape(legenda_extra))
            partes.append(texto_menu_vip())
            legenda = "\n\n".join(partes)
            markup = teclado_post_vip()

            midia = None
            if origem and origem.photo:
                midia = (bot.send_photo, origem.photo[-1].file_id)
            elif origem and origem.video:
                midia = (bot.send_video, origem.video.file_id)

            if midia:
                enviar, file_id = midia
                if len(legenda) <= 1024:  # limite de caption do Telegram
                    enviar(canal_id, file_id, caption=legenda, parse_mode="HTML", reply_markup=markup)
                else:
                    # Legenda longa demais: mídia sozinha + texto completo com botões.
                    enviar(canal_id, file_id)
                    bot.send_message(canal_id, legenda, parse_mode="HTML", reply_markup=markup)
            else:
                bot.send_message(canal_id, legenda, parse_mode="HTML", reply_markup=markup)

            bot.send_message(messagem.chat.id, "✅ Postado no canal com os botões VIP.")
        except ValueError:
            bot.send_message(messagem.chat.id, "post_channel_id inválido no .env — precisa ser o ID numérico do canal (ex: -1001234567890).")
        except Exception as e:
            print(f"Erro no /postar (chat_id={messagem.chat.id}): {e}")
            bot.send_message(messagem.chat.id, "❌ Não foi possível completar o comando agora. Tente novamente em instantes.")

    @bot.message_handler(commands=['pegarmidia'])
    def pegarmidia(messagem):
        """Devolve o file_id de uma foto/vídeo — a forma confiável de preencher
        preview_banner/preview_media no .env, já que um file_id nunca dá o erro
        'wrong type of the web page content' que uma URL indireta pode dar."""
        if not _is_admin(messagem.from_user.id):
            bot.send_message(messagem.chat.id, "❌ Comando restrito.")
            return
        origem = messagem.reply_to_message
        if not origem or not (origem.photo or origem.video):
            bot.send_message(
                messagem.chat.id,
                "Manda uma foto ou vídeo aqui pro bot (nesse chat mesmo) e depois dê "
                "Reply nela com /pegarmidia."
            )
            return
        if origem.photo:
            file_id = origem.photo[-1].file_id
            bot.send_message(
                messagem.chat.id,
                f"📸 file_id da foto:\n<code>{file_id}</code>\n\n"
                "Cola em <code>preview_banner=</code> no .env e reinicia o bot.",
                parse_mode="HTML",
            )
        else:
            file_id = origem.video.file_id
            bot.send_message(
                messagem.chat.id,
                f"🎬 file_id do vídeo:\n<code>{file_id}</code>\n\n"
                "Cola em <code>preview_media=</code> e deixa <code>preview_media_type=video</code> "
                "no .env, depois reinicia o bot.",
                parse_mode="HTML",
            )

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

    def _enviar_pix_vip(chat_id, tier, id_transaction, codigo, qr_base64, sandbox, is_admin, reaproveitado=False):
        """Manda o card do PIX VIP + QR code (usado tanto no PIX novo quanto ao reenviar um pendente)."""
        texto = card(
            f"PIX gerado — {tier['nome']}",
            {
                "Valor": f"R$ {tier['preco']:.2f}",
                "Status": f"{status_emoji('pending')} {status_label('pending')}",
                "ID": f"<code>{id_transaction}</code>",
            },
            emoji="💠",
        )
        if reaproveitado:
            texto += "\n\n♻️ Você já tinha esse PIX em aberto — é o mesmo código, não precisa gerar outro."
        texto += f"\n\n📋 Código copia-e-cola:\n<code>{codigo}</code>"
        texto += (
            "\n\n⏳ Pagou e a confirmação demorou? Clique em <b>💳 Verificar pagamento</b> "
            "aqui embaixo que eu confirmo e libero seu acesso."
        )

        bot.send_message(chat_id, texto, parse_mode="HTML", reply_markup=teclado_vip_pix(id_transaction, sandbox, is_admin=is_admin))

        if qr_base64:
            bot.send_photo(chat_id, base64.b64decode(qr_base64), caption="📱 Ou escaneie o QR Code")
        else:
            bot.send_photo(chat_id, gerar_qrcode_imagem(codigo), caption="📱 Ou escaneie o QR Code")

    def iniciar_compra_vip(chat_id, tier_id):
        """Gera o PIX de um plano VIP e manda o card + QR code pro chat_id.
        Usado tanto pelo botão direto do post no canal (/start vip_<tier_id>)
        quanto pelo menu de planos no privado (callback vip_tier:<tier_id>).
        Se o usuário já tem um PIX pendente recente desse plano, reenvia o mesmo
        em vez de criar outro (anti-spam). Retorna (ok: bool, mensagem_erro: str | None)."""
        tier = VIP_TIERS.get(tier_id)

        if not tier:
            return False, "❌ Plano inválido."
        if not tier.get("canal_id"):
            return False, f"⚠️ O plano {tier['nome']} ainda não tem canal_id configurado em vip.py."

        sandbox = LOFYPAY_API_KEY.startswith("sk_test_")
        is_admin = _is_admin(chat_id)  # em chat privado, chat_id == telegram_id de quem está comprando

        existente = buscar_pix_pendente_recente(chat_id, tier_id)
        if existente:
            _enviar_pix_vip(
                chat_id, tier, existente["id_transaction"], existente["codigo_pix"],
                existente.get("qr_base64"), sandbox, is_admin, reaproveitado=True,
            )
            return True, None

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

        registrar_transacao(
            id_transaction, chat_id, tier["preco"], produto_id=tier_id, dias=tier.get("dias"),
            codigo_pix=codigo, qr_base64=qr_base64,
        )
        _enviar_pix_vip(chat_id, tier, id_transaction, codigo, qr_base64, sandbox, is_admin)

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
        if status == "PAID_OUT":
            # Já pago: em vez de só mostrar o status, entrega o acesso (mesma lógica
            # do "Liberar acesso VIP", idempotente — nunca gera 2º convite).
            return cb_vip_release(call)
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
        concedeu_agora = conceder_assinatura_se_necessario(bot, id_transaction, call.message.chat.id)

        transacao = buscar_transacao(id_transaction)
        tier_id = transacao.get("produto_id") if transacao else None
        tier = VIP_TIERS.get(tier_id) if tier_id else None
        canal_id = tier.get("canal_id") if tier else None

        link = gerar_link_acesso(bot, id_transaction, canal_id=canal_id, permitir_criar=concedeu_agora)
        if link == LINK_JA_UTILIZADO:
            bot.answer_callback_query(
                call.id,
                "⚠️ O acesso dessa compra já foi utilizado e não pode ser gerado novamente.",
                show_alert=True,
            )
            return
        if not link:
            bot.answer_callback_query(call.id, "⚠️ Nenhum canal configurado pra esse plano.", show_alert=True)
            return

        bot.send_message(call.message.chat.id, f"🔓 Aqui está seu acesso VIP:\n{link}")
        bot.answer_callback_query(call.id, "Acesso liberado ✅")

    @bot.chat_member_handler()
    def on_chat_member_update(update: types.ChatMemberUpdated):
        """Detecta quando alguém entra num canal usando um dos convites de
        acesso VIP que o bot gerou, e destrói o convite na hora: revoga ele
        explicitamente (reforço — o member_limit=1 já devia invalidar sozinho)
        e marca no banco que essa transação já consumiu seu acesso, pra
        gerar_link_acesso nunca mais devolver um link novo pra ela."""
        try:
            invite = update.invite_link
            if not invite or not invite.invite_link:
                return  # entrada sem passar por um convite (ex: já era membro, foi adicionado direto)

            entrou_status = {"member", "restricted"}
            saiu_status = {"left", "kicked"}
            entrou_agora = (
                update.new_chat_member.status in entrou_status
                and update.old_chat_member.status in saiu_status
            )
            if not entrou_agora:
                return

            id_transaction = transacao_por_invite_link(invite.invite_link)
            if not id_transaction:
                return  # convite de outra origem, não gerado por gerar_link_acesso

            if marcar_link_usado(id_transaction):
                try:
                    bot.revoke_chat_invite_link(update.chat.id, invite.invite_link)
                except Exception:
                    pass  # member_limit=1 já deve ter revogado sozinho; ignora erro aqui
        except Exception as e:
            print(f"Erro ao processar entrada via convite VIP: {e}")

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