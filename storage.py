import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

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
                produto_id TEXT,
                dias INTEGER,
                processado INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                criado_em TEXT NOT NULL,
                confirmado_em TEXT
            )
        """)
        # Migrações pra quem já tinha o bot.db de antes dessas colunas existirem.
        for coluna, definicao in (
            ("produto_id", "TEXT"),
            ("dias", "INTEGER"),
            ("processado", "INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(f"ALTER TABLE transacoes ADD COLUMN {coluna} {definicao}")
            except sqlite3.OperationalError:
                pass  # coluna já existe

        conn.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                telegram_id INTEGER PRIMARY KEY,
                nome TEXT,
                username TEXT,
                atualizado_em TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assinaturas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                plano TEXT NOT NULL,
                data_entrada TEXT NOT NULL,
                data_saida TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ativo',
                aviso_vencimento_enviado INTEGER NOT NULL DEFAULT 0,
                removido_em TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_assinaturas_usuario ON assinaturas(usuario_id)")


# ---------- tokens de acesso único (fallback via deep link, quando não há canal) ----------

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


# ---------- histórico de transações / pagamentos ----------

def registrar_transacao(id_transaction, chat_id, valor, nome=None, produto_id=None, dias=None):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO transacoes "
            "(id_transaction, chat_id, nome, valor, produto_id, dias, status, criado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)",
            (id_transaction, chat_id, nome, valor, produto_id, dias, datetime.utcnow().isoformat())
        )


def buscar_transacao(id_transaction):
    """Retorna a transação (dict) ou None."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM transacoes WHERE id_transaction = ?", (id_transaction,)
        ).fetchone()
    return dict(row) if row else None


def buscar_ultima_transacao_pendente(chat_id):
    """Última cobrança do usuário ainda não concluída — usada por /verificar_pagamento sem ID."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM transacoes WHERE chat_id = ? AND status IN ('PENDING', 'TIMEOUT') "
            "ORDER BY criado_em DESC LIMIT 1",
            (chat_id,)
        ).fetchone()
    return dict(row) if row else None


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


def conceder_pagamento_uma_vez(id_transaction):
    """Marca a transação como 'processada' (assinatura concedida) só na 1ª chamada.
    Evita conceder dias duas vezes quando o polling e um clique manual (ou reentrega
    de webhook) confirmam o mesmo pagamento em paralelo. Retorna True só pra quem
    ganhou a corrida."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE transacoes SET processado = 1 WHERE id_transaction = ? AND processado = 0",
            (id_transaction,)
        )
    return cur.rowcount == 1


def resumo_vendas():
    """Total confirmado e contagem de vendas pagas — usado pelo /relatorio."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS qtd, COALESCE(SUM(valor), 0) AS total "
            "FROM transacoes WHERE status = 'PAID_OUT'"
        ).fetchone()
    return {"quantidade": row["qtd"], "total": row["total"]}


# ---------- usuários ----------

def registrar_usuario(telegram_id, nome=None, username=None):
    """Cria ou atualiza o cadastro básico do usuário (upsert)."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO usuarios (telegram_id, nome, username, atualizado_em) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET "
            "nome = excluded.nome, username = excluded.username, atualizado_em = excluded.atualizado_em",
            (telegram_id, nome, username, datetime.utcnow().isoformat())
        )


# ---------- assinaturas VIP (planos com validade) ----------

def assinatura_ativa(usuario_id, plano):
    """Assinatura ativa do usuário para esse plano específico, se houver."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM assinaturas WHERE usuario_id = ? AND plano = ? AND status = 'ativo' "
            "ORDER BY data_saida DESC LIMIT 1",
            (usuario_id, plano)
        ).fetchone()
    return dict(row) if row else None


def assinaturas_ativas_do_usuario(usuario_id):
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM assinaturas WHERE usuario_id = ? AND status = 'ativo'",
            (usuario_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def criar_ou_renovar_assinatura(usuario_id, plano, dias):
    """Concede acesso ao plano. Se já existe uma assinatura ativa do mesmo plano,
    soma os dias a partir do maior entre 'agora' e a data de saída atual — nunca
    perde tempo que o usuário já tinha pago. Retorna (assinatura_id, nova_data_saida)."""
    agora = datetime.utcnow()
    existente = assinatura_ativa(usuario_id, plano)
    with _conn() as conn:
        if existente:
            data_saida_atual = datetime.fromisoformat(existente["data_saida"])
            base = max(data_saida_atual, agora)
            nova_saida = base + timedelta(days=dias)
            conn.execute(
                "UPDATE assinaturas SET data_saida = ?, aviso_vencimento_enviado = 0 WHERE id = ?",
                (nova_saida.isoformat(), existente["id"])
            )
            return existente["id"], nova_saida
        else:
            nova_saida = agora + timedelta(days=dias)
            cur = conn.execute(
                "INSERT INTO assinaturas (usuario_id, plano, data_entrada, data_saida, status) "
                "VALUES (?, ?, ?, ?, 'ativo')",
                (usuario_id, plano, agora.isoformat(), nova_saida.isoformat())
            )
            return cur.lastrowid, nova_saida


def assinaturas_para_avisar(dias_restantes=1):
    """Assinaturas ativas que vencem dentro de `dias_restantes` e ainda não foram avisadas."""
    agora = datetime.utcnow().isoformat()
    limite = (datetime.utcnow() + timedelta(days=dias_restantes)).isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM assinaturas WHERE status = 'ativo' AND aviso_vencimento_enviado = 0 "
            "AND data_saida > ? AND data_saida <= ?",
            (agora, limite)
        ).fetchall()
    return [dict(r) for r in rows]


def marcar_aviso_enviado(assinatura_id):
    with _conn() as conn:
        conn.execute(
            "UPDATE assinaturas SET aviso_vencimento_enviado = 1 WHERE id = ?",
            (assinatura_id,)
        )


def assinaturas_expiradas():
    """Assinaturas ainda marcadas como ativas cuja data de saída já passou."""
    agora = datetime.utcnow().isoformat()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM assinaturas WHERE status = 'ativo' AND data_saida <= ?",
            (agora,)
        ).fetchall()
    return [dict(r) for r in rows]


def expirar_assinatura(assinatura_id):
    """Marca como expirada só se ainda estava ativa — idempotente (rodar de novo não
    reprocessa nem reenvia nada pra quem já foi expirado)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE assinaturas SET status = 'expirado', removido_em = ? "
            "WHERE id = ? AND status = 'ativo'",
            (datetime.utcnow().isoformat(), assinatura_id)
        )
    return cur.rowcount == 1