"""
Importa a situação ATUAL do clube a partir da planilha.

Usa apenas a aba do mês corrente (26 JUN): traz a lista de atiradores com
suas categorias atuais e as etapas de junho que já têm resultado.
As demais abas (fevereiro a maio) são desconsideradas — junho é o mês aberto.

Uso:
    python -m scripts.import_planilha /caminho/Tiro_Guerra_AAAAMMDD.xlsx
"""
import re
import sys

from openpyxl import load_workbook

from app import db

# Aba do mês corrente e seu (ano, mês).
SHEET = "26 JUN"
YEAR, MONTH = 2026, 6

# Colunas de "Tempo" de cada etapa (1-indexed): D,F,H,J,L
TIME_COLS = [4, 6, 8, 10, 12]


def norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip()).upper()


def map_category(conn, label: str):
    u = norm(label)
    if "CARABINA" in u:
        modality, cat_name = "carabina", "Geral"
    elif "VETERANO" in u:
        modality, cat_name = "pistola", "Veterano de Guerra"
    elif "GUERREIRO" in u:
        modality, cat_name = "pistola", "Guerreiro"
    elif "COMBATENTE" in u:
        modality, cat_name = "pistola", "Combatente"
    else:
        return None, None
    row = conn.execute(
        "SELECT id FROM categories WHERE modality=? AND name=?",
        (modality, cat_name)).fetchone()
    return modality, (row["id"] if row else None)


def main(xlsx_path: str):
    db.init_db()
    conn = db.get_conn()
    wb = load_workbook(xlsx_path, data_only=True)
    if SHEET not in wb.sheetnames:
        print(f"Aba '{SHEET}' nao encontrada na planilha.")
        sys.exit(1)
    ws = wb[SHEET]

    conn.execute(
        "INSERT OR IGNORE INTO months(year,month,status,created_at) VALUES (?,?,?,?)",
        (YEAR, MONTH, "aberto", db.now()))
    m = conn.execute("SELECT * FROM months WHERE year=? AND month=?",
                     (YEAR, MONTH)).fetchone()
    month_id = m["id"]

    # cria UMA etapa por coluna que tenha pelo menos um tempo
    cols_with_data = []
    for col in TIME_COLS:
        has = any(
            isinstance(ws.cell(row=r, column=col).value, (int, float))
            and ws.cell(row=r, column=col).value > 0
            for r in range(5, ws.max_row + 1))
        if has:
            cols_with_data.append(col)

    col_to_stage = {}
    for col in cols_with_data:
        d = ws.cell(row=2, column=col).value
        the_date = d.date().isoformat() if hasattr(d, "date") else (
            str(d) if d else None)
        stage = db.create_stage(conn, month_id, the_date)
        col_to_stage[col] = stage["id"]
    conn.commit()

    shooter_cache = {}

    def get_or_create_shooter(name):
        key = norm(name)
        if key in shooter_cache:
            return shooter_cache[key]
        existing = conn.execute(
            "SELECT id FROM shooters WHERE UPPER(name)=?", (key,)).fetchone()
        if existing:
            shooter_cache[key] = existing["id"]
            return existing["id"]
        sid = db.create_shooter(conn, str(name).strip())
        shooter_cache[key] = sid
        return sid

    total_runs = 0
    current_modality, current_cat = None, None
    for r in range(5, ws.max_row + 1):
        a = ws.cell(row=r, column=1).value
        name = ws.cell(row=r, column=3).value
        if a and "PARTICIPANTES" in norm(a):
            break
        if a and str(a).strip():
            mod, cat = map_category(conn, a)
            if cat:
                current_modality, current_cat = mod, cat
        if not name or current_cat is None:
            continue
        if "PARTICIPANTES" in norm(name):
            continue

        sid = get_or_create_shooter(name)
        db.set_shooter_category(conn, sid, current_modality, current_cat)
        conn.execute(
            "INSERT OR REPLACE INTO month_category"
            "(month_id,shooter_id,modality,category_id) VALUES (?,?,?,?)",
            (month_id, sid, current_modality, current_cat))

        for col, stage_id in col_to_stage.items():
            t = ws.cell(row=r, column=col).value
            if not isinstance(t, (int, float)) or t <= 0:
                continue
            enr = db.get_enrollment(conn, stage_id, sid, current_modality)
            if not enr:
                enr = db.enroll(conn, stage_id, sid, current_modality,
                                current_cat, 1, 0)
            db.add_run(conn, enr["id"], float(t), 0, 0, 0, False, 0)
            total_runs += 1

    conn.execute("UPDATE months SET status='aberto' WHERE id=?", (month_id,))
    # etapas já realizadas entram encerradas; a "ao vivo" só aparece quando o
    # admin abrir uma nova etapa durante o tiro.
    conn.execute("UPDATE stages SET status='fechada' WHERE month_id=?", (month_id,))
    conn.commit()

    n_shooters = conn.execute("SELECT COUNT(*) c FROM shooters").fetchone()["c"]
    n_stages = len(col_to_stage)
    conn.close()
    print(f"Importacao concluida (apenas {SHEET}): {n_shooters} atiradores, "
          f"{n_stages} etapas, {total_runs} resultados. Mes {MONTH}/{YEAR} aberto.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.import_planilha caminho/arquivo.xlsx")
        sys.exit(1)
    main(sys.argv[1])
