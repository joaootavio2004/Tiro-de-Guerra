"""
Área interna — admin.tirodeguerra.com.br (rotas registradas sob /interno;
app/hosts.py mapeia o subdomínio para elas e gera links sem o prefixo).

Operação do clube em páginas, com foco em automatizar a linha de tiro
(timer Special Pie por Bluetooth + impressora térmica). Usa as mesmas regras
(standings/scoring/report) do site público.

Cargos e permissões vêm da tabela `roles`:
  inscrever   -> inscrições da etapa e lista de passadas (cobrança)
  resultados  -> linha de tiro e lançamentos
  atiradores  -> cadastro/edição de atiradores
  admin       -> tudo (etapas, categorias, usuários)
"""
import logging
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Request, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from markupsafe import Markup

from . import config, db, fotos, texts, util, webdb
from . import standings as st
from .website import templates, MONTH_PT, _fmt_date

log = logging.getLogger("interno")
router = APIRouter(prefix="/interno")

COOKIE = "tg_session"


def _icon(name: str) -> Markup:
    """Ícone do sprite static/icons.svg, para usar nos templates: {{ ic('trash') }}"""
    kit = " k" if name.startswith("k-") else ""
    return Markup(f'<svg class="ic{kit}" aria-hidden="true"><use href="/static/icons.svg#{name}"/></svg>')


templates.env.globals["ic"] = _icon

NAV = [
    # (perm, href, label, ícone em static/icons.svg, grupo na barra lateral)
    ("resultados", "/linha", "Linha de tiro", "k-target", "Operação"),
    ("resultados", "/lancamentos", "Lançamentos", "list", "Operação"),
    ("inscrever", "/inscricoes", "Inscrições", "k-dogtag", "Operação"),
    ("atiradores", "/atiradores", "Atiradores", "k-soldier", "Cadastro"),
    (None, "/classificacao", "Classificação", "k-medal", "Cadastro"),
    ("admin", "/indicadores", "Indicadores", "chart", "Administração"),
    ("admin", "/etapas", "Etapas", "calendar", "Administração"),
    ("admin", "/categorias", "Categorias", "k-rank", "Administração"),
    ("admin", "/usuarios", "Usuários", "users", "Administração"),
]


# ============================================================================
# Autenticação
# ============================================================================
def current_user(request: Request, conn):
    return webdb.user_by_session(conn, request.cookies.get(COOKIE))


def perms_of(conn, user) -> set:
    if not user:
        return set()
    if user["role"] == "admin":
        return {p for p, _ in db.PERMISSIONS} | {"admin"}
    return db.role_perms(conn, user["role"])


def public_path(request: Request) -> str:
    """Caminho como o usuário vê (sem o /interno interno do roteador)."""
    p = getattr(request.state, "public_path", None)
    if p is not None:
        return p
    path = request.url.path
    return path[len("/interno"):] or "/" if path.startswith("/interno") else path


def need(request: Request, conn, perm: Optional[str] = None):
    """Devolve (user, perms) ou interrompe com redirect (página) / 401 (API)."""
    user = current_user(request, conn)
    is_api = request.url.path.startswith("/interno/api/")
    if not user:
        if is_api:
            raise HTTPException(401, "não autenticado")
        raise HTTPException(303, headers={
            "Location": "/login?next=" + quote(public_path(request))})
    perms = perms_of(conn, user)
    if perm and perm not in perms:
        if is_api:
            raise HTTPException(403, "sem permissão")
        raise HTTPException(303, headers={"Location": "/?msg=sem+permiss%C3%A3o"})
    return user, perms


def render(request, conn, template, user, perms, **ctx):
    stage = db.get_open_stage(conn)
    month = db.get_open_month(conn)
    ctx.update({
        "club": config.CLUB_NAME,
        "user": user,
        "perms": perms,
        "nav": [n for n in NAV if n[0] is None or n[0] in perms],
        "path": public_path(request),
        "open_stage": stage,
        "open_stage_label": stage_label(conn, stage) if stage else None,
        "open_month_label": f"{MONTH_PT[month['month']]} {month['year']}" if month else "",
        "msg": request.query_params.get("msg", ""),
        "role_label": texts.role_label,
        "modality_label": texts.modality_label,
        "fmt_date": texts.fmt_date,
        "fmt_cpf": util.fmt_cpf,
    })
    return templates.TemplateResponse(request, f"interno/{template}", ctx)


