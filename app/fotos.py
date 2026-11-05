"""
Fotos de perfil dos atiradores.

Arquivos em <pasta do banco>/fotos/<id>.webp (volume persistente). Toda foto
é reprocessada no servidor: orientação corrigida, recorte quadrado central,
512x512 e WebP; a regravação descarta metadados (EXIF, inclusive GPS).

O envio pela página pública usa um token assinado (HMAC) e de validade curta,
emitido logo após a inscrição, que só permite gravar a foto daquele atirador.
"""
import hashlib
import hmac
import io
import os
import secrets
import time
from typing import Optional

from . import config, db

SIZE = 512
MAX_BYTES = 12 * 1024 * 1024
TOKEN_TTL = 30 * 60  # 30 minutos para concluir o envio


def folder() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "fotos")
    os.makedirs(path, exist_ok=True)
    return path


def path_for(shooter_id: int) -> str:
    return os.path.join(folder(), f"{int(shooter_id)}.webp")


def has_photo(shooter_id: int) -> bool:
    return os.path.exists(path_for(shooter_id))


# ---------------------------------------------------------------- token
def _key(conn) -> bytes:
    """Chave de assinatura guardada no banco (criada na primeira vez)."""
    row = conn.execute("SELECT value FROM settings WHERE key='signing_key'").fetchone()
    if row:
        return row["value"].encode()
    k = secrets.token_hex(32)
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES ('signing_key',?)", (k,))
    conn.commit()
    return conn.execute("SELECT value FROM settings WHERE key='signing_key'").fetchone()["value"].encode()


def make_token(conn, shooter_id: int) -> str:
    exp = int(time.time()) + TOKEN_TTL
    msg = f"{int(shooter_id)}.{exp}"
    sig = hmac.new(_key(conn), msg.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{msg}.{sig}"


def read_token(conn, token: str) -> Optional[int]:
    """Id do atirador se o token for válido e não expirado; senão None."""
    try:
        sid, exp, sig = (token or "").split(".")
        msg = f"{int(sid)}.{int(exp)}"
    except ValueError:
        return None
    good = hmac.new(_key(conn), msg.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(good, sig) or int(exp) < time.time():
        return None
    return int(sid)


# ---------------------------------------------------------------- imagem
def save_photo(conn, shooter_id: int, data: bytes, consent: bool) -> None:
    """Valida, normaliza e grava a foto. Levanta ValueError com mensagem
    para o usuário se o arquivo não servir."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Arquivo vazio ou maior que 12 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Não consegui ler a imagem. Envie uma foto JPG ou PNG.")
    img = ImageOps.exif_transpose(img).convert("RGB")
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)
    tmp = path_for(shooter_id) + ".tmp"
    img.save(tmp, "WEBP", quality=82, method=6)
    os.replace(tmp, path_for(shooter_id))
    conn.execute("UPDATE shooters SET photo_at=?, photo_consent_at=? WHERE id=?",
                 (db.now(), db.now() if consent else None, shooter_id))


def delete_photo(conn, shooter_id: int) -> None:
    try:
        os.remove(path_for(shooter_id))
    except FileNotFoundError:
        pass
    conn.execute("UPDATE shooters SET photo_at=NULL, photo_consent_at=NULL WHERE id=?",
                 (shooter_id,))
