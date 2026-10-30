"""
Extensões de banco para a ÁREA INTERNA (/interno).

Tudo aqui é ADITIVO: cria tabelas novas e colunas novas com
"IF NOT EXISTS" / ALTER TABLE ADD COLUMN. Nunca apaga nem altera dados
existentes — o banco em produção continua válido para o bot.

Novidades:
- web_users / web_sessions : login da área interna (cargos = tabela roles)
- stage_checkins           : "inscrição" da recepção na etapa SEM modalidade
                             e SEM quantidade (o RO escolhe a arma na hora)
- run_shots                : disparos de cada passada (vindos do timer), para
                             a aba de estatísticas do site
- runs.source / n_shots / first_shot / segments : metadados da passada
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from . import db, util

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,                 -- código em roles
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_checkins (
    stage_id    INTEGER NOT NULL,
    shooter_id  INTEGER NOT NULL,
    created_by  INTEGER,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (stage_id, shooter_id)
);

CREATE TABLE IF NOT EXISTS run_shots (
    run_id INTEGER NOT NULL,
    idx    INTEGER NOT NULL,
    t      REAL NOT NULL,                        -- segundos desde o beep
    PRIMARY KEY (run_id, idx)
);
"""

SESSION_DAYS = 30


def migrate(conn) -> None:
    conn.executescript(SCHEMA)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(runs)")]
    if "source" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN source TEXT")
    if "n_shots" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN n_shots INTEGER")
    if "first_shot" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN first_shot REAL")
    if "segments" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN segments INTEGER")
    if "created_by_user" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN created_by_user INTEGER")
    conn.commit()


# ----------------------------------------------------------------------------
# Usuários / senhas / sessões
# ----------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                             200_000)
    return f"pbkdf2${salt}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, hexdk = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt), 200_000)
        return hmac.compare_digest(dk.hex(), hexdk)
    except Exception:
        return False


def list_users(conn) -> List:
    return conn.execute(
        "SELECT * FROM web_users ORDER BY active DESC, role, name").fetchall()


def get_user(conn, user_id: int):
    return conn.execute("SELECT * FROM web_users WHERE id=?",
                        (user_id,)).fetchone()


def get_user_by_username(conn, username: str):
    return conn.execute("SELECT * FROM web_users WHERE username=?",
                        (username.strip().lower(),)).fetchone()


def create_user(conn, username: str, name: str, password: str, role: str) -> int:
    cur = conn.execute(
        "INSERT INTO web_users(username,name,password_hash,role,active,created_at) "
        "VALUES (?,?,?,?,1,?)",
        (username.strip().lower(), name.strip(), hash_password(password), role,
         db.now()))
    return cur.lastrowid


def update_user(conn, user_id: int, name: str = None, role: str = None,
                password: str = None, active: int = None) -> None:
    if name is not None:
        conn.execute("UPDATE web_users SET name=? WHERE id=?", (name.strip(), user_id))
    if role is not None:
        conn.execute("UPDATE web_users SET role=? WHERE id=?", (role, user_id))
    if password:
        conn.execute("UPDATE web_users SET password_hash=? WHERE id=?",
                     (hash_password(password), user_id))
    if active is not None:
        conn.execute("UPDATE web_users SET active=? WHERE id=?", (active, user_id))
        if not active:
            conn.execute("DELETE FROM web_sessions WHERE user_id=?", (user_id,))


def ensure_admin(conn, username: str, password: str) -> Optional[str]:
    """Cria o primeiro administrador se ainda não existir nenhum usuário.
    Devolve a senha usada (gerada, se não veio do ambiente) ou None."""
    n = conn.execute("SELECT COUNT(*) AS n FROM web_users").fetchone()["n"]
    if n:
        return None
    pw = password or secrets.token_urlsafe(9)
    create_user(conn, username or "admin", "Administrador", pw, "admin")
    return pw


def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO web_sessions(token,user_id,created_at,expires_at) VALUES (?,?,?,?)",
        (token, user_id, db.now(), exp))
    # limpeza oportunista
    conn.execute("DELETE FROM web_sessions WHERE expires_at < ?", (db.now(),))
    return token