def stage_label(conn, stage) -> str:
    month = db.get_month(conn, stage["month_id"])
    return f"{stage['number']}ª Etapa · {MONTH_PT[month['month']]}/{month['year']}"


def back(url: str, msg: str = "") -> RedirectResponse:
    if msg:
        url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
    return RedirectResponse(url, status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    conn = db.get_conn()
    try:
        if current_user(request, conn):
            return RedirectResponse("/", status_code=303)
    finally:
        conn.close()
    return templates.TemplateResponse(request, "interno/login.html", {
        "club": config.CLUB_NAME, "error": request.query_params.get("e", ""),
        "next": request.query_params.get("next", "/")})


@router.post("/login")
async def login_post(request: Request, username: str = Form(""),
                     password: str = Form(""), next: str = Form("/")):
    conn = db.get_conn()
    try:
        u = webdb.get_user_by_username(conn, username)
        if not u or not u["active"] or not webdb.check_password(password, u["password_hash"]):
            return RedirectResponse("/login?e=1", status_code=303)
        token = webdb.create_session(conn, u["id"])
        conn.commit()
    finally:
        conn.close()
    # só caminho local: "//outro-site" e "/\outro" seriam redirecionamento externo
    dest = next if next.startswith("/") and not next.startswith(("//", "/\\")) else "/"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie(COOKIE, token, max_age=webdb.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


@router.post("/logout")
async def logout(request: Request):
    conn = db.get_conn()
    try:
        webdb.delete_session(conn, request.cookies.get(COOKIE, ""))
        conn.commit()
    finally:
        conn.close()
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ============================================================================
# Início
# ============================================================================
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn)
        stage = db.get_open_stage(conn)
        cards = []
        if stage:
            parts = webdb.stage_participants(conn, stage["id"])
            cards = [
                ("Inscritos na etapa", len(parts)),
                ("Já passaram", sum(1 for p in parts if p["runs"])),
                ("Aguardando", sum(1 for p in parts if not p["runs"])),
                ("Passadas lançadas", sum(p["runs"] for p in parts)),
            ]
        return render(request, conn, "home.html", user, perms, cards=cards)
    finally:
        conn.close()


# ============================================================================
# LINHA DE TIRO — lançamento com timer e impressora
# ============================================================================
@router.get("/linha", response_class=HTMLResponse)
async def linha(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "resultados")
        mods = [{"code": m["code"], "label": m["label"]} for m in db.list_modalities(conn)]
        return render(request, conn, "linha.html", user, perms, modalities=mods,
                      site_url=config.SITE_URL)
    finally:
        conn.close()


@router.get("/api/linha/dados")
async def api_linha_dados(request: Request):
    conn = db.get_conn()
    try:
        need(request, conn)
        stage = db.get_open_stage(conn)
        if not stage:
            return {"stage": None, "participants": []}
        return {
            "stage": {"id": stage["id"], "label": stage_label(conn, stage),
                      "date": texts.fmt_date(stage["date"])},
            "participants": webdb.stage_participants(conn, stage["id"]),
        }
    finally:
        conn.close()


def _resolve_category(conn, stage, shooter_id: int, modality: str, persist: bool = True):
    """Categoria atual do atirador (ou a inicial da
    modalidade), congelada no mês.
    persist=False só consulta: não cadastra o atirador na modalidade nem cria
    o snapshot do mês (usado ao abrir o atirador na linha, antes de existir
    passada naquela arma)."""
    month = db.get_month(conn, stage["month_id"])
    current = db.get_shooter_category(conn, shooter_id, modality)
    if not current:
        current = db.default_category_id(conn, modality)
        if current and persist:
            db.set_shooter_category(conn, shooter_id, modality, current)
    if not current:
        return None
    if persist:
        cat_id = db.month_category_id(conn, month["id"], shooter_id, modality, current)
    elif month["status"] == "aberto":
        cat_id = current  # mês aberto: a categoria atual é a que vale
    else:
        row = conn.execute(
            "SELECT category_id FROM month_category "
            "WHERE month_id=? AND shooter_id=? AND modality=?",
            (month["id"], shooter_id, modality)).fetchone()
        cat_id = row["category_id"] if row else current
    return db.get_category(conn, cat_id)


@router.get("/api/linha/atirador/{shooter_id}")
async def api_linha_atirador(request: Request, shooter_id: int):
    conn = db.get_conn()
    try:
        need(request, conn, "resultados")
        stage = db.get_open_stage(conn)
        sh = db.get_shooter(conn, shooter_id)
        if not stage or not sh:
            raise HTTPException(404)
        cats = {}
        for m in db.list_modalities(conn):
            c = _resolve_category(conn, stage, shooter_id, m["code"], persist=False)
            cats[m["code"]] = {"id": c["id"], "name": c["name"]} if c else None
        # passadas de toda a vida por arma: a tela do RO usa para sugerir a arma
        # habitual e avisar quando é a primeira vez do atirador naquela arma
        hist = conn.execute(
            "SELECT e.modality, COUNT(ru.id) AS n FROM enrollments e "
            "JOIN runs ru ON ru.enrollment_id=e.id WHERE e.shooter_id=? "
            "GROUP BY e.modality", (shooter_id,)).fetchall()
        return {
            "shooter": {"id": sh["id"], "name": sh["name"],
                        "photo": (f"/foto/{sh['id']}?v={sh['photo_at']}"
                                  if fotos.has_photo(sh["id"]) else None)},
            "categories": cats,
            "history": {h["modality"]: h["n"] for h in hist},
            "runs": webdb.shooter_stage_runs(conn, stage["id"], shooter_id),
            "stage_label": stage_label(conn, stage),
        }
    finally:
        conn.close()


@router.post("/api/linha/salvar")
async def api_linha_salvar(request: Request):
    body = await request.json()
    conn = db.get_conn()
    try:
        user, _ = need(request, conn, "resultados")
        stage = db.get_open_stage(conn)
        if not stage:
            raise HTTPException(400, "não há etapa aberta")
        shooter_id = int(body.get("shooter_id"))
        modality = str(body.get("modality") or "")
        if not db.get_modality(conn, modality):
            raise HTTPException(400, "modalidade inválida")
        dq = bool(body.get("dq"))
        raw = body.get("raw_time")
        try:
            raw = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "tempo inválido")
        if not dq and (raw is None or raw <= 0):
            raise HTTPException(400, "tempo inválido")
        pens = [max(0, int(body.get(k) or 0)) for k in ("pen2", "pen5", "pen10")]
        shots = [float(t) for t in (body.get("shots") or []) if t is not None]
        segments = int(body.get("segments") or 1)
        source = "timer" if shots else "manual"

        cat = _resolve_category(conn, stage, shooter_id, modality)
        if not cat:
            raise HTTPException(400, "modalidade sem categoria cadastrada")
        enr = db.get_enrollment(conn, stage["id"], shooter_id, modality)
        if not enr:
            enr = db.enroll(conn, stage["id"], shooter_id, modality, cat["id"], 1, None)
        webdb.checkin(conn, stage["id"], shooter_id, user["id"])
        run_id = webdb.add_run_web(conn, enr["id"], raw, pens[0], pens[1], pens[2],
                                   dq, user["id"], source, shots, segments)
        conn.commit()
        r = db.get_run(conn, run_id)
        d = webdb.run_dict(conn, r)
        d["modality"] = modality
        d["category"] = cat["name"]
        sh = db.get_shooter(conn, shooter_id)
        # posição parcial na categoria (só para a tela do RO)
        cl = st.stage_classification(conn, stage["id"], modality, cat["id"])
        pos = next((x["pos"] for x in cl if x["shooter_id"] == shooter_id), None)
        best = db.best_run(conn, enr["id"])
        return {"ok": True, "run": d, "shooter": sh["name"],
                "stage_label": stage_label(conn, stage), "pos": pos,
                "n_in_cat": len(cl), "best": best["final_time"] if best else None,
                "runs_total": db.runs_count(conn, enr["id"])}
    finally:
        conn.close()


