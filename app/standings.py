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


def count_held_stages(conn, month_id: int, modality: str) -> int:
    """Quantas etapas do mês têm pelo menos um resultado nesta modalidade."""
    stage_ids = month_stage_ids(conn, month_id)
    n = 0
    for sid in stage_ids:
        r = conn.execute(
            "SELECT COUNT(*) AS c FROM runs ru "
            "JOIN enrollments e ON e.id=ru.enrollment_id "
            "WHERE e.stage_id=? AND e.modality=?", (sid, modality)).fetchone()
        if r["c"] > 0:
            n += 1
    return n


def monthly_best_n(conn, month_id: int, modality: str) -> int:
    """Quantas etapas contam no mês: todas menos a pior (mínimo 1)."""
    held = count_held_stages(conn, month_id, modality)
    return max(1, held - 1)


def monthly_classification(conn, month_id: int, modality: str,
                           category_id: int, best_n: int = None) -> List[Dict[str, Any]]:
    """
    Classificação mensal de uma categoria.
    Conta todas as etapas realizadas no mês MENOS a pior (descarte da pior).
    Ex.: 4 etapas -> contam 3; 5 etapas -> contam 4; 3 etapas -> contam 2.
    """
    if best_n is None:
        best_n = monthly_best_n(conn, month_id, modality)
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


def promotion_proposal(conn, month_id: int) -> Dict[str, Any]:
    """
    Proposta de subidas/descidas para as categorias de PISTOLA.
    - 3 melhores de cada categoria sobem (exceto a mais alta)
    - 3 piores de cada categoria descem (exceto a mais baixa)
    Carabina (geral) não tem subida/descida.
    """
    cats = db.list_categories(conn, "pistola")  # ordenadas por rank asc
    cats = sorted(cats, key=lambda c: c["rank"])
    by_rank = {c["rank"]: c for c in cats}
    max_rank = max(by_rank) if by_rank else 1
    min_rank = min(by_rank) if by_rank else 1

    moves = []  # {shooter_id, name, from, to, direction}
    for c in cats:
        cl = monthly_classification(conn, month_id, "pistola", c["id"])
        # só conta quem efetivamente pontuou
        ranked = [r for r in cl if r["total"] > 0]
        n = len(ranked)
        # sobe: top 3 (se não for a categoria mais alta)
        if c["rank"] < max_rank:
            up_target = by_rank[c["rank"] + 1]
            for r in ranked[:3]:
                moves.append({
                    "shooter_id": r["shooter_id"], "name": r["shooter_name"],
                    "from": c["name"], "to": up_target["name"],
                    "to_id": up_target["id"], "direction": "sobe",
                })
        # desce: piores 3 (se não for a categoria mais baixa)
        if c["rank"] > min_rank and n > 0:
            down_target = by_rank[c["rank"] - 1]
            # evita sobrepor com quem subiu numa categoria de 1-5 pessoas
            top_ids = {r["shooter_id"] for r in ranked[:3]}
            bottom = [r for r in ranked[-3:] if r["shooter_id"] not in top_ids]
            for r in bottom:
                moves.append({
                    "shooter_id": r["shooter_id"], "name": r["shooter_name"],
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


def monthly_table(conn, month_id: int, modality: str, category_id: int) -> Dict[str, Any]:
    """
    Tabela no formato planilha para o site: colunas por etapa + total.
    Marca a(s) pior(es) etapa(s) descartada(s) de cada atirador.
    """
    stages = [s for s in db.list_stages(conn, month_id)
              if _modality_has_results(conn, s["id"], modality)]
    best_n = monthly_best_n(conn, month_id, modality)

    stage_pts: Dict[int, Dict[int, float]] = {}
    enrolled_in: Dict[int, set] = {}
    for s in stages:
        times = stage_best_times(conn, s["id"], modality, category_id)
        stage_pts[s["id"]] = stage_points(times)
        enrolled_in[s["id"]] = set(times.keys())

    shooters: set = set()
    for s in stages:
        shooters |= enrolled_in[s["id"]]

    rows = []
    for sid in shooters:
        cells = []
        pts_list = []
        for s in stages:
            if sid in enrolled_in[s["id"]]:
                p = round(stage_pts[s["id"]][sid], 2)
                cells.append({"points": p, "present": True, "dropped": False})
                pts_list.append(stage_pts[s["id"]][sid])
            else:
                cells.append({"points": None, "present": False, "dropped": False})
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
    rows.sort(key=lambda r: -r["total"])
    for i, r in enumerate(rows, 1):
        r["pos"] = i
    return {
        "stages": [{"number": s["number"], "date": s["date"]} for s in stages],
        "rows": rows,
        "best_n": best_n,
    }
