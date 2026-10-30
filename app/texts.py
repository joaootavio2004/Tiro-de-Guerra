"""Formatações e rótulos (pt-BR) usados nas páginas e PDFs."""


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


# Rótulos dinâmicos (carregados do banco no boot e após alterações do admin)
_ROLE_LABELS = {"admin": "Administrador", "ro": "RO (linha de tiro)",
                "recepcao": "Recepção"}
_MOD_LABELS = {"pistola": "Pistola", "carabina": "Carabina"}


def set_role_labels(d):
    _ROLE_LABELS.update(d or {})


def set_modality_labels(d):
    _MOD_LABELS.update(d or {})


def role_label(role: str) -> str:
    return _ROLE_LABELS.get(role, str(role).replace("_", " ").title())


def modality_label(m: str) -> str:
    return _MOD_LABELS.get(m, str(m).replace("_", " ").title())