# ============================================================================
# LANÇAMENTOS — consultar / apagar / reimprimir
# ============================================================================
@router.get("/lancamentos", response_class=HTMLResponse)
async def lancamentos(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "resultados")
        stage = db.get_open_stage(conn)
        rows = []
        if stage:
            for p in webdb.stage_participants(conn, stage["id"]):
                if p["runs"]:
                    rows.append({"shooter": p,
                                 "runs": webdb.shooter_stage_runs(conn, stage["id"], p["id"])})
        mods = [{"code": m["code"], "label": m["label"]} for m in db.list_modalities(conn)]
        return render(request, conn, "lancamentos.html", user, perms, rows=rows,
                      site_url=config.SITE_URL, modalities=mods)
    finally:
        conn.close()


@router.post("/lancamentos/apagar")
async def lancamentos_apagar(request: Request, run_id: int = Form(...)):
    conn = db.get_conn()
    try:
        need(request, conn, "resultados")
        webdb.delete_run_web(conn, run_id)
        conn.commit()
    finally:
        conn.close()
    return back("/lancamentos", "Passada apagada.")


# ============================================================================
# INSCRIÇÕES — presença na etapa (sem arma, sem quantidade) + cobrança
# ============================================================================
@router.get("/inscricoes", response_class=HTMLResponse)
async def inscricoes(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "inscrever")
        stage = db.get_open_stage(conn)
        parts = webdb.stage_participants(conn, stage["id"]) if stage else []
        mods = [{"code": m["code"], "label": m["label"]} for m in db.list_modalities(conn)]
        return render(request, conn, "inscricoes.html", user, perms,
                      participants=parts, modalities=mods,
                      can_create="atiradores" in perms)
    finally:
        conn.close()


