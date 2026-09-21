import base64
import threading
import time

from config import LOFYPAY_API_KEY, ACCESS_LINK
from currency import obter_cotacao
from lofypay import (
    gerar_pix, consultar_status_pix, simular_pagamento_pix,
    gerar_qrcode_imagem, poll_pagamento,
)
from storage import carregar_tokens, salvar_tokens

TEXTO_HELP = '''
Comandos disponíveis:
/start - Inicia o bot
/help - Mostra esta mensagem de ajuda
/cambio - Lista as moedas disponíveis
/cotacao <moeda_base> <moeda_destino> - Obtém a cotação entre duas moedas
/pix <valor> [nome] - Gera uma cobrança PIX
/statuspix <idTransaction> - Consulta o status de um pagamento
/simularpix <idTransaction> [status] - Simula pagamento no sandbox (só sk_test_)
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


def registrar_handlers(bot):
    """Registra todos os comandos na instância do bot passada."""

    @bot.message_handler(commands=['start'])
    def start(messagem):
        args = messagem.text.split(maxsplit=1)

        if len(args) == 2 and args[1].startswith("acesso_"):
            id_transaction = args[1][len("acesso_"):]
            tokens = carregar_tokens()
            registro = tokens.get(id_transaction)

            if not registro:
                bot.send_message(messagem.chat.id, "❌ Link inválido.")
                return
            if registro["usado"]:
                bot.send_message(messagem.chat.id, "⚠️ Esse link já foi utilizado e não é mais válido.")
                return

            registro["usado"] = True
            salvar_tokens(tokens)
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
                bot.send_message(messagem.chat.id, f"Erro ao gerar PIX: {resultado.get('message', resultado)}")
                return

            id_transaction = resultado["idTransaction"]
            codigo = resultado["paymentCode"]
            qr_base64 = resultado.get("paymentCodeBase64")

            bot.send_message(
                messagem.chat.id,
                f"💠 PIX gerado! Valor: R$ {valor:.2f}\n\n`{codigo}`\n\n"
                f"Copie o código acima e pague no app do seu banco.\nidTransaction: `{id_transaction}`",
                parse_mode="Markdown"
            )

            if qr_base64:
                bot.send_photo(messagem.chat.id, base64.b64decode(qr_base64), caption="Ou escaneie o QR Code")
            else:
                bot.send_photo(messagem.chat.id, gerar_qrcode_imagem(codigo), caption="Ou escaneie o QR Code")

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

            if not LOFYPAY_API_KEY.startswith("sk_test_"):
                bot.send_message(messagem.chat.id, "⚠️ Esse comando só funciona com uma chave sk_test_ (sandbox). Com sk_live_ a API recusa (403).")
                return

            resp = simular_pagamento_pix(id_transaction, status)

            if resp.status_code == 403:
                bot.send_message(messagem.chat.id, "❌ 403: essa rota só aceita chaves sk_test_.")
                return

            if resp.status_code == 200:
                bot.send_message(messagem.chat.id, f"✅ Simulação enviada! idTransaction: {id_transaction} → status: {status}")
            else:
                bot.send_message(messagem.chat.id, f"Erro ao simular: {resp.json()}")

        except Exception as e:
            bot.send_message(messagem.chat.id, f"Ocorreu um erro: {e}")

    @bot.message_handler(commands=['statuspix'])
    def statuspix(messagem):
        try:
            args = messagem.text.split()
            if len(args) != 2:
                bot.send_message(messagem.chat.id, "Formato: /statuspix <idTransaction>")
                return

            resultado = consultar_status_pix(args[1])
            status = resultado.get("status", resultado.get("error", "desconhecido"))
            bot.send_message(messagem.chat.id, f"Status: {status}")
        except Exception as e:
            bot.send_message(messagem.chat.id, f"Ocorreu um erro: {e}")

    @bot.message_handler(func=lambda m: True)
    def fallback(messagem):
        bot.reply_to(messagem, TEXTO_HELP)