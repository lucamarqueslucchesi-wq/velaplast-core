"""
auth.py — Primitivas de autenticacao compartilhadas (JWT + bcrypt) e decorators Flask.

Modulo unificado do monorepo Velaplast. Fornece apenas as primitivas:
    - hash/verify de senha (bcrypt)
    - encode/decode de JWT (HS256)
    - decorators `login_required` e `role_required` para Flask

Nao inclui endpoints de login, criacao de usuarios ou queries de DB:
isso e responsabilidade de cada projeto consumidor, pois os schemas de
usuario divergem (ex: velaplast-sap tem `vendedor_code`/`is_sdr`; o
command-center nao). Cada projeto define seu proprio conjunto de papeis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable

import bcrypt
import jwt
from flask import jsonify, request

from velaplast_core.config import get_jwt_expiry_hours, get_jwt_secret

log = logging.getLogger(__name__)

_JWT_ALGORITHM = "HS256"


class AuthError(Exception):
    """Erro de autenticacao — token invalido, expirado ou ausente."""


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Gera um hash bcrypt da senha em texto puro.

    Args:
        plain: senha em texto claro.

    Returns:
        Hash bcrypt como string UTF-8, pronto para persistir no banco.
    """
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verifica se a senha em texto puro corresponde ao hash armazenado.

    Args:
        plain: senha em texto claro digitada pelo usuario.
        hashed: hash bcrypt previamente armazenado.

    Returns:
        True se a senha bate; False caso contrario (inclusive se o hash
        estiver corrompido ou vazio).
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _get_secret() -> str:
    """Retorna o segredo JWT, logando warning se estiver vazio.

    Nao aborta — deixa a decisao para o projeto consumidor. Com secret
    vazio o jwt.encode ainda funciona, porem sem garantia de integridade.
    """
    secret = get_jwt_secret() or ""
    if not secret:
        log.warning(
            "JWT secret vazio — tokens serao assinados com string vazia. "
            "Defina a env var apropriada (ex: SECRET_KEY) antes de produzir."
        )
    return secret


def generate_token(payload: dict[str, Any]) -> str:
    """Gera um JWT HS256 a partir do payload fornecido.

    Adiciona automaticamente a claim `exp` baseada em `get_jwt_expiry_hours()`.
    Se o payload ja contiver `exp`, ele e preservado.

    Args:
        payload: dicionario com as claims do token (ex: user_id, email, role).

    Returns:
        Token JWT codificado como string.
    """
    data = dict(payload)
    if "exp" not in data:
        expiry_hours = get_jwt_expiry_hours()
        data["exp"] = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
    return jwt.encode(data, _get_secret(), algorithm=_JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decodifica e valida um JWT HS256.

    Args:
        token: string do token JWT.

    Returns:
        Payload decodificado como dicionario.

    Raises:
        AuthError: se o token estiver ausente, expirado ou invalido.
    """
    if not token:
        raise AuthError("Token nao fornecido")
    try:
        return jwt.decode(token, _get_secret(), algorithms=[_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Token invalido") from exc


# ---------------------------------------------------------------------------
# Flask decorators
# ---------------------------------------------------------------------------

def _extract_token() -> str | None:
    """Extrai o token do header `Authorization: Bearer <token>` ou do cookie `token`."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip() or None
    cookie_token = request.cookies.get("token")
    return cookie_token or None


def login_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator Flask que exige JWT valido.

    Le `Authorization: Bearer <token>` (ou cookie `token`), decoda, e
    anexa o payload em `request.user`. Retorna 401 JSON se ausente ou
    invalido.
    """

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        token = _extract_token()
        if not token:
            return jsonify({"error": "Token nao fornecido"}), 401
        try:
            payload = decode_token(token)
        except AuthError as exc:
            return jsonify({"error": str(exc)}), 401
        request.user = payload  # type: ignore[attr-defined]
        return f(*args, **kwargs)

    return decorated


def role_required(*roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator Flask que exige JWT valido E role em `roles`.

    Aplica `login_required` internamente. Retorna 403 JSON se o role
    do usuario nao estiver na lista. Aceita qualquer string de role —
    cada projeto define seu proprio conjunto (admin, gestor, vendedor,
    etc).

    Args:
        *roles: roles aceitos para acessar o endpoint.
    """

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        @login_required
        def decorated(*args: Any, **kwargs: Any) -> Any:
            user_role = request.user.get("role")  # type: ignore[attr-defined]
            if user_role not in roles:
                return jsonify({"error": "Sem permissao"}), 403
            return f(*args, **kwargs)

        return decorated

    return decorator


__all__ = [
    "AuthError",
    "hash_password",
    "verify_password",
    "generate_token",
    "decode_token",
    "login_required",
    "role_required",
]
