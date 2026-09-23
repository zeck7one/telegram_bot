import base64
import threading
import time

from telebot import types

from config import LOFYPAY_API_KEY, ACCESS_LINK
from currency import obter_cotacao
from lofypay import (
    gerar_pix, consultar_status_pix, simular_pagamento_pix,
    gerar_qrcode_imagem, poll_pagamento,
)
from storage import token_info, marcar_token_usado, registrar_transacao, resumo_vendas
from styles import card, status_emoji, status_label

TEXTO_HELP = '''
Comandos disponíveis:
/start - Inicia o bot
/help - Mostra esta mensagem de ajuda
/cambio - Lista as moedas disponíveis
/cotacao <moeda_base> <moeda_destino> - Obtém a cotação entre duas moedas
/pix <valor> [nome] - Gera uma cobrança PIX
/statuspix <idTransaction> - Consulta o status de um pagamento
/simularpix <idTransaction> [status] - Simula pagamento no sandbox (só sk_test_)
/relatorio - Mostra total de vendas confirmadas
Exemplo: /cotacao USD BRL
Exemplo: /pix 25.90 Pedro Santos
'''

TEXTO_CAMBIO = '''
Principais moedas:
    /USD - United States Dollar
    /EUR - Euro
    /JPY - Japanese Yen
    /GBP - British Pound Sterling
    /AUD - Australian Dollar
    /CAD - Canadian Dollar
    /CHF - Swiss Franc
    /CNY - Chinese Yuan
    /HKD - Hong Kong Dollar
    /NZD - New Zealand Dollar
    /SEK - Swedish Krona
    /KRW - South Korean Won
    /SGD - Singapore Dollar
    /NOK - Norwegian Krone
    /MXN - Mexican Peso
    /INR - Indian Rupee
    /RUB - Russian Ruble
    /ZAR - South African Rand
    /TRY - Turkish Lira
    /BRL - Brazilian Real
    /BTC - BitCoin
Para obter a cotação, use o comando /cotacao.
Exemplo: /cotacao USD BRL
'''


def teclado_pix(id_transaction: str, sandbox: bool) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔄 Verificar status", callback_data=f"check:{id_transaction}"),
    )
    if sandbox:
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


def registrar_handlers(bot):
    """Registra todos os comandos na instância do bot passada."""

    @bot.message_handler(commands=['start'])
    def start(messagem):
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

        bot.send_message(messagem.chat.id, "Olá! Eu sou o Zeck, seu bot. Use /help para ver os comandos disponíveis.")

    @bot.message_handler(commands=['help'])
    def help_cmd(messagem):
        bot.send_message(messagem.chat.id, TEXTO_HELP)

    @bot.message_handler(commands=['cambio'])
    def cambio(messagem):
        bot.send_message(messagem.chat.id, TEXTO_CAMBIO)

    @bot.message_handler(commands=['cotacao'])
    def cotacao(messagem):
        try:
            args = messagem.text.split()
            if len(args) != 3:
                bot.send_message(messagem.chat.id, "Formato incorreto! Use: /cotacao <moeda_base> <moeda_destino>\nExemplo: /cotacao USD BRL")
                return

            resposta = obter_cotacao(args[2].upper(), args[1].upper())
            bot.send_message(messagem.chat.id, resposta)
        except Exception as e:
            bot.send_message(messagem.chat.id, f"Ocorreu um erro: {e}")

    @bot.message_handler(commands=['pix'])
    def pix(messagem):
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
                bot.send_message(messagem.chat.id, f"❌ Erro ao gerar PIX: {resultado.get('message', resultado)}")
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
                reply_markup=teclado_pix(id_transaction, sandbox),
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
            bot.send_message(messagem.chat.id, f"Ocorreu um erro: {e}")

    @bot.message_handler(commands=['simularpix'])
    def simularpix(messagem):
        try:
            args = messagem.text.split()
            if len(args) not in (2, 3):
                bot.send_message(messagem.chat.id, "Formato: /simularpix <idTransaction> [status]\nstatus pode ser: paid, failed, expired, pending (padrão: paid)")
                return

            id_transaction = args[1]
            status = args[2] if len(args) == 3 else "paid"
            _executar_simulacao(bot, messagem.chat.id, id_transaction, status)

        except Exception as e:
            bot.send_message(messagem.chat.id, f"Ocorreu um erro: {e}")

    @bot.message_handler(commands=['statuspix'])
    def statuspix(messagem):
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
            bot.send_message(messagem.chat.id, f"Ocorreu um erro: {e}")

    @bot.message_handler(commands=['relatorio'])
    def relatorio(messagem):
        bot.send_message(
            messagem.chat.id,
            texto_relatorio(),
            parse_mode="HTML",
            reply_markup=teclado_relatorio(),
        )

    # === Botões (callbacks) ===

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
        _, id_transaction, status = call.data.split(":", 2)

        if not LOFYPAY_API_KEY.startswith("sk_test_"):
            bot.answer_callback_query(call.id, "⚠️ Só funciona em sandbox (sk_test_)", show_alert=True)
            return

        resp = simular_pagamento_pix(id_transaction, status)
        if resp.status_code == 200:
            bot.answer_callback_query(call.id, f"✅ Simulação enviada: {status}")
        else:
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

    @bot.message_handler(func=lambda m: True)
    def fallback(messagem):
        bot.reply_to(messagem, TEXTO_HELP)


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
        bot.send_message(chat_id, f"Erro ao simular: {resp.json()}")