"""
Camada de banco de dados (SQLite).

Tudo fica num único arquivo .db, guardado num volume persistente.
Esta camada cria as tabelas, faz a carga inicial das categorias e
oferece funções simples para o resto do sistema usar.
"""
import os
import sqlite3
from datetime import datetime, date
from typing import Optional, List, Dict, Any

from . import config
from . import util

MODALITIES = ("pistola", "carabina")

# Categorias padrão (pistola tem hierarquia; carabina é geral).
DEFAULT_CATEGORIES = [
    ("Combatente", "pistola", 1),
    ("Guerreiro", "pistola", 2),
    ("Veterano de Guerra", "pistola", 3),
    ("Geral", "carabina", 1),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    telegram_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    username    TEXT,
    role        TEXT NOT NULL,                 -- 'admin' | 'ro' | 'recepcao'
    status      TEXT NOT NULL DEFAULT 'ativo', -- 'ativo' | 'inativo'
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_requests (
    telegram_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    username    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    modality  TEXT NOT NULL,                   -- 'pistola' | 'carabina'
    rank      INTEGER NOT NULL,                -- maior = categoria mais alta
    active    INTEGER NOT NULL DEFAULT 1,
    UNIQUE(name, modality)
);

CREATE TABLE IF NOT EXISTS shooters (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    name_key   TEXT,
    cpf        TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- categoria ATUAL do atirador em cada modalidade
CREATE TABLE IF NOT EXISTS shooter_modality (
    shooter_id  INTEGER NOT NULL,
    modality    TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (shooter_id, modality)
);

CREATE TABLE IF NOT EXISTS months (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    year       INTEGER NOT NULL,
    month      INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'aberto', -- 'aberto' | 'fechado'
    created_at TEXT NOT NULL,
    UNIQUE(year, month)
);

-- categoria do atirador "congelada" no mês (não muda no meio do mês)
CREATE TABLE IF NOT EXISTS month_category (
    month_id    INTEGER NOT NULL,
    shooter_id  INTEGER NOT NULL,
    modality    TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    PRIMARY KEY (month_id, shooter_id, modality)
);

CREATE TABLE IF NOT EXISTS stages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    month_id   INTEGER NOT NULL,
    number     INTEGER NOT NULL,
    date       TEXT,
    status     TEXT NOT NULL DEFAULT 'aberta',  -- 'aberta' | 'fechada'
    created_at TEXT NOT NULL,
    UNIQUE(month_id, number)
);

CREATE TABLE IF NOT EXISTS enrollments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id    INTEGER NOT NULL,
    shooter_id  INTEGER NOT NULL,
    modality    TEXT NOT NULL,
    category_id INTEGER NOT NULL,
    runs_total  INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER,
    created_at  TEXT NOT NULL,
    UNIQUE(stage_id, shooter_id, modality)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enrollment_id INTEGER NOT NULL,
    raw_time      REAL,
    pen2          INTEGER NOT NULL DEFAULT 0,
    pen5          INTEGER NOT NULL DEFAULT 0,
    pen10         INTEGER NOT NULL DEFAULT 0,
    dq            INTEGER NOT NULL DEFAULT 0,
    final_time    REAL,
    created_by    INTEGER,
    created_at    TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        # Migração: garante colunas novas em bancos antigos
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(shooters)")]
        if "name_key" not in cols:
            conn.execute("ALTER TABLE shooters ADD COLUMN name_key TEXT")
        if "cpf" not in cols:
            conn.execute("ALTER TABLE shooters ADD COLUMN cpf TEXT")
        # Preenche a chave de busca onde estiver vazia
        for r in conn.execute(
                "SELECT id, name FROM shooters "
                "WHERE name_key IS NULL OR name_key=''").fetchall():
            conn.execute("UPDATE shooters SET name_key=? WHERE id=?",
                         (util.norm_name(r["name"]), r["id"]))
        # Carga inicial de categorias
        for name, modality, rank in DEFAULT_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, modality, rank, active) "
                "VALUES (?,?,?,1)", (name, modality, rank))
        conn.commit()
        ensure_current_month(conn)
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# EQUIPE / ACESSO
# ----------------------------------------------------------------------------
def get_staff(conn, telegram_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM staff WHERE telegram_id=? AND status='ativo'",
        (telegram_id,)).fetchone()


