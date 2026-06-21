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
from fastapi.responses import HTMLResponse, JSONResponse

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


@web.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@web.get("/", response_class=HTMLResponse)
async def home():
    # Página placeholder. O site de resultados entra na próxima fase.
    return f"""<!doctype html><html lang="pt-br"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{config.CLUB_NAME}</title>
    <style>
      body{{margin:0;font-family:system-ui,sans-serif;background:#0e1a12;color:#e8e2cf;
           display:flex;min-height:100vh;align-items:center;justify-content:center;text-align:center}}
      h1{{color:#c9b87a;letter-spacing:2px}} p{{opacity:.7}}
    </style></head><body><div>
      <h1>{config.CLUB_NAME.upper()}</h1>
      <p>Sistema no ar. O bot do Telegram está ativo.<br>
      O site de resultados será publicado aqui em breve.</p>
    </div></body></html>"""


if __name__ == "__main__":
    uvicorn.run(web, host="0.0.0.0", port=config.WEB_PORT)
