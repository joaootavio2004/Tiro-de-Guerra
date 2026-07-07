"""
Bot do Telegram — Tiro de Guerra.

Cada pessoa fala com o bot no privado. O acesso é por aprovação do admin.
Fluxos: inscrição (recepção/RO), lançamento de resultado (RO),
classificação e administração (equipe, categorias, etapas/mês).
"""
import calendar
import logging
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup)
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, ContextTypes, filters)

from . import config, db, texts, util
from . import standings as st

log = logging.getLogger("bot")

MONTH_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


# ============================================================================
# Helpers de papel / acesso
# ============================================================================
def role_of(telegram_id: int) -> str | None:
    conn = db.get_conn()
    try:
        if telegram_id in config.ADMIN_IDS:
            # garante registro do admin bootstrap
            if not db.get_staff(conn, telegram_id):
                db.upsert_staff(conn, telegram_id, "Administrador", "admin")
                conn.commit()
            return "admin"
        s = db.get_staff(conn, telegram_id)
        return s["role"] if s else None
    finally:
        conn.close()


def perms_of(role: str) -> set:
    """Permissões do cargo, lidas do banco (admin tem todas)."""
    if not role:
        return set()
    if role == "admin":
        return {p for p, _ in db.PERMISSIONS}
    conn = db.get_conn()
    try:
        return db.role_perms(conn, role)
    finally:
        conn.close()


def can_enroll(role): return role == "admin" or "inscrever" in perms_of(role)
def can_result(role): return role == "admin" or "resultados" in perms_of(role)
def can_shooters(role): return role == "admin" or "atiradores" in perms_of(role)
def is_admin(role): return role == "admin"


def kb(rows):
    return InlineKeyboardMarkup(rows)


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


# ============================================================================
# Menu principal
# ============================================================================
def main_menu_kb(role):
    perms = perms_of(role)
    rows = []
    if "inscrever" in perms:
        rows.append([btn("📝 Inscrever atirador", "enroll")])
    rows.append([btn("📋 Inscritos da etapa", "enrolled")])
    if "resultados" in perms:
        rows.append([btn("🎯 Lançar resultado", "result")])
        rows.append([btn("🔍 Consultar lançamentos", "lanc")])
    rows.append([btn("🏆 Classificação", "stand")])
    if "atiradores" in perms:
        rows.append([btn("👤 Atiradores", "shooters")])
    if is_admin(role):
        rows.append([btn("⚙️ Administração", "admin")])
    return kb(rows)


async def show_main_menu(update, context, role, edit=False):
    txt = (f"🪖 *{config.CLUB_NAME}*\n"
           f"Você é: *{texts.role_label(role)}*\n\nO que deseja fazer?")
    markup = main_menu_kb(role)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            txt, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await context.bot.send_message(update.effective_chat.id, txt,
                                       reply_markup=markup,
                                       parse_mode=ParseMode.MARKDOWN)


# ============================================================================
# /start  e  pedido de acesso
# ============================================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    role = role_of(user.id)
    if role:
        await show_main_menu(update, context, role)
        return
    # não autorizado
    await update.message.reply_text(
        f"🪖 *{config.CLUB_NAME}*\n\n"
        "Você ainda não tem acesso a este sistema.\n"
        "Toque abaixo para pedir acesso ao administrador.",
        reply_markup=kb([[btn("🙋 Pedir acesso", "ask_access")]]),
        parse_mode=ParseMode.MARKDOWN)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Seu ID do Telegram é: `{update.effective_user.id}`",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    role = role_of(update.effective_user.id)
    if role:
        await update.message.reply_text("Operação cancelada.")
        await show_main_menu(update, context, role)
    else:
        await update.message.reply_text("Operação cancelada.")