def upsert_staff(conn, telegram_id: int, name: str, role: str,
                 username: str = None) -> None:
    conn.execute(
        "INSERT INTO staff(telegram_id,name,username,role,status,created_at) "
        "VALUES (?,?,?,?, 'ativo', ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET name=excluded.name, "
        "username=excluded.username, role=excluded.role, status='ativo'",
        (telegram_id, name, username, role, now()))


def list_staff(conn) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM staff WHERE status='ativo' ORDER BY role, name").fetchall()


def deactivate_staff(conn, telegram_id: int) -> None:
    conn.execute("UPDATE staff SET status='inativo' WHERE telegram_id=?",
                 (telegram_id,))


def add_access_request(conn, telegram_id: int, name: str, username: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO access_requests(telegram_id,name,username,created_at) "
        "VALUES (?,?,?,?)", (telegram_id, name, username, now()))


def get_access_request(conn, telegram_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM access_requests WHERE telegram_id=?",
                        (telegram_id,)).fetchone()


def remove_access_request(conn, telegram_id: int) -> None:
    conn.execute("DELETE FROM access_requests WHERE telegram_id=?", (telegram_id,))


def list_admin_ids(conn) -> List[int]:
    rows = conn.execute(
        "SELECT telegram_id FROM staff WHERE role='admin' AND status='ativo'"
    ).fetchall()
    ids = {r["telegram_id"] for r in rows} | set(config.ADMIN_IDS)
    return list(ids)


# ----------------------------------------------------------------------------
# CATEGORIAS
# ----------------------------------------------------------------------------
def list_categories(conn, modality: str = None) -> List[sqlite3.Row]:
    if modality:
        return conn.execute(
            "SELECT * FROM categories WHERE active=1 AND modality=? ORDER BY rank",
            (modality,)).fetchall()
    return conn.execute(
        "SELECT * FROM categories WHERE active=1 ORDER BY modality, rank").fetchall()


def get_category(conn, category_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM categories WHERE id=?",
                        (category_id,)).fetchone()


def add_category(conn, name: str, modality: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(rank),0)+1 AS r FROM categories WHERE modality=?",
        (modality,)).fetchone()
    cur = conn.execute(
        "INSERT INTO categories(name,modality,rank,active) VALUES (?,?,?,1)",
        (name, modality, row["r"]))
    return cur.lastrowid


# ----------------------------------------------------------------------------
# ATIRADORES
# ----------------------------------------------------------------------------
def search_shooters(conn, term: str, limit: int = 8) -> List[sqlite3.Row]:
    """Busca sem depender de acento/maiúsculas (usa name_key normalizado)."""
    key = util.norm_name(term)
    if not key:
        return []
    return conn.execute(
        "SELECT * FROM shooters WHERE active=1 AND name_key LIKE ? "
        "ORDER BY name LIMIT ?", (f"%{key}%", limit)).fetchall()


def get_shooter(conn, shooter_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM shooters WHERE id=?",
                        (shooter_id,)).fetchone()


def get_shooter_by_cpf(conn, cpf: str) -> Optional[sqlite3.Row]:
    digits = util.only_digits(cpf)
    if not digits:
        return None
    return conn.execute(
        "SELECT * FROM shooters WHERE active=1 AND cpf=?", (digits,)).fetchone()


def create_shooter(conn, name: str, cpf: str = None) -> int:
    cur = conn.execute(
        "INSERT INTO shooters(name, name_key, cpf, active, created_at) "
        "VALUES (?,?,?,1,?)",
        (name.strip(), util.norm_name(name),
         util.only_digits(cpf) if cpf else None, now()))
    return cur.lastrowid


def update_shooter(conn, shooter_id: int, name: str = None,
                   cpf: str = None) -> None:
    if name is not None:
        conn.execute("UPDATE shooters SET name=?, name_key=? WHERE id=?",
                     (name.strip(), util.norm_name(name), shooter_id))
    if cpf is not None:
        conn.execute("UPDATE shooters SET cpf=? WHERE id=?",
                     (util.only_digits(cpf), shooter_id))


def count_shooter_enrollments(conn, shooter_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM enrollments WHERE shooter_id=?",
        (shooter_id,)).fetchone()["n"]


