"""
Monta as classificações (etapa e mês) a partir do banco.
É usado pelo bot agora e será reaproveitado pelo site depois.
"""
from typing import List, Dict, Any, Optional
from . import db
from .scoring import stage_points, monthly_score


def stage_best_times(conn, stage_id: int, modality: str,
                     category_id: int) -> Dict[int, Optional[float]]:
    """{shooter_id: melhor_tempo_final ou None} numa etapa/modalidade/categoria."""
    enrolls = conn.execute(
        "SELECT * FROM enrollments WHERE stage_id=? AND modality=? AND category_id=?",
        (stage_id, modality, category_id)).fetchall()
    out: Dict[int, Optional[float]] = {}
    for e in enrolls:
        br = db.best_run(conn, e["id"])
        out[e["shooter_id"]] = br["final_time"] if br else None
    return out


def stage_classification(conn, stage_id: int, modality: str,
                         category_id: int) -> List[Dict[str, Any]]:
    """Lista classificada de uma etapa (uma categoria)."""
    times = stage_best_times(conn, stage_id, modality, category_id)
    pts = stage_points(times)
    rows = []
    for sid, p in pts.items():
        sh = db.get_shooter(conn, sid)
        rows.append({
            "shooter_id": sid,
            "shooter_name": sh["name"] if sh else "?",
            "time": times[sid],
            "points": round(p, 2),
        })
    rows.sort(key=lambda r: (-r["points"], r["time"] if r["time"] else 9e9))
    for i, r in enumerate(rows, 1):
        r["pos"] = i
    return rows


def month_stage_ids(conn, month_id: int) -> List[int]:
    return [s["id"] for s in db.list_stages(conn, month_id)]


def month_held_stages(conn, month_id: int):
    """Etapas do mês que já foram realizadas (têm ao menos um resultado, em
    qualquer modalidade), em ordem. É o 'número de etapas do mês'."""
    held = []
    for s in db.list_stages(conn, month_id):
        r = conn.execute(
            "SELECT COUNT(*) AS c FROM runs ru "
            "JOIN enrollments e ON e.id=ru.enrollment_id WHERE e.stage_id=?",
            (s["id"],)).fetchone()
        if r["c"] > 0:
            held.append(s)
    return held


def count_held_stages(conn, month_id: int, modality: str = None) -> int:
    return len(month_held_stages(conn, month_id))


def monthly_best_n(conn, month_id: int, modality: str = None) -> int:
    """Quantas etapas contam no mês: todas as realizadas menos a pior (mín. 1).
    Vale igual para pistola e carabina."""
    held = len(month_held_stages(conn, month_id))
    return max(1, held - 1)


def monthly_classification(conn, month_id: int, modality: str,
                           category_id: int, best_n: int = None) -> List[Dict[str, Any]]:
    """
    Classificação mensal de uma categoria.
    Conta todas as etapas realizadas no mês MENOS a pior (descarte da pior).
    Ex.: 4 etapas -> contam 3; 5 etapas -> contam 4; 3 etapas -> contam 2.
    """
    if best_n is None:
        best_n = monthly_best_n(conn, month_id)
    stage_ids = month_stage_ids(conn, month_id)
    # pontos por etapa por atirador
    per_shooter: Dict[int, List[float]] = {}
    for sid in stage_ids:
        times = stage_best_times(conn, sid, modality, category_id)
        pts = stage_points(times)
        for shooter_id, p in pts.items():
            per_shooter.setdefault(shooter_id, []).append(p)

    rows = []
    for shooter_id, plist in per_shooter.items():
        sh = db.get_shooter(conn, shooter_id)
        total = monthly_score(plist, best_n=best_n)
        rows.append({
            "shooter_id": shooter_id,
            "shooter_name": sh["name"] if sh else "?",
            "total": round(total, 2),
            "stage_points": [round(x, 2) for x in sorted(plist, reverse=True)],
        })
    rows.sort(key=lambda r: -r["total"])
    for i, r in enumerate(rows, 1):
        r["pos"] = i
    return rows


def category_roster_ids(conn, month_id, modality, category_id):
    return [r["shooter_id"] for r in conn.execute(
        "SELECT DISTINCT shooter_id FROM month_category "
        "WHERE month_id=? AND modality=? AND category_id=?",
        (month_id, modality, category_id)).fetchall()]


def monthly_classification_full(conn, month_id, modality, category_id,
                                best_n=None):
    """Classificação incluindo os integrantes que NÃO pontuaram (total 0),
    para efeito de descida de categoria. Quem pontuou vem primeiro; os zerados
    no fim, por nome."""
    cl = monthly_classification(conn, month_id, modality, category_id, best_n)
    scored = {r["shooter_id"] for r in cl}
    zeros = []
    for sid in category_roster_ids(conn, month_id, modality, category_id):
        if sid in scored:
            continue
        sh = db.get_shooter(conn, sid)
        if sh:
            zeros.append({"shooter_id": sid, "shooter_name": sh["name"],
                          "total": 0.0, "stage_points": []})
    zeros.sort(key=lambda r: r["shooter_name"])
    full = cl + zeros
    for i, r in enumerate(full, 1):
        r["pos"] = i
    return full