async def on_ask_access(update, context):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    conn = db.get_conn()
    try:
        name = user.full_name or "Sem nome"
        db.add_access_request(conn, user.id, name, user.username)
        conn.commit()
        admin_ids = db.list_admin_ids(conn)
        roles = db.list_roles(conn)
    finally:
        conn.close()
    await q.edit_message_text(
        "✅ Pedido enviado! Assim que um administrador aprovar, você recebe "
        "uma mensagem aqui. Pode fechar por enquanto.")
    # avisa admins (botões de cargo dinâmicos, conforme cadastrados)
    uname = f" (@{user.username})" if user.username else ""
    role_btns = [btn(f"✅ {r['label']}", f"adm:approve:{user.id}:{r['code']}")
                 for r in roles]
    role_rows = [role_btns[i:i + 2] for i in range(0, len(role_btns), 2)]
    role_rows.append([btn("❌ Negar", f"adm:deny:{user.id}")])
    for aid in admin_ids:
        try:
            await context.bot.send_message(
                aid,
                f"🔔 *Novo pedido de acesso*\n{user.full_name}{uname}\n"
                f"ID: `{user.id}`\n\nQual cargo conceder?",
                reply_markup=kb(role_rows),
                parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            log.warning("Falha ao avisar admin %s: %s", aid, e)


# ============================================================================
# Roteador de callbacks
# ============================================================================
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    role = role_of(update.effective_user.id)

    if data == "ask_access":
        return await on_ask_access(update, context)

    # ações de admin de aprovação podem chegar antes do menu
    if data.startswith("adm:approve:") or data.startswith("adm:deny:"):
        return await admin_access_action(update, context, role)

    if not role:
        await q.answer("Sem acesso.", show_alert=True)
        return

    await q.answer()

    if data == "home":
        context.user_data.clear()
        return await show_main_menu(update, context, role, edit=True)

    # ---- Inscrição ----
    if data == "enroll":
        return await enroll_start(update, context, role)
    if data.startswith("enroll:"):
        return await enroll_router(update, context, role)

    # ---- Inscritos ----
    if data == "enrolled":
        return await show_enrolled(update, context)
    if data.startswith("enrolled:mod:"):
        return await show_enrolled(update, context, data.split(":")[2])
    if data == "enrolled:manage":
        return await enroll_manage_list(update, context, role)
    if data.startswith("enrmg:"):
        return await enroll_manage_router(update, context, role)

    # ---- Resultado ----
    if data == "result":
        return await result_start(update, context, role)
    if data.startswith("result:") or data.startswith("pen:"):
        return await result_router(update, context, role)

    # ---- Classificação ----
    if data == "stand":
        return await stand_start(update, context)
    if data.startswith("stand:"):
        return await stand_router(update, context)

    # ---- Admin ----
    if data == "admin":
        return await admin_menu(update, context, role)
    if data.startswith("adm:"):
        return await admin_router(update, context, role)

    # ---- Atiradores (cadastro/edição) ----
    if data == "shooters":
        return await shooters_menu(update, context, role)
    if data.startswith("sh:"):
        return await shooters_router(update, context, role)

    # ---- Consultar lançamentos (passadas da etapa aberta) ----
    if data == "lanc":
        return await lanc_menu(update, context, role)
    if data.startswith("lc:"):
        return await lanc_router(update, context, role)


# ============================================================================
# Entrada de texto (depende do estado em user_data['await'])
# ============================================================================
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = role_of(update.effective_user.id)
    if not role:
        return
    awaiting = context.user_data.get("await")
    if not awaiting:
        return  # ignora texto solto
    text = (update.message.text or "").strip()

    if awaiting == "enroll_search":
        return await enroll_do_search(update, context, text)
    if awaiting == "enroll_newname":
        return await enroll_new_name(update, context, text)
    if awaiting == "enroll_newcpf":
        return await enroll_new_cpf(update, context, text)
    if awaiting == "result_time":
        return await result_set_time(update, context, text)
    if awaiting == "cat_new_name":
        return await admin_cat_create(update, context, text)
    if awaiting == "cat_new_max":
        return await admin_cat_create_max(update, context, text)
    if awaiting == "cat_new_prom":
        return await admin_cat_create_prom(update, context, text)
    if awaiting == "cat_new_dem":
        return await admin_cat_create_dem(update, context, text)
    if awaiting == "cat_rename":
        return await admin_cat_rename(update, context, text)
    if awaiting == "cat_max":
        return await admin_cat_set_max(update, context, text)
    if awaiting == "cat_prom":
        return await admin_cat_set_prom(update, context, text)
    if awaiting == "cat_dem":
        return await admin_cat_set_dem(update, context, text)
    if awaiting == "mod_new_name":
        return await admin_mod_create(update, context, text)
    if awaiting == "mod_rename":
        return await admin_mod_rename(update, context, text)
    if awaiting == "member_rename":
        return await admin_member_rename_save(update, context, text)
    if awaiting == "role_rename":
        return await admin_role_rename_save(update, context, text)
    if awaiting == "role_new_name":
        return await admin_role_create_save(update, context, text)
    if awaiting == "stage_date":
        return await admin_stage_create_with_date(update, context, text)
    if awaiting == "sh_new_name":
        return await sh_new_name(update, context, text)
    if awaiting == "sh_new_cpf":
        return await sh_new_cpf(update, context, text)
    if awaiting == "sh_find":
        return await sh_do_search(update, context, text)
    if awaiting == "sh_edit_name":
        return await sh_save_name(update, context, text)
    if awaiting == "sh_edit_cpf":
        return await sh_save_cpf(update, context, text)
    if awaiting == "sh_merge_search":
        return await sh_merge_search(update, context, text)


# ============================================================================
# FLUXO: INSCRIÇÃO
# ============================================================================
def current_stage_or_none(conn):
    return db.get_open_stage(conn)


async def enroll_start(update, context, role):
    if not can_enroll(role):
        return
    conn = db.get_conn()
    try:
        stage = current_stage_or_none(conn)
        if not stage:
            await update.callback_query.edit_message_text(
                "⚠️ Não há etapa aberta. Peça ao administrador para abrir uma "
                "etapa em *Administração ▸ Etapas/Mês*.",
                reply_markup=kb([[btn("⬅️ Voltar", "home")]]),
                parse_mode=ParseMode.MARKDOWN)
            return
        context.user_data["enroll"] = {"stage_id": stage["id"]}
        label = stage_label(conn, stage)
        mods = db.list_modalities(conn)
    finally:
        conn.close()
    rows = _modality_button_rows(mods, "enroll:mod")
    rows.append([btn("⬅️ Voltar", "home")])
    await update.callback_query.edit_message_text(
        f"📝 *Inscrição — {label}*\n\nQual a modalidade?",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def enroll_router(update, context, role):
    q = update.callback_query
    parts = q.data.split(":")
    sub = parts[1]
    e = context.user_data.get("enroll", {})

    if sub == "mod":
        e["modality"] = parts[2]
        context.user_data["enroll"] = e
        context.user_data["await"] = "enroll_search"
        await q.edit_message_text(
            f"Modalidade: *{texts.modality_label(e['modality'])}*\n\n"
            "Digite parte do *nome* do atirador para buscar, ou cadastre um novo:",
            reply_markup=kb([[btn("➕ Novo atirador", "enroll:new")],
                             [btn("⬅️ Voltar", "home")]]),
            parse_mode=ParseMode.MARKDOWN)

    elif sub == "new":
        context.user_data["await"] = "enroll_newname"
        await q.edit_message_text(
            "➕ *Novo atirador*\n\nDigite o nome completo:",
            parse_mode=ParseMode.MARKDOWN)

    elif sub == "pick":
        e["shooter_id"] = int(parts[2])
        context.user_data["enroll"] = e
        context.user_data.pop("await", None)
        await enroll_after_shooter(update, context)

    elif sub == "cat":
        e["chosen_cat"] = int(parts[2])
        context.user_data["enroll"] = e
        await enroll_ask_qty(update, context)

    elif sub == "qty":
        e["qty"] = int(parts[2])
        context.user_data["enroll"] = e
        await enroll_confirm(update, context)


async def enroll_do_search(update, context, term):
    if len(term) < 2:
        await update.message.reply_text("Digite ao menos 2 letras.")
        return
    conn = db.get_conn()
    try:
        results = db.search_shooters(conn, term)
    finally:
        conn.close()
    rows = [[btn(r["name"], f"enroll:pick:{r['id']}")] for r in results]
    rows.append([btn("➕ Novo atirador", "enroll:new")])
    rows.append([btn("⬅️ Voltar", "home")])
    msg = "Selecione o atirador:" if results else \
        "Nenhum encontrado. Cadastre um novo ou tente outro nome:"
    await update.message.reply_text(msg, reply_markup=kb(rows))


async def enroll_new_name(update, context, name):
    if len(name) < 3:
        await update.message.reply_text("Nome muito curto. Digite o nome completo.")
        return
    e = context.user_data.get("enroll", {})
    e["new_name"] = name
    context.user_data["enroll"] = e
    context.user_data["await"] = "enroll_newcpf"
    await update.message.reply_text(
        f"Nome: *{name}*\n\nAgora digite o *CPF* (obrigatório):",
        parse_mode=ParseMode.MARKDOWN)


async def enroll_new_cpf(update, context, cpf):
    if not util.valid_cpf(cpf):
        await update.message.reply_text(
            "CPF inválido. Digite os 11 números do CPF (ex.: 123.456.789-09).")
        return
    e = context.user_data.get("enroll", {})
    conn = db.get_conn()
    try:
        existing = db.get_shooter_by_cpf(conn, cpf)
        if existing:
            # já existe: usa o cadastro existente em vez de duplicar
            e["shooter_id"] = existing["id"]
            context.user_data["enroll"] = e
            context.user_data.pop("await", None)
            name = existing["name"]
            already = True
        else:
            sid = db.create_shooter(conn, e["new_name"], cpf)
            conn.commit()
            e["shooter_id"] = sid
            context.user_data["enroll"] = e
            context.user_data.pop("await", None)
            name = e["new_name"]
            already = False
    finally:
        conn.close()
    if already:
        await update.message.reply_text(
            f"ℹ️ Esse CPF já é de *{name}* — vou usar esse cadastro (sem duplicar).",
            parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(
            f"✅ Atirador cadastrado: *{name}* · CPF {util.fmt_cpf(cpf)}",
            parse_mode=ParseMode.MARKDOWN)
    await enroll_after_shooter(update, context, from_text=True)


async def enroll_after_shooter(update, context, from_text=False):
    """Atirador definido: segue direto para a quantidade. A categoria de
    atirador novo é a *categoria inicial* configurada pelo administrador."""
    await enroll_ask_qty(update, context, from_text=from_text)


async def enroll_ask_qty(update, context, from_text=False):
    rows = [[btn("1", "enroll:qty:1"), btn("2", "enroll:qty:2"),
             btn("3", "enroll:qty:3"), btn("4", "enroll:qty:4")],
            [btn("⬅️ Voltar", "home")]]
    await _send_or_edit(update, context, from_text,
                        "Quantas *inscrições* (corridas) este atirador comprou?",
                        kb(rows))


async def enroll_confirm(update, context):
    e = context.user_data["enroll"]
    conn = db.get_conn()
    try:
        stage = db.get_stage(conn, e["stage_id"])
        month = db.get_month(conn, stage["month_id"])
        shooter = db.get_shooter(conn, e["shooter_id"])
        modality = e["modality"]
        # resolve categoria: a atual do atirador ou a categoria inicial
        # configurada pelo administrador (congelada no mês).
        current = db.get_shooter_category(conn, e["shooter_id"], modality)
        if not current:
            current = db.default_category_id(conn, modality)
            db.set_shooter_category(conn, e["shooter_id"], modality, current)
        cat_id = db.month_category_id(conn, month["id"], e["shooter_id"],
                                      modality, current)
        db.enroll(conn, e["stage_id"], e["shooter_id"], modality, cat_id,
                  e["qty"], update.effective_user.id)
        conn.commit()
        cat = db.get_category(conn, cat_id)
        label = stage_label(conn, stage)
    finally:
        conn.close()
    context.user_data.clear()
    txt = (f"✅ *Inscrição confirmada!*\n\n"
           f"Atirador: *{shooter['name']}*\n"
           f"Modalidade: {texts.modality_label(modality)} · {cat['name']}\n"
           f"Inscrições: {e['qty']}\n"
           f"Etapa: {label}")
    await _send_or_edit(update, context, False, txt,
                        kb([[btn("📝 Nova inscrição", "enroll")],
                            [btn("🏠 Menu", "home")]]))


# ============================================================================
# FLUXO: INSCRITOS DA ETAPA
# ============================================================================
async def show_enrolled(update, context, modality=None):
    conn = db.get_conn()
    try:
        stage = current_stage_or_none(conn)
        if not stage:
            await update.callback_query.edit_message_text(
                "⚠️ Não há etapa aberta.",
                reply_markup=kb([[btn("🏠 Menu", "home")]]))
            return
        enrolls = db.list_enrollments(conn, stage["id"], modality)
        data = []
        for e in enrolls:
            done = db.has_any_run(conn, e["id"])
            data.append({
                "shooter_name": e["shooter_name"],
                "modality": e["modality"],
                "category_name": e["category_name"],
                "status": "✅" if done else "⏳",
            })
        label = stage_label(conn, stage)
        mods = db.list_modalities(conn)
    finally:
        conn.close()
    txt = texts.enrolled_list_text(label, data)
    rows = _modality_button_rows(mods, "enrolled:mod")
    rows.append([btn("🔄 Todas", "enrolled")])
    if can_enroll(role_of(update.effective_user.id)):
        rows.append([btn("✏️ Editar inscrições", "enrolled:manage")])
    rows.append([btn("🏠 Menu", "home")])
    await update.callback_query.edit_message_text(
        txt, reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def enroll_manage_list(update, context, role):
    if not can_enroll(role):
        return
    conn = db.get_conn()
    try:
        stage = current_stage_or_none(conn)
        if not stage:
            await update.callback_query.edit_message_text(
                "⚠️ Não há etapa aberta.",
                reply_markup=kb([[btn("🏠 Menu", "home")]]))
            return
        enrolls = db.list_enrollments(conn, stage["id"])
        rows = []
        for e in enrolls:
            mlabel = texts.modality_emoji(e["modality"])
            rows.append([btn(f"{mlabel} {e['shooter_name']} ({e['runs_total']}x)",
                             f"enrmg:pick:{e['id']}")])
        label = stage_label(conn, stage)
    finally:
        conn.close()
    if not rows:
        return await update.callback_query.edit_message_text(
            f"✏️ *{label}*\n\n_Nenhuma inscrição para editar._",
            reply_markup=kb([[btn("⬅️ Voltar", "enrolled")]]),
            parse_mode=ParseMode.MARKDOWN)
    rows.append([btn("⬅️ Voltar", "enrolled")])
    await update.callback_query.edit_message_text(
        f"✏️ *Editar inscrições — {label}*\nToque no atirador:",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def enroll_manage_detail(update, context, eid):
    conn = db.get_conn()
    try:
        e = db.get_enrollment_full(conn, eid)
        if not e:
            return await enroll_manage_list(update, context,
                                            role_of(update.effective_user.id))
        rcount = db.runs_count(conn, eid)
    finally:
        conn.close()
    txt = (f"✏️ *{e['shooter_name']}*\n"
           f"{texts.modality_label(e['modality'])} · {e['category_name']}\n\n"
           f"Inscrições (corridas): *{e['runs_total']}*\n"
           f"Resultados lançados: {rcount}\n\n"
           "Defina a quantidade ou exclua:")
    qty_buttons = [btn(("✅ " if n == e["runs_total"] else "") + str(n),
                       f"enrmg:qty:{eid}:{n}") for n in range(1, 7)]
    rows = [qty_buttons[:3], qty_buttons[3:],
            [btn("🗑️ Excluir inscrição", f"enrmg:del:{eid}")],
            [btn("⬅️ Voltar", "enrolled:manage")]]
    await update.callback_query.edit_message_text(
        txt, reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def enroll_manage_router(update, context, role):
    if not can_enroll(role):
        return
    parts = update.callback_query.data.split(":")
    action = parts[1]
    eid = int(parts[2])
    if action == "pick":
        return await enroll_manage_detail(update, context, eid)
    if action == "qty":
        n = int(parts[3])
        conn = db.get_conn()
        try:
            db.set_enrollment_qty(conn, eid, n)
            conn.commit()
        finally:
            conn.close()
        await update.callback_query.answer(f"Quantidade ajustada para {n}.")
        return await enroll_manage_detail(update, context, eid)
    if action == "del":
        conn = db.get_conn()
        try:
            e = db.get_enrollment_full(conn, eid)
            rcount = db.runs_count(conn, eid)
        finally:
            conn.close()
        aviso = (f"⚠️ *Excluir a inscrição de {e['shooter_name']}?*\n\n"
                 + (f"Isso também apaga *{rcount} resultado(s)* já lançado(s). "
                    if rcount else "")
                 + "Esta ação não pode ser desfeita.")
        return await update.callback_query.edit_message_text(
            aviso, reply_markup=kb([
                [btn("🗑️ Sim, excluir", f"enrmg:delok:{eid}")],
                [btn("⬅️ Cancelar", f"enrmg:pick:{eid}")],
            ]), parse_mode=ParseMode.MARKDOWN)
    if action == "delok":
        conn = db.get_conn()
        try:
            db.delete_enrollment(conn, eid)
            conn.commit()
        finally:
            conn.close()
        await update.callback_query.answer("Inscrição excluída.")
        return await enroll_manage_list(update, context, role)


# ============================================================================
# FLUXO: LANÇAR RESULTADO (RO)
# ============================================================================
async def result_start(update, context, role):
    """Passo 1: escolher o tipo (modalidade) para localizar o atirador."""
    if not can_result(role):
        return
    conn = db.get_conn()
    try:
        stage = current_stage_or_none(conn)
        if not stage:
            await update.callback_query.edit_message_text(
                "⚠️ Não há etapa aberta.",
                reply_markup=kb([[btn("🏠 Menu", "home")]]))
            return
        label = stage_label(conn, stage)
        mods = db.list_modalities(conn)
        counts = {}
        for m in mods:
            counts[m["code"]] = conn.execute(
                "SELECT COUNT(*) AS n FROM enrollments WHERE stage_id=? "
                "AND modality=?", (stage["id"], m["code"])).fetchone()["n"]
    finally:
        conn.close()
    rows = []
    for m in mods:
        n = counts.get(m["code"], 0)
        rows.append([btn(f"{texts.modality_emoji(m['code'])} {m['label']} "
                         f"({n} inscrito{'s' if n != 1 else ''})",
                         f"result:mod:{m['code']}")])
    rows.append([btn("🏠 Menu", "home")])
    await update.callback_query.edit_message_text(
        f"🎯 *Linha de tiro — {label}*\n\nEscolha o *tipo* para ver a lista:",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def result_list_modality(update, context, role, mcode):
    """Passo 2: lista de atiradores do tipo — quem ainda NÃO passou primeiro
    (ordem alfabética) e, depois, quem já tem resultado (ordem alfabética)."""
    conn = db.get_conn()
    try:
        stage = current_stage_or_none(conn)
        if not stage:
            return await result_start(update, context, role)
        enrolls = conn.execute(
            "SELECT e.*, s.name AS shooter_name FROM enrollments e "
            "JOIN shooters s ON s.id=e.shooter_id "
            "WHERE e.stage_id=? AND e.modality=? ORDER BY s.name",
            (stage["id"], mcode)).fetchall()
        pending, done = [], []
        for e in enrolls:
            (done if db.has_any_run(conn, e["id"]) else pending).append(e)
        label = stage_label(conn, stage)
    finally:
        conn.close()
    context.user_data["result_mod"] = mcode
    mlabel = texts.modality_label(mcode)
    if not enrolls:
        return await update.callback_query.edit_message_text(
            f"🎯 *{label} — {mlabel}*\n\n_Nenhum atirador inscrito neste tipo._",
            reply_markup=kb([[btn("⬅️ Outros tipos", "result")],
                             [btn("🏠 Menu", "home")]]),
            parse_mode=ParseMode.MARKDOWN)
    rows = [[btn(f"⏳ {e['shooter_name']}", f"result:pick:{e['id']}")]
            for e in pending]
    rows += [[btn(f"✅ {e['shooter_name']}", f"result:pick:{e['id']}")]
             for e in done]
    rows.append([btn("🔄 Atualizar lista", f"result:mod:{mcode}")])
    rows.append([btn("⬅️ Outros tipos", "result"), btn("🏠 Menu", "home")])
    await update.callback_query.edit_message_text(
        f"🎯 *Linha de tiro — {label}*\n*{mlabel}* — ⏳ ainda não passaram · "
        "✅ já têm resultado.\nToque no atirador para lançar:",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def result_router(update, context, role):
    q = update.callback_query
    parts = q.data.split(":")

    if parts[0] == "result" and parts[1] == "mod":
        return await result_list_modality(update, context, role, parts[2])

    if parts[0] == "result" and parts[1] == "pick":
        eid = int(parts[2])
        context.user_data["result"] = {"enrollment_id": eid, "pen2": 0,
                                       "pen5": 0, "pen10": 0, "dq": False,
                                       "raw_time": None}
        context.user_data["await"] = "result_time"
        conn = db.get_conn()
        try:
            e = conn.execute(
                "SELECT e.*, s.name AS nm FROM enrollments e "
                "JOIN shooters s ON s.id=e.shooter_id WHERE e.id=?",
                (eid,)).fetchone()
            n = db.runs_count(conn, eid)
        finally:
            conn.close()
        extra = f"\n_(já tem {n} corrida(s); vale a melhor)_" if n else ""
        await q.edit_message_text(
            f"🎯 *{e['nm']}* — {texts.modality_label(e['modality'])}{extra}\n\n"
            "Digite o *tempo* da pista em segundos (ex.: `32.57`):",
            parse_mode=ParseMode.MARKDOWN)
        return

    # penalidades
    r = context.user_data.get("result")
    if not r:
        await q.edit_message_text("Sessão expirada.",
                                  reply_markup=kb([[btn("🏠 Menu", "home")]]))
        return

    if parts[0] == "pen":
        action = parts[1]
        if action in ("2", "5", "10"):
            key = f"pen{action}"
            delta = 1 if parts[2] == "inc" else -1
            r[key] = max(0, r[key] + delta)
        elif action == "dq":
            r["dq"] = not r["dq"]
        elif action == "save":
            return await result_save(update, context)
        elif action == "cancel":
            context.user_data.pop("result", None)
            context.user_data.pop("await", None)
            return await result_start(update, context, role)
        context.user_data["result"] = r
        await render_penalties(update, context)


async def result_set_time(update, context, text):
    r = context.user_data.get("result")
    if not r:
        return
    txt = text.replace(",", ".")
    try:
        val = float(txt)
        if val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("Tempo inválido. Ex.: `32.57`",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    r["raw_time"] = val
    context.user_data["result"] = r
    context.user_data.pop("await", None)
    await render_penalties(update, context, from_text=True)


def penalties_kb(r):
    def row(label, key, secs):
        return [btn(f"{label}", "noop"),
                btn("➖", f"pen:{key}:dec"),
                btn(f"{r['pen'+key]}", "noop"),
                btn("➕", f"pen:{key}:inc")]
    dq_label = "🟥 DQ: SIM" if r["dq"] else "⬜ DQ: não"
    return kb([
        row("Penal. 2s", "2", 2),
        row("Penal. 5s", "5", 5),
        row("Penal. 10s", "10", 10),
        [btn(dq_label, "pen:dq:x")],
        [btn("✅ Salvar resultado", "pen:save:x")],
        [btn("✖️ Cancelar", "pen:cancel:x")],
    ])


def preview_text(r):
    from .scoring import final_time
    ft = final_time(r["raw_time"], r["pen2"], r["pen5"], r["pen10"], r["dq"])
    if r["dq"]:
        result = "*DESQUALIFICADO* (0 pontos na etapa)"
    else:
        result = f"Tempo final: *{ft:.2f}s*"
    return (f"⏱️ Tempo cru: {r['raw_time']:.2f}s\n"
            f"Penalidades: {r['pen2']}×2 + {r['pen5']}×5 + {r['pen10']}×10\n\n"
            f"{result}\n\nAjuste e salve:")


async def render_penalties(update, context, from_text=False):
    r = context.user_data["result"]
    txt = preview_text(r)
    if from_text:
        await update.message.reply_text(txt, reply_markup=penalties_kb(r),
                                        parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(
            txt, reply_markup=penalties_kb(r), parse_mode=ParseMode.MARKDOWN)


async def result_save(update, context):
    r = context.user_data["result"]
    conn = db.get_conn()
    try:
        db.add_run(conn, r["enrollment_id"], r["raw_time"], r["pen2"],
                   r["pen5"], r["pen10"], r["dq"], update.effective_user.id)
        conn.commit()
        e = conn.execute(
            "SELECT e.*, s.name AS nm FROM enrollments e "
            "JOIN shooters s ON s.id=e.shooter_id WHERE e.id=?",
            (r["enrollment_id"],)).fetchone()
        # classificação parcial da etapa/categoria
        rows = st.stage_classification(conn, e["stage_id"], e["modality"],
                                       e["category_id"])
        stage = db.get_stage(conn, e["stage_id"])
        label = stage_label(conn, stage)
        cat = db.get_category(conn, e["category_id"])
    finally:
        conn.close()
    context.user_data.pop("result", None)
    context.user_data.pop("await", None)
    back = context.user_data.get("result_mod")
    next_data = f"result:mod:{back}" if back else "result"
    cls = texts.stage_class_text(label, e["modality"], cat["name"], rows)
    await update.callback_query.edit_message_text(
        f"✅ Resultado salvo para *{e['nm']}*!\n\n{cls}",
        reply_markup=kb([[btn("🎯 Próximo atirador", next_data)],
                         [btn("🏠 Menu", "home")]]),
        parse_mode=ParseMode.MARKDOWN)


# ============================================================================
# FLUXO: CLASSIFICAÇÃO
# ============================================================================
async def stand_start(update, context):
    conn = db.get_conn()
    try:
        month = db.get_open_month(conn)
        cats = db.list_categories(conn)
    finally:
        conn.close()
    rows = []
    for c in cats:
        emoji = texts.modality_emoji(c["modality"])
        rows.append([btn(f"{emoji} {c['name']}", f"stand:cat:{c['id']}")])
    rows.append([btn("🏠 Menu", "home")])
    await update.callback_query.edit_message_text(
        "🏆 *Classificação do mês*\nEscolha a categoria:",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def stand_router(update, context):
    q = update.callback_query
    parts = q.data.split(":")
    if parts[1] == "cat":
        cat_id = int(parts[2])
        conn = db.get_conn()
        try:
            month = db.get_open_month(conn)
            cat = db.get_category(conn, cat_id)
            rows = st.monthly_classification(conn, month["id"], cat["modality"],
                                             cat_id)
            mlabel = f"{MONTH_PT[month['month']]}/{month['year']}"
        finally:
            conn.close()
        txt = texts.monthly_class_text(mlabel, cat["modality"], cat["name"], rows)
        await q.edit_message_text(
            txt, reply_markup=kb([[btn("⬅️ Outras categorias", "stand")],
                                  [btn("🏠 Menu", "home")]]),
            parse_mode=ParseMode.MARKDOWN)


# ============================================================================
# FLUXO: ADMINISTRAÇÃO
# ============================================================================
async def admin_menu(update, context, role):
    if not is_admin(role):
        return
    await update.callback_query.edit_message_text(
        "⚙️ *Administração*",
        reply_markup=kb([
            [btn("👥 Equipe", "adm:team")],
            [btn("🏷️ Categorias", "adm:cats")],
            [btn("📅 Etapas / Mês", "adm:stages")],
            [btn("🏠 Menu", "home")],
        ]), parse_mode=ParseMode.MARKDOWN)


async def admin_router(update, context, role):
    if not is_admin(role) and not update.callback_query.data.startswith(
            ("adm:approve", "adm:deny")):
        return
    data = update.callback_query.data
    parts = data.split(":")
    sub = parts[1]

    # ---- equipe ----
    if sub == "team":
        return await admin_team(update, context)
    if sub == "member":
        return await admin_member_detail(update, context, int(parts[2]))
    if sub == "mren":
        context.user_data["await"] = "member_rename"
        context.user_data["adm_tid"] = int(parts[2])
        return await update.callback_query.edit_message_text(
            "✏️ Digite o *novo nome* do membro:", parse_mode=ParseMode.MARKDOWN)
    if sub == "mrole":
        return await admin_member_role(update, context, int(parts[2]))
    if sub == "mroleset":
        return await admin_member_role_set(update, context, int(parts[2]),
                                            parts[3])
    if sub == "remove":
        return await admin_remove_staff(update, context, int(parts[2]))
    # ---- cargos ----
    if sub == "roles":
        return await admin_roles(update, context)
    if sub == "role":
        return await admin_role_detail(update, context, parts[2])
    if sub == "rperm":
        return await admin_role_toggle_perm(update, context, parts[2], parts[3])
    if sub == "rren":
        context.user_data["await"] = "role_rename"
        context.user_data["adm_role"] = parts[2]
        return await update.callback_query.edit_message_text(
            "✏️ Digite o *novo nome* do cargo:", parse_mode=ParseMode.MARKDOWN)
    if sub == "roleadd":
        context.user_data["await"] = "role_new_name"
        return await update.callback_query.edit_message_text(
            "➕ *Novo cargo*\n\nDigite o *nome* do cargo (ex.: `Auxiliar`):",
            parse_mode=ParseMode.MARKDOWN)
    if sub == "rdel":
        return await admin_role_delete_confirm(update, context, parts[2])
    if sub == "rdelok":
        return await admin_role_delete(update, context, parts[2])
    # ---- categorias (tipos e subcategorias) ----
    if sub == "cats":
        return await admin_cats(update, context)
    if sub == "mod":
        return await admin_mod_detail(update, context, parts[2])
    if sub == "modadd":
        context.user_data["await"] = "mod_new_name"
        return await update.callback_query.edit_message_text(
            "➕ *Novo tipo*\n\nDigite o *nome* do novo tipo "
            "(ex.: `Fuzil`, `Revólver`):", parse_mode=ParseMode.MARKDOWN)
    if sub == "modren":
        context.user_data["await"] = "mod_rename"
        context.user_data["adm_mod"] = parts[2]
        return await update.callback_query.edit_message_text(
            "✏️ Digite o *novo nome* do tipo:", parse_mode=ParseMode.MARKDOWN)
    if sub == "moddel":
        return await admin_mod_delete_confirm(update, context, parts[2])
    if sub == "moddelok":
        return await admin_mod_delete(update, context, parts[2])
    if sub == "cat":
        return await admin_cat_detail(update, context, int(parts[2]))
    if sub == "catadd":
        context.user_data["await"] = "cat_new_name"
        context.user_data["cat_modality"] = parts[2]
        return await update.callback_query.edit_message_text(
            f"➕ *Nova categoria em {texts.modality_label(parts[2])}*\n\n"
            "Digite o *nome* da categoria:", parse_mode=ParseMode.MARKDOWN)
    if sub == "catren":
        context.user_data["await"] = "cat_rename"
        context.user_data["adm_cat"] = int(parts[2])
        return await update.callback_query.edit_message_text(
            "✏️ Digite o *novo nome* da categoria:",
            parse_mode=ParseMode.MARKDOWN)
    if sub == "catmax":
        context.user_data["await"] = "cat_max"
        context.user_data["adm_cat"] = int(parts[2])
        return await update.callback_query.edit_message_text(
            "👥 Digite o *limite de atiradores* da categoria.\n"
            "Envie `0` para *sem limite*.", parse_mode=ParseMode.MARKDOWN)
    if sub == "catprom":
        context.user_data["await"] = "cat_prom"
        context.user_data["adm_cat"] = int(parts[2])
        return await update.callback_query.edit_message_text(
            "⬆️ Digite *quantos atiradores sobem* desta categoria por mês "
            "(ex.: `3`; envie `0` para ninguém subir):",
            parse_mode=ParseMode.MARKDOWN)
    if sub == "catdem":
        context.user_data["await"] = "cat_dem"
        context.user_data["adm_cat"] = int(parts[2])
        return await update.callback_query.edit_message_text(
            "⬇️ Digite *quantos atiradores descem* desta categoria por mês "
            "(ex.: `3`; envie `0` para ninguém descer):",
            parse_mode=ParseMode.MARKDOWN)
    if sub == "catrank":
        return await admin_cat_move_rank(update, context, int(parts[2]),
                                         parts[3])
    if sub == "catentry":
        return await admin_cat_set_entry(update, context, int(parts[2]))
    if sub == "catdel":
        return await admin_cat_delete_confirm(update, context, int(parts[2]))
    if sub == "catdelok":
        return await admin_cat_delete(update, context, int(parts[2]))
    # ---- etapas / mês ----
    if sub == "stages":
        return await admin_stages(update, context)
    if sub == "stage":
        return await admin_stage_detail(update, context, int(parts[2]))
    if sub == "stagenew":
        return await admin_stage_new(update, context)
    if sub == "stageclose":
        return await admin_stage_close(update, context, int(parts[2]))
    if sub == "stageopen":
        return await admin_stage_reopen(update, context, int(parts[2]))
    if sub == "stagedel":
        return await admin_stage_delete_confirm(update, context, int(parts[2]))
    if sub == "stagedelok":
        return await admin_stage_delete(update, context, int(parts[2]))
    if sub == "monthwipe":
        return await admin_month_wipe_confirm(update, context)
    if sub == "monthwipeok":
        return await admin_month_wipe(update, context)
    if sub == "monthclose":
        return await admin_month_close(update, context)
    if sub == "monthconfirm":
        return await admin_month_confirm(update, context)


async def admin_access_action(update, context, role):
    q = update.callback_query
    await q.answer()
    if not is_admin(role):
        await q.answer("Apenas administradores.", show_alert=True)
        return
    parts = q.data.split(":")
    tid = int(parts[2])
    conn = db.get_conn()
    try:
        req = db.get_access_request(conn, tid)
        if parts[1] == "deny":
            db.remove_access_request(conn, tid)
            conn.commit()
            await q.edit_message_text("❌ Pedido negado.")
            try:
                await context.bot.send_message(
                    tid, "Seu pedido de acesso foi negado pelo administrador.")
            except Exception:
                pass
            return
        new_role = parts[3]
        name = req["name"] if req else "Membro"
        uname = req["username"] if req else None
        db.upsert_staff(conn, tid, name, new_role, uname)
        db.remove_access_request(conn, tid)
        conn.commit()
    finally:
        conn.close()
    await q.edit_message_text(
        f"✅ Acesso concedido a *{name}* como *{texts.role_label(new_role)}*.",
        parse_mode=ParseMode.MARKDOWN)
    try:
        await context.bot.send_message(
            tid, f"🎉 Seu acesso foi liberado como *{texts.role_label(new_role)}*!\n"
                 "Envie /start para começar.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass


def _approval_role_rows(conn, tid):
    """Botões dinâmicos de cargo para aprovar um pedido de acesso."""
    roles = db.list_roles(conn)
    btns = [btn(f"✅ {r['label']}", f"adm:approve:{tid}:{r['code']}")
            for r in roles]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([btn("❌ Negar", f"adm:deny:{tid}")])
    return rows


async def admin_team(update, context, from_text=False):
    conn = db.get_conn()
    try:
        staff = db.list_staff(conn)
        reqs = conn.execute(
            "SELECT * FROM access_requests ORDER BY created_at").fetchall()
        req_rows = {r["telegram_id"]: _approval_role_rows(conn, r["telegram_id"])
                    for r in reqs}
    finally:
        conn.close()
    lines = ["👥 *Equipe*\n\nToque no membro para renomear, alterar o cargo "
             "ou remover:"]
    rows = []
    for s in staff:
        rows.append([btn(f"👤 {s['name']} — {texts.role_label(s['role'])}",
                         f"adm:member:{s['telegram_id']}")])
    if reqs:
        lines.append("\n*Pedidos de acesso pendentes* — escolha o cargo:")
        for r in reqs:
            lines.append(f"• {r['name']} (ID `{r['telegram_id']}`)")
            rows.extend(req_rows[r["telegram_id"]])
    rows.append([btn("🎖️ Cargos e permissões", "adm:roles:x")])
    rows.append([btn("⬅️ Voltar", "admin")])
    await _send_or_edit(update, context, from_text, "\n".join(lines), kb(rows))


async def admin_member_detail(update, context, tid, from_text=False):
    conn = db.get_conn()
    try:
        s = db.get_staff_any(conn, tid)
    finally:
        conn.close()
    if not s:
        return await admin_team(update, context, from_text)
    uname = f"@{s['username']}" if s["username"] else "—"
    txt = (f"👤 *{s['name']}*\n\n"
           f"Cargo: *{texts.role_label(s['role'])}*\n"
           f"Usuário: {uname}\nID: `{s['telegram_id']}`\n"
           f"Status: {s['status']}")
    rows = [
        [btn("✏️ Renomear", f"adm:mren:{tid}"),
         btn("🎖️ Alterar cargo", f"adm:mrole:{tid}")],
    ]
    if s["role"] != "admin" or tid not in config.ADMIN_IDS:
        rows.append([btn("🗑️ Remover da equipe", f"adm:remove:{tid}")])
    rows.append([btn("⬅️ Voltar", "adm:team")])
    await _send_or_edit(update, context, from_text, txt, kb(rows))


async def admin_member_role(update, context, tid):
    conn = db.get_conn()
    try:
        s = db.get_staff_any(conn, tid)
        roles = db.list_roles(conn)
    finally:
        conn.close()
    rows = []
    for r in roles:
        mark = "✅ " if s and s["role"] == r["code"] else ""
        rows.append([btn(f"{mark}{r['label']}",
                         f"adm:mroleset:{tid}:{r['code']}")])
    rows.append([btn("⬅️ Voltar", f"adm:member:{tid}")])
    await update.callback_query.edit_message_text(
        f"🎖️ Novo cargo de *{s['name']}*:", reply_markup=kb(rows),
        parse_mode=ParseMode.MARKDOWN)


async def admin_member_role_set(update, context, tid, role_code):
    conn = db.get_conn()
    try:
        db.set_staff_role(conn, tid, role_code)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Cargo alterado.")
    try:
        await context.bot.send_message(
            tid, f"ℹ️ Seu cargo no bot foi alterado para "
                 f"*{texts.role_label(role_code)}*. Envie /start para "
                 "atualizar o menu.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    return await admin_member_detail(update, context, tid)


async def admin_member_rename_save(update, context, name):
    tid = context.user_data.get("adm_tid")
    if len(name.strip()) < 2 or not tid:
        await update.message.reply_text("Nome inválido.")
        return
    conn = db.get_conn()
    try:
        db.rename_staff(conn, tid, name.strip())
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    context.user_data.pop("adm_tid", None)
    await update.message.reply_text("✅ Membro renomeado.")
    await admin_member_detail(update, context, tid, from_text=True)


# ---- cargos e permissões ----
async def admin_roles(update, context, from_text=False):
    conn = db.get_conn()
    try:
        roles = db.list_roles(conn)
        info = []
        for r in roles:
            n = db.role_member_count(conn, r["code"])
            perms = db.role_perms(conn, r["code"])
            info.append((r, n, perms))
    finally:
        conn.close()
    perm_labels = dict(db.PERMISSIONS)
    lines = ["🎖️ *Cargos e permissões*\n"]
    rows = []
    for r, n, perms in info:
        if r["code"] == "admin":
            plabel = "acesso total"
        else:
            plabel = ", ".join(perm_labels[p].split(" ", 1)[1]
                               for p in perm_labels if p in perms) or "nenhuma"
        lines.append(f"• *{r['label']}* — {n} membro(s) · {plabel}")
        rows.append([btn(f"⚙️ {r['label']}", f"adm:role:{r['code']}")])
    lines.append("\nToque no cargo para ligar/desligar permissões, renomear "
                 "ou excluir.")
    rows.append([btn("➕ Novo cargo", "adm:roleadd:x")])
    rows.append([btn("⬅️ Voltar", "adm:team")])
    await _send_or_edit(update, context, from_text, "\n".join(lines), kb(rows))


async def admin_role_detail(update, context, code, from_text=False):
    conn = db.get_conn()
    try:
        r = db.get_role(conn, code)
        if not r:
            return await admin_roles(update, context, from_text)
        perms = db.role_perms(conn, code)
        n = db.role_member_count(conn, code)
    finally:
        conn.close()
    txt = (f"🎖️ *{r['label']}* — {n} membro(s)\n\n"
           "Toque para *ligar/desligar* cada permissão:")
    rows = []
    if code == "admin":
        txt = (f"🎖️ *{r['label']}* — {n} membro(s)\n\n"
               "O cargo de administrador tem *acesso total* e não pode ser "
               "alterado nem excluído.")
    else:
        for p, label in db.PERMISSIONS:
            mark = "🟢" if p in perms else "🔴"
            rows.append([btn(f"{mark} {label}", f"adm:rperm:{code}:{p}")])
        rows.append([btn("✏️ Renomear cargo", f"adm:rren:{code}")])
        if not r["builtin"]:
            rows.append([btn("🗑️ Excluir cargo", f"adm:rdel:{code}")])
    rows.append([btn("⬅️ Voltar", "adm:roles:x")])
    await _send_or_edit(update, context, from_text, txt, kb(rows))


async def admin_role_toggle_perm(update, context, code, perm):
    if code == "admin":
        return await update.callback_query.answer(
            "O administrador sempre tem todas as permissões.", show_alert=True)
    conn = db.get_conn()
    try:
        state = db.toggle_role_perm(conn, code, perm)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer(
        "Permissão ativada." if state else "Permissão desativada.")
    return await admin_role_detail(update, context, code)


async def admin_role_delete_confirm(update, context, code):
    conn = db.get_conn()
    try:
        r = db.get_role(conn, code)
        n = db.role_member_count(conn, code)
    finally:
        conn.close()
    if r["builtin"]:
        return await update.callback_query.answer(
            "Cargos padrão não podem ser excluídos.", show_alert=True)
    if n:
        return await update.callback_query.edit_message_text(
            f"⚠️ O cargo *{r['label']}* tem {n} membro(s). Mova-os para outro "
            "cargo antes de excluir.",
            reply_markup=kb([[btn("⬅️ Voltar", f"adm:role:{code}")]]),
            parse_mode=ParseMode.MARKDOWN)
    await update.callback_query.edit_message_text(
        f"⚠️ *Excluir o cargo {r['label']}?*",
        reply_markup=kb([[btn("🗑️ Sim, excluir", f"adm:rdelok:{code}")],
                         [btn("⬅️ Cancelar", f"adm:role:{code}")]]),
        parse_mode=ParseMode.MARKDOWN)


async def admin_role_delete(update, context, code):
    conn = db.get_conn()
    try:
        db.delete_role(conn, code)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Cargo excluído.")
    return await admin_roles(update, context)


async def admin_role_rename_save(update, context, name):
    code = context.user_data.get("adm_role")
    if len(name.strip()) < 2 or not code:
        await update.message.reply_text("Nome inválido.")
        return
    conn = db.get_conn()
    try:
        db.rename_role(conn, code, name.strip())
        conn.commit()
        texts.set_role_labels({r["code"]: r["label"] for r in
                               conn.execute("SELECT * FROM roles")})
    finally:
        conn.close()
    context.user_data.pop("await", None)
    context.user_data.pop("adm_role", None)
    await update.message.reply_text("✅ Cargo renomeado.")
    await admin_role_detail(update, context, code, from_text=True)


async def admin_role_create_save(update, context, name):
    if len(name.strip()) < 3:
        await update.message.reply_text("Nome muito curto.")
        return
    conn = db.get_conn()
    try:
        code = db.add_role(conn, name.strip())
        conn.commit()
        texts.set_role_labels({r["code"]: r["label"] for r in
                               conn.execute("SELECT * FROM roles")})
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text(
        f"✅ Cargo *{name.strip()}* criado. Agora ative as permissões dele:",
        parse_mode=ParseMode.MARKDOWN)
    await admin_role_detail(update, context, code, from_text=True)


async def admin_remove_staff(update, context, tid):
    conn = db.get_conn()
    try:
        db.deactivate_staff(conn, tid)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Membro removido.")
    await admin_team(update, context)


async def admin_cats(update, context, from_text=False):
    """Nível 1: tipos (modalidades)."""
    conn = db.get_conn()
    try:
        mods = db.list_modalities(conn)
        counts = {m["code"]: len(db.list_categories(conn, m["code"]))
                  for m in mods}
    finally:
        conn.close()
    rows = [[btn(f"{texts.modality_emoji(m['code'])} {m['label']} "
                 f"({counts[m['code']]} categoria"
                 f"{'s' if counts[m['code']] != 1 else ''})",
                 f"adm:mod:{m['code']}")] for m in mods]
    rows.append([btn("➕ Novo tipo", "adm:modadd:x")])
    rows.append([btn("⬅️ Voltar", "admin")])
    await _send_or_edit(update, context, from_text,
                        "🏷️ *Categorias*\n\nEscolha o *tipo* para gerenciar "
                        "suas categorias, ou crie um novo tipo:", kb(rows))


async def admin_mod_detail(update, context, mcode, from_text=False):
    """Nível 2: categorias (subcategorias) de um tipo, na hierarquia."""
    conn = db.get_conn()
    try:
        mod = db.get_modality(conn, mcode)
        if not mod:
            return await admin_cats(update, context, from_text)
        cats = sorted(db.list_categories(conn, mcode), key=lambda c: -c["rank"])
        counts = {c["id"]: db.category_member_count(conn, c["id"]) for c in cats}
        in_use = db.modality_in_use(conn, mcode)
    finally:
        conn.close()
    lines = [f"{texts.modality_emoji(mcode)} *{mod['label']}* — categorias "
             "(da mais alta para a mais baixa):\n"]
    rows = []
    if not cats:
        lines.append("_Nenhuma categoria ainda._")
    for c in cats:
        lim = f"{counts[c['id']]}/{c['max_shooters']}" if c["max_shooters"] \
            else f"{counts[c['id']]}"
        flags = []
        if c["promote_n"]:
            flags.append(f"⬆️{c['promote_n']}")
        if c["demote_n"]:
            flags.append(f"⬇️{c['demote_n']}")
        if c["entry"]:
            flags.append("🚪inicial")
        lines.append(f"• *{c['name']}* — {lim} atirador(es)"
                     + (f" · {' '.join(flags)}" if flags else ""))
        rows.append([btn(f"⚙️ {c['name']}", f"adm:cat:{c['id']}")])
    lines.append("\n_🚪 = categoria inicial de novos atiradores._")
    rows.append([btn("➕ Nova categoria", f"adm:catadd:{mcode}")])
    rows.append([btn("✏️ Renomear tipo", f"adm:modren:{mcode}")])
    if not in_use:
        rows.append([btn("🗑️ Excluir tipo", f"adm:moddel:{mcode}")])
    rows.append([btn("⬅️ Voltar", "adm:cats")])
    await _send_or_edit(update, context, from_text, "\n".join(lines), kb(rows))


async def admin_cat_detail(update, context, cat_id, from_text=False):
    """Nível 3: configuração de UMA categoria."""
    conn = db.get_conn()
    try:
        c = db.get_category(conn, cat_id)
        if not c:
            return await admin_cats(update, context, from_text)
        n = db.category_member_count(conn, cat_id)
        used = db.category_in_use(conn, cat_id)
        cats = sorted(db.list_categories(conn, c["modality"]),
                      key=lambda x: x["rank"])
        idx = next((i for i, x in enumerate(cats) if x["id"] == cat_id), 0)
        pos = f"{len(cats) - idx}ª de {len(cats)} (da mais alta p/ mais baixa)"
    finally:
        conn.close()
    lim = c["max_shooters"] if c["max_shooters"] else "sem limite"
    txt = (f"⚙️ *{c['name']}* — {texts.modality_label(c['modality'])}\n\n"
           f"Posição na hierarquia: {pos}\n"
           f"Atiradores atualmente: {n}\n"
           f"Limite de atiradores: *{lim}*\n"
           f"Sobem por mês: *{c['promote_n']}* · Descem por mês: *{c['demote_n']}*\n"
           f"Categoria inicial: {'✅ sim' if c['entry'] else 'não'}")
    rows = [
        [btn("✏️ Renomear", f"adm:catren:{cat_id}"),
         btn("👥 Limite", f"adm:catmax:{cat_id}")],
        [btn("⬆️ Quantos sobem", f"adm:catprom:{cat_id}"),
         btn("⬇️ Quantos descem", f"adm:catdem:{cat_id}")],
        [btn("🔼 Subir na hierarquia", f"adm:catrank:{cat_id}:up"),
         btn("🔽 Descer na hierarquia", f"adm:catrank:{cat_id}:down")],
    ]
    if not c["entry"]:
        rows.append([btn("🚪 Definir como categoria inicial",
                         f"adm:catentry:{cat_id}")])
    if not used:
        rows.append([btn("🗑️ Excluir categoria", f"adm:catdel:{cat_id}")])
    rows.append([btn("⬅️ Voltar", f"adm:mod:{c['modality']}")])
    await _send_or_edit(update, context, from_text, txt, kb(rows))


async def admin_cat_move_rank(update, context, cat_id, direction):
    conn = db.get_conn()
    try:
        ok = db.move_category_rank(conn, cat_id, direction)
        conn.commit()
    finally:
        conn.close()
    if not ok:
        await update.callback_query.answer(
            "A categoria já está no extremo da hierarquia.", show_alert=True)
    return await admin_cat_detail(update, context, cat_id)


async def admin_cat_set_entry(update, context, cat_id):
    conn = db.get_conn()
    try:
        db.set_entry_category(conn, cat_id)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Categoria inicial definida.")
    return await admin_cat_detail(update, context, cat_id)


async def admin_cat_delete_confirm(update, context, cat_id):
    conn = db.get_conn()
    try:
        c = db.get_category(conn, cat_id)
        used = db.category_in_use(conn, cat_id)
    finally:
        conn.close()
    if used:
        return await update.callback_query.edit_message_text(
            f"⚠️ A categoria *{c['name']}* tem inscrições/atiradores/histórico "
            "ligados a ela e não pode ser excluída sem perder dados.\n\n"
            "Mova os atiradores para outra categoria antes de excluir.",
            reply_markup=kb([[btn("⬅️ Voltar", f"adm:cat:{cat_id}")]]),
            parse_mode=ParseMode.MARKDOWN)
    await update.callback_query.edit_message_text(
        f"⚠️ *Excluir a categoria {c['name']}?*\n\nEsta ação não pode ser "
        "desfeita.",
        reply_markup=kb([[btn("🗑️ Sim, excluir", f"adm:catdelok:{cat_id}")],
                         [btn("⬅️ Cancelar", f"adm:cat:{cat_id}")]]),
        parse_mode=ParseMode.MARKDOWN)


async def admin_cat_delete(update, context, cat_id):
    conn = db.get_conn()
    try:
        c = db.get_category(conn, cat_id)
        mcode = c["modality"]
        db.delete_category(conn, cat_id)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Categoria excluída.")
    return await admin_mod_detail(update, context, mcode)


async def admin_mod_delete_confirm(update, context, mcode):
    conn = db.get_conn()
    try:
        mod = db.get_modality(conn, mcode)
        in_use = db.modality_in_use(conn, mcode)
    finally:
        conn.close()
    if in_use:
        return await update.callback_query.edit_message_text(
            f"⚠️ O tipo *{mod['label']}* tem categorias ou inscrições e não "
            "pode ser excluído. Exclua as categorias dele primeiro.",
            reply_markup=kb([[btn("⬅️ Voltar", f"adm:mod:{mcode}")]]),
            parse_mode=ParseMode.MARKDOWN)
    await update.callback_query.edit_message_text(
        f"⚠️ *Excluir o tipo {mod['label']}?*",
        reply_markup=kb([[btn("🗑️ Sim, excluir", f"adm:moddelok:{mcode}")],
                         [btn("⬅️ Cancelar", f"adm:mod:{mcode}")]]),
        parse_mode=ParseMode.MARKDOWN)


async def admin_mod_delete(update, context, mcode):
    conn = db.get_conn()
    try:
        db.delete_modality(conn, mcode)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Tipo excluído.")
    return await admin_cats(update, context)


# ---- entradas de texto do fluxo de categorias ----
async def admin_mod_create(update, context, name):
    if len(name.strip()) < 3:
        await update.message.reply_text("Nome muito curto.")
        return
    conn = db.get_conn()
    try:
        code = db.add_modality(conn, name.strip())
        conn.commit()
        texts.set_modality_labels({r["code"]: r["label"] for r in
                                   conn.execute("SELECT * FROM modalities")})
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text(
        f"✅ Tipo *{name.strip()}* criado. Agora cadastre as categorias dele.",
        parse_mode=ParseMode.MARKDOWN)
    await admin_mod_detail(update, context, code, from_text=True)


async def admin_mod_rename(update, context, name):
    mcode = context.user_data.get("adm_mod")
    if len(name.strip()) < 2 or not mcode:
        await update.message.reply_text("Nome inválido.")
        return
    conn = db.get_conn()
    try:
        db.rename_modality(conn, mcode, name.strip())
        conn.commit()
        texts.set_modality_labels({r["code"]: r["label"] for r in
                                   conn.execute("SELECT * FROM modalities")})
    finally:
        conn.close()
    context.user_data.pop("await", None)
    context.user_data.pop("adm_mod", None)
    await update.message.reply_text("✅ Tipo renomeado.")
    await admin_mod_detail(update, context, mcode, from_text=True)


async def admin_cat_create(update, context, name):
    """Assistente da nova categoria: nome → limite → sobem → descem."""
    if len(name.strip()) < 2:
        await update.message.reply_text("Nome muito curto.")
        return
    context.user_data["cat_new"] = {"name": name.strip()}
    context.user_data["await"] = "cat_new_max"
    await update.message.reply_text(
        f"Nome: *{name.strip()}*\n\nAgora o *limite de atiradores* "
        "(envie `0` para sem limite):", parse_mode=ParseMode.MARKDOWN)


def _parse_nonneg(text):
    try:
        v = int(str(text).strip())
        return v if v >= 0 else None
    except (ValueError, TypeError):
        return None


async def admin_cat_create_max(update, context, text):
    v = _parse_nonneg(text)
    if v is None:
        await update.message.reply_text("Envie um número (ex.: `8`, ou `0` "
                                        "para sem limite).",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data["cat_new"]["max"] = v or None
    context.user_data["await"] = "cat_new_prom"
    await update.message.reply_text(
        "⬆️ Quantos atiradores *sobem* desta categoria por mês? (`0` = nenhum)",
        parse_mode=ParseMode.MARKDOWN)


async def admin_cat_create_prom(update, context, text):
    v = _parse_nonneg(text)
    if v is None:
        await update.message.reply_text("Envie um número (ex.: `3`).",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data["cat_new"]["prom"] = v
    context.user_data["await"] = "cat_new_dem"
    await update.message.reply_text(
        "⬇️ Quantos atiradores *descem* desta categoria por mês? (`0` = nenhum)",
        parse_mode=ParseMode.MARKDOWN)


async def admin_cat_create_dem(update, context, text):
    v = _parse_nonneg(text)
    if v is None:
        await update.message.reply_text("Envie um número (ex.: `3`).",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    data = context.user_data.get("cat_new", {})
    modality = context.user_data.get("cat_modality", "pistola")
    conn = db.get_conn()
    try:
        cid = db.add_category(conn, data["name"], modality,
                              max_shooters=data.get("max"),
                              promote_n=data.get("prom", 0), demote_n=v)
        conn.commit()
    finally:
        conn.close()
    for k in ("await", "cat_new", "cat_modality"):
        context.user_data.pop(k, None)
    await update.message.reply_text(
        f"✅ Categoria *{data['name']}* criada em "
        f"{texts.modality_label(modality)}.\nEla entrou no *topo* da "
        "hierarquia — ajuste a posição abaixo, se quiser.",
        parse_mode=ParseMode.MARKDOWN)
    await admin_cat_detail(update, context, cid, from_text=True)


async def admin_cat_rename(update, context, name):
    cat_id = context.user_data.get("adm_cat")
    if len(name.strip()) < 2 or not cat_id:
        await update.message.reply_text("Nome inválido.")
        return
    conn = db.get_conn()
    try:
        db.update_category(conn, cat_id, name=name.strip())
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text("✅ Categoria renomeada.")
    await admin_cat_detail(update, context, cat_id, from_text=True)


async def admin_cat_set_max(update, context, text):
    cat_id = context.user_data.get("adm_cat")
    v = _parse_nonneg(text)
    if v is None or not cat_id:
        await update.message.reply_text("Envie um número (`0` = sem limite).",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    conn = db.get_conn()
    try:
        db.update_category(conn, cat_id, max_shooters=(v or None))
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text("✅ Limite atualizado.")
    await admin_cat_detail(update, context, cat_id, from_text=True)


async def admin_cat_set_prom(update, context, text):
    cat_id = context.user_data.get("adm_cat")
    v = _parse_nonneg(text)
    if v is None or not cat_id:
        await update.message.reply_text("Envie um número (ex.: `3`).",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    conn = db.get_conn()
    try:
        db.update_category(conn, cat_id, promote_n=v)
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text("✅ Quantidade de subidas atualizada.")
    await admin_cat_detail(update, context, cat_id, from_text=True)


async def admin_cat_set_dem(update, context, text):
    cat_id = context.user_data.get("adm_cat")
    v = _parse_nonneg(text)
    if v is None or not cat_id:
        await update.message.reply_text("Envie um número (ex.: `3`).",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    conn = db.get_conn()
    try:
        db.update_category(conn, cat_id, demote_n=v)
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text("✅ Quantidade de descidas atualizada.")
    await admin_cat_detail(update, context, cat_id, from_text=True)


async def admin_stages(update, context):
    conn = db.get_conn()
    try:
        month = db.get_open_month(conn)
        stages = db.list_stages(conn, month["id"])
        mlabel = f"{MONTH_PT[month['month']]}/{month['year']}"
        lines = [f"📅 *Etapas de {mlabel}* _(mês aberto)_\n"]
        rows = []
        for s in stages:
            status = "🟢" if s["status"] == "aberta" else "🔒"
            rcount = db.stage_result_count(conn, s["id"])
            lines.append(f"{status} {s['number']}ª Etapa · {texts.fmt_date(s['date'])}"
                         f" · {rcount} result.")
            rows.append([btn(f"{status} {s['number']}ª Etapa · "
                             f"{texts.fmt_date(s['date'])}",
                             f"adm:stage:{s['id']}")])
        if not stages:
            lines.append("_Nenhuma etapa ainda. Toque em ➕ para abrir a 1ª._")
    finally:
        conn.close()
    rows.append([btn("➕ Abrir nova etapa", "adm:stagenew:x")])
    rows.append([btn("🏁 Fechar mês (subidas/descidas)", "adm:monthclose:x")])
    if stages:
        rows.append([btn("🧹 Limpar mês (apagar todas)", "adm:monthwipe:x")])
    rows.append([btn("⬅️ Voltar", "admin")])
    await _send_or_edit(update, context, False, "\n".join(lines), kb(rows))


async def admin_stage_detail(update, context, stage_id):
    conn = db.get_conn()
    try:
        s = db.get_stage(conn, stage_id)
        if not s:
            return await admin_stages(update, context)
        label = stage_label(conn, s)
        rcount = db.stage_result_count(conn, stage_id)
        ecount = db.stage_enroll_count(conn, stage_id)
        status = "🟢 aberta" if s["status"] == "aberta" else "🔒 fechada"
    finally:
        conn.close()
    txt = (f"📅 *{label}*\n\n"
           f"Data: {texts.fmt_date(s['date'])}\n"
           f"Situação: {status}\n"
           f"Inscritos: {ecount} · Resultados: {rcount}")
    toggle = ([btn("🔒 Encerrar etapa", f"adm:stageclose:{stage_id}")]
              if s["status"] == "aberta"
              else [btn("🔓 Reabrir etapa", f"adm:stageopen:{stage_id}")])
    rows = [toggle,
            [btn("🗑️ Excluir etapa", f"adm:stagedel:{stage_id}")],
            [btn("⬅️ Voltar", "adm:stages")]]
    await update.callback_query.edit_message_text(
        txt, reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def admin_stage_new(update, context):
    context.user_data["await"] = "stage_date"
    await update.callback_query.edit_message_text(
        "➕ *Nova etapa*\n\nDigite a *data* da etapa (ex.: `21/06/2026`).\n"
        "Você também pode escrever `hoje`.",
        parse_mode=ParseMode.MARKDOWN)


async def admin_stage_create_with_date(update, context, text):
    iso = texts.parse_date(text)
    if not iso:
        await update.message.reply_text(
            "Data inválida. Use o formato `DD/MM/AAAA` (ex.: `21/06/2026`) ou `hoje`.",
            parse_mode=ParseMode.MARKDOWN)
        return
    conn = db.get_conn()
    try:
        month = db.ensure_current_month(conn)
        conn.commit()
        stage = db.create_stage(conn, month["id"], iso)
        conn.commit()
        label = stage_label(conn, stage)
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text(
        f"✅ Nova etapa aberta: *{label}* ({texts.fmt_date(iso)}).\n"
        "Já pode receber inscrições.",
        reply_markup=kb([[btn("📅 Etapas/Mês", "adm:stages")],
                         [btn("🏠 Menu", "home")]]),
        parse_mode=ParseMode.MARKDOWN)


async def admin_stage_close(update, context, stage_id):
    # 1) Gera o PDF do resultado da etapa ANTES de fechar — se der problema,
    #    o fechamento acontece do mesmo jeito.
    pdf_bytes, pdf_name = None, None
    try:
        from . import report
        conn = db.get_conn()
        try:
            pdf_bytes = report.build_stage_pdf(conn, stage_id)
            pdf_name = report.stage_pdf_filename(conn, stage_id)
        finally:
            conn.close()
    except Exception as e:
        log.warning("Falha ao gerar PDF da etapa: %s", e)
    # 2) Fecha a etapa
    conn = db.get_conn()
    try:
        db.close_stage(conn, stage_id)
        conn.commit()
    finally:
        conn.close()
    await admin_stage_detail(update, context, stage_id)
    # 3) Envia o PDF do resultado da etapa
    if pdf_bytes:
        try:
            import io
            doc = io.BytesIO(pdf_bytes)
            doc.name = pdf_name
            await context.bot.send_document(
                update.effective_chat.id, document=doc, filename=pdf_name,
                caption="📄 Resultado da etapa — Guerra Clube de Tiro")
        except Exception as e:
            log.warning("Falha ao enviar PDF da etapa: %s", e)
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ A etapa foi fechada, mas não consegui enviar o PDF.")


async def admin_stage_reopen(update, context, stage_id):
    conn = db.get_conn()
    try:
        db.reopen_stage(conn, stage_id)
        conn.commit()
    finally:
        conn.close()
    await admin_stage_detail(update, context, stage_id)


async def admin_stage_delete_confirm(update, context, stage_id):
    conn = db.get_conn()
    try:
        s = db.get_stage(conn, stage_id)
        label = stage_label(conn, s)
        rcount = db.stage_result_count(conn, stage_id)
    finally:
        conn.close()
    aviso = (f"⚠️ *Excluir {label}?*\n\n"
             f"Isso apaga a etapa e seus *{rcount} resultado(s)* lançados. "
             "Esta ação não pode ser desfeita.")
    await update.callback_query.edit_message_text(
        aviso, reply_markup=kb([
            [btn("🗑️ Sim, excluir", f"adm:stagedelok:{stage_id}")],
            [btn("⬅️ Cancelar", f"adm:stage:{stage_id}")],
        ]), parse_mode=ParseMode.MARKDOWN)


async def admin_stage_delete(update, context, stage_id):
    conn = db.get_conn()
    try:
        db.delete_stage(conn, stage_id)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Etapa excluída.")
    await admin_stages(update, context)


async def admin_month_wipe_confirm(update, context):
    conn = db.get_conn()
    try:
        month = db.get_open_month(conn)
        mlabel = f"{MONTH_PT[month['month']]}/{month['year']}"
        n = len(db.list_stages(conn, month["id"]))
    finally:
        conn.close()
    await update.callback_query.edit_message_text(
        f"⚠️ *Limpar todo o mês de {mlabel}?*\n\n"
        f"Isso apaga as *{n} etapas* do mês com todas as inscrições e resultados, "
        "deixando o mês em branco para recomeçar. Os atiradores e suas categorias "
        "são mantidos. Esta ação não pode ser desfeita.",
        reply_markup=kb([[btn("🧹 Sim, limpar o mês", "adm:monthwipeok:x")],
                         [btn("⬅️ Cancelar", "adm:stages")]]),
        parse_mode=ParseMode.MARKDOWN)


async def admin_month_wipe(update, context):
    conn = db.get_conn()
    try:
        month = db.get_open_month(conn)
        db.wipe_month(conn, month["id"])
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Mês limpo.")
    await admin_stages(update, context)


async def admin_month_close(update, context):
    conn = db.get_conn()
    try:
        month = db.get_open_month(conn)
        prop = st.promotion_proposal(conn, month["id"])
        mlabel = f"{MONTH_PT[month['month']]}/{month['year']}"
    finally:
        conn.close()
    moves = prop["moves"]
    warnings = prop.get("warnings", [])
    lines = [f"🏁 *Fechamento de {mlabel}*\n",
             "Proposta de subidas/descidas:\n"]
    if not moves:
        lines.append("_Sem mudanças (pouca participação ou empate)._")
    else:
        by_mod = {}
        for m in moves:
            by_mod.setdefault(m.get("modality", "pistola"), []).append(m)
        for mcode, mms in by_mod.items():
            lines.append(f"*{texts.modality_label(mcode)}:*")
            for m in mms:
                arrow = "⬆️" if m["direction"] == "sobe" else "⬇️"
                lines.append(f"{arrow} {m['name']}: {m['from']} → {m['to']}")
            lines.append("")
    if warnings:
        lines.append("⚠️ *Atenção aos limites de vagas:*")
        for w in warnings:
            lines.append(f"• {w}")
        lines.append("")
    lines.append("Confirmar e fechar o mês?")
    context.user_data["month_moves"] = moves
    context.user_data["month_id"] = month["id"]
    await update.callback_query.edit_message_text(
        "\n".join(lines),
        reply_markup=kb([[btn("✅ Confirmar e fechar", "adm:monthconfirm:x")],
                         [btn("⬅️ Cancelar", "adm:stages")]]),
        parse_mode=ParseMode.MARKDOWN)


async def admin_month_confirm(update, context):
    moves = context.user_data.get("month_moves", [])
    month_id = context.user_data.get("month_id")
    # 1) Gera o PDF ANTES de fechar — nunca pode quebrar o fechamento
    pdf_bytes, pdf_name = None, None
    try:
        from . import report
        conn = db.get_conn()
        try:
            pdf_bytes = report.build_month_pdf(conn, month_id, moves)
            pdf_name = report.month_pdf_filename(conn, month_id)
        finally:
            conn.close()
    except Exception as e:
        log.warning("Falha ao gerar PDF do mês: %s", e)
    # 2) Aplica subidas/descidas e fecha o mês
    conn = db.get_conn()
    try:
        st.apply_promotions(conn, moves)
        conn.execute("UPDATE months SET status='fechado' WHERE id=?", (month_id,))
        conn.commit()
        db.ensure_current_month(conn)
        conn.commit()
    finally:
        conn.close()
    context.user_data.clear()
    await update.callback_query.edit_message_text(
        "✅ *Mês fechado!* Categorias atualizadas e novo mês aberto."
        + ("\n\n📄 Enviando o PDF do resultado mensal..." if pdf_bytes else ""),
        reply_markup=kb([[btn("🏠 Menu", "home")]]),
        parse_mode=ParseMode.MARKDOWN)
    # 3) Envia o PDF do resultado mensal
    if pdf_bytes:
        try:
            import io
            doc = io.BytesIO(pdf_bytes)
            doc.name = pdf_name
            await context.bot.send_document(
                update.effective_chat.id, document=doc, filename=pdf_name,
                caption="📄 Resultado mensal — Guerra Clube de Tiro")
        except Exception as e:
            log.warning("Falha ao enviar PDF do mês: %s", e)
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ O mês foi fechado, mas não consegui enviar o PDF.")


# ============================================================================
# Utilidades
# ============================================================================
def stage_label(conn, stage) -> str:
    month = db.get_month(conn, stage["month_id"])
    return f"{stage['number']}ª Etapa · {MONTH_PT[month['month']]}/{month['year']}"


def _modality_button_rows(mods, prefix):
    """Botões das modalidades em pares: prefix:<code>."""
    btns = [btn(f"{texts.modality_emoji(m['code'])} {m['label']}",
                f"{prefix}:{m['code']}") for m in mods]
    return [btns[i:i + 2] for i in range(0, len(btns), 2)]


async def _send_or_edit(update, context, from_text, txt, markup):
    if from_text or not update.callback_query:
        await context.bot.send_message(update.effective_chat.id, txt,
                                       reply_markup=markup,
                                       parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(
            txt, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


async def on_noop(update, context):
    await update.callback_query.answer()


# ============================================================================
# FLUXO: ATIRADORES (cadastro / edição / mesclagem)
# ============================================================================
async def shooters_menu(update, context, role):
    if not can_shooters(role):
        return
    context.user_data.pop("await", None)
    context.user_data.pop("sh", None)
    conn = db.get_conn()
    try:
        ndup = len(db.duplicate_groups(conn))
    finally:
        conn.close()
    rows = [
        [btn("➕ Cadastrar atirador", "sh:new")],
        [btn("🔎 Buscar / editar", "sh:find")],
    ]
    extra = ""
    if ndup:
        rows.append([btn(f"🧩 Resolver duplicados ({ndup})", "sh:dups")])
        extra = f"\n\n⚠️ Encontrei *{ndup}* nome(s) repetido(s) para resolver."
    rows.append([btn("🏠 Menu", "home")])
    await update.callback_query.edit_message_text(
        "👤 *Atiradores*\n\nCadastre, edite ou junte cadastros repetidos." + extra,
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def shooters_router(update, context, role):
    if not can_shooters(role):
        return
    q = update.callback_query
    parts = q.data.split(":")
    sub = parts[1]

    if sub == "new":
        context.user_data["sh"] = {}
        context.user_data["await"] = "sh_new_name"
        return await q.edit_message_text(
            "➕ *Cadastrar atirador*\n\nDigite o *nome completo*:",
            parse_mode=ParseMode.MARKDOWN)
    if sub == "find":
        context.user_data["await"] = "sh_find"
        return await q.edit_message_text(
            "🔎 Digite parte do *nome* do atirador:",
            reply_markup=kb([[btn("⬅️ Voltar", "shooters")]]),
            parse_mode=ParseMode.MARKDOWN)
    if sub == "pick":
        return await sh_detail(update, context, int(parts[2]))
    if sub == "cat":
        return await sh_cat_modality(update, context, int(parts[2]))
    if sub == "catmod":
        return await sh_cat_pick(update, context, int(parts[2]), parts[3])
    if sub == "catset":
        return await sh_cat_set(update, context, int(parts[2]), int(parts[3]))
    if sub == "editname":
        context.user_data["await"] = "sh_edit_name"
        context.user_data["sh"] = {"id": int(parts[2])}
        return await q.edit_message_text("✏️ Digite o *novo nome*:",
                                         parse_mode=ParseMode.MARKDOWN)
    if sub == "editcpf":
        context.user_data["await"] = "sh_edit_cpf"
        context.user_data["sh"] = {"id": int(parts[2])}
        return await q.edit_message_text("✏️ Digite o *CPF*:",
                                         parse_mode=ParseMode.MARKDOWN)
    if sub == "merge":
        context.user_data["sh"] = {"src": int(parts[2])}
        context.user_data["await"] = "sh_merge_search"
        conn = db.get_conn()
        try:
            s = db.get_shooter(conn, int(parts[2]))
        finally:
            conn.close()
        return await q.edit_message_text(
            f"🧩 *Juntar cadastro*\n\nVocê escolheu *{s['name']}* como o cadastro "
            "*duplicado* (que vai sumir).\n\nAgora digite o nome do cadastro "
            "*correto* (que vai ficar):", parse_mode=ParseMode.MARKDOWN)
    if sub == "mergeto":
        src = context.user_data.get("sh", {}).get("src")
        dst = int(parts[2])
        return await sh_merge_confirm(update, context, src, dst)
    if sub == "mergeok":
        return await sh_merge_do(update, context, int(parts[2]), int(parts[3]))
    if sub == "del":
        return await sh_delete_confirm(update, context, int(parts[2]))
    if sub == "delok":
        return await sh_delete(update, context, int(parts[2]))
    if sub == "dups":
        return await sh_dups(update, context)


# ---- cadastro novo ----
async def sh_new_name(update, context, name):
    if len(name) < 3:
        await update.message.reply_text("Nome muito curto. Digite o nome completo.")
        return
    context.user_data["sh"] = {"name": name}
    context.user_data["await"] = "sh_new_cpf"
    await update.message.reply_text(
        f"Nome: *{name}*\n\nAgora o *CPF* (obrigatório):",
        parse_mode=ParseMode.MARKDOWN)


async def sh_new_cpf(update, context, cpf):
    if not util.valid_cpf(cpf):
        await update.message.reply_text(
            "CPF inválido. Digite os 11 números (ex.: 123.456.789-09).")
        return
    name = context.user_data.get("sh", {}).get("name", "")
    conn = db.get_conn()
    try:
        existing = db.get_shooter_by_cpf(conn, cpf)
        if existing:
            context.user_data.pop("await", None)
            await update.message.reply_text(
                f"ℹ️ Esse CPF já está cadastrado para *{existing['name']}*. "
                "Não criei duplicado.", parse_mode=ParseMode.MARKDOWN)
            return await sh_detail(update, context, existing["id"], from_text=True)
        sid = db.create_shooter(conn, name, cpf)
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    context.user_data.pop("sh", None)
    await update.message.reply_text(
        f"✅ Cadastrado: *{name}* · CPF {util.fmt_cpf(cpf)}",
        reply_markup=kb([[btn("👤 Atiradores", "shooters")],
                         [btn("🏠 Menu", "home")]]),
        parse_mode=ParseMode.MARKDOWN)


# ---- busca / detalhe ----
async def sh_do_search(update, context, term):
    if len(term) < 2:
        await update.message.reply_text("Digite ao menos 2 letras.")
        return
    conn = db.get_conn()
    try:
        results = db.search_shooters(conn, term, limit=10)
    finally:
        conn.close()
    rows = [[btn(r["name"], f"sh:pick:{r['id']}")] for r in results]
    rows.append([btn("⬅️ Voltar", "shooters")])
    msg = "Selecione:" if results else "Nenhum encontrado. Tente outro nome."
    await update.message.reply_text(msg, reply_markup=kb(rows))


async def sh_detail(update, context, sid, from_text=False):
    conn = db.get_conn()
    try:
        s = db.get_shooter(conn, sid)
        if not s:
            return
        ncpf = util.fmt_cpf(s["cpf"]) if s["cpf"] else "— (não cadastrado)"
        nenr = db.count_shooter_enrollments(conn, sid)
        cats = []
        for mod in db.list_modalities(conn):
            cid = db.get_shooter_category(conn, sid, mod["code"])
            if cid:
                c = db.get_category(conn, cid)
                cats.append(f"{mod['label']}: {c['name']}")
    finally:
        conn.close()
    txt = (f"👤 *{s['name']}*\n\n"
           f"CPF: {ncpf}\n"
           + ("Categorias: " + " · ".join(cats) + "\n" if cats else "")
           + f"Inscrições registradas: {nenr}")
    rows = [
        [btn("✏️ Editar nome", f"sh:editname:{sid}"),
         btn("✏️ Editar CPF", f"sh:editcpf:{sid}")],
        [btn("🏷️ Alterar categoria", f"sh:cat:{sid}")],
        [btn("🧩 Juntar com duplicado", f"sh:merge:{sid}")],
        [btn("🗑️ Excluir", f"sh:del:{sid}")],
        [btn("⬅️ Voltar", "shooters")],
    ]
    await _send_or_edit(update, context, from_text, txt, kb(rows))


# ---- alterar categoria manualmente ----
async def sh_cat_modality(update, context, sid):
    conn = db.get_conn()
    try:
        s = db.get_shooter(conn, sid)
        mods = db.list_modalities(conn)
    finally:
        conn.close()
    rows = _modality_button_rows(mods, f"sh:catmod:{sid}")
    rows.append([btn("⬅️ Voltar", f"sh:pick:{sid}")])
    await update.callback_query.edit_message_text(
        f"🏷️ *{s['name']}*\n\nAlterar a categoria de qual *tipo*?",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def sh_cat_pick(update, context, sid, mcode):
    conn = db.get_conn()
    try:
        s = db.get_shooter(conn, sid)
        cats = sorted(db.list_categories(conn, mcode), key=lambda c: -c["rank"])
        current = db.get_shooter_category(conn, sid, mcode)
        counts = {c["id"]: db.category_member_count(conn, c["id"]) for c in cats}
    finally:
        conn.close()
    if not cats:
        return await update.callback_query.edit_message_text(
            "Este tipo ainda não tem categorias cadastradas.",
            reply_markup=kb([[btn("⬅️ Voltar", f"sh:cat:{sid}")]]))
    rows = []
    for c in cats:
        mark = "✅ " if c["id"] == current else ""
        lim = f"/{c['max_shooters']}" if c["max_shooters"] else ""
        rows.append([btn(f"{mark}{c['name']} ({counts[c['id']]}{lim})",
                         f"sh:catset:{sid}:{c['id']}")])
    rows.append([btn("⬅️ Voltar", f"sh:cat:{sid}")])
    await update.callback_query.edit_message_text(
        f"🏷️ *{s['name']}* — {texts.modality_label(mcode)}\n\n"
        "Escolha a nova categoria _(entre parênteses: ocupação atual)_:",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def sh_cat_set(update, context, sid, cat_id):
    conn = db.get_conn()
    try:
        cat = db.get_category(conn, cat_id)
        db.set_shooter_category(conn, sid, cat["modality"], cat_id)
        conn.commit()
        n = db.category_member_count(conn, cat_id)
        over = cat["max_shooters"] and n > cat["max_shooters"]
    finally:
        conn.close()
    msg = f"Categoria alterada para {cat['name']}."
    if over:
        msg += f" ⚠️ A categoria passou do limite ({n}/{cat['max_shooters']})."
    await update.callback_query.answer(msg, show_alert=bool(over))
    await sh_detail(update, context, sid)


async def sh_save_name(update, context, name):
    if len(name) < 3:
        await update.message.reply_text("Nome muito curto.")
        return
    sid = context.user_data.get("sh", {}).get("id")
    conn = db.get_conn()
    try:
        db.update_shooter(conn, sid, name=name)
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text("✅ Nome atualizado.")
    await sh_detail(update, context, sid, from_text=True)


async def sh_save_cpf(update, context, cpf):
    if not util.valid_cpf(cpf):
        await update.message.reply_text(
            "CPF inválido. Digite os 11 números (ex.: 123.456.789-09).")
        return
    sid = context.user_data.get("sh", {}).get("id")
    conn = db.get_conn()
    try:
        other = db.get_shooter_by_cpf(conn, cpf)
        if other and other["id"] != sid:
            await update.message.reply_text(
                f"⚠️ Esse CPF já é de *{other['name']}*. Se forem a mesma pessoa, "
                "use *Juntar com duplicado*.", parse_mode=ParseMode.MARKDOWN)
            return
        db.update_shooter(conn, sid, cpf=cpf)
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("await", None)
    await update.message.reply_text("✅ CPF atualizado.")
    await sh_detail(update, context, sid, from_text=True)


# ---- mesclagem ----
async def sh_merge_search(update, context, term):
    src = context.user_data.get("sh", {}).get("src")
    conn = db.get_conn()
    try:
        results = [r for r in db.search_shooters(conn, term, limit=10)
                   if r["id"] != src]
    finally:
        conn.close()
    rows = [[btn(r["name"], f"sh:mergeto:{r['id']}")] for r in results]
    rows.append([btn("⬅️ Cancelar", "shooters")])
    msg = ("Escolha o cadastro *correto* (que vai ficar):" if results
           else "Nenhum encontrado. Tente outro nome.")
    await update.message.reply_text(msg, reply_markup=kb(rows),
                                    parse_mode=ParseMode.MARKDOWN)


async def sh_merge_confirm(update, context, src, dst):
    conn = db.get_conn()
    try:
        a = db.get_shooter(conn, src)
        b = db.get_shooter(conn, dst)
        na = db.count_shooter_enrollments(conn, src)
    finally:
        conn.close()
    await update.callback_query.edit_message_text(
        f"🧩 *Confirmar junção*\n\n"
        f"O cadastro *{a['name']}* (duplicado) será apagado e suas {na} "
        f"inscrição(ões) passam para *{b['name']}*.\n\nConfirmar?",
        reply_markup=kb([[btn("✅ Juntar", f"sh:mergeok:{src}:{dst}")],
                         [btn("⬅️ Cancelar", "shooters")]]),
        parse_mode=ParseMode.MARKDOWN)


async def sh_merge_do(update, context, src, dst):
    conn = db.get_conn()
    try:
        db.merge_shooters(conn, src, dst)
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("sh", None)
    await update.callback_query.answer("Cadastros juntados.")
    await sh_detail(update, context, dst)


# ---- exclusão ----
async def sh_delete_confirm(update, context, sid):
    conn = db.get_conn()
    try:
        s = db.get_shooter(conn, sid)
        n = db.count_shooter_enrollments(conn, sid)
    finally:
        conn.close()
    aviso = (f"⚠️ *Excluir {s['name']}?*\n\n"
             + (f"Ele tem *{n}* inscrição(ões); excluir apaga também os "
                "resultados dele. " if n else "")
             + "Esta ação não pode ser desfeita.")
    await update.callback_query.edit_message_text(
        aviso, reply_markup=kb([[btn("🗑️ Sim, excluir", f"sh:delok:{sid}")],
                                [btn("⬅️ Cancelar", f"sh:pick:{sid}")]]),
        parse_mode=ParseMode.MARKDOWN)


async def sh_delete(update, context, sid):
    conn = db.get_conn()
    try:
        db.delete_shooter(conn, sid)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Atirador excluído.")
    await shooters_menu(update, context, role_of(update.effective_user.id))


# ---- duplicados óbvios (mesmo nome normalizado) ----
async def sh_dups(update, context):
    conn = db.get_conn()
    try:
        groups = db.duplicate_groups(conn)
    finally:
        conn.close()
    if not groups:
        return await update.callback_query.edit_message_text(
            "✅ Nenhum duplicado óbvio encontrado.\n\n"
            "Para juntar cadastros com grafias diferentes, use "
            "*Buscar / editar* ▸ abrir o atirador ▸ *Juntar com duplicado*.",
            reply_markup=kb([[btn("⬅️ Voltar", "shooters")]]),
            parse_mode=ParseMode.MARKDOWN)
    rows = []
    lines = ["🧩 *Duplicados encontrados*\nToque para abrir e juntar:\n"]
    for g in groups[:8]:
        lines.append("• " + " = ".join(s["name"] for s in g))
        for s in g:
            rows.append([btn(f"Abrir: {s['name']}", f"sh:pick:{s['id']}")])
    rows.append([btn("⬅️ Voltar", "shooters")])
    await update.callback_query.edit_message_text(
        "\n".join(lines), reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


# ============================================================================
# FLUXO: CONSULTAR LANÇAMENTOS (passadas da etapa aberta)
# ============================================================================
def _pen_seconds(r):
    return r["pen2"] * 2 + r["pen5"] * 5 + r["pen10"] * 10


def _run_label(idx, r):
    if r["dq"]:
        return f"Passada {idx}: DQ"
    pen = _pen_seconds(r)
    base = texts.fmt_time(r["final_time"])
    if pen:
        return f"Passada {idx}: {base} (bruto {texts.fmt_time(r['raw_time'])} +{pen}s)"
    return f"Passada {idx}: {base}"


async def lanc_menu(update, context, role):
    if not can_result(role):
        return
    conn = db.get_conn()
    try:
        stage = db.get_open_stage(conn)
        if not stage:
            return await update.callback_query.edit_message_text(
                "Nenhuma etapa aberta no momento.",
                reply_markup=kb([[btn("🏠 Menu", "home")]]))
        shooters = db.stage_shooters_with_runs(conn, stage["id"])
        label = f"{stage['number']}ª Etapa · {texts.fmt_date(stage['date'])}"
    finally:
        conn.close()
    if not shooters:
        return await update.callback_query.edit_message_text(
            f"🔍 *Lançamentos — {label}*\n\nNenhuma passada lançada ainda.",
            reply_markup=kb([[btn("🏠 Menu", "home")]]),
            parse_mode=ParseMode.MARKDOWN)
    rows = [[btn(f"{s['name']} · {s['n_runs']} passada"
                 f"{'s' if s['n_runs'] != 1 else ''}", f"lc:sh:{s['id']}")]
            for s in shooters]
    rows.append([btn("🏠 Menu", "home")])
    await update.callback_query.edit_message_text(
        f"🔍 *Lançamentos — {label}*\n\nToque num atirador para ver as passadas:",
        reply_markup=kb(rows), parse_mode=ParseMode.MARKDOWN)


async def lanc_router(update, context, role):
    if not can_result(role):
        return
    parts = update.callback_query.data.split(":")
    sub = parts[1]
    if sub == "sh":
        return await lanc_shooter(update, context, int(parts[2]))
    if sub == "del":
        return await lanc_del_confirm(update, context, int(parts[2]), int(parts[3]))
    if sub == "delok":
        return await lanc_del(update, context, int(parts[2]), int(parts[3]))


async def lanc_shooter(update, context, sid):
    conn = db.get_conn()
    try:
        stage = db.get_open_stage(conn)
        if not stage:
            return await lanc_menu(update, context,
                                   role_of(update.effective_user.id))
        sh = db.get_shooter(conn, sid)
        enrolls = db.shooter_stage_enrollments(conn, stage["id"], sid)
        blocks = []   # (titulo, [(run_id, label)])
        for e in enrolls:
            cat = db.get_category(conn, e["category_id"])
            runs = db.list_runs(conn, e["id"])
            if not runs:
                continue
            title = f"{texts.modality_label(e['modality'])} · {cat['name']}"
            items = [(r["id"], _run_label(i, r)) for i, r in enumerate(runs, 1)]
            blocks.append((title, items))
    finally:
        conn.close()
    if not blocks:
        return await update.callback_query.edit_message_text(
            f"*{sh['name']}* não tem passadas nesta etapa.",
            reply_markup=kb([[btn("⬅️ Voltar", "lanc")]]),
            parse_mode=ParseMode.MARKDOWN)
    lines = [f"🎯 *{sh['name']}*", ""]
    rows = []
    for title, items in blocks:
        lines.append(f"*{title}*")
        for run_id, label in items:
            lines.append(f"  • {label}")
            rows.append([btn(f"🗑️ {label}", f"lc:del:{run_id}:{sid}")])
        lines.append("")
    rows.append([btn("⬅️ Voltar", "lanc")])
    await update.callback_query.edit_message_text(
        "\n".join(lines).strip(), reply_markup=kb(rows),
        parse_mode=ParseMode.MARKDOWN)


async def lanc_del_confirm(update, context, run_id, sid):
    conn = db.get_conn()
    try:
        r = db.get_run(conn, run_id)
        if not r:
            return await lanc_shooter(update, context, sid)
        label = _run_label(0, r).split(": ", 1)[-1]
    finally:
        conn.close()
    await update.callback_query.edit_message_text(
        f"🗑️ Excluir esta passada?\n\n*{label}*\n\nNão pode ser desfeito.",
        reply_markup=kb([[btn("🗑️ Sim, excluir", f"lc:delok:{run_id}:{sid}")],
                         [btn("⬅️ Cancelar", f"lc:sh:{sid}")]]),
        parse_mode=ParseMode.MARKDOWN)


async def lanc_del(update, context, run_id, sid):
    conn = db.get_conn()
    try:
        db.delete_run(conn, run_id)
        conn.commit()
    finally:
        conn.close()
    await update.callback_query.answer("Passada excluída.")
    await lanc_shooter(update, context, sid)


# ============================================================================
# Construção da aplicação
# ============================================================================
def build_application() -> Application:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN não definido nas variáveis de ambiente.")
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app
