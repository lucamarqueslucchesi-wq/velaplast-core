import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask, jsonify, request

from velaplast_core import agente
from velaplast_core.auth import generate_token, login_required, role_required

USUARIOS = {
    "luca@velaplast.com.br": {"user_id": 1, "email": "luca@velaplast.com.br", "role": "admin"},
    "ana@velaplast.com.br": {"user_id": 2, "email": "ana@velaplast.com.br", "role": "vendedor"},
}


def _par():
    priv = Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv_pem, pub_pem


PRIV, PUB = _par()
OUTRA_PRIV, _ = _par()


def _token(sub="luca@velaplast.com.br", aud="crm", metodo="GET", rota="/x", iat=None, vida=60, chave=None, **extra):
    iat = int(time.time()) if iat is None else iat
    claims = {"iss": "velaplast-mcp", "aud": aud, "sub": sub, "metodo": metodo, "rota": rota,
              "iat": iat, "exp": iat + vida, "jti": "j1", **extra}
    return jwt.encode(claims, chave or PRIV, algorithm="EdDSA")


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("AGENTE_GATEWAY_PUBKEY", PUB)
    monkeypatch.setenv("JWT_SECRET", "segredo-teste")
    vistos = []

    def carregar(email):
        vistos.append(email)
        return USUARIOS.get(email)

    app = Flask(__name__)
    agente.instalar(app, "crm", carregar)

    @app.route("/x", methods=["GET", "POST"])
    @login_required
    def x():
        return jsonify(request.user)

    @app.get("/so-admin")
    @role_required("admin")
    def so_admin():
        return jsonify(ok=True)

    c = app.test_client()
    c.vistos = vistos
    return c


def _h(tok):
    return {"X-Agente-Assertion": tok}


def test_sem_header_e_sem_bearer_segue_401_como_hoje(cliente):
    r = cliente.get("/x")
    assert r.status_code == 401 and r.get_json()["error"] == "Token nao fornecido"


def test_sem_header_com_bearer_segue_funcionando(cliente):
    tok = generate_token({"user_id": 9, "email": "b@v.com", "role": "gestor"})
    r = cliente.get("/x", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.get_json()["user_id"] == 9


def test_assercao_valida_vira_request_user(cliente):
    r = cliente.get("/x", headers=_h(_token()))
    assert r.status_code == 200
    u = r.get_json()
    assert u["user_id"] == 1 and u["role"] == "admin" and u["via_agente"] is True


def test_email_normalizado(cliente):
    r = cliente.get("/x", headers=_h(_token(sub="  Luca@Velaplast.com.br ")))
    assert r.status_code == 200 and cliente.vistos[-1] == "luca@velaplast.com.br"


def test_aud_errado_da_401(cliente):
    r = cliente.get("/x", headers=_h(_token(aud="estoque")))
    assert r.status_code == 401 and r.get_json()["error"] == "assercao_invalida"


def test_expirada_da_401(cliente):
    r = cliente.get("/x", headers=_h(_token(iat=int(time.time()) - 200)))
    assert r.status_code == 401 and r.get_json()["motivo"] == "expirada"


def test_assinada_por_outra_chave_da_401(cliente):
    assert cliente.get("/x", headers=_h(_token(chave=OUTRA_PRIV))).status_code == 401


def test_rota_trocada_da_401(cliente):
    r = cliente.get("/x", headers=_h(_token(rota="/outra")))
    assert r.status_code == 401 and r.get_json()["motivo"] == "rota_ou_metodo"


def test_vida_longa_da_401(cliente):
    r = cliente.get("/x", headers=_h(_token(vida=3600)))
    assert r.status_code == 401 and r.get_json()["motivo"] == "vida_longa"


def test_emissor_errado_da_401(cliente):
    tok = jwt.encode({"iss": "outro", "aud": "crm", "sub": "luca@velaplast.com.br", "metodo": "GET", "rota": "/x",
                      "iat": int(time.time()), "exp": int(time.time()) + 60}, PRIV, algorithm="EdDSA")
    assert cliente.get("/x", headers=_h(tok)).status_code == 401


def test_sem_claim_rota_da_401(cliente):
    tok = jwt.encode({"iss": "velaplast-mcp", "aud": "crm", "sub": "luca@velaplast.com.br", "metodo": "GET",
                      "iat": int(time.time()), "exp": int(time.time()) + 60}, PRIV, algorithm="EdDSA")
    assert cliente.get("/x", headers=_h(tok)).status_code == 401


def test_post_com_assercao_valida_da_403(cliente):
    r = cliente.post("/x", headers=_h(_token(metodo="POST")))
    assert r.status_code == 403 and r.get_json()["error"] == "metodo_nao_permitido"


def test_email_sem_usuario_da_403(cliente):
    r = cliente.get("/x", headers=_h(_token(sub="fulano@x.com")))
    assert r.status_code == 403 and r.get_json()["error"] == "usuario_sem_acesso"


def test_sem_chave_publica_recusa(cliente, monkeypatch):
    monkeypatch.delenv("AGENTE_GATEWAY_PUBKEY")
    r = cliente.get("/x", headers=_h(_token()))
    assert r.status_code == 401 and r.get_json()["motivo"] == "sem_chave_publica"


def test_pem_em_uma_linha_com_barra_n(cliente, monkeypatch):
    monkeypatch.setenv("AGENTE_GATEWAY_PUBKEY", PUB.replace("\n", "\\n"))
    assert cliente.get("/x", headers=_h(_token())).status_code == 200


def test_header_vazio_da_401(cliente):
    assert cliente.get("/x", headers=_h("")).status_code == 401


def test_role_required_respeita_papel_do_agente(cliente):
    ok = cliente.get("/so-admin", headers=_h(_token(rota="/so-admin")))
    nao = cliente.get("/so-admin", headers=_h(_token(sub="ana@velaplast.com.br", rota="/so-admin")))
    assert ok.status_code == 200 and nao.status_code == 403
