"""Área interna — páginas de administração (etapas, categorias, usuários)."""
import logging
import os

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, Response

from . import config, db, texts, webdb, report
from . import standings as st
from .interno import need, render, back, stage_label
from .website import MONTH_PT

log = logging.getLogger("interno.admin")
router = APIRouter(prefix="/interno")


# ============================================================================
# INDICADORES (participação por etapa e por mês)
# ============================================================================
@router.get("/indicadores", response_class=HTMLResponse)
async def indicadores(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "admin")
        data = webdb.participation_stats(conn)
        # lista (não dict): o tojson do Jinja ordena chaves e trocaria a ordem das cores
        mods = [[m["code"], m["label"]] for m in db.list_modalities(conn)]
        for s in data["stages"]:
            s["label"] = f"{s['number']}ª {MONTH_PT[s['month']][:3]}/{str(s['year'])[2:]}"
        for m in data["months"]:
            m["label"] = f"{MONTH_PT[m['month']][:3]}/{str(m['year'])[2:]}"
            m["full"] = f"{MONTH_PT[m['month']]} {m['year']}"
        return render(request, conn, "indicadores.html", user, perms, data=data, mods=mods)
    finally:
        conn.close()


# ============================================================================
# ETAPAS / MÊS
# ============================================================================
@router.get("/etapas", response_class=HTMLResponse)
async def etapas(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "admin")
        month = db.ensure_current_month(conn)
        conn.commit()
        rows = []
        for s in db.list_stages(conn, month["id"]):
            rows.append({"s": s, "results": db.stage_result_count(conn, s["id"]),
                         "participants": len(webdb.stage_participants(conn, s["id"]))})
        closed = conn.execute(
            "SELECT * FROM months WHERE status='fechado' ORDER BY year DESC, month DESC "
            "LIMIT 12").fetchall()
        has_open = any(r["s"]["status"] == "aberta" for r in rows)
        return render(request, conn, "etapas.html", user, perms, month=month, rows=rows,
                      has_open=has_open, closed_months=closed, month_pt=MONTH_PT,
                      n_held=len(st.month_held_stages(conn, month["id"])),
                      best_n=st.monthly_best_n(conn, month["id"]))
    finally:
        conn.close()


