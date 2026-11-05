"""
Ponto de entrada único: sobe o servidor web (FastAPI).

- Site público de resultados: tirodeguerra.com.br
- Área interna: admin.tirodeguerra.com.br (ver app/hosts.py e ADMIN_HOST)
Tudo usa o mesmo banco SQLite (volume persistente).
"""
import contextlib
import logging
import os
import shutil

import uvicorn
from fastapi import FastAPI

from app import config, db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("main")


def seed_if_empty():
    """Na primeira execução (volume vazio), carrega o histórico já pronto.
    Para começar do zero, basta apagar a pasta 'seed/' antes de implantar."""
    seed = os.path.join(os.path.dirname(__file__), "seed", "tiro_guerra.db")
    if not os.path.exists(config.DB_PATH) and os.path.exists(seed):
        os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
        shutil.copy(seed, config.DB_PATH)
        log.info("Histórico carregado a partir de seed/tiro_guerra.db")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    seed_if_empty()
    db.init_db()
    log.info("Banco inicializado em %s", config.DB_PATH)
    # primeiro administrador da área interna (só se não houver nenhum usuário)
    from app import webdb
    conn = db.get_conn()
    try:
        pw = webdb.ensure_admin(conn, config.ADMIN_USER, config.ADMIN_PASSWORD)
        conn.commit()
    finally:
        conn.close()
    if pw:
        log.warning("=== Área interna: usuário '%s' criado. Senha: %s ===",
                    config.ADMIN_USER, pw if not config.ADMIN_PASSWORD else "(a de ADMIN_PASSWORD)")
    if config.ADMIN_HOST:
        log.info("Área interna em %s", config.ADMIN_HOST)
    yield


web = FastAPI(title=f"{config.CLUB_NAME} — Resultados", lifespan=lifespan)

# Site público de resultados + rota /health
from app.website import register_web  # noqa: E402
register_web(web)

# Área interna: login, linha de tiro, inscrições, administração
from app.interno import router as interno_router  # noqa: E402
from app.interno_admin import router as interno_admin_router  # noqa: E402
web.include_router(interno_router)
web.include_router(interno_admin_router)

# Autoinscrição pública na etapa aberta (/inscrever-me, via QR code)
from app.autoinscricao import router as autoinscricao_router  # noqa: E402
web.include_router(autoinscricao_router)

# Subdomínio da área interna (admin.*) -> rotas /interno
from app.hosts import AdminHostMiddleware  # noqa: E402
web.add_middleware(AdminHostMiddleware, admin_host=config.ADMIN_HOST)


if __name__ == "__main__":
    # atrás do proxy do Coolify: confia no X-Forwarded-Proto para saber que é
    # HTTPS (cookie de sessão com "secure" e redirecionamentos corretos)
    uvicorn.run(web, host="0.0.0.0", port=config.WEB_PORT,
                proxy_headers=True, forwarded_allow_ips="*")
