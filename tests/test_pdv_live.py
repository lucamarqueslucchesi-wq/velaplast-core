"""Testes do cliente PDV e das tools MCP contra a API REAL.

Não há mock aqui de propósito: o objetivo é provar que o servidor responde
com dados verdadeiros e que as contagens fecham com a própria API.
Pular automaticamente se PDV_API_KEY não estiver configurada.

    pytest tests/test_pdv_live.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from velaplast_core.config import get_pdv_api_key  # noqa: E402
from velaplast_core.pdv import PDVClient, PDVValidationError  # noqa: E402
from velaplast_core.pdv.resources import FASES, RECURSOS  # noqa: E402

pytestmark = pytest.mark.skipif(not get_pdv_api_key(), reason="PDV_API_KEY não configurada")


@pytest.fixture(scope="module")
def pdv() -> PDVClient:
    return PDVClient()


@pytest.fixture(scope="module")
def srv():
    """Carrega mcp/server.py isolando a colisão de nome com o pacote `mcp`."""
    caminho = ROOT / "mcp" / "server.py"
    salvo = list(sys.path)
    sys.path.insert(0, str(caminho.parent))
    try:
        spec = importlib.util.spec_from_file_location("pdv_mcp_server", caminho)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo
    finally:
        sys.path[:] = salvo


# ─── Cliente ──────────────────────────────────────────────────────────────


def test_ping(pdv):
    r = pdv.ping()
    assert r["ok"] is True
    assert r["pedidos_visiveis"] > 0


def test_todos_os_recursos_respondem(pdv):
    """Cada recurso mapeado existe na API e devolve os campos que declaramos."""
    for nome, meta in RECURSOS.items():
        r = pdv.listar(nome, limit=1)
        assert "meta" in r, nome
        if meta["campos"] and r["data"]:
            assert set(r["data"][0]) == set(meta["campos"]), f"campos divergentes em {nome}"


def test_total_bate_com_soma_das_partes(pdv):
    """meta.total do universo == soma dos totais por tipo de pedido (reconciliação)."""
    total = pdv.contar("pedidos")
    tipos = pdv.catalogos_por_tipo("tipo_pedido")
    soma = sum(pdv.contar("pedidos", {"tipo_pedido_id": t["id"]}) for t in tipos)
    assert soma == total, f"soma por tipo {soma} != total {total}"


def test_group_by_local_bate_com_a_api(pdv):
    """contar_por (feito aqui) reproduz o meta.total que a API devolve por fase."""
    todos = pdv.listar_tudo("pedidos", fields="id,fase")
    local = pdv.contar_por(todos, "fase")
    for fase in FASES:
        esperado = pdv.contar("pedidos", {"fase": fase})
        assert local.get(fase, 0) == esperado, f"fase {fase}: local={local.get(fase,0)} api={esperado}"


def test_paginacao_traz_tudo_sem_duplicar(pdv):
    linhas = pdv.listar_tudo("pedidos", fields="id")
    ids = [ln["id"] for ln in linhas]
    assert len(ids) == len(set(ids)), "listar_tudo duplicou linhas"
    assert len(ids) == pdv.contar("pedidos")


def test_resolver_traduz_fk(pdv):
    linhas = pdv.listar("pedidos", fields="id,tipo_pedido_id,parceiro_id,usuario_id", limit=5)["data"]
    pdv.resolver("pedidos", linhas)
    for ln in linhas:
        assert ln.get("tipo_pedido") in ("Pedido", "Pedido Urgente", "Amostra", "Cotação")
        assert isinstance(ln.get("parceiro"), str)


def test_resolver_confere_com_o_registro_de_origem(pdv):
    """O nome resolvido é o mesmo que /parceiros/{id} devolve (não é chute)."""
    linha = pdv.listar("pedidos", fields="id,parceiro_id", limit=1, order="-id")["data"][0]
    pdv.resolver("pedidos", [linha])
    origem = pdv.obter("parceiros", linha["parceiro_id"])
    assert linha["parceiro"] == origem["nome_empresa"]


def test_obter_inexistente_devolve_none(pdv):
    assert pdv.obter("pedidos", 99999999) is None


def test_validacao_rejeita_campo_e_recurso_invalidos(pdv):
    with pytest.raises(PDVValidationError):
        pdv.listar("pedidos", filtros={"campo_que_nao_existe": 1})
    with pytest.raises(PDVValidationError):
        pdv.listar("tabela_inexistente")
    with pytest.raises(PDVValidationError):
        pdv.listar("pedidos", limit=5000)


def test_id_do_catalogo(pdv):
    assert pdv.id_do_catalogo("tipo_pedido", "amostra") == pdv.id_do_catalogo("tipo_pedido", "Amostra")
    assert pdv.id_do_catalogo("tipo_pedido", "inexistente") is None


# ─── Tools MCP ────────────────────────────────────────────────────────────


def test_tool_ping(srv):
    assert srv.pdv_ping()["status"] == "ok"


def test_tool_recursos_lista_15(srv):
    r = srv.pdv_recursos()
    assert r["status"] == "ok"
    assert r["rows"] == 15


def test_tool_pedidos_filtra_e_resolve(srv):
    r = srv.pdv_pedidos(tipo="Amostra", limit=5)
    assert r["status"] == "ok"
    assert all(p["tipo_pedido"] == "Amostra" for p in r["data"])
    assert r["total"] >= len(r["data"])


def test_tool_pedidos_tipo_invalido_lista_opcoes(srv):
    r = srv.pdv_pedidos(tipo="Banana")
    assert r["status"] == "error"
    assert "Amostra" in r["tipos"]


def test_tool_pedido_por_numero_e_por_id_coincidem(srv, pdv):
    ultimo = pdv.listar("pedidos", fields="id,numero_pedido", order="-id", limit=1)["data"][0]
    por_id = srv.pdv_pedido(pedido_id=ultimo["id"])
    por_numero = srv.pdv_pedido(numero_pedido=ultimo["numero_pedido"])
    assert por_id["status"] == "ok"
    assert por_numero["data"]["pedido"]["id"] == ultimo["id"]


def test_tool_pedido_valor_bate_com_soma_manual(srv, pdv):
    """O valor total da ficha == soma feita à mão sobre /pedido-itens."""
    alvo = pdv.listar(
        "pedido-itens", fields="pedido_id", order="-id", limit=1
    )["data"][0]["pedido_id"]
    ficha = srv.pdv_pedido(pedido_id=alvo)["data"]
    itens = pdv.listar_tudo("pedido-itens", filtros={"pedido_id": alvo})
    manual = round(
        sum(
            float(i.get("quantidade_real") or i.get("quantidade_pedida") or 0)
            * float(i.get("valor_unitario") or 0)
            for i in itens
        ),
        2,
    )
    assert ficha["totais"]["valor_itens"] == manual
    assert ficha["totais"]["qtd_itens"] >= len(itens)


def test_tool_pipeline_soma_bate_com_a_api(srv, pdv):
    r = srv.pdv_pipeline()
    assert r["status"] == "ok"
    soma = sum(b["pedidos"] for b in r["por_fase"])
    assert soma == r["em_andamento"]
    for bloco in r["por_fase"]:
        assert bloco["pedidos"] == pdv.contar("pedidos", {"fase": bloco["fase"]})


def test_tool_amostras_soma_bate(srv, pdv):
    r = srv.pdv_amostras()
    assert r["status"] == "ok"
    assert sum(r["por_status"].values()) == r["total_amostras"]
    assert r["total_amostras"] == pdv.contar(
        "pedidos", {"tipo_pedido_id": pdv.id_do_catalogo("tipo_pedido", "Amostra")}
    )


def test_tool_pendentes_sap_bate_com_filtro_null(srv, pdv):
    r = srv.pdv_pendentes_sap()
    assert r["total_sem_num_sap"] == pdv.contar("pedidos", {"num_sap__null": 1})
    assert sum(r["por_fase"].values()) == r["total_sem_num_sap"]
    assert sum(r["por_tipo"].values()) == r["total_sem_num_sap"]


def test_divergencia_sap_so_conta_venda_de_verdade(srv, pdv):
    """Venda concluída sem num_sap tem que bater com a contagem direta na API."""
    r = srv.pdv_pendentes_sap()
    esperado = sum(
        pdv.contar(
            "pedidos",
            {
                "tipo_pedido_id": pdv.id_do_catalogo("tipo_pedido", t),
                "fase": "concluido",
                "num_sap__null": 1,
            },
        )
        for t in ("Pedido", "Pedido Urgente")
    )
    assert len(r["divergencias_venda_concluida_sem_sap"]) == esperado


def test_tool_parceiros_busca_parcial(srv):
    r = srv.pdv_parceiros(uf="SP", limit=5)
    assert r["status"] == "ok"
    assert all(p["uf"] == "SP" for p in r["data"])


def test_tool_catalogos(srv):
    tipos = srv.pdv_catalogos()
    assert tipos["status"] == "ok"
    valores = srv.pdv_catalogos(tipo="tipo_pedido")
    assert {v["name"] for v in valores["data"]} == {"Pedido", "Pedido Urgente", "Amostra", "Cotação"}


def test_tool_query_generica_e_erro_de_json(srv):
    ok = srv.pdv_query("departamentos", limit=10)
    assert ok["status"] == "ok" and ok["rows"] > 0
    ruim = srv.pdv_query("pedidos", filtros="{isso não é json}")
    assert ruim["status"] == "error"


def test_tool_usuarios_resolve_departamento(srv):
    r = srv.pdv_usuarios()
    assert r["status"] == "ok"
    assert any(u.get("departamento") for u in r["data"])
