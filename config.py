import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
API_KEY = os.getenv('api_key')
BOT_USERNAME = os.getenv('bot_username')  # sem @, usado no deep link de acesso único


# LofyPay
LOFYPAY_API_KEY = os.getenv('lofypay_api_key')  # sk_live_... ou sk_test_...
LOFYPAY_BASE_URL = "https://app.lofypay.com/api/v1"

# Liberação de acesso pós-pagamento
ACCESS_LINK = os.getenv('access_link')          # link fixo (fallback)
PRIVATE_GROUP_ID = os.getenv('private_group_id')  # canal/grupo privado (recomendado)

# Seu user_id do Telegram — se configurado, só você consegue rodar /postarvip.
# Pegue seu ID com @userinfobot. Deixe vazio pra não restringir (não recomendado).
ADMIN_CHAT_ID = os.getenv('admin_chat_id')

# Armazenamento local dos tokens de acesso único
TOKENS_FILE = "acessos.json"


def validar():
    """Confere se as variáveis obrigatórias estão presentes. Chame no início do main.py."""
    faltando = []
    if not API_KEY:
        faltando.append("api_key")
    if not LOFYPAY_API_KEY:
        faltando.append("lofypay_api_key")
    
    if faltando:
        raise RuntimeError(
            f"Variáveis de ambiente faltando no .env: {', '.join(faltando)}"
        )

    if not PRIVATE_GROUP_ID and not ACCESS_LINK:
        print("⚠️  Aviso: nem private_group_id nem access_link configurados — "
              "pagamentos confirmados não vão liberar nenhum acesso.")