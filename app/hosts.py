"""
Área interna no subdomínio próprio (ex.: admin.tirodeguerra.com.br).

As rotas continuam registradas sob /interno (APIRouter), mas os links das
páginas são gerados sem esse prefixo. Este middleware faz a ponte:

- host da área interna: "/linha" é atendido pela rota "/interno/linha";
  "/interno/..." (endereço antigo) redireciona para o caminho limpo;
  /static e /health passam direto.
- qualquer outro host: "/interno/..." redireciona para o subdomínio, para
  favoritos e QR codes antigos continuarem funcionando.

O caminho original fica em request.state.public_path (ver interno.public_path).
"""
from urllib.parse import quote

PREFIX = "/interno"
_PASSTHROUGH = ("/static/", "/health")


def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers") or []:
        if k == name:
            return v.decode("latin-1")
    return ""


async def _redirect(send, location: str, status: int) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"location", location.encode("latin-1")),
                            (b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


class AdminHostMiddleware:
    def __init__(self, app, admin_host: str):
        self.app = app
        self.admin_host = (admin_host or "").lower()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.admin_host:
            return await self.app(scope, receive, send)
        host_port = _header(scope, b"host").lower()
        host = host_port.split(":")[0]
        path = scope["path"]
        qs = scope.get("query_string", b"").decode("latin-1")
        is_prefixed = path == PREFIX or path.startswith(PREFIX + "/")

        if host == self.admin_host.split(":")[0]:
            if is_prefixed:
                # 308 mantém método e corpo (POST de formulário antigo continua valendo)
                clean = path[len(PREFIX):] or "/"
                return await _redirect(send, quote(clean) + (f"?{qs}" if qs else ""), 308)
            if not path.startswith(_PASSTHROUGH):
                scope = dict(scope)
                scope["state"] = dict(scope.get("state") or {}, public_path=path)
                inner = PREFIX + (path if path != "/" else "")
                scope["path"] = inner
                scope["raw_path"] = inner.encode("utf-8")
            return await self.app(scope, receive, send)

        if is_prefixed:
            proto = _header(scope, b"x-forwarded-proto") or scope.get("scheme", "https")
            port = host_port.split(":")[1] if ":" in host_port else ""
            target = self.admin_host + (f":{port}" if port and ":" not in self.admin_host else "")
            clean = path[len(PREFIX):] or "/"
            return await _redirect(send, f"{proto}://{target}{quote(clean)}"
                                   + (f"?{qs}" if qs else ""), 301)
        return await self.app(scope, receive, send)