def promotion_proposal(conn, month_id: int) -> Dict[str, Any]:
    """
    Proposta de subidas/descidas para as categorias de PISTOLA.
    - Sobem os 3 melhores de cada categoria (exceto a mais alta).
    - Descem os 3 últimos E quem não pontuou no mês (0 pontos), exceto na
      categoria mais baixa.
    Carabina (geral) não tem subida/descida.
    """
    cats = db.list_categories(conn, "pistola")
    cats = sorted(cats, key=lambda c: c["rank"])
    by_rank = {c["rank"]: c for c in cats}
    max_rank = max(by_rank) if by_rank else 1
    min_rank = min(by_rank) if by_rank else 1

    moves = []  # {shooter_id, name, from, to, to_id, direction}
    for c in cats:
        relega = c["rank"] > min_rank      # esta categoria rebaixa para a de baixo?
        table = monthly_table(conn, month_id, "pistola", c["id"],
                              include_roster=relega)
        standings = table["rows"]          # ordenado por total desc, depois nome
        if not standings:
            continue
        scorers = [r for r in standings if r["total"] > 0]

        up_ids = set()
        if c["rank"] < max_rank:           # sobe (exceto categoria mais alta)
            up_target = by_rank[c["rank"] + 1]
            for r in scorers[:3]:
                up_ids.add(r["shooter_id"])
                moves.append({
                    "shooter_id": r["shooter_id"], "name": r["name"],
                    "from": c["name"], "to": up_target["name"],
                    "to_id": up_target["id"], "direction": "sobe",
                })

        if relega:                         # desce: os 3 últimos (no máx.), exceto quem subiu
            down_target = by_rank[c["rank"] - 1]
            cand = [r for r in standings if r["shooter_id"] not in up_ids]
            for r in cand[-3:]:
                moves.append({
                    "shooter_id": r["shooter_id"], "name": r["name"],
                    "from": c["name"], "to": down_target["name"],
                    "to_id": down_target["id"], "direction": "desce",
                })
    return {"moves": moves}


def apply_promotions(conn, moves: List[Dict[str, Any]]) -> None:
    for m in moves:
        db.set_shooter_category(conn, m["shooter_id"], "pistola", m["to_id"])


def _modality_has_results(conn, stage_id: int, modality: str) -> bool:
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM runs ru JOIN enrollments e ON e.id=ru.enrollment_id "
        "WHERE e.stage_id=? AND e.modality=?", (stage_id, modality)).fetchone()
    return r["c"] > 0


def monthly_table(conn, month_id: int, modality: str, category_id: int,
                  include_roster: bool = False) -> Dict[str, Any]:
    """
    Tabela no formato planilha para o site: TODAS as etapas do mês como colunas,
    com tempo + pontos por etapa e o total descartando a pior. Vale igual para
    pistola e carabina (carabina mostra '—' nas etapas em que não houve carabina).
    """
    stages = month_held_stages(conn, month_id)
    best_n = monthly_best_n(conn, month_id)

    stage_pts: Dict[int, Dict[int, float]] = {}
    stage_time: Dict[int, Dict[int, float]] = {}
    scored_in: Dict[int, set] = {}
    for s in stages:
        times = stage_best_times(conn, s["id"], modality, category_id)
        stage_pts[s["id"]] = stage_points(times)
        stage_time[s["id"]] = times
        scored_in[s["id"]] = {sid for sid, t in times.items() if t is not None}

    # participantes = quem se inscreveu em ALGUMA etapa do mês nessa categoria
    parts = conn.execute(
        "SELECT DISTINCT e.shooter_id FROM enrollments e "
        "JOIN stages st ON st.id=e.stage_id "
        "WHERE st.month_id=? AND e.modality=? AND e.category_id=?",
        (month_id, modality, category_id)).fetchall()
    shooters: set = {r["shooter_id"] for r in parts}
    for s in stages:
        shooters |= scored_in[s["id"]]
    # Ausentes da categoria (no elenco do mês, mas não pontuaram) entram como 0
    if include_roster:
        shooters |= set(category_roster_ids(conn, month_id, modality, category_id))

    rows = []
    for sid in shooters:
        cells = []
        pts_list = []
        for s in stages:
            if sid in scored_in[s["id"]]:
                p = round(stage_pts[s["id"]][sid], 2)
                t = stage_time[s["id"]][sid]
                cells.append({"points": p, "time": f"{t:.2f}",
                              "present": True, "dropped": False})
                pts_list.append(stage_pts[s["id"]][sid])
            else:
                cells.append({"points": None, "time": None,
                              "present": False, "dropped": False})
        total = monthly_score(pts_list, best_n)
        n_drop = max(0, len(pts_list) - best_n)
        if n_drop > 0:
            present_idx = [i for i, c in enumerate(cells) if c["present"]]
            present_idx.sort(key=lambda i: cells[i]["points"])
            for i in present_idx[:n_drop]:
                cells[i]["dropped"] = True
        sh = db.get_shooter(conn, sid)
        rows.append({"shooter_id": sid, "name": sh["name"] if sh else "?",
                     "cells": cells, "total": round(total, 2)})
    rows.sort(key=lambda r: (-r["total"], r["name"]))
    for i, r in enumerate(rows, 1):
        r["pos"] = i
    return {
        "stages": [{"number": s["number"], "date": s["date"]} for s in stages],
        "rows": rows,
        "best_n": best_n,
    }


def monthly_table_combined(conn, month_id: int, modality: str,
                           categories) -> Dict[str, Any]:
    """Junta várias categorias numa só tabela (ex.: Pistola Geral), mantendo o
    rótulo da categoria de cada atirador. Colunas = etapas do mês."""
    stages = month_held_stages(conn, month_id)
    best_n = monthly_best_n(conn, month_id)
    all_rows = []
    for cat in categories:
        t = monthly_table(conn, month_id, modality, cat["id"])
        for r in t["rows"]:
            r = dict(r)
            r["category"] = cat["name"]
            all_rows.append(r)
    all_rows.sort(key=lambda r: -r["total"])
    for i, r in enumerate(all_rows, 1):
        r["pos"] = i
    return {
        "stages": [{"number": s["number"], "date": s["date"]} for s in stages],
        "rows": all_rows,
        "best_n": best_n,
    }
