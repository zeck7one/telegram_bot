import telebot

import config
from storage import init_db
from handlers import registrar_handlers
from scheduler import iniciar_scheduler

config.validar()
init_db()

bot = telebot.TeleBot(config.API_KEY)

# BOT_USERNAME monta os deep links (t.me/<bot>?start=...) usados pelos botões
# VIP e pelo link de acesso pós-pagamento. Se não vier certo do .env, detecta
# sozinho via API — evita o bug de link quebrado (t.me/None) por username
# ausente/errado, que jogava o usuário numa tela de "perfil desconhecido".
if not config.BOT_USERNAME:
    config.BOT_USERNAME = bot.get_me().username
print(f"Bot rodando como @{config.BOT_USERNAME}")

registrar_handlers(bot)
iniciar_scheduler(bot)  # avisos de vencimento (1 dia antes) + expiração/remoção do canal

if __name__ == "__main__":
    print("Bot está rodando...")
    # allowed_updates precisa incluir "chat_member" explicitamente (Bot API 5.1+),
    # senão o Telegram não entrega os eventos de entrada/saída de canal que o
    # bot usa pra detectar quando um convite VIP foi consumido e destruí-lo.
    bot.infinity_polling(
        timeout=20, long_polling_timeout=20,
        allowed_updates=telebot.util.update_types,
    )