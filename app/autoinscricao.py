"""
Autoinscrição do atirador na etapa aberta — tirodeguerra.com.br/inscrever-me

O atirador lê o QR code na recepção, digita o CPF e:
- já cadastrado: presença marcada na etapa aberta;
- sem cadastro: informa o nome completo, o cadastro é criado e a presença
  marcada. A categoria (inicial da arma) é atribuída pelo RO na primeira
  passada, como em qualquer inscrição feita pela recepção.

Foto de perfil (galeria ou câmera, recortada em quadrado no próprio celular):
opcional nas duas primeiras etapas do mês; da 3ª etapa em diante, quem não
tem foto só fica inscrito depois de enviá-la (o pódio usa as fotos). A foto só
pode ser enviada para quem ainda não tem: trocar uma foto existente é feito
na área interna.

Para não expor dados de terceiros a quem digitar um CPF qualquer, a página
só mostra o primeiro nome. Há limite de tentativas por endereço de acesso.
"""
import time
from collections import defaultdict, deque

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, db, fotos, util, webdb
from .website import templates, MONTH_PT

router = APIRouter()

# a partir de qual etapa do mês a foto é obrigatória para se inscrever
PHOTO_REQUIRED_FROM_STAGE = 3

# limite simples em memória: N envios por janela, por endereço de acesso
_LIMIT, _WINDOW = 12, 600
_hits = defaultdict(deque)


def _too_many(request: Request) -> bool:
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > _WINDOW:
        q.popleft()
    q.append(now)
    return len(q) > _LIMIT


def _stage_label(conn, stage) -> str:
    month = db.get_month(conn, stage["month_id"])
    return f"{stage['number']}ª Etapa · {MONTH_PT[month['month']]}/{month['year']}"


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0].title()


def _already_in(conn, stage_id: int, shooter_id: int) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM stage_checkins WHERE stage_id=? AND shooter_id=? UNION "
        "SELECT 1 FROM enrollments WHERE stage_id=? AND shooter_id=? LIMIT 1",
        (stage_id, shooter_id, stage_id, shooter_id)).fetchone())


def _page(request, state: str, **ctx):
    ctx.update({"club": config.CLUB_NAME, "state": state})
    return templates.TemplateResponse(request, "inscrever.html", ctx)


@router.get("/inscrever-me", response_class=HTMLResponse)
async def inscrever_form(request: Request):
    conn = db.get_conn()
    try:
        stage = db.get_open_stage(conn)
        if not stage:
            return _page(request, "fechado")
        return _page(request, "cpf", stage=_stage_label(conn, stage))
    finally:
        conn.close()


@router.post("/inscrever-me", response_class=HTMLResponse)
async def inscrever_post(request: Request, cpf: str = Form(""), nome: str = Form("")):
    conn = db.get_conn()
    try:
        stage = db.get_open_stage(conn)
        if not stage:
            return _page(request, "fechado")
        label = _stage_label(conn, stage)
        if _too_many(request):
            return _page(request, "cpf", stage=label,
                         error="Muitas tentativas. Aguarde alguns minutos ou procure a recepção.")
        cpf_digits = util.only_digits(cpf)
        if not util.valid_cpf(cpf_digits):
            return _page(request, "cpf", stage=label, cpf=cpf,
                         error="CPF inválido. Confira os números.")

        sh = db.get_shooter_by_cpf(conn, cpf_digits)
        if not sh:
            nome = " ".join(nome.split())
            if not nome:
                # primeiro envio: CPF sem cadastro -> pede o nome
                return _page(request, "nome", stage=label, cpf=util.fmt_cpf(cpf_digits))
            if len(nome.split(" ")) < 2 or not all(
                    c.isalpha() or c in " '-." for c in nome):
                return _page(request, "nome", stage=label, cpf=util.fmt_cpf(cpf_digits), nome=nome,
                             error="Informe o nome completo (nome e sobrenome), só com letras.")
            # cadastro antigo com o mesmo nome e sem CPF: completa em vez de duplicar
            legacy = conn.execute(
                "SELECT * FROM shooters WHERE active=1 AND name_key=? AND "
                "(cpf IS NULL OR cpf='') ORDER BY id LIMIT 1",
                (util.norm_name(nome),)).fetchone()
            if legacy:
                db.update_shooter(conn, legacy["id"], cpf=cpf_digits)
                sid = legacy["id"]
            else:
                sid = db.create_shooter(conn, nome, cpf_digits)
            sh = db.get_shooter(conn, sid)

        already = _already_in(conn, stage["id"], sh["id"])
        need_photo = not fotos.has_photo(sh["id"])
        token = fotos.make_token(conn, sh["id"]) if need_photo else None
        first = _first_name(sh["name"])
        if need_photo and not already and stage["number"] >= PHOTO_REQUIRED_FROM_STAGE:
            # inscrição só é concluída quando a foto chega (ver inscrever_foto)
            conn.commit()
            return _page(request, "foto", stage=label, first=first, photo_token=token,
                         photo_required=True)
        if not already:
            webdb.checkin(conn, stage["id"], sh["id"], None)  # created_by NULL = autoinscrição
        conn.commit()
        return _page(request, "ja" if already else "ok", stage=label, first=first,
                     photo_token=token, photo_required=False)
    finally:
        conn.close()


@router.post("/inscrever-me/foto")
async def inscrever_foto(request: Request, token: str = Form(""), consent: str = Form(""),
                         foto: UploadFile = File(...)):
    conn = db.get_conn()
    try:
        if _too_many(request):
            return JSONResponse({"ok": False, "error": "Muitas tentativas. Aguarde alguns minutos."}, 429)
        sid = fotos.read_token(conn, token)
        if not sid or not db.get_shooter(conn, sid):
            return JSONResponse({"ok": False, "error": "Link expirado. Faça a inscrição de novo."}, 400)
        if fotos.has_photo(sid):
            return JSONResponse({"ok": False, "error": "Você já tem foto. Para trocar, fale com a recepção."}, 400)
        if consent != "1":
            return JSONResponse({"ok": False, "error": "Marque a autorização de uso da imagem."}, 400)
        data = await foto.read(fotos.MAX_BYTES + 1)
        try:
            fotos.save_photo(conn, sid, data, consent=True)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, 400)
        # conclui a inscrição quando a foto era obrigatória (ou se ainda faltava)
        stage = db.get_open_stage(conn)
        checked_in = False
        if stage and not _already_in(conn, stage["id"], sid):
            webdb.checkin(conn, stage["id"], sid, None)
            checked_in = True
        conn.commit()
        return {"ok": True, "checked_in": checked_in}
    finally:
        conn.close()
