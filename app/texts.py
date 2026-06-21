"""Textos e formatações das mensagens (pt-BR)."""
from typing import List, Dict, Any

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def fmt_time(t) -> str:
    return f"{t:.2f}s" if t else "—"


def fmt_date(iso) -> str:
    """ISO (YYYY-MM-DD) ou datetime -> DD/MM/AAAA. Tolerante a formatos."""
    if not iso:
        return "sem data"
    s = str(iso)[:10]
    parts = s.split("-")
    if len(parts) == 3:
        y, m, d = parts
        return f"{d}/{m}/{y}"
    return s


def parse_date(text: str):
    """Converte '21/06/2026', '21/06/26', '21/06' ou 'hoje' em ISO (YYYY-MM-DD).
    Retorna None se não conseguir."""
    from datetime import date
    t = text.strip().lower()
    if t in ("hoje", "today"):
        return date.today().isoformat()
    t = t.replace("-", "/").replace(".", "/")
    parts = [p for p in t.split("/") if p != ""]
    try:
        if len(parts) == 2:
            d, m = int(parts[0]), int(parts[1])
            y = date.today().year
        elif len(parts) == 3:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
        else:
            return None
        return date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


def role_label(role: str) -> str:
    return {"admin": "Administrador", "ro": "RO (linha de tiro)",
            "recepcao": "Recepção"}.get(role, role)


def modality_label(m: str) -> str:
    return {"pistola": "Pistola", "carabina": "Carabina"}.get(m, m)


def enrolled_list_text(stage_label: str, enrolls: List[Dict[str, Any]]) -> str:
    if not enrolls:
        return f"📋 *{stage_label}*\n\n_Nenhum atirador inscrito ainda._"
    lines = [f"📋 *Inscritos — {stage_label}*", ""]
    current = None
    for e in enrolls:
        key = (e["modality"], e["category_name"])
        if key != current:
            current = key
            lines.append(f"\n*{modality_label(e['modality'])} · {e['category_name']}*")
        status = e.get("status", "")
        lines.append(f"• {e['shooter_name']} {status}")
    return "\n".join(lines)


def stage_class_text(stage_label: str, modality: str, category: str,
                     rows: List[Dict[str, Any]]) -> str:
    head = f"🎯 *{stage_label}*\n*{modality_label(modality)} · {category}*\n"
    if not rows:
        return head + "\n_Sem lançamentos._"
    lines = [head]
    for r in rows:
        medal = MEDALS.get(r["pos"], f"{r['pos']}º")
        t = fmt_time(r["time"]) if r["time"] else "DNF/DQ"
        lines.append(f"{medal} {r['shooter_name']} — {r['points']:.2f} pts ({t})")
    return "\n".join(lines)


def monthly_class_text(month_label: str, modality: str, category: str,
                       rows: List[Dict[str, Any]]) -> str:
    head = (f"🏆 *Classificação do mês — {month_label}*\n"
            f"*{modality_label(modality)} · {category}*\n"
            f"_(soma das etapas, descartando a pior)_\n")
    if not rows:
        return head + "\n_Sem pontuação ainda._"
    lines = [head]
    for r in rows:
        medal = MEDALS.get(r["pos"], f"{r['pos']}º")
        lines.append(f"{medal} {r['shooter_name']} — *{r['total']:.2f}* pts")
    return "\n".join(lines)
