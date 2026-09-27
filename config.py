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

# Canal de prévias — quem manda /start pela 1ª vez e ainda não é membro desse
# canal é direcionado pra ele antes de ver o menu normal do bot.
PREVIEW_CHANNEL_ID = os.getenv('preview_channel_id')      # chat_id do canal (p/ checar se o usuário já é membro)
PREVIEW_CHANNEL_LINK = os.getenv('preview_channel_link')  # link público ou de convite, mostrado ao usuário

# Prévia enviada depois que o usuário já está no canal. Pode ser caminho local,
# URL ou file_id do Telegram. Se for deixado vazio, o bot envia apenas o menu de preços.
# Servem como FALLBACK: se o BANNER_SOURCE_CHANNEL_ID abaixo já tiver capturado
# uma mídia, ela tem prioridade sobre esses valores fixos.
PREVIEW_BANNER = os.getenv('preview_banner')
PREVIEW_MEDIA = os.getenv('preview_media')
PREVIEW_MEDIA_TYPE = os.getenv('preview_media_type', 'photo').lower()

# Canal (que o bot já participa) onde você posta a arte/vídeo de divulgação.
# O bot fica ouvindo esse canal e guarda automaticamente a última foto e o
# último vídeo postados nele como banner/vídeo de prévia atuais — pra trocar
# a arte basta postar uma nova lá, sem mexer no .env nem reiniciar o bot.
BANNER_SOURCE_CHANNEL_ID = os.getenv('banner_source_channel_id')

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

    if PREVIEW_CHANNEL_ID and not PREVIEW_CHANNEL_LINK:
        print("⚠️  Aviso: preview_channel_id configurado sem preview_channel_link — "
              "o bot não vai conseguir mostrar o link do canal de prévias pro usuário.")