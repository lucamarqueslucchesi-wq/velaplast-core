"""Gancho do agente (gateway velaplast-mcp → app Flask).

Com o header X-Agente-Assertion (JWT EdDSA assinado pelo gateway), a request roda como o
usuário LOCAL do app com aquele e-mail — papel e filtros do app valem. Sem o header, nada
muda. Onda 1a: só GET. Chave pública em AGENTE_GATEWAY_PUBKEY (PEM; aceita '\\n' literal).
"""
from __future__ import annotations

import os
from typing import Any, Callable

import jwt
from flask import Flask, g, has_request_context, jsonify, request

HEADER = "X-Agente-Assertion"
EMISSOR = "velaplast-mcp"
TOLERANCIA_S = 10
VIDA_MAXIMA_S = 120
METODOS_PERMITIDOS = frozenset({"GET"})
_OBRIGATORIAS = ["exp", "iat", "iss", "aud", "sub", "metodo", "rota"]


class AssercaoInvalida(Exception):
    """Motivo curto em str(e): expirada, rota_ou_metodo, vida_longa, sem_chave_publica, <Erro do PyJWT>."""


def _chave_publica() -> str | None:
    pem = os.environ.get("AGENTE_GATEWAY_PUBKEY", "").strip().replace("\\n", "\n")
    return pem or None


def verificar(token: str, app_id: str, metodo: str, rota: str) -> dict[str, Any]:
    pem = _chave_publica()
    if not pem:
        raise AssercaoInvalida("sem_chave_publica")
    try:
        claims = jwt.decode(
            token, pem, algorithms=["EdDSA"], audience=app_id, issuer=EMISSOR,
            leeway=TOLERANCIA_S, options={"require": _OBRIGATORIAS},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AssercaoInvalida("expirada") from exc
    except jwt.InvalidKeyError as exc:
        raise AssercaoInvalida("chave_publica_invalida") from exc
    except jwt.InvalidTokenError as exc:
        raise AssercaoInvalida(type(exc).__name__) from exc
    if int(claims["exp"]) - int(claims["iat"]) > VIDA_MAXIMA_S:
        raise AssercaoInvalida("vida_longa")
    if claims["metodo"] != metodo or claims["rota"] != rota:
        raise AssercaoInvalida("rota_ou_metodo")
    return claims


def instalar(app: Flask, app_id: str, carregar_usuario: Callable[[str], dict | None]) -> None:
    """carregar_usuario(email) devolve o dict no formato do request.user do app, ou None (sem acesso)."""

    @app.before_request
    def _agente():
        token = request.headers.get(HEADER)
        if token is None:
            return None
        try:
            claims = verificar(token, app_id, request.method, request.path)
        except AssercaoInvalida as exc:
            return jsonify({"error": "assercao_invalida", "motivo": str(exc)}), 401
        if request.method not in METODOS_PERMITIDOS:
            return jsonify({"error": "metodo_nao_permitido"}), 403
        usuario = carregar_usuario(str(claims["sub"]).strip().lower())
        if not usuario:
            return jsonify({"error": "usuario_sem_acesso"}), 403
        g.agente_usuario = dict(usuario, via_agente=True)
        g.agente_assercao = claims
        return None


def usuario_do_agente() -> dict | None:
    if not has_request_context():
        return None
    return g.get("agente_usuario")


__all__ = ["HEADER", "EMISSOR", "AssercaoInvalida", "verificar", "instalar", "usuario_do_agente"]
