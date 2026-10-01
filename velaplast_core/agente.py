"""Gancho do agente (gateway velaplast-mcp → app Flask).

Com o header X-Agente-Assertion (JWT EdDSA assinado pelo gateway), a request roda como o
usuário LOCAL do app com aquele e-mail — papel e filtros do app valem. Sem o header, nada
muda. Onda 1a: só GET. Onda 1b: escrita só na lista fechada do app, com jti de uso único e hash do corpo. Chave pública em AGENTE_GATEWAY_PUBKEY (PEM; aceita '\\n' literal).
"""
from __future__ import annotations

import hashlib
import hmac
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


def _checar_escrita(claims: dict, permitidas: frozenset, registrar_jti):
    if not permitidas:
        return jsonify({"error": "metodo_nao_permitido"}), 403
    regra = request.url_rule.rule if request.url_rule is not None else None
    if (request.method, regra) not in permitidas:
        return jsonify({"error": "escrita_nao_permitida"}), 403
    jti, esperado = claims.get("jti"), claims.get("corpo_sha256")
    if not jti or not esperado:
        return jsonify({"error": "assercao_invalida", "motivo": "sem_jti_ou_corpo"}), 401
    recebido = hashlib.sha256(request.get_data(cache=True)).hexdigest()
    if not hmac.compare_digest(recebido, str(esperado)):
        return jsonify({"error": "assercao_invalida", "motivo": "corpo_divergente"}), 401
    if not registrar_jti(str(jti), int(claims["exp"])):
        return jsonify({"error": "assercao_reutilizada"}), 409
    return None


def instalar(app: Flask, app_id: str, carregar_usuario: Callable[[str], dict | None],
             escritas: set[tuple[str, str]] | None = None,
             registrar_jti: Callable[[str, int], bool] | None = None) -> None:
    """carregar_usuario(email) devolve o dict no formato do request.user do app, ou None (sem acesso).
    escritas = {(METODO, regra Flask)} liberadas ao agente; exige registrar_jti(jti, exp) -> True se inédito."""
    if escritas is not None and registrar_jti is None:
        raise ValueError("escritas exige registrar_jti (anti-replay)")
    permitidas = frozenset((m.upper(), r) for m, r in (escritas or ()))

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
            erro = _checar_escrita(claims, permitidas, registrar_jti)
            if erro is not None:
                return erro
        usuario = carregar_usuario(str(claims["sub"]).strip().lower())
        if not usuario:
            return jsonify({"error": "usuario_sem_acesso"}), 403
        g.agente_usuario = dict(usuario, via_agente=True)
        g.agente_assercao = claims
        return None

    def _eu():
        u = usuario_do_agente()
        if not u:
            return jsonify({"error": "nao_autenticado"}), 401
        return jsonify({"user_id": u.get("user_id"), "email": u.get("email"),
                        "nome": u.get("nome") or u.get("name"), "role": u.get("role")})

    app.add_url_rule("/_agente/eu", "velaplast_agente_eu", _eu, methods=["GET"])


def usuario_do_agente() -> dict | None:
    if not has_request_context():
        return None
    return g.get("agente_usuario")


def via_agente() -> bool:
    return usuario_do_agente() is not None


__all__ = ["HEADER", "EMISSOR", "AssercaoInvalida", "verificar", "instalar", "usuario_do_agente", "via_agente"]
