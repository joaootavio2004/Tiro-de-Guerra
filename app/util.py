"""Utilidades: normalização de nomes (para busca sem acento) e CPF."""
import re
import unicodedata


def norm_name(s: str) -> str:
    """Remove acentos, deixa em MAIÚSCULAS e colapsa espaços.
    Usado como chave de busca para não depender de acentuação/caixa."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def valid_cpf(cpf: str) -> bool:
    """Valida CPF (11 dígitos + dígitos verificadores)."""
    c = only_digits(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(c[n]) * ((i + 1) - n) for n in range(i))
        dig = (soma * 10) % 11
        dig = 0 if dig == 10 else dig
        if dig != int(c[i]):
            return False
    return True


def fmt_cpf(cpf: str) -> str:
    c = only_digits(cpf)
    if len(c) != 11:
        return cpf or "—"
    return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}"