@router.get("/api/atiradores/busca")
async def api_busca(request: Request, q: str = ""):
    conn = db.get_conn()
    try:
        need(request, conn)
        stage = db.get_open_stage(conn)
        ids = set()
        if stage:
            ids = {p["id"] for p in webdb.stage_participants(conn, stage["id"])}
        rows = db.search_shooters(conn, q, limit=12) if q.strip() else []
        return [{"id": r["id"], "name": r["name"],
                 "cpf": util.fmt_cpf(r["cpf"]) if r["cpf"] else "",
                 "has_cpf": bool(r["cpf"]), "checked_in": r["id"] in ids}
                for r in rows]
    finally:
        conn.close()


@router.post("/api/inscricoes/checkin")
async def api_checkin(request: Request):
    body = await request.json()
    conn = db.get_conn()
    try:
        user, _ = need(request, conn, "inscrever")
        stage = db.get_open_stage(conn)
        if not stage:
            raise HTTPException(400, "não há etapa aberta")
        sid = int(body["shooter_id"])
        if body.get("remove"):
            if db.shooter_stage_enrollments(conn, stage["id"], sid) and any(
                    db.has_any_run(conn, e["id"]) for e in
                    db.shooter_stage_enrollments(conn, stage["id"], sid)):
                raise HTTPException(400, "já tem passada lançada — apague pelo RO")
            for e in db.shooter_stage_enrollments(conn, stage["id"], sid):
                db.delete_enrollment(conn, e["id"])
            webdb.remove_checkin(conn, stage["id"], sid)
        else:
            sh = db.get_shooter(conn, sid)
            if not sh:
                raise HTTPException(404, "atirador não encontrado")
            cpf = (body.get("cpf") or "").strip()
            if not sh["cpf"]:
                # CPF é obrigatório para marcar presença — é o que evita
                # cadastros duplicados. Cadastro antigo sem CPF é completado aqui.
                if not cpf:
                    return {"ok": False, "need_cpf": True, "shooter_id": sid,
                            "name": sh["name"]}
                if not util.valid_cpf(cpf):
                    raise HTTPException(400, "CPF inválido")
                other = db.get_shooter_by_cpf(conn, cpf)
                if other and other["id"] != sid:
                    raise HTTPException(
                        400, f"Este CPF já é de {other['name']} — use o cadastro dele.")
                db.update_shooter(conn, sid, cpf=cpf)
            webdb.checkin(conn, stage["id"], sid, user["id"])
        conn.commit()
        return {"ok": True, "participants": webdb.stage_participants(conn, stage["id"])}
    finally:
        conn.close()