def user_by_session(conn, token: str):
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM web_sessions s JOIN web_users u ON u.id=s.user_id "
        "WHERE s.token=? AND s.expires_at > ? AND u.active=1",
        (token, db.now())).fetchone()
    return row


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM web_sessions WHERE token=?", (token,))


# ----------------------------------------------------------------------------
# Presença na etapa (inscrição sem modalidade)
# ----------------------------------------------------------------------------
def checkin(conn, stage_id: int, shooter_id: int, user_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO stage_checkins(stage_id,shooter_id,created_by,created_at) "
        "VALUES (?,?,?,?)", (stage_id, shooter_id, user_id, db.now()))


def remove_checkin(conn, stage_id: int, shooter_id: int) -> None:
    conn.execute("DELETE FROM stage_checkins WHERE stage_id=? AND shooter_id=?",
                 (stage_id, shooter_id))


def stage_participants(conn, stage_id: int) -> List[Dict[str, Any]]:
    """Todos os atiradores da etapa: check-ins da recepção (site) + inscrições
    feitas pelo bot. Cada um com quantas passadas já tem e em quais armas."""
    rows = conn.execute(
        "SELECT DISTINCT s.id, s.name FROM shooters s WHERE s.active=1 AND ("
        " s.id IN (SELECT shooter_id FROM stage_checkins WHERE stage_id=?) OR"
        " s.id IN (SELECT shooter_id FROM enrollments WHERE stage_id=?)) "
        "ORDER BY s.name", (stage_id, stage_id)).fetchall()
    out = []
    for r in rows:
        runs = conn.execute(
            "SELECT e.modality, COUNT(ru.id) AS n FROM enrollments e "
            "LEFT JOIN runs ru ON ru.enrollment_id=e.id "
            "WHERE e.stage_id=? AND e.shooter_id=? GROUP BY e.modality",
            (stage_id, r["id"])).fetchall()
        by_mod = {x["modality"]: x["n"] for x in runs}
        total = sum(by_mod.values())
        out.append({"id": r["id"], "name": r["name"], "name_key": util.norm_name(r["name"]),
                    "runs": total, "by_modality": by_mod})
    return out


def shooter_stage_runs(conn, stage_id: int, shooter_id: int) -> List[Dict[str, Any]]:
    """Passadas do atirador na etapa (todas as modalidades), mais recente primeiro."""
    rows = conn.execute(
        "SELECT ru.*, e.modality, e.category_id, c.name AS category_name "
        "FROM runs ru JOIN enrollments e ON e.id=ru.enrollment_id "
        "JOIN categories c ON c.id=e.category_id "
        "WHERE e.stage_id=? AND e.shooter_id=? ORDER BY ru.id DESC",
        (stage_id, shooter_id)).fetchall()
    return [run_dict(conn, r) for r in rows]


def run_dict(conn, r) -> Dict[str, Any]:
    shots = [s["t"] for s in conn.execute(
        "SELECT t FROM run_shots WHERE run_id=? ORDER BY idx", (r["id"],))]
    keys = r.keys()
    return {
        "id": r["id"],
        "modality": r["modality"] if "modality" in keys else None,
        "category": r["category_name"] if "category_name" in keys else None,
        "raw": r["raw_time"], "pen2": r["pen2"], "pen5": r["pen5"],
        "pen10": r["pen10"], "dq": bool(r["dq"]), "final": r["final_time"],
        "pen_secs": r["pen2"] * 2 + r["pen5"] * 5 + r["pen10"] * 10,
        "source": r["source"] if "source" in keys else None,
        "n_shots": (r["n_shots"] if "n_shots" in keys and r["n_shots"] is not None
                    else (len(shots) or None)),
        "first_shot": (r["first_shot"] if "first_shot" in keys and r["first_shot"]
                       is not None else (shots[0] if shots else None)),
        "segments": r["segments"] if "segments" in keys else None,
        "shots": shots,
        "created_at": r["created_at"],
    }


