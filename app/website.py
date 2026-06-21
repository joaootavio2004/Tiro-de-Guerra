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


def build_sections(conn, month_id):
    sections = []
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
            "fmt_date": _fmt_date,
        })

    @app.get("/health")
    async def health():
        return {"status": "ok"}
