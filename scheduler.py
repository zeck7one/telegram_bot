import threading
import time

from storage import (
    assinaturas_para_avisar, marcar_aviso_enviado,
    assinaturas_expiradas, expirar_assinatura,
    assinaturas_ativas_do_usuario,
)
from vip import VIP_TIERS

# Roda a cada 1h. Aviso e expiração são decididos comparando datas salvas no banco,
# não por um horário fixo do dia — então rodar a cada hora é suficiente e simples.
INTERVALO_SEG = 3600


def _nome_plano(plano_id):
    tier = VIP_TIERS.get(plano_id)
    return tier["nome"] if tier else plano_id


def _canal_do_plano(plano_id):
    tier = VIP_TIERS.get(plano_id)
    return tier.get("canal_id") if tier else None


def _outra_assinatura_cobre_canal(usuario_id, canal_id, ignorar_assinatura_id):
    """Evita remover o usuário de um canal que ele ainda tem acesso legítimo via
    outro plano ativo (relevante quando dois tiers apontam pro mesmo canal_id)."""
    for assinatura in assinaturas_ativas_do_usuario(usuario_id):
        if assinatura["id"] == ignorar_assinatura_id:
            continue
        if _canal_do_plano(assinatura["plano"]) == canal_id:
            return True
    return False


def _remover_do_canal(bot, chat_id, canal_id):
    if not canal_id:
        return
    try:
        bot.ban_chat_member(canal_id, chat_id)
        bot.unban_chat_member(canal_id, chat_id, only_if_banned=True)
    except Exception as e:
        print(f"Erro ao remover {chat_id} do canal {canal_id}: {e}")


def _checar_avisos(bot):
    for assinatura in assinaturas_para_avisar(dias_restantes=1):
        try:
            bot.send_message(
                assinatura["usuario_id"],
                "⚠️ Seu acesso está próximo do vencimento!\n\n"
                f"Seu plano {_nome_plano(assinatura['plano'])} termina amanhã.\n\n"
                "Para continuar com acesso ao canal, faça a renovação do seu plano."
            )
            marcar_aviso_enviado(assinatura["id"])
        except Exception as e:
            print(f"Erro ao enviar aviso de vencimento (assinatura {assinatura['id']}): {e}")


def _checar_expiracoes(bot):
    for assinatura in assinaturas_expiradas():
        # expirar_assinatura só retorna True pra quem realmente muda o status —
        # garante que essa rotina pode rodar de novo sem reprocessar ninguém.
        if not expirar_assinatura(assinatura["id"]):
            continue

        canal_id = _canal_do_plano(assinatura["plano"])
        if canal_id and not _outra_assinatura_cobre_canal(assinatura["usuario_id"], canal_id, assinatura["id"]):
            _remover_do_canal(bot, assinatura["usuario_id"], canal_id)

        try:
            bot.send_message(
                assinatura["usuario_id"],
                "🔒 Seu acesso ao canal expirou.\n\n"
                "Seu plano chegou ao fim e o acesso foi encerrado.\n\n"
                "Se quiser continuar, você pode contratar um novo plano pelo bot."
            )
        except Exception as e:
            print(f"Erro ao avisar expiração (assinatura {assinatura['id']}): {e}")


def _loop(bot):
    while True:
        try:
            _checar_avisos(bot)
            _checar_expiracoes(bot)
        except Exception as e:
            print(f"Erro no scheduler de assinaturas: {e}")
        time.sleep(INTERVALO_SEG)


def iniciar_scheduler(bot):
    """Roda em background verificando avisos de vencimento (1 dia antes) e
    expirações de assinaturas VIP. Chame uma vez no main.py, depois de
    registrar_handlers."""
    threading.Thread(target=_loop, args=(bot,), daemon=True).start()