def add_run_web(conn, enrollment_id: int, raw_time: Optional[float], pen2: int,
                pen5: int, pen10: int, dq: bool, user_id: int, source: str,
                shots: List[float], segments: int) -> int:
    from .scoring import final_time
    ft = final_time(raw_time, pen2, pen5, pen10, dq)
    cur = conn.execute(
        "INSERT INTO runs(enrollment_id,raw_time,pen2,pen5,pen10,dq,final_time,"
        "created_by,created_at,source,n_shots,first_shot,segments,created_by_user) "
        "VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,?,?)",
        (enrollment_id, raw_time, pen2, pen5, pen10, 1 if dq else 0, ft,
         db.now(), source, len(shots) if shots else None,
         shots[0] if shots else None, segments or 1, user_id))
    run_id = cur.lastrowid
    for i, t in enumerate(shots or [], 1):
        conn.execute("INSERT INTO run_shots(run_id,idx,t) VALUES (?,?,?)",
                     (run_id, i, float(t)))
    return run_id


def delete_run_web(conn, run_id: int):
    conn.execute("DELETE FROM run_shots WHERE run_id=?", (run_id,))
    return db.delete_run(conn, run_id)


def billing_list(conn, stage_id: int) -> List[Dict[str, Any]]:
    """Recepção: atiradores da etapa com quantidade de passadas FINALIZADAS
    (tempo lançado), por arma — para cobrança."""
    parts = stage_participants(conn, stage_id)
    return sorted(parts, key=lambda p: p["name"])


def shooter_history(conn, shooter_id: int) -> List[Dict[str, Any]]:
    """Todas as passadas do atirador (todas as etapas), mais recente primeiro —
    para a aba de estatísticas do site."""
    rows = conn.execute(
        "SELECT ru.*, e.modality, c.name AS category_name, st.id AS stage_id, "
        "st.number AS stage_number, st.date AS stage_date, m.year, m.month "
        "FROM runs ru JOIN enrollments e ON e.id=ru.enrollment_id "
        "JOIN categories c ON c.id=e.category_id "
        "JOIN stages st ON st.id=e.stage_id JOIN months m ON m.id=st.month_id "
        "WHERE e.shooter_id=? ORDER BY st.date DESC, ru.id DESC",
        (shooter_id,)).fetchall()
    out = []
    for r in rows:
        d = run_dict(conn, r)
        d["stage_id"] = r["stage_id"]
        d["stage_number"] = r["stage_number"]
        d["stage_date"] = r["stage_date"]
        d["year"], d["month"] = r["year"], r["month"]
        out.append(d)
    return out


def participation_stats(conn) -> Dict[str, Any]:
    """Números de participação para a página de indicadores.
    - por etapa: inscritos (check-in ou inscrição), atiradores que passaram,
      passadas e DQs, e passadas por modalidade
    - por mês: atiradores distintos, novos (primeira passada da vida no mês),
      passadas e etapas realizadas"""
    stages = conn.execute(
        "SELECT st.id, st.number, st.date, m.id AS month_id, m.year, m.month "
        "FROM stages st JOIN months m ON m.id=st.month_id "
        "ORDER BY m.year, m.month, st.number").fetchall()
    run_rows = conn.execute(
        "SELECT e.stage_id, e.shooter_id, e.modality, ru.dq FROM runs ru "
        "JOIN enrollments e ON e.id=ru.enrollment_id").fetchall()
    enrolled = conn.execute(
        "SELECT stage_id, shooter_id FROM enrollments UNION "
        "SELECT stage_id, shooter_id FROM stage_checkins").fetchall()

    by_stage = {s["id"]: {"enrolled": set(), "shooters": set(), "runs": 0, "dq": 0,
                          "by_mod": {}} for s in stages}
    for r in enrolled:
        if r["stage_id"] in by_stage:
            by_stage[r["stage_id"]]["enrolled"].add(r["shooter_id"])
    for r in run_rows:
        b = by_stage.get(r["stage_id"])
        if b is None:
            continue
        b["shooters"].add(r["shooter_id"])
        b["runs"] += 1
        b["dq"] += 1 if r["dq"] else 0
        b["by_mod"][r["modality"]] = b["by_mod"].get(r["modality"], 0) + 1

    out_stages, months, seen = [], {}, set()
    for s in stages:
        b = by_stage[s["id"]]
        if not b["runs"] and not b["enrolled"]:
            continue
        out_stages.append({
            "id": s["id"], "number": s["number"], "date": s["date"],
            "month_id": s["month_id"], "year": s["year"], "month": s["month"],
            "enrolled": len(b["enrolled"]), "shooters": len(b["shooters"]),
            "runs": b["runs"], "dq": b["dq"], "by_mod": b["by_mod"],
        })
        m = months.setdefault(s["month_id"], {
            "id": s["month_id"], "year": s["year"], "month": s["month"],
            "stages": 0, "shooters": set(), "new": 0, "runs": 0, "by_mod": {}})
        if b["runs"]:
            m["stages"] += 1
        new = b["shooters"] - seen - m["shooters"]
        m["new"] += len(new)
        seen |= b["shooters"]
        m["shooters"] |= b["shooters"]
        m["runs"] += b["runs"]
        for k, v in b["by_mod"].items():
            m["by_mod"][k] = m["by_mod"].get(k, 0) + v
    out_months = []
    for m in months.values():
        m["shooters"] = len(m["shooters"])
        out_months.append(m)
    return {"stages": out_stages, "months": out_months}


