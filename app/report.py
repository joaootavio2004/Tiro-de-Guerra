"""
Gera o PDF do resultado mensal (enviado no fechamento do mês).
Reaproveita o design do site. Mostra, por categoria, tempo + pontos por etapa
e o total (descartando a pior), com setas de subida/descida na pistola.
"""
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

from . import config, db
from . import standings as st

BASE = os.path.dirname(os.path.dirname(__file__))
MONTH_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

# (modalidade, categoria, inclui_ausentes_com_0, mostra_setas)
SECTIONS = [
    ("pistola", "Veterano de Guerra", True, True),
    ("pistola", "Guerreiro", True, True),
    ("pistola", "Combatente", False, True),   # só quem participou; só sobe
    ("carabina", "Geral", False, False),       # só quem participou; sem setas
]


def _fmt_date(iso):
    if not iso:
        return ""
    s = str(iso)[:10].split("-")
    return f"{s[2]}/{s[1]}" if len(s) == 3 else str(iso)


def build_month_pdf(conn, month_id: int, moves: list) -> bytes:
    month = db.get_month(conn, month_id)
    month_label = f"{MONTH_PT[month['month']]} {month['year']}"
    up_set = {m["shooter_id"] for m in moves if m["direction"] == "sobe"}
    down_set = {m["shooter_id"] for m in moves if m["direction"] == "desce"}

    sections = []
    for modality, cat_name, include_roster, show_arrows in SECTIONS:
        cat = conn.execute(
            "SELECT * FROM categories WHERE modality=? AND name=?",
            (modality, cat_name)).fetchone()
        if not cat:
            continue
        table = st.monthly_table(conn, month_id, modality, cat["id"],
                                 include_roster=include_roster)
        rows = table["rows"]
        if not rows:
            continue
        for r in rows:
            if show_arrows and r["shooter_id"] in up_set:
                r["move"] = "up"
            elif show_arrows and r["shooter_id"] in down_set:
                r["move"] = "down"
            else:
                r["move"] = None
        sections.append({
            "modality_label": "Pistola" if modality == "pistola" else "Carabina",
            "category": cat_name,
            "table": table,
            "rows": rows,
            "show_arrows": show_arrows,
            "flow": (len(rows) > 14),  # tabelas longas podem quebrar de página
        })

    env = Environment(loader=FileSystemLoader(os.path.join(BASE, "templates")))
    html = env.get_template("report.html").render(
        club=config.CLUB_NAME,
        month_label=month_label,
        sections=sections,
        generated=datetime.now().strftime("%d/%m/%Y %H:%M"),
        fmt_date=_fmt_date,
    )
    from weasyprint import HTML
    return HTML(string=html, base_url=BASE).write_pdf()


def month_pdf_filename(conn, month_id: int) -> str:
    month = db.get_month(conn, month_id)
    return f"Resultado_{MONTH_PT[month['month']]}_{month['year']}.pdf"
