# Catálogo de planos VIP.
# canal_id: chat_id do canal privado VIP (negativo, tipo -100xxxxxxxxxx).
# Pegue encaminhando uma mensagem do canal pro @userinfobot.
# dias: duração do plano em dias — usado pra calcular a data de término do acesso.
# Ajuste os valores de dias abaixo se algum plano não for mensal (30 dias).
#preços
VIP_TIERS = {
    "bronze":   {"nome": "Bronze",   "preco": 01.00, "canal_id": -1003984048024, "dias": 7},
    "prata":    {"nome": "Prata",    "preco": 24.99, "canal_id": -1003984048024, "dias": 30},
    "ouro":     {"nome": "Ouro",     "preco": 34.99, "canal_id": -1003984048024, "dias": 30},
    "diamante": {"nome": "Diamante", "preco": 49.99, "canal_id": -1003984048024, "dias": 30},
}