# ----------------------------------------------------------------------------
# Limpeza de cadastros por arma criados sem uso
# ----------------------------------------------------------------------------
# Até a correção de _resolve_category, abrir um atirador na linha de tiro o
# cadastrava na categoria inicial de TODAS as armas. Só é removido o vínculo
# que (a) está na categoria inicial da arma e (b) não tem nenhuma inscrição
# naquela arma: removê-lo não muda nada, pois a primeira passada real o
# coloca de novo na mesma categoria. Categorias escolhidas pelo admin
# (diferentes da inicial) e meses fechados não são tocados.
_ORPHAN_WHERE = (
    "sm.category_id = (SELECT id FROM categories c WHERE c.modality=sm.modality "
    "  AND c.active=1 AND c.entry=1 ORDER BY c.id LIMIT 1) "
    "AND NOT EXISTS (SELECT 1 FROM enrollments e WHERE e.shooter_id=sm.shooter_id "
    "  AND e.modality=sm.modality)")


def orphan_memberships(conn) -> List[Dict[str, Any]]:
    """Quantos vínculos sem uso há por arma/categoria (prévia da limpeza)."""
    return [dict(r) for r in conn.execute(
        "SELECT sm.modality, c.name AS category, COUNT(*) AS n "
        "FROM shooter_modality sm JOIN categories c ON c.id=sm.category_id "
        f"WHERE {_ORPHAN_WHERE} GROUP BY sm.modality, c.name ORDER BY sm.modality")]


def backup_db(conn, folder: str) -> str:
    """Cópia consistente do banco (API de backup do SQLite) antes de limpezas."""
    import os
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"tiro_guerra_{datetime.now():%Y%m%d_%H%M%S}.db")
    import sqlite3
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def clean_orphan_memberships(conn, modality: str) -> int:
    """Remove os vínculos sem uso de UMA arma e o retrato deles no mês ABERTO.
    Por arma porque vínculos antigos (importados da planilha, sem inscrição
    no sistema) também aparecem como "sem uso": o admin decide quais limpar."""
    rows = conn.execute(
        "SELECT sm.shooter_id, sm.modality FROM shooter_modality sm "
        f"WHERE sm.modality=? AND {_ORPHAN_WHERE}", (modality,)).fetchall()
    open_months = [r["id"] for r in conn.execute("SELECT id FROM months WHERE status='aberto'")]
    for r in rows:
        conn.execute("DELETE FROM shooter_modality WHERE shooter_id=? AND modality=?",
                     (r["shooter_id"], r["modality"]))
        for mid in open_months:
            conn.execute("DELETE FROM month_category WHERE month_id=? AND shooter_id=? "
                         "AND modality=?", (mid, r["shooter_id"], r["modality"]))
    return len(rows)
