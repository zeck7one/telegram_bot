import currencyapicom

from config import CURRENCY_API_KEY

MOEDAS = ['USD', 'EUR', 'JPY', 'GBP', 'AUD', 'CAD', 'CHF', 'CNY', 'HKD',
          'NZD', 'SEK', 'KRW', 'SGD', 'NOK', 'MXN', 'INR', 'RUB', 'ZAR',
          'TRY', 'BRL', 'BTC']


def obter_cotacao(moeda, moeda_base):
    if moeda in MOEDAS and moeda_base in MOEDAS:
        try:
            client = currencyapicom.Client(CURRENCY_API_KEY)
            result = client.latest(moeda_base, currencies=[moeda])
            valor = result['data'][moeda]['value']
            return f"um {moeda_base} esta valendo: {valor} {moeda}"
        except Exception as e:
            return f"Erro ao acessar a API: {e}"
    else:
        return "Moeda não encontrada. Consulte /cambio para ver as moedas suportadas."