@router.post("/api/atiradores/novo")
async def api_novo_atirador(request: Request):
    body = await request.json()
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "atiradores")
        name = (body.get("name") or "").strip()
        cpf = (body.get("cpf") or "").strip()
        if len(name) < 3:
            raise HTTPException(400, "nome muito curto")
        if body.get("checkin") and not cpf:
            raise HTTPException(400, "CPF é obrigatório para inscrever")
        if cpf and not util.valid_cpf(cpf):
            raise HTTPException(400, "CPF inválido")
        if cpf:
            other = db.get_shooter_by_cpf(conn, cpf)
            if other:
                raise HTTPException(
                    400, f"Este CPF já está cadastrado para {other['name']}.")
        dup = [r for r in db.search_shooters(conn, name, 3)
               if r["name_key"] == util.norm_name(name)]
        if dup and not body.get("force"):
            return {"ok": False, "duplicate": {"id": dup[0]["id"], "name": dup[0]["name"]}}
        sid = db.create_shooter(conn, name, cpf or None)
        stage = db.get_open_stage(conn)
        if stage and body.get("checkin", True) and "inscrever" in perms:
            webdb.checkin(conn, stage["id"], sid, user["id"])
        conn.commit()
        return {"ok": True, "id": sid, "name": db.get_shooter(conn, sid)["name"]}
    finally:
        conn.close()


@router.get("/api/inscricoes/cobranca")
async def api_cobranca(request: Request):
    conn = db.get_conn()
    try:
        need(request, conn, "inscrever")
        stage = db.get_open_stage(conn)
        return webdb.billing_list(conn, stage["id"]) if stage else []
    finally:
        conn.close()


# ============================================================================
# ATIRADORES — cadastro / edição / categoria / mesclar / excluir
# ============================================================================
@router.get("/atiradores", response_class=HTMLResponse)
async def atiradores(request: Request, q: str = ""):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "atiradores")
        if q.strip():
            rows = db.search_shooters(conn, q, limit=50)
        else:
            rows = conn.execute("SELECT * FROM shooters WHERE active=1 ORDER BY name").fetchall()
        dups = db.duplicate_groups(conn)
        return render(request, conn, "atiradores.html", user, perms, rows=rows, q=q,
                      dups=dups, total=len(rows))
    finally:
        conn.close()


@router.post("/atiradores/novo")
async def atiradores_novo(request: Request, name: str = Form(""), cpf: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "atiradores")
        if len(name.strip()) < 3:
            return back("/atiradores", "Nome muito curto.")
        if cpf.strip() and not util.valid_cpf(cpf):
            return back("/atiradores", "CPF inválido.")
        sid = db.create_shooter(conn, name, cpf.strip() or None)
        conn.commit()
    finally:
        conn.close()
    return back(f"/atiradores/{sid}", "Atirador cadastrado.")


@router.get("/atiradores/{sid}", response_class=HTMLResponse)
async def atirador_detail(request: Request, sid: int):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "atiradores")
        sh = db.get_shooter(conn, sid)
        if not sh:
            return back("/atiradores", "Atirador não encontrado.")
        mods = []
        for m in db.list_modalities(conn):
            cid = db.get_shooter_category(conn, sid, m["code"])
            cat = db.get_category(conn, cid) if cid else None
            mods.append({"code": m["code"], "label": m["label"],
                         "category": cat["name"] if cat else "— (inicial ao inscrever)",
                         "category_id": cid,
                         "options": db.list_categories(conn, m["code"])})
        hist = webdb.shooter_history(conn, sid)
        return render(request, conn, "atirador.html", user, perms, sh=sh, mods=mods,
                      n_enroll=db.count_shooter_enrollments(conn, sid), hist=hist[:20],
                      month_pt=MONTH_PT, has_photo=fotos.has_photo(sid))
    finally:
        conn.close()


