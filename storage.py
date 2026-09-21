import json
import os

from config import TOKENS_FILE


def carregar_tokens():
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE, "r") as f:
            return json.load(f)
    return {}


def salvar_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f)