@router.post("/etapas/nova")
async def etapa_nova(request: Request, date: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if db.get_open_stage(conn):
            return back("/interno/etapas", "Já existe uma etapa aberta. Feche-a antes.")
        month = db.ensure_current_month(conn)
        iso = texts.parse_date(date) if date else None
        s = db.create_stage(conn, month["id"], iso)
        conn.commit()
        label = stage_label(conn, s)
    finally:
        conn.close()
    return back("/interno/etapas", f"{label} aberta.")


@router.post("/etapas/{stage_id}/fechar")
async def etapa_fechar(request: Request, stage_id: int):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        db.close_stage(conn, stage_id)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/etapas", "Etapa fechada. Baixe os PDFs pelos botões da etapa.")


@router.post("/etapas/{stage_id}/reabrir")
async def etapa_reabrir(request: Request, stage_id: int):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if db.get_open_stage(conn):
            return back("/interno/etapas", "Já existe uma etapa aberta.")
        s = db.get_stage(conn, stage_id)
        m = db.get_month(conn, s["month_id"]) if s else None
        if not m or m["status"] != "aberto":
            return back("/interno/etapas", "Só é possível reabrir etapas do mês aberto.")
        db.reopen_stage(conn, stage_id)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/etapas", "Etapa reaberta.")


@router.post("/etapas/{stage_id}/data")
async def etapa_data(request: Request, stage_id: int, date: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        iso = texts.parse_date(date)
        if not iso:
            return back("/interno/etapas", "Data inválida (use DD/MM/AAAA).")
        db.update_stage_date(conn, stage_id, iso)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/etapas", "Data alterada.")


@router.post("/etapas/{stage_id}/excluir")
async def etapa_excluir(request: Request, stage_id: int):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        db.delete_stage(conn, stage_id)
        conn.execute("DELETE FROM stage_checkins WHERE stage_id=?", (stage_id,))
        conn.commit()
    finally:
        conn.close()
    return back("/interno/etapas", "Etapa excluída.")


@router.get("/etapas/{stage_id}/pdf")
async def etapa_pdf(request: Request, stage_id: int, tipo: str = "etapa"):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if tipo == "geral":
            pdf = report.build_stage_general_pdf(conn, stage_id)
            name = report.stage_general_pdf_filename(conn, stage_id)
        else:
            pdf = report.build_stage_pdf(conn, stage_id)
            name = report.stage_pdf_filename(conn, stage_id)
    finally:
        conn.close()
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/mes/fechar", response_class=HTMLResponse)
async def mes_fechar(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "admin")
        month = db.get_open_month(conn)
        prop = st.promotion_proposal(conn, month["id"])
        for m in prop["moves"]:
            m["modality_label"] = texts.modality_label(m.get("modality", ""))
        return render(request, conn, "mes_fechar.html", user, perms, month=month,
                      moves=prop["moves"], warnings=prop["warnings"], month_pt=MONTH_PT,
                      open_stage_exists=bool(db.get_open_stage(conn)))
    finally:
        conn.close()


@router.post("/mes/fechar")
async def mes_fechar_post(request: Request):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        month = db.get_open_month(conn)
        if db.get_open_stage(conn):
            return back("/interno/etapas", "Feche a etapa aberta antes de fechar o mês.")
        prop = st.promotion_proposal(conn, month["id"])
        st.apply_promotions(conn, prop["moves"])
        conn.execute("UPDATE months SET status='fechado' WHERE id=?", (month["id"],))
        conn.commit()
        db.ensure_current_month(conn)
        conn.commit()
        mid = month["id"]
    finally:
        conn.close()
    return back("/interno/etapas",
                f"Mês fechado e categorias atualizadas. PDFs em 'Meses fechados' (id {mid}).")


@router.get("/mes/{month_id}/pdf")
async def mes_pdf(request: Request, month_id: int, tipo: str = "mensal"):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if tipo == "geral":
            pdf = report.build_month_general_pdf(conn, month_id)
            name = report.month_general_pdf_filename(conn, month_id)
        else:
            moves = st.promotion_proposal(conn, month_id)["moves"] \
                if db.get_month(conn, month_id)["status"] == "aberto" else []
            pdf = report.build_month_pdf(conn, month_id, moves)
            name = report.month_pdf_filename(conn, month_id)
    finally:
        conn.close()
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/mes/limpar")
async def mes_limpar(request: Request, confirm: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if confirm != "LIMPAR":
            return back("/interno/etapas", "Digite LIMPAR para confirmar.")
        month = db.get_open_month(conn)
        for s in db.list_stages(conn, month["id"]):
            conn.execute("DELETE FROM stage_checkins WHERE stage_id=?", (s["id"],))
        db.wipe_month(conn, month["id"])
        conn.commit()
    finally:
        conn.close()
    return back("/interno/etapas", "Mês limpo: etapas, inscrições e resultados apagados.")


# ============================================================================
# CATEGORIAS / MODALIDADES
# ============================================================================
@router.get("/categorias", response_class=HTMLResponse)
async def categorias(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "admin")
        mods = []
        for m in db.list_modalities(conn):
            cats = []
            for c in sorted(db.list_categories(conn, m["code"]), key=lambda c: -c["rank"]):
                cats.append({"c": c, "members": db.category_member_count(conn, c["id"]),
                             "in_use": db.category_in_use(conn, c["id"])})
            mods.append({"m": m, "cats": cats, "in_use": db.modality_in_use(conn, m["code"])})
        orphans = webdb.orphan_memberships(conn)
        for o in orphans:
            o["label"] = texts.modality_label(o["modality"])
        return render(request, conn, "categorias.html", user, perms, mods=mods, orphans=orphans)
    finally:
        conn.close()


@router.post("/categorias/limpar-sem-uso")
async def categorias_limpar(request: Request, modality: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if not db.get_modality(conn, modality):
            return back("/interno/categorias", "Modalidade inválida.")
        path = webdb.backup_db(conn, os.path.join(os.path.dirname(config.DB_PATH), "backups"))
        n = webdb.clean_orphan_memberships(conn, modality)
        conn.commit()
        log.info("Limpeza de vínculos sem uso (%s): %d removido(s). Backup em %s",
                 modality, n, path)
        return back("/interno/categorias",
                    f"{texts.modality_label(modality)}: {n} cadastro(s) sem uso removido(s). Backup feito antes.")
    finally:
        conn.close()


@router.post("/modalidades/nova")
async def modalidade_nova(request: Request, label: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if len(label.strip()) < 2:
            return back("/interno/categorias", "Nome inválido.")
        db.add_modality(conn, label)
        conn.commit()
        _refresh_labels(conn)
    finally:
        conn.close()
    return back("/interno/categorias", "Modalidade criada. Cadastre ao menos uma categoria.")


@router.post("/modalidades/{code}/renomear")
async def modalidade_renomear(request: Request, code: str, label: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        db.rename_modality(conn, code, label)
        conn.commit()
        _refresh_labels(conn)
    finally:
        conn.close()
    return back("/interno/categorias", "Modalidade renomeada.")


@router.post("/modalidades/{code}/excluir")
async def modalidade_excluir(request: Request, code: str):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if db.modality_in_use(conn, code):
            return back("/interno/categorias", "Modalidade em uso — exclua as categorias antes.")
        db.delete_modality(conn, code)
        conn.commit()
        _refresh_labels(conn)
    finally:
        conn.close()
    return back("/interno/categorias", "Modalidade excluída.")


@router.post("/categorias/nova")
async def categoria_nova(request: Request, modality: str = Form(...), name: str = Form(""),
                         max_shooters: str = Form(""), promote_n: int = Form(0),
                         demote_n: int = Form(0)):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if len(name.strip()) < 2:
            return back("/interno/categorias", "Nome inválido.")
        mx = int(max_shooters) if max_shooters.strip().isdigit() else None
        db.add_category(conn, name.strip(), modality, mx, promote_n, demote_n)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/categorias", "Categoria criada.")


@router.post("/categorias/{cat_id}/editar")
async def categoria_editar(request: Request, cat_id: int, name: str = Form(""),
                           max_shooters: str = Form(""), promote_n: int = Form(0),
                           demote_n: int = Form(0)):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        mx = int(max_shooters) if max_shooters.strip().isdigit() else None
        db.update_category(conn, cat_id, name=name if name.strip() else None,
                           max_shooters=mx, promote_n=promote_n, demote_n=demote_n)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/categorias", "Categoria salva.")


@router.post("/categorias/{cat_id}/acao")
async def categoria_acao(request: Request, cat_id: int, acao: str = Form(...)):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        msg = "Feito."
        if acao in ("up", "down"):
            db.move_category_rank(conn, cat_id, acao)
        elif acao == "entry":
            db.set_entry_category(conn, cat_id)
            msg = "Categoria inicial definida."
        elif acao == "delete":
            if db.category_in_use(conn, cat_id):
                return back("/interno/categorias", "Categoria em uso — não pode ser excluída.")
            db.delete_category(conn, cat_id)
            msg = "Categoria excluída."
        conn.commit()
    finally:
        conn.close()
    return back("/interno/categorias", msg)


def _refresh_labels(conn):
    texts.set_modality_labels({r["code"]: r["label"] for r in
                               conn.execute("SELECT * FROM modalities")})
    texts.set_role_labels({r["code"]: r["label"] for r in
                           conn.execute("SELECT * FROM roles")})


# ============================================================================
# USUÁRIOS / CARGOS
# ============================================================================
@router.get("/usuarios", response_class=HTMLResponse)
async def usuarios(request: Request):
    conn = db.get_conn()
    try:
        user, perms = need(request, conn, "admin")
        roles = []
        for r in db.list_roles(conn):
            roles.append({"r": r, "perms": db.role_perms(conn, r["code"]),
                          "members": db.role_member_count(conn, r["code"]) + conn.execute(
                              "SELECT COUNT(*) AS n FROM web_users WHERE role=? AND active=1",
                              (r["code"],)).fetchone()["n"]})
        return render(request, conn, "usuarios.html", user, perms,
                      users=webdb.list_users(conn), roles=roles,
                      permissions=db.PERMISSIONS)
    finally:
        conn.close()


@router.post("/usuarios/novo")
async def usuario_novo(request: Request, username: str = Form(""), name: str = Form(""),
                       password: str = Form(""), role: str = Form("ro")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        u = username.strip().lower()
        if len(u) < 3 or not u.replace(".", "").replace("_", "").isalnum():
            return back("/interno/usuarios", "Usuário inválido (letras, números, ponto ou _).")
        if len(password) < 6:
            return back("/interno/usuarios", "Senha muito curta (mínimo 6).")
        if webdb.get_user_by_username(conn, u):
            return back("/interno/usuarios", "Usuário já existe.")
        if not db.get_role(conn, role):
            return back("/interno/usuarios", "Cargo inválido.")
        webdb.create_user(conn, u, name or u, password, role)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/usuarios", f"Usuário {u} criado.")


@router.post("/usuarios/{uid}/editar")
async def usuario_editar(request: Request, uid: int, name: str = Form(""),
                         role: str = Form(""), password: str = Form(""),
                         active: str = Form("1")):
    conn = db.get_conn()
    try:
        me, _ = need(request, conn, "admin")
        if password and len(password) < 6:
            return back("/interno/usuarios", "Senha muito curta (mínimo 6).")
        if uid == me["id"] and (active != "1" or role != "admin"):
            return back("/interno/usuarios", "Você não pode se rebaixar nem se desativar.")
        webdb.update_user(conn, uid, name=name or None, role=role or None,
                          password=password or None, active=1 if active == "1" else 0)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/usuarios", "Usuário salvo.")


@router.post("/cargos/novo")
async def cargo_novo(request: Request, label: str = Form("")):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if len(label.strip()) < 2:
            return back("/interno/usuarios", "Nome inválido.")
        db.add_role(conn, label)
        conn.commit()
        _refresh_labels(conn)
    finally:
        conn.close()
    return back("/interno/usuarios", "Cargo criado — marque as permissões dele.")


@router.post("/cargos/{code}/perm")
async def cargo_perm(request: Request, code: str, perm: str = Form(...)):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        if code == "admin":
            return back("/interno/usuarios", "Administrador tem todas as permissões.")
        db.toggle_role_perm(conn, code, perm)
        conn.commit()
    finally:
        conn.close()
    return back("/interno/usuarios")


@router.post("/cargos/{code}/excluir")
async def cargo_excluir(request: Request, code: str):
    conn = db.get_conn()
    try:
        need(request, conn, "admin")
        r = db.get_role(conn, code)
        if not r or r["builtin"]:
            return back("/interno/usuarios", "Cargos padrão não podem ser excluídos.")
        n = db.role_member_count(conn, code) + conn.execute(
            "SELECT COUNT(*) AS n FROM web_users WHERE role=?", (code,)).fetchone()["n"]
        if n:
            return back("/interno/usuarios", "Há membros neste cargo — mova-os antes.")
        db.delete_role(conn, code)
        conn.commit()
        _refresh_labels(conn)
    finally:
        conn.close()
    return back("/interno/usuarios", "Cargo excluído.")
