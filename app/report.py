"""
Gera os PDFs enviados pelo bot no Telegram, com o design do site:
- Resultado MENSAL (fechamento do mês): tempo + pontos por etapa e total,
  com setas de subida/descida.
- Resultado da ETAPA (fechamento da etapa): tempo, penalidades, tempo final
  e pontuação de cada atirador, por categoria, apenas daquela etapa.
As seções são montadas dinamicamente a partir das modalidades e categorias
cadastradas (nada é fixo no código).
"""
import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader

from . import config, db, texts
from . import standings as st
from .scoring import stage_points

BASE = os.path.dirname(os.path.dirname(__file__))
MONTH_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _fmt_date(iso):
    if not iso:
        return ""
    s = str(iso)[:10].split("-")
    return f"{s[2]}/{s[1]}" if len(s) == 3 else str(iso)


def _fmt_date_full(iso):
    if not iso:
        return ""
    s = str(iso)[:10].split("-")
    return f"{s[2]}/{s[1]}/{s[0]}" if len(s) == 3 else str(iso)


def _render(template, **ctx):
    env = Environment(loader=FileSystemLoader(os.path.join(BASE, "templates")))
    html = env.get_template(template).render(**ctx)
    from weasyprint import HTML
    return HTML(string=html, base_url=BASE).write_pdf()


def _month_sections_config(conn):
    """(modality_code, category_row, include_roster, show_arrows) na ordem de
    exibição: modalidades por sort; categorias da mais alta para a mais baixa."""
    out = []
    for mod in db.list_modalities(conn):
        cats = sorted(db.list_categories(conn, mod["code"]),
                      key=lambda c: -c["rank"])
        multi = len(cats) > 1
        for c in cats:
            include_roster = bool(c["demote_n"]) and c["rank"] > min(
                x["rank"] for x in cats)
            out.append((mod["code"], c, include_roster, multi))
    return out


# ----------------------------------------------------------------------------
# PDF MENSAL
# ----------------------------------------------------------------------------
def build_month_pdf(conn, month_id: int, moves: list) -> bytes:
    month = db.get_month(conn, month_id)
    month_label = f"{MONTH_PT[month['month']]} {month['year']}"
    up_set = {m["shooter_id"] for m in moves if m["direction"] == "sobe"}
    down_set = {m["shooter_id"] for m in moves if m["direction"] == "desce"}

    sections = []
    for modality, cat, include_roster, show_arrows in _month_sections_config(conn):
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
            "modality_label": texts.modality_label(modality),
            "category": cat["name"],
            "table": table,
            "rows": rows,
            "show_arrows": show_arrows,
            "flow": (len(rows) > 14),  # tabelas longas podem quebrar de página
        })

    return _render("report.html",
                   club=config.CLUB_NAME,
                   month_label=month_label,
                   sections=sections,
                   generated=datetime.now().strftime("%d/%m/%Y %H:%M"),
                   fmt_date=_fmt_date)


def month_pdf_filename(conn, month_id: int) -> str:
    month = db.get_month(conn, month_id)
    return f"Resultado_{MONTH_PT[month['month']]}_{month['year']}.pdf"


# ----------------------------------------------------------------------------
# PDF DA ETAPA
# ----------------------------------------------------------------------------
def _pen_label(run) -> str:
    parts = []
    if run["pen2"]:
        parts.append(f"{run['pen2']}×2s")
    if run["pen5"]:
        parts.append(f"{run['pen5']}×5s")
    if run["pen10"]:
        parts.append(f"{run['pen10']}×10s")
    if not parts:
        return "—"
    total = run["pen2"] * 2 + run["pen5"] * 5 + run["pen10"] * 10
    return " + ".join(parts) + f" (+{total}s)"


def build_stage_pdf(conn, stage_id: int) -> bytes:
    stage = db.get_stage(conn, stage_id)
    month = db.get_month(conn, stage["month_id"])
    stage_label = f"{stage['number']}ª Etapa"
    month_label = f"{MONTH_PT[month['month']]} {month['year']}"

    sections = []
    for mod in db.list_modalities(conn):
        cats = sorted(db.list_categories(conn, mod["code"]),
                      key=lambda c: -c["rank"])
        for cat in cats:
            enrolls = conn.execute(
                "SELECT e.*, s.name AS shooter_name FROM enrollments e "
                "JOIN shooters s ON s.id=e.shooter_id "
                "WHERE e.stage_id=? AND e.modality=? AND e.category_id=? "
                "ORDER BY s.name",
                (stage_id, mod["code"], cat["id"])).fetchall()
            if not enrolls:
                continue
            scored, dq_rows, pending = [], [], []
            times = {}
            for e in enrolls:
                br = db.best_run(conn, e["id"])
                if br:
                    times[e["shooter_id"]] = br["final_time"]
                    scored.append({
                        "shooter_id": e["shooter_id"],
                        "name": e["shooter_name"],
                        "raw": f"{br['raw_time']:.2f}",
                        "pens": _pen_label(br),
                        "final": f"{br['final_time']:.2f}",
                    })
                elif db.any_dq(conn, e["id"]):
                    dq_rows.append({"name": e["shooter_name"]})
                else:
                    pending.append({"name": e["shooter_name"]})
            if not (scored or dq_rows):
                continue
            pts = stage_points(times)
            for r in scored:
                r["points"] = f"{pts.get(r['shooter_id'], 0.0):.2f}"
            scored.sort(key=lambda r: float(r["final"]))
            for i, r in enumerate(scored, 1):
                r["pos"] = i
            sections.append({
                "modality_label": texts.modality_label(mod["code"]),
                "category": cat["name"],
                "rows": scored,
                "dq_rows": dq_rows,
                "pending": pending,
                "flow": (len(scored) + len(dq_rows) > 14),
            })

    return _render("stage_report.html",
                   club=config.CLUB_NAME,
                   stage_label=stage_label,
                   stage_date=_fmt_date_full(stage["date"]),
                   month_label=month_label,
                   sections=sections,
                   generated=datetime.now().strftime("%d/%m/%Y %H:%M"))


def stage_pdf_filename(conn, stage_id: int) -> str:
    stage = db.get_stage(conn, stage_id)
    month = db.get_month(conn, stage["month_id"])
    return (f"Resultado_{stage['number']}a_Etapa_"
            f"{MONTH_PT[month['month']]}_{month['year']}.pdf")
