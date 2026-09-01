"""Configurações lidas das variáveis de ambiente."""
import os

# Token do bot (criado no @BotFather). NUNCA escreva o token no código.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# IDs do Telegram dos administradores (separados por vírgula).
# Ex.: ADMIN_IDS=123456789,987654321
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Caminho do banco de dados (fica num volume persistente no Coolify).
DB_PATH = os.environ.get("DB_PATH", "data/tiro_guerra.db")

# Nome do clube (usado em mensagens e, depois, no site).
CLUB_NAME = os.environ.get("CLUB_NAME", "GUERRA Clube de Tiro")

# Área interna do site (/interno): primeiro administrador.
# Só é usado se ainda não existir nenhum usuário cadastrado.
ADMIN_USER = os.environ.get("ADMIN_USER", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# Endereço público do site (impresso no QR do cupom térmico).
SITE_URL = os.environ.get("SITE_URL", "https://tirodeguerra.com.br").strip()

# Porta do servidor web.
WEB_PORT = int(os.environ.get("PORT", "8000"))

# Fuso horário.
TZ = os.environ.get("TZ", "America/Sao_Paulo")
