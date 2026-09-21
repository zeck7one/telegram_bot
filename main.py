import telebot

import config
from handlers import registrar_handlers

config.validar()

bot = telebot.TeleBot(config.API_KEY)
registrar_handlers(bot)

if __name__ == "__main__":
    print("Bot está rodando...")
    bot.polling()