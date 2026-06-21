"""
Site público de resultados (Fase 2).

Roda no mesmo app/contêiner do bot, lendo o mesmo banco. Mostra a
classificação do mês por categoria, no formato planilha (colunas por etapa,
descartando a pior). Identidade visual da marca: verde-militar, dourado e branco.
"""
import os
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config, db
from . import standings as st

BASE = os.path.dirname(os.path.dirname(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE, "templates"))

MONTH_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

# Ordem de exibição das categorias (pistola do topo para a base, depois carabina)
SECTION_ORDER = [
    ("pistola", "Veterano de Guerra"),
    ("pistola", "Guerreiro"),
    ("pistola", "Combatente"),
    ("carabina", "Geral"),
]


def _fmt_date(iso):
    if not iso:
        return ""
    s = str(iso)[:10].split("-")
    return f"{s[2]}/{s[1]}" if len(s) == 3 else str(iso)


def _fmt_date_long(iso):
    if not iso:
        return ""
    s = str(iso)[:10].split("-")
    if len(s) == 3:
        return f"{int(s[2])} de {MONTH_PT[int(s[1])].lower()}"
    return str(iso)


def open_stage_payload(conn):
    """Dados da etapa ABERTA para a página ao vivo (tempos sendo lançados)."""
    from datetime import datetime
    stage = db.get_open_stage(conn)
    if not stage:
        return {"open": False}
    sections = []
    for modality, cat_name in SECTION_ORDER:
        cat = conn.execute(
            "SELECT * FROM categories WHERE modality=? AND name=?",
            (modality, cat_name)).fetchone()
        if not cat:
            continue
        enrolls = conn.execute(
            "SELECT e.id AS eid, e.shooter_id AS sid, s.name AS nm "
            "FROM enrollments e JOIN shooters s ON s.id=e.shooter_id "
            "WHERE e.stage_id=? AND e.modality=? AND e.category_id=? ORDER BY s.name",
            (stage["id"], modality, cat["id"])).fetchall()
        if not enrolls:
            continue
        ranked, pending = [], []
        for e in enrolls:
            br = db.best_run(conn, e["eid"])
            if br:
                ranked.append({"name": e["nm"], "ft": br["final_time"]})
            else:
                pending.append({"name": e["nm"],
                                "dq": db.any_dq(conn, e["eid"])})
        ranked.sort(key=lambda r: r["ft"])
        best = ranked[0]["ft"] if ranked else None
        rows = []
        for i, r in enumerate(ranked, 1):
            pts = 100.0 if r["ft"] == best else best / r["ft"] * 100
            rows.append({"pos": i, "name": r["name"],
                         "time": f"{r['ft']:.2f}", "points": f"{pts:.2f}"})
        sections.append({
            "id": f"{modality}-{cat['id']}",
            "modality_label": "Pistola" if modality == "pistola" else "Carabina",
            "category": cat_name, "rows": rows, "pending": pending,
        })
    return {
        "open": True,
        "stage_label": f"{stage['number']}ª Etapa",
        "date": _fmt_date(stage["date"]),
        "updated": datetime.now().strftime("%H:%M"),
        "sections": sections,
    }


CAT_SHORT = {
    "Veterano de Guerra": "VET",
    "Guerreiro": "GUE",
    "Combatente": "COM",
    "Geral": "GER",
}


def build_sections(conn, month_id):
    sections = []
    # Pistola · Geral (junta as 3 categorias)
    pistol_cats = [c for c in db.list_categories(conn, "pistola")]
    pistol_cats = sorted(pistol_cats, key=lambda c: -c["rank"])
    combined = st.monthly_table_combined(conn, month_id, "pistola", pistol_cats)
    if combined["rows"]:
        for r in combined["rows"]:
            r["cat_short"] = CAT_SHORT.get(r.get("category", ""), "")
        sections.append({
            "id": "pistola-geral", "modality": "pistola",
            "modality_label": "Pistola", "category": "Geral",
            "combined": True, "table": combined,
        })
    # Categorias individuais (pistola do topo p/ base, depois carabina)
    for modality, cat_name in SECTION_ORDER:
        cat = conn.execute(
            "SELECT * FROM categories WHERE modality=? AND name=?",
            (modality, cat_name)).fetchone()
        if not cat:
            continue
        table = st.monthly_table(conn, month_id, modality, cat["id"])
        if not table["rows"]:
            continue
        sections.append({
            "id": f"{modality}-{cat['id']}",
            "modality": modality,
            "modality_label": "Pistola" if modality == "pistola" else "Carabina",
            "category": cat_name,
            "combined": False,
            "table": table,
        })
    return sections


def register_web(app):
    app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")),
              name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, mes: int = None):
        conn = db.get_conn()
        try:
            months = conn.execute(
                "SELECT * FROM months ORDER BY year DESC, month DESC").fetchall()
            if not months:
                db.ensure_current_month(conn)
                conn.commit()
                months = conn.execute(
                    "SELECT * FROM months ORDER BY year DESC, month DESC").fetchall()
            current = None
            if mes:
                current = conn.execute("SELECT * FROM months WHERE id=?",
                                       (mes,)).fetchone()
            if current is None:
                current = db.get_open_month(conn) or months[0]
            sections = build_sections(conn, current["id"])
            all_stages = db.list_stages(conn, current["id"])
            stage_dates = [_fmt_date_long(s["date"]) for s in all_stages
                           if st._modality_has_results(conn, s["id"], "pistola")
                           or st._modality_has_results(conn, s["id"], "carabina")]
            month_options = [{
                "id": m["id"],
                "label": f"{MONTH_PT[m['month']]} {m['year']}",
                "selected": m["id"] == current["id"],
            } for m in months]
            open_stage = db.get_open_stage(conn)
            live_available = bool(
                open_stage and db.stage_result_count(conn, open_stage["id"]) > 0)
        finally:
            conn.close()
        return templates.TemplateResponse(request, "index.html", {
            "club": config.CLUB_NAME,
            "month_label": f"{MONTH_PT[current['month']]} {current['year']}",
            "month_is_open": current["status"] == "aberto",
            "n_stages": len(stage_dates),
            "stage_dates": stage_dates,
            "sections": sections,
            "month_options": month_options,
            "live_available": live_available,
            "fmt_date": _fmt_date,
        })

    @app.get("/agora", response_class=HTMLResponse)
    async def agora(request: Request):
        conn = db.get_conn()
        try:
            data = open_stage_payload(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(request, "live.html", {
            "club": config.CLUB_NAME,
            "data": data,
        })

    @app.get("/api/agora")
    async def api_agora():
        conn = db.get_conn()
        try:
            return open_stage_payload(conn)
        finally:
            conn.close()

    @app.get("/health")
    async def health():
        return {"status": "ok"}
