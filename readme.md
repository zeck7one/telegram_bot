OBS: o arquivo file.zip eo que vai para a producao quando for
configurado um webholk
PROJETO EXECULTADO COM O Python 3.13
====================================================================
# Bot do Telegram — Cotação + PIX (LofyPay)

Bot pessoal que responde cotação de moedas e gera cobranças PIX via LofyPay,
liberando acesso a um grupo/canal privado automaticamente após confirmação
do pagamento.

## Arquitetura

```
telegram_bot/
├── config.py          # carrega .env e valida variáveis obrigatórias
├── storage.py         # SQLite (bot.db) — tokens de acesso e histórico de vendas
├── currency.py        # cotação de moedas (currencyapicom)
├── lofypay.py          # integração PIX: gerar, status, simular, QR code, confirmação
├── handlers.py         # comandos do Telegram
├── webhook_server.py    # servidor Flask p/ webhook assinado (opcional, ver abaixo)
├── main.py             # ponto de entrada — sobe o bot (e o webhook, se configurado)
└── requirements.txt
```

Fluxo de confirmação de pagamento tem duas camadas independentes:
- **Polling** (sempre ativo): a cada `/pix`, uma thread consulta `/status` a
  cada 10s por até 10min.
- **Webhook** (opcional, liga sozinho se `lofypay_webhook_secret` estiver no
  `.env`): a LofyPay avisa na hora via evento assinado (HMAC).

As duas chamam a mesma função (`finalizar_pagamento`), que atualiza o banco
de forma atômica — não importa qual canal confirma primeiro, o usuário só
recebe uma mensagem.

## Instalação

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Configuração (`.env`)

Crie um arquivo `.env` na raiz do projeto:

```dotenv
# Telegram
api_key=SEU_TOKEN_DO_BOTFATHER
bot_username=SeuBotSemArroba_bot

# Cotação de moedas
currency_api_key=SUA_CHAVE_CURRENCYAPI

# LofyPay
lofypay_api_key=sk_test_...   # use sk_live_ em produção

# Liberação de acesso pós-pagamento (escolha uma opção)
private_group_id=-100xxxxxxxxxx   # recomendado: convite de uso único
# access_link=https://...          # alternativa: link fixo (menos seguro)

# Webhook (opcional — deixe em branco pra rodar só com polling)
# lofypay_webhook_secret=
# webhook_port=5000
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `api_key` | sim | Token do bot, gerado no @BotFather |
| `currency_api_key` | sim | Chave da currencyapi.com |
| `lofypay_api_key` | sim | Chave LofyPay (`sk_test_` ou `sk_live_`) |
| `bot_username` | não | Necessário só se usar `access_link` (deep link de uso único) |
| `private_group_id` | não | Chat ID do grupo/canal privado — bot precisa ser admin com "Invite via link" |
| `access_link` | não | Alternativa ao grupo privado; sem `bot_username` não tem controle de uso único |
| `lofypay_webhook_secret` | não | Secret do endpoint, gerado em `/gateway` > Webhooks no painel LofyPay |
| `webhook_port` | não | Porta do servidor Flask (padrão 5000) |

## Rodando

```bash
python main.py
```

## Comandos do bot

| Comando | Descrição |
|---|---|
| `/start` | Mensagem de boas-vindas (ou libera acesso, se vier de um deep link `?start=acesso_...`) |
| `/help` | Lista os comandos |
| `/cambio` | Lista as moedas suportadas |
| `/cotacao <base> <destino>` | Ex: `/cotacao USD BRL` |
| `/pix <valor> [nome]` | Gera cobrança PIX com QR code |
| `/statuspix <idTransaction>` | Consulta status manualmente |
| `/simularpix <idTransaction> [status]` | Simula pagamento no sandbox (só `sk_test_`) |
| `/relatorio` | Total de vendas confirmadas |

## Banco de dados

SQLite em `bot.db`, criado automaticamente na primeira execução. Duas tabelas:
- `tokens`: controle de uso único dos links de acesso (quando usando `access_link`)
- `transacoes`: histórico de cada cobrança gerada, com status e valor

## Ativando o webhook (quando tiver hospedagem pública)

1. No painel LofyPay: `/gateway` > Webhooks > cadastre `https://seudominio.com/webhooks/lofypay`
2. Copie o "Secret de assinatura" do endpoint (não é a `sk_live_`)
3. Preencha `lofypay_webhook_secret` no `.env`
4. Rode `python main.py` normalmente — o terminal confirma que o webhook subiu

## Segurança

- Nunca commite o `.env`, `bot.db` ou `acessos.json` (já estão no `.gitignore`)
- Use `sk_test_` pra desenvolvimento; só troque pra `sk_live_` em produção
- Se vazar a `sk_live_`, regenere imediatamente no painel LofyPay