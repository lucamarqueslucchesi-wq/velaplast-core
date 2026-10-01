import hashlib

import pytest
from flask import Flask

from velaplast_core import agente
from velaplast_core.auth import login_required
from tests.test_agente import PUB, _token

LUCA = "luca@velaplast.com.br"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _carregar(email):
    return {"user_id": 1, "email": email, "name": "Luca", "role": "admin"} if email == LUCA else None


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("AGENTE_GATEWAY_PUBKEY", PUB)
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    vistos = set()

    def registrar(jti, exp):
        if jti in vistos:
            return False
        vistos.add(jti)
        return True

    app = Flask(__name__)
    agente.instalar(app, "crm", _carregar, escritas={("POST", "/w/<int:wid>")}, registrar_jti=registrar)

    @app.route("/w/<int:wid>", methods=["POST", "DELETE"])
    @login_required
    def w(wid):
        return {"ok": True, "via": agente.via_agente(), "wid": wid}

    @app.route("/fora", methods=["POST"])
    @login_required
    def fora():
        return {"ok": True}

    return app.test_client()


def _post(c, rota, corpo: bytes, **claims):
    tok = _token(LUCA, metodo="POST", rota=rota, **claims)
    return c.post(rota, data=corpo, content_type="application/json", headers={"X-Agente-Assertion": tok})


def test_escrita_na_lista_executa_e_marca_via_agente(cliente):
    corpo = b'{"a":1}'
    r = _post(cliente, "/w/7", corpo, jti="j1", corpo_sha256=_sha(corpo))
    assert r.status_code == 200 and r.json == {"ok": True, "via": True, "wid": 7}


def test_mesma_assercao_duas_vezes_da_409(cliente):
    corpo = b'{"a":1}'
    tok = _token(LUCA, metodo="POST", rota="/w/7", jti="j2", corpo_sha256=_sha(corpo))
    h = {"X-Agente-Assertion": tok}
    assert cliente.post("/w/7", data=corpo, content_type="application/json", headers=h).status_code == 200
    r = cliente.post("/w/7", data=corpo, content_type="application/json", headers=h)
    assert r.status_code == 409 and r.json["error"] == "assercao_reutilizada"


def test_corpo_trocado_da_401(cliente):
    r = _post(cliente, "/w/7", b'{"a":2}', jti="j3", corpo_sha256=_sha(b'{"a":1}'))
    assert r.status_code == 401 and r.json["motivo"] == "corpo_divergente"


@pytest.mark.parametrize("faltando", ["jti", "corpo_sha256"])
def test_escrita_sem_jti_ou_hash_da_401(cliente, faltando):
    # _token já injeta jti="j1" por padrão e o PyJWT recusa jti não-str; "" sobrescreve e equivale a ausente
    claims = {"jti": "j4", "corpo_sha256": _sha(b"{}")}
    claims[faltando] = ""
    r = _post(cliente, "/w/7", b"{}", **claims)
    assert r.status_code == 401 and r.json["motivo"] == "sem_jti_ou_corpo"


def test_metodo_fora_da_lista_da_403(cliente):
    tok = _token(LUCA, metodo="DELETE", rota="/w/7", jti="j5", corpo_sha256=_sha(b""))
    r = cliente.delete("/w/7", headers={"X-Agente-Assertion": tok})
    assert r.status_code == 403 and r.json["error"] == "escrita_nao_permitida"


def test_rota_fora_da_lista_da_403(cliente):
    r = _post(cliente, "/fora", b"{}", jti="j6", corpo_sha256=_sha(b"{}"))
    assert r.status_code == 403 and r.json["error"] == "escrita_nao_permitida"


def test_escritas_sem_registrar_jti_recusa_instalacao():
    with pytest.raises(ValueError):
        agente.instalar(Flask(__name__), "crm", _carregar, escritas={("POST", "/x")})


def test_eu_devolve_usuario_do_agente(cliente):
    tok = _token(LUCA, metodo="GET", rota="/_agente/eu")
    r = cliente.get("/_agente/eu", headers={"X-Agente-Assertion": tok})
    assert r.status_code == 200 and r.json == {"user_id": 1, "email": LUCA, "nome": "Luca", "role": "admin"}


def test_eu_sem_header_da_401(cliente):
    assert cliente.get("/_agente/eu").status_code == 401


def test_via_agente_falso_sem_header(cliente, monkeypatch):
    app = Flask(__name__)
    with app.test_request_context("/"):
        assert agente.via_agente() is False
