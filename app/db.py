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
        # Carga inicial de categorias
        for name, modality, rank in DEFAULT_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories(name, modality, rank, active) "
                "VALUES (?,?,?,1)", (name, modality, rank))
        # Garante o mês atual aberto
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
    return conn.execute(
        "SELECT * FROM shooters WHERE active=1 AND name LIKE ? "
        "ORDER BY name LIMIT ?", (f"%{term.strip()}%", limit)).fetchall()


def get_shooter(conn, shooter_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM shooters WHERE id=?",
                        (shooter_id,)).fetchone()


def create_shooter(conn, name: str) -> int:
    cur = conn.execute(
        "INSERT INTO shooters(name, active, created_at) VALUES (?,1,?)",
        (name.strip(), now()))
    return cur.lastrowid


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
    today = date.today()
    row = conn.execute(
        "SELECT * FROM months WHERE year=? AND month=?",
        (today.year, today.month)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO months(year,month,status,created_at) VALUES (?,?, 'aberto', ?)",
            (today.year, today.month, now()))
        row = conn.execute("SELECT * FROM months WHERE year=? AND month=?",
                           (today.year, today.month)).fetchone()
    return row


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
