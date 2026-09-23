# Helpers visuais compartilhados: cards em HTML, emoji e label por status.
# Cobre tanto os status internos (pending/paid/...) quanto os que a LofyPay
# retorna em maiúsculo (PAID_OUT, EXPIRED, FAILED, REFUNDED).

STATUS_EMOJI = {
    "pending": "🟡",
    "aguardando": "🟡",
    "waiting_payment": "🟡",
    "paid": "🟢",
    "paid_out": "🟢",
    "approved": "🟢",
    "success": "🟢",
    "cancelled": "🔴",
    "canceled": "🔴",
    "failed": "🔴",
    "refunded": "🟣",
    "expired": "⚫",
    "timeout": "⏱",
    "desconhecido": "⚪",
}

STATUS_LABEL = {
    "pending": "Aguardando pagamento",
    "waiting_payment": "Aguardando pagamento",
    "paid": "Pago",
    "paid_out": "Pago",
    "approved": "Pago",
    "success": "Pago",
    "cancelled": "Cancelado",
    "canceled": "Cancelado",
    "failed": "Falhou",
    "refunded": "Reembolsado",
    "expired": "Expirado",
    "timeout": "Monitoramento encerrado (tempo limite)",
    "desconhecido": "Desconhecido",
}


def status_emoji(status: str) -> str:
    return STATUS_EMOJI.get((status or "").lower(), "⚪")


def status_label(status: str) -> str:
    return STATUS_LABEL.get((status or "").lower(), status or "Desconhecido")


def card(title: str, lines: dict, emoji: str = "📦") -> str:
    """Monta um bloco de texto formatado em HTML no estilo 'card'."""
    body = "\n".join(f"<b>{k}:</b> {v}" for k, v in lines.items())
    return f"{emoji} <b>{title}</b>\n\n{body}"