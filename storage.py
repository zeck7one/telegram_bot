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
            ("invite_link", "TEXT"),
            ("invite_link_canal_id", "INTEGER"),
            ("invite_link_usado", "INTEGER NOT NULL DEFAULT 0"),
            ("codigo_pix", "TEXT"),   # copia-e-cola, pra reaproveitar PIX pendente
            ("qr_base64", "TEXT"),
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS midia_previa (
                tipo TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                canal_id INTEGER,
                message_id INTEGER,
                atualizado_em TEXT NOT NULL
            )
        """)


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

def registrar_transacao(id_transaction, chat_id, valor, nome=None, produto_id=None, dias=None,
                        codigo_pix=None, qr_base64=None):
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO transacoes "
            "(id_transaction, chat_id, nome, valor, produto_id, dias, status, criado_em, codigo_pix, qr_base64) "
            "VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)",
            (id_transaction, chat_id, nome, valor, produto_id, dias,
             datetime.utcnow().isoformat(), codigo_pix, qr_base64)
        )


def buscar_pix_pendente_recente(chat_id, produto_id, max_idade_seg=480):
    """PIX ainda pendente desse usuário pra esse plano, criado há no máximo
    `max_idade_seg` (padrão 8 min — o polling de fundo dura 10). Usado pra
    reenviar o mesmo PIX em vez de gerar um novo a cada clique."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM transacoes WHERE chat_id = ? AND produto_id = ? "
            "AND status = 'PENDING' AND codigo_pix IS NOT NULL "
            "ORDER BY criado_em DESC LIMIT 1",
            (chat_id, produto_id)
        ).fetchone()
    if not row:
        return None
    idade = (datetime.utcnow() - datetime.fromisoformat(row["criado_em"])).total_seconds()
    return dict(row) if idade <= max_idade_seg else None


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


def obter_estado_link(id_transaction):
    """Retorna {'invite_link', 'canal_id', 'usado'} se essa transação já tem um
    convite gerado, ou None se ainda não foi gerado nenhum. Usado pra nunca
    emitir mais de um convite pra mesma transação — /verificar_pagamento e
    'Liberar acesso VIP' chamados várias vezes devolvem sempre o mesmo link
    até ele ser consumido."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT invite_link, invite_link_canal_id, invite_link_usado "
            "FROM transacoes WHERE id_transaction = ?",
            (id_transaction,)
        ).fetchone()
    if not row or row["invite_link"] is None:
        return None
    return {
        "invite_link": row["invite_link"],
        "canal_id": row["invite_link_canal_id"],
        "usado": bool(row["invite_link_usado"]),
    }


def salvar_link_convite_se_necessario(id_transaction, canal_id, invite_link):
    """Grava o convite gerado pra essa transação, só se ela ainda não tinha
    nenhum salvo — atômico, evita duas threads (polling + clique manual)
    gravando dois convites diferentes pra mesma transação. Retorna True pra
    quem ganhou a corrida; False significa que já existe um convite salvo
    (o convite recém-criado pelo chamador deve ser revogado e descartado)."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE transacoes SET invite_link = ?, invite_link_canal_id = ? "
            "WHERE id_transaction = ? AND invite_link IS NULL",
            (invite_link, canal_id, id_transaction)
        )
    return cur.rowcount == 1


def transacao_por_invite_link(invite_link):
    """Acha o id_transaction dono de um convite específico — usado quando o
    bot detecta alguém entrando no canal pra saber qual link marcar como usado."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id_transaction FROM transacoes WHERE invite_link = ?",
            (invite_link,)
        ).fetchone()
    return row["id_transaction"] if row else None


def marcar_link_usado(id_transaction):
    """Marca o convite dessa transação como consumido — atômico, só na 1ª vez
    (evita reprocessar se o evento de entrada chegar duplicado). Retorna True
    só pra quem conseguiu marcar primeiro."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE transacoes SET invite_link_usado = 1 "
            "WHERE id_transaction = ? AND invite_link_usado = 0",
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


def transacoes_nao_pagas(limite=15):
    """PIX gerados que não foram pagos (status diferente de PAID_OUT/REFUNDED),
    mais recentes primeiro, com nome/@username de quem gerou. Retorna
    (total, lista) — a lista é cortada em `limite` itens."""
    filtro = "t.status NOT IN ('PAID_OUT', 'REFUNDED')"
    with _conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transacoes t WHERE {filtro}").fetchone()[0]
        rows = conn.execute(
            "SELECT t.id_transaction, t.chat_id, t.valor, t.status, t.produto_id, t.criado_em, "
            "t.nome AS nome_cobranca, u.nome AS nome_usuario, u.username "
            "FROM transacoes t LEFT JOIN usuarios u ON u.telegram_id = t.chat_id "
            f"WHERE {filtro} ORDER BY t.criado_em DESC LIMIT ?",
            (limite,)
        ).fetchall()
    return total, [dict(r) for r in rows]


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


def usuario_existe(telegram_id):
    """True se esse telegram_id já apareceu antes em 'usuarios' — usado pra saber
    se um /start é a 1ª interação do usuário com o bot."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM usuarios WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
    return row is not None


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


# ---------- banner/vídeo de prévia capturados automaticamente de um canal ----------

def salvar_midia_previa(tipo, file_id, canal_id=None, message_id=None):
    """Guarda o file_id mais recente de banner/vídeo vindo do canal de mídia
    (tipo = 'banner' ou 'video'). Upsert: cada postagem nova substitui a anterior
    daquele tipo, então a prévia enviada aos usuários sempre reflete a última arte."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO midia_previa (tipo, file_id, canal_id, message_id, atualizado_em) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tipo) DO UPDATE SET "
            "file_id = excluded.file_id, canal_id = excluded.canal_id, "
            "message_id = excluded.message_id, atualizado_em = excluded.atualizado_em",
            (tipo, file_id, canal_id, message_id, datetime.utcnow().isoformat())
        )


def obter_midia_previa(tipo):
    """Retorna {'file_id', 'canal_id', 'message_id', 'atualizado_em'} pra esse
    tipo ('banner' ou 'video'), ou None se nada foi capturado ainda."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT file_id, canal_id, message_id, atualizado_em FROM midia_previa WHERE tipo = ?",
            (tipo,)
        ).fetchone()
    return dict(row) if row else None


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