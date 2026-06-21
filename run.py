"""
Ponto de entrada único.

Sobe o servidor web (FastAPI) e o bot do Telegram no mesmo processo.
- O bot funciona por "polling" (não precisa configurar URL/HTTPS).
- O site entra na próxima fase usando as mesmas funções de classificação.
Tudo compartilha o mesmo banco SQLite (volume persistente).
"""
import asyncio
import contextlib
import logging
import os
import shutil

import uvicorn
from fastapi import FastAPI

from app import config, db
from app.bot import build_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("main")

application = build_application()  # bot do Telegram


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
    # inicia o bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    log.info("Bot do Telegram iniciado (polling).")
    try:
        yield
    finally:
        log.info("Encerrando bot...")
        with contextlib.suppress(Exception):
            await application.updater.stop()
        with contextlib.suppress(Exception):
            await application.stop()
        with contextlib.suppress(Exception):
            await application.shutdown()


web = FastAPI(title=f"{config.CLUB_NAME} — Resultados", lifespan=lifespan)

# Site público de resultados + rota /health
from app.website import register_web  # noqa: E402
register_web(web)


if __name__ == "__main__":
    uvicorn.run(web, host="0.0.0.0", port=config.WEB_PORT)