def delete_shooter(conn, shooter_id: int) -> None:
    """Remove o atirador e tudo ligado a ele (inscrições, resultados, etc.)."""
    conn.execute(
        "DELETE FROM runs WHERE enrollment_id IN "
        "(SELECT id FROM enrollments WHERE shooter_id=?)", (shooter_id,))
    conn.execute("DELETE FROM enrollments WHERE shooter_id=?", (shooter_id,))
    conn.execute("DELETE FROM shooter_modality WHERE shooter_id=?", (shooter_id,))
    conn.execute("DELETE FROM month_category WHERE shooter_id=?", (shooter_id,))
    conn.execute("DELETE FROM shooters WHERE id=?", (shooter_id,))


def merge_shooters(conn, src_id: int, dst_id: int) -> None:
    """Funde o atirador 'src' no 'dst' (mantém o dst) e apaga o src.
    Move inscrições e resultados; em conflito de mesma etapa+modalidade,
    junta as corridas na inscrição do dst."""
    if src_id == dst_id:
        return
    src_enrolls = conn.execute(
        "SELECT * FROM enrollments WHERE shooter_id=?", (src_id,)).fetchall()
    for e in src_enrolls:
        dst_enr = conn.execute(
            "SELECT * FROM enrollments WHERE stage_id=? AND shooter_id=? "
            "AND modality=?", (e["stage_id"], dst_id, e["modality"])).fetchone()
        if dst_enr:
            # já existe inscrição do dst nessa etapa/modalidade: move as corridas
            conn.execute("UPDATE runs SET enrollment_id=? WHERE enrollment_id=?",
                         (dst_enr["id"], e["id"]))
            conn.execute(
                "UPDATE enrollments SET runs_total=runs_total+? WHERE id=?",
                (e["runs_total"], dst_enr["id"]))
            conn.execute("DELETE FROM enrollments WHERE id=?", (e["id"],))
        else:
            conn.execute("UPDATE enrollments SET shooter_id=? WHERE id=?",
                         (dst_id, e["id"]))
    # preferências de categoria do dst são mantidas; remove o src
    conn.execute("DELETE FROM shooter_modality WHERE shooter_id=?", (src_id,))
    conn.execute("DELETE FROM month_category WHERE shooter_id=?", (src_id,))
    conn.execute("DELETE FROM shooters WHERE id=?", (src_id,))


def duplicate_groups(conn) -> List[List[sqlite3.Row]]:
    """Grupos de atiradores com a MESMA chave normalizada (duplicados óbvios)."""
    rows = conn.execute(
        "SELECT * FROM shooters WHERE active=1 ORDER BY name").fetchall()
    by_key: Dict[str, list] = {}
    for r in rows:
        by_key.setdefault(r["name_key"] or "", []).append(r)
    return [v for v in by_key.values() if len(v) > 1]


def get_shooter_category(conn, shooter_id: int, modality: str) -> Optional[int]:
    row = conn.execute(
        "SELECT category_id FROM shooter_modality WHERE shooter_id=? AND modality=?",
        (shooter_id, modality)).fetchone()
    return row["category_id"] if row else None


def set_shooter_category(conn, shooter_id: int, modality: str,
                         category_id: int) -> None:
    conn.execute(
        "INSERT INTO shooter_modality(shooter_id,modality,category_id) VALUES (?,?,?) "
        "ON CONFLICT(shooter_id,modality) DO UPDATE SET category_id=excluded.category_id",
        (shooter_id, modality, category_id))


def default_category_id(conn, modality: str) -> int:
    """Categoria de entrada: a mais baixa (rank menor) da modalidade."""
    row = conn.execute(
        "SELECT id FROM categories WHERE active=1 AND modality=? ORDER BY rank LIMIT 1",
        (modality,)).fetchone()
    return row["id"]


# ----------------------------------------------------------------------------
# MESES / ETAPAS
# ----------------------------------------------------------------------------
def ensure_current_month(conn) -> sqlite3.Row:
    """Garante que exista UM mês de competição aberto e o devolve.
    Se já houver um aberto, devolve esse. Senão, abre o mês seguinte ao
    último existente (ou o mês do calendário, se o banco estiver vazio)."""
    m = get_open_month(conn)
    if m:
        return m
    latest = conn.execute(
        "SELECT * FROM months ORDER BY year DESC, month DESC LIMIT 1").fetchone()
    if latest:
        y, mo = latest["year"], latest["month"] + 1
        if mo > 12:
            mo = 1
            y += 1
    else:
        today = date.today()
        y, mo = today.year, today.month
    conn.execute(
        "INSERT OR IGNORE INTO months(year,month,status,created_at) "
        "VALUES (?,?, 'aberto', ?)", (y, mo, now()))
    conn.execute("UPDATE months SET status='aberto' WHERE year=? AND month=?",
                 (y, mo))
    return conn.execute("SELECT * FROM months WHERE year=? AND month=?",
                        (y, mo)).fetchone()


