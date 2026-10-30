"""Configurações lidas das variáveis de ambiente."""
import os

# Caminho do banco de dados (fica num volume persistente no Coolify).
DB_PATH = os.environ.get("DB_PATH", "data/tiro_guerra.db")

# Nome do clube (usado no site, na área interna e nos PDFs).
CLUB_NAME = os.environ.get("CLUB_NAME", "GUERRA Clube de Tiro")

# Área interna: primeiro administrador.
# Só é usado se ainda não existir nenhum usuário cadastrado.
ADMIN_USER = os.environ.get("ADMIN_USER", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

# Domínio da área interna. Requisições nele vão para as páginas internas;
# em qualquer outro domínio, /interno/... redireciona para cá.
# Vazio desliga o subdomínio (área interna fica só em /interno).
ADMIN_HOST = os.environ.get("ADMIN_HOST", "admin.tirodeguerra.com.br").strip().lower()

# Endereço público do site (impresso no QR do cupom térmico).
SITE_URL = os.environ.get("SITE_URL", "https://tirodeguerra.com.br").strip()

# Porta do servidor web.
WEB_PORT = int(os.environ.get("PORT", "8000"))

# Fuso horário.
TZ = os.environ.get("TZ", "America/Sao_Paulo")