@router.get("/foto/{sid}")
async def foto_atirador(request: Request, sid: int):
    """Foto de perfil, só para quem está logado na área interna."""
    conn = db.get_conn()
    try:
        need(request, conn)
    finally:
        conn.close()
    if not fotos.has_photo(sid):
        raise HTTPException(404)
    return FileResponse(fotos.path_for(sid), media_type="image/webp",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.post("/atiradores/{sid}/foto")
async def atirador_foto(request: Request, sid: int, acao: str = Form("enviar"),
                        consent: str = Form(""), foto: UploadFile = File(None)):
    conn = db.get_conn()
    try:
        need(request, conn, "atiradores")
        if not db.get_shooter(conn, sid):
            return back("/atiradores", "Atirador não encontrado.")
        if acao == "remover":
            fotos.delete_photo(conn, sid)
            conn.commit()
            return back(f"/atiradores/{sid}", "Foto removida.")
        if consent != "1":
            return back(f"/atiradores/{sid}", "Confirme que o atirador autorizou o uso da foto.")
        if foto is None:
            return back(f"/atiradores/{sid}", "Escolha uma foto.")
        try:
            fotos.save_photo(conn, sid, await foto.read(fotos.MAX_BYTES + 1), consent=True)
        except ValueError as e:
            return back(f"/atiradores/{sid}", str(e))
        conn.commit()
        return back(f"/atiradores/{sid}", "Foto atualizada.")
    finally:
        conn.close()


@router.post("/atiradores/{sid}/editar")
async def atirador_editar(request: Request, sid: int, name: str = Form(""),
                          cpf: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "atiradores")
        if len(name.strip()) < 3:
            return back(f"/atiradores/{sid}", "Nome muito curto.")
        if cpf.strip() and not util.valid_cpf(cpf):
            return back(f"/atiradores/{sid}", "CPF inválido.")
        other = db.get_shooter_by_cpf(conn, cpf) if cpf.strip() else None
        if other and other["id"] != sid:
            return back(f"/atiradores/{sid}", "CPF já usado por outro atirador.")
        db.update_shooter(conn, sid, name=name, cpf=cpf.strip())
        conn.commit()
    finally:
        conn.close()
    return back(f"/atiradores/{sid}", "Dados salvos.")


@router.post("/atiradores/{sid}/categoria")
async def atirador_categoria(request: Request, sid: int, modality: str = Form(...),
                             category_id: int = Form(...)):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        moved = db.change_shooter_category_open_month(conn, sid, modality, category_id)
        conn.commit()
    finally:
        conn.close()
    return back(f"/atiradores/{sid}",
                f"Categoria alterada ({moved} inscrição(ões) do mês aberto movida(s)).")


@router.post("/atiradores/{sid}/mesclar")
async def atirador_mesclar(request: Request, sid: int, dst_id: int = Form(...)):
    conn = db.get_conn()
    try:
        need(request, conn, "atiradores")
        if not db.get_shooter(conn, dst_id) or dst_id == sid:
            return back(f"/atiradores/{sid}", "Destino inválido.")
        db.merge_shooters(conn, sid, dst_id)
        conn.commit()
    finally:
        conn.close()
    return back(f"/atiradores/{dst_id}", "Cadastros mesclados.")


@router.post("/atiradores/{sid}/excluir")
async def atirador_excluir(request: Request, sid: int):
    conn = db.get_conn()
    try:
        need(request, conn, "atiradores")
        db.delete_shooter(conn, sid)
        conn.commit()
    finally:
        conn.close()
    return back("/atiradores", "Atirador excluído.")


# ============================================================================
# CLASSIFICAÇÃO — etapa aberta e mês
# ============================================================================
@router.get("/classificacao", response_class=HTMLResponse)
async def classificacao(request: Request, mes: int = None):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn)
        month = db.get_month(conn, mes) if mes else db.get_open_month(conn)
        months = conn.execute("SELECT * FROM months ORDER BY year DESC, month DESC").fetchall()
        stage = db.get_open_stage(conn)
        stage_secs, month_secs = [], []
        for m in db.list_modalities(conn):
            cats = sorted(db.list_categories(conn, m["code"]), key=lambda c: -c["rank"])
            for c in cats:
                if stage:
                    rows = st.stage_classification(conn, stage["id"], m["code"], c["id"])
                    if rows:
                        stage_secs.append({"modality": m["label"],
                                           "category": c["name"] if len(cats) > 1 else None,
                                           "rows": rows})
                t = st.monthly_table(conn, month["id"], m["code"], c["id"])
                if t["rows"]:
                    month_secs.append({"modality": m["label"],
                                       "category": c["name"] if len(cats) > 1 else None,
                                       "table": t})
        return render(request, conn, "classificacao.html", user, perms,
                      stage_secs=stage_secs, month_secs=month_secs,
                      months=months, month=month,
                      month_label=f"{MONTH_PT[month['month']]} {month['year']}",
                      month_pt=MONTH_PT, fmt_date_short=_fmt_date)
    finally:
        conn.close()
