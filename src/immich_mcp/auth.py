import hmac

from fastmcp.server.auth import AccessToken, TokenVerifier


class SharedSecretVerifier(TokenVerifier):
    """Accepts `Authorization: Bearer <token>` only when it matches the configured secret."""

    def __init__(self, secret: str):
        if not secret:
            raise ValueError("MCP auth token must not be empty")
        super().__init__()
        self._secret = secret.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if hmac.compare_digest(token.encode(), self._secret):
            return AccessToken(token=token, client_id="immich-mcp-client", scopes=[])
        return None