def get_open_month(conn) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM months WHERE status='aberto' ORDER BY year DESC, month DESC "
        "LIMIT 1").fetchone()


def get_month(conn, month_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()


def list_stages(conn, month_id: int) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM stages WHERE month_id=? ORDER BY number", (month_id,)).fetchall()


def get_open_stage(conn) -> Optional[sqlite3.Row]:
    """Etapa aberta mais recente (a 'linha de tiro' atual)."""
    return conn.execute(
        "SELECT s.* FROM stages s JOIN months m ON m.id=s.month_id "
        "WHERE s.status='aberta' AND m.status='aberto' "
        "ORDER BY s.month_id DESC, s.number DESC LIMIT 1").fetchone()


def get_stage(conn, stage_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM stages WHERE id=?", (stage_id,)).fetchone()


def create_stage(conn, month_id: int, the_date: str = None) -> sqlite3.Row:
    row = conn.execute(
        "SELECT COALESCE(MAX(number),0)+1 AS n FROM stages WHERE month_id=?",
        (month_id,)).fetchone()
    number = row["n"]
    cur = conn.execute(
        "INSERT INTO stages(month_id,number,date,status,created_at) "
        "VALUES (?,?,?, 'aberta', ?)",
        (month_id, number, the_date or date.today().isoformat(), now()))
    return conn.execute("SELECT * FROM stages WHERE id=?", (cur.lastrowid,)).fetchone()


def close_stage(conn, stage_id: int) -> None:
    conn.execute("UPDATE stages SET status='fechada' WHERE id=?", (stage_id,))


def reopen_stage(conn, stage_id: int) -> None:
    conn.execute("UPDATE stages SET status='aberta' WHERE id=?", (stage_id,))


def update_stage_date(conn, stage_id: int, the_date: str) -> None:
    conn.execute("UPDATE stages SET date=? WHERE id=?", (the_date, stage_id))


def stage_result_count(conn, stage_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM runs r JOIN enrollments e ON e.id=r.enrollment_id "
        "WHERE e.stage_id=?", (stage_id,)).fetchone()
    return row["n"]


def stage_enroll_count(conn, stage_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM enrollments WHERE stage_id=?",
        (stage_id,)).fetchone()
    return row["n"]


def delete_stage(conn, stage_id: int) -> None:
    """Apaga a etapa e tudo ligado a ela (inscrições e resultados)."""
    conn.execute(
        "DELETE FROM runs WHERE enrollment_id IN "
        "(SELECT id FROM enrollments WHERE stage_id=?)", (stage_id,))
    conn.execute("DELETE FROM enrollments WHERE stage_id=?", (stage_id,))
    conn.execute("DELETE FROM stages WHERE id=?", (stage_id,))


def wipe_month(conn, month_id: int) -> None:
    """Limpa TODAS as etapas/inscrições/resultados do mês (recomeçar do zero).
    Mantém atiradores e suas categorias atuais."""
    stage_ids = [s["id"] for s in
                 conn.execute("SELECT id FROM stages WHERE month_id=?",
                              (month_id,)).fetchall()]
    for sid in stage_ids:
        delete_stage(conn, sid)
    conn.execute("DELETE FROM month_category WHERE month_id=?", (month_id,))


# ----------------------------------------------------------------------------
# SNAPSHOT DE CATEGORIA NO MÊS
# ----------------------------------------------------------------------------
def month_category_id(conn, month_id: int, shooter_id: int, modality: str,
                      fallback_category_id: int) -> int:
    """
    Categoria do atirador NAQUELE mês. Se ainda não existir snapshot,
    cria a partir da categoria atual (ou do fallback) e congela.
    """
    row = conn.execute(
        "SELECT category_id FROM month_category "
        "WHERE month_id=? AND shooter_id=? AND modality=?",
        (month_id, shooter_id, modality)).fetchone()
    if row:
        return row["category_id"]
    conn.execute(
        "INSERT INTO month_category(month_id,shooter_id,modality,category_id) "
        "VALUES (?,?,?,?)", (month_id, shooter_id, modality, fallback_category_id))
    return fallback_category_id


# ----------------------------------------------------------------------------
# INSCRIÇÕES / CORRIDAS
# ----------------------------------------------------------------------------
def get_enrollment(conn, stage_id: int, shooter_id: int,
                   modality: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM enrollments WHERE stage_id=? AND shooter_id=? AND modality=?",
        (stage_id, shooter_id, modality)).fetchone()


def enroll(conn, stage_id: int, shooter_id: int, modality: str,
           category_id: int, qty: int, created_by: int) -> sqlite3.Row:
    """Inscreve (ou soma corridas se já inscrito) e devolve a inscrição."""
    existing = get_enrollment(conn, stage_id, shooter_id, modality)
    if existing:
        conn.execute(
            "UPDATE enrollments SET runs_total = runs_total + ? WHERE id=?",
            (qty, existing["id"]))
        return get_enrollment(conn, stage_id, shooter_id, modality)
    cur = conn.execute(
        "INSERT INTO enrollments(stage_id,shooter_id,modality,category_id,"
        "runs_total,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
        (stage_id, shooter_id, modality, category_id, qty, created_by, now()))
    return conn.execute("SELECT * FROM enrollments WHERE id=?",
                        (cur.lastrowid,)).fetchone()


def list_enrollments(conn, stage_id: int, modality: str = None) -> List[sqlite3.Row]:
    q = ("SELECT e.*, s.name AS shooter_name, c.name AS category_name, c.rank AS rank "
         "FROM enrollments e "
         "JOIN shooters s ON s.id=e.shooter_id "
         "JOIN categories c ON c.id=e.category_id "
         "WHERE e.stage_id=? ")
    args: list = [stage_id]
    if modality:
        q += "AND e.modality=? "
        args.append(modality)
    q += "ORDER BY e.modality, c.rank DESC, s.name"
    return conn.execute(q, args).fetchall()


def get_enrollment_full(conn, enrollment_id: int):
    return conn.execute(
        "SELECT e.*, s.name AS shooter_name, c.name AS category_name "
        "FROM enrollments e JOIN shooters s ON s.id=e.shooter_id "
        "JOIN categories c ON c.id=e.category_id WHERE e.id=?",
        (enrollment_id,)).fetchone()


def set_enrollment_qty(conn, enrollment_id: int, qty: int) -> None:
    conn.execute("UPDATE enrollments SET runs_total=? WHERE id=?",
                 (max(1, qty), enrollment_id))


def delete_enrollment(conn, enrollment_id: int) -> None:
    """Remove a inscrição e os resultados ligados a ela."""
    conn.execute("DELETE FROM runs WHERE enrollment_id=?", (enrollment_id,))
    conn.execute("DELETE FROM enrollments WHERE id=?", (enrollment_id,))


def add_run(conn, enrollment_id: int, raw_time: Optional[float], pen2: int,
            pen5: int, pen10: int, dq: bool, created_by: int) -> None:
    from .scoring import final_time
    ft = final_time(raw_time, pen2, pen5, pen10, dq)
    conn.execute(
        "INSERT INTO runs(enrollment_id,raw_time,pen2,pen5,pen10,dq,final_time,"
        "created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (enrollment_id, raw_time, pen2, pen5, pen10, 1 if dq else 0, ft,
         created_by, now()))


def best_run(conn, enrollment_id: int) -> Optional[sqlite3.Row]:
    """Melhor corrida (menor tempo final válido) de uma inscrição."""
    return conn.execute(
        "SELECT * FROM runs WHERE enrollment_id=? AND dq=0 AND final_time IS NOT NULL "
        "ORDER BY final_time ASC LIMIT 1", (enrollment_id,)).fetchone()


def runs_count(conn, enrollment_id: int) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM runs WHERE enrollment_id=?",
                        (enrollment_id,)).fetchone()["n"]


def has_any_run(conn, enrollment_id: int) -> bool:
    return runs_count(conn, enrollment_id) > 0


def any_dq(conn, enrollment_id: int) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) AS n FROM runs WHERE enrollment_id=? AND dq=1",
        (enrollment_id,)).fetchone()
    return r["n"] > 0
