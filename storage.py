import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_FILE = "bot.db"


@contextmanager
def _conn():
    """Abre uma conexão nova por operação — simples e seguro com múltiplas threads."""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")  # permite leitura+escrita concorrente sem travar
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Cria as tabelas se ainda não existirem. Chame uma vez no início do main.py."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id_transaction TEXT PRIMARY KEY,
                usado INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transacoes (
                id_transaction TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                nome TEXT,
                valor REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                criado_em TEXT NOT NULL,
                confirmado_em TEXT
            )
        """)


# ---------- tokens de acesso único ----------

def registrar_token(id_transaction):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tokens (id_transaction, usado, criado_em) VALUES (?, 0, ?)",
            (id_transaction, datetime.utcnow().isoformat())
        )


def token_info(id_transaction):
    """Retorna {'usado': bool} ou None se o token não existir."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT usado FROM tokens WHERE id_transaction = ?", (id_transaction,)
        ).fetchone()
    return {"usado": bool(row["usado"])} if row else None


def marcar_token_usado(id_transaction):
    """Marca como usado só se ainda não estava — retorna True se conseguiu (evita corrida)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE tokens SET usado = 1 WHERE id_transaction = ? AND usado = 0",
            (id_transaction,)
        )
    return cur.rowcount == 1


# ---------- histórico de transações ----------

def registrar_transacao(id_transaction, chat_id, valor, nome=None):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO transacoes (id_transaction, chat_id, nome, valor, status, criado_em) "
            "VALUES (?, ?, ?, ?, 'PENDING', ?)",
            (id_transaction, chat_id, nome, valor, datetime.utcnow().isoformat())
        )


def atualizar_status_transacao(id_transaction, status):
    with _conn() as conn:
        if status == "PAID_OUT":
            conn.execute(
                "UPDATE transacoes SET status = ?, confirmado_em = ? WHERE id_transaction = ?",
                (status, datetime.utcnow().isoformat(), id_transaction)
            )
        else:
            conn.execute(
                "UPDATE transacoes SET status = ? WHERE id_transaction = ?",
                (status, id_transaction)
            )


def resumo_vendas():
    """Total confirmado e contagem de vendas pagas — usado pelo /relatorio."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS total "
            "FROM transacoes WHERE status = 'PAID_OUT'"
        ).fetchone()
    return {"quantidade": row["qtd"], "total": row["total"]}