"""Testes do motor de amostras.

As regras (trilha, tempo parado, SLA, área) são testadas com dados sintéticos e
relógio injetado — precisam ser determinísticas. A carga é testada contra o PDV
real, reconciliando com a contagem que a própria API devolve.

    pytest tests/test_pdv_amostras.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from velaplast_core.config import BRT, get_pdv_api_key  # noqa: E402
from velaplast_core.pdv import PDVClient  # noqa: E402
from velaplast_core.pdv.amostras import (  # noqa: E402
    ESTAGIO_DESCONHECIDO,
    SLA_DIAS,
    Amostra,
    area_responsavel,
    carregar_amostras,
    dias_no_estagio,
    normalizar_estagio,
    resumo_por_area,
    travadas,
    trilha,
)

AGORA = datetime(2026, 7, 28, 12, 0, tzinfo=BRT)


def _h(fase_nova: str, quando: str, user_id: int | None = None) -> dict:
    return {"fase_nova": fase_nova, "created_at": quando, "user_id": user_id}


def _amostra(**kw) -> Amostra:
    base = dict(
        pedido_id=1, numero_pedido=1, estagio="Nao_Iniciado", estagio_desde=AGORA,
        dias_no_estagio=0.0, trilha_parcial=False, parceiro_id=1, parceiro="ACME",
        cnpj=None, usuario_id=1, vendedor="Fulano", vendedor_email=None,
        data_lancamento="2026-07-01", criado_em=AGORA, atualizado_em=AGORA,
        fase_pedido=None,
    )
    base.update(kw)
    return Amostra(**base)


# ─── Estágio e área ───────────────────────────────────────────────────────


def test_normalizar_tira_o_prefixo_do_pdv():
    assert normalizar_estagio("amostra:Enviado") == "Enviado"
    assert normalizar_estagio("Enviado") == "Enviado"
    assert normalizar_estagio(None) is None


def test_estagio_novo_no_pdv_nao_quebra():
    """Se o TI criar um estágio, ele cai como 'outro' — não estoura."""
    assert normalizar_estagio("amostra:Em_Analise_Nova") == ESTAGIO_DESCONHECIDO
    assert area_responsavel(ESTAGIO_DESCONHECIDO) is None


def test_area_segue_o_que_o_historico_mostra():
    assert area_responsavel("Nao_Iniciado") == "qualidade"
    assert area_responsavel("Aguardando_Separacao") == "qualidade"
    assert area_responsavel("Aguardando_Enviar") == "logistica"
    assert area_responsavel("Enviado") == "comercial"
    assert area_responsavel("Cancelado") is None


# ─── Trilha ───────────────────────────────────────────────────────────────


def test_trilha_reconstroi_as_etapas_com_duracao():
    etapas = trilha(
        [
            _h("amostra:Nao_Iniciado", "2026-07-01 09:00:00", 10),
            _h("amostra:Aguardando_Separacao", "2026-07-03 09:00:00", 20),
            _h("amostra:Aguardando_Enviar", "2026-07-06 09:00:00", 20),
        ],
        agora=AGORA,
    )
    assert [e.estagio for e in etapas] == [
        "Nao_Iniciado", "Aguardando_Separacao", "Aguardando_Enviar",
    ]
    assert etapas[0].dias == 2.0
    assert etapas[1].dias == 3.0
    assert etapas[0].por_usuario_id == 10
    # a última etapa é a atual: sem saída, contando até agora
    assert etapas[-1].saiu_em is None
    assert etapas[-1].dias == pytest.approx(22.125, abs=0.01)


def test_trilha_ordena_por_data_mesmo_fora_de_ordem():
    etapas = trilha(
        [
            _h("amostra:Aguardando_Separacao", "2026-07-03 09:00:00"),
            _h("amostra:Nao_Iniciado", "2026-07-01 09:00:00"),
        ],
        agora=AGORA,
    )
    assert [e.estagio for e in etapas] == ["Nao_Iniciado", "Aguardando_Separacao"]


def test_trilha_ignora_linha_sem_data_ou_sem_estagio():
    etapas = trilha(
        [
            _h("amostra:Nao_Iniciado", "2026-07-01 09:00:00"),
            _h("", "2026-07-02 09:00:00"),
            _h("amostra:Enviado", ""),
        ],
        agora=AGORA,
    )
    assert len(etapas) == 1


def test_trilha_inclui_cancelamento_do_pedido():
    """O PDV encerra amostra cancelada gravando 'cancelado' (sem prefixo, minúsculo).

    Tem que virar o estágio Cancelado — se virasse 'outro', a amostra cancelada
    apareceria como estágio desconhecido no painel.
    """
    etapas = trilha(
        [
            _h("amostra:Nao_Iniciado", "2026-07-01 09:00:00"),
            _h("cancelado", "2026-07-05 09:00:00"),
        ],
        agora=AGORA,
    )
    assert etapas[-1].estagio == "Cancelado"
    assert etapas[0].dias == 4.0
    assert area_responsavel(etapas[-1].estagio) is None


# ─── Tempo parado ─────────────────────────────────────────────────────────


def test_dias_no_estagio_mede_desde_a_ultima_transicao():
    etapas = trilha([_h("amostra:Nao_Iniciado", "2026-07-20 12:00:00")], agora=AGORA)
    dias, desde, parcial = dias_no_estagio(etapas, None, agora=AGORA)
    assert dias == 8.0
    assert desde == datetime(2026, 7, 20, 12, 0, tzinfo=BRT)
    assert parcial is False


def test_sem_trilha_cai_no_fallback_e_avisa():
    """143 das 201 amostras do PDV não têm trilha — não podem sumir do painel."""
    fallback = datetime(2026, 7, 18, 12, 0, tzinfo=BRT)
    dias, desde, parcial = dias_no_estagio([], fallback, agora=AGORA)
    assert dias == 10.0
    assert desde == fallback
    assert parcial is True, "sem trilha, a tela precisa saber que o tempo é aproximado"


def test_sem_trilha_e_sem_fallback_nao_inventa_numero():
    dias, desde, parcial = dias_no_estagio([], None, agora=AGORA)
    assert dias is None and desde is None and parcial is True


# ─── SLA ──────────────────────────────────────────────────────────────────


def test_travada_so_vale_para_estagio_interno():
    interna = _amostra(estagio="Nao_Iniciado", dias_no_estagio=30)
    enviada = _amostra(estagio="Enviado", dias_no_estagio=300)
    cancelada = _amostra(estagio="Cancelado", dias_no_estagio=300)
    assert interna.esta_travada() is True
    assert enviada.esta_travada() is False, "amostra enviada não está travada na fábrica"
    assert cancelada.esta_travada() is False


def test_sla_e_configuravel_nao_fixo():
    a = _amostra(estagio="Nao_Iniciado", dias_no_estagio=7)
    assert a.esta_travada(sla_dias=5) is True
    assert a.esta_travada(sla_dias=10) is False


def test_limite_do_sla_e_inclusivo():
    assert _amostra(dias_no_estagio=SLA_DIAS).esta_travada() is True
    assert _amostra(dias_no_estagio=SLA_DIAS - 0.1).esta_travada() is False


def test_travadas_ordena_pela_mais_parada():
    lista = [
        _amostra(pedido_id=1, dias_no_estagio=10),
        _amostra(pedido_id=2, dias_no_estagio=144),
        _amostra(pedido_id=3, dias_no_estagio=1),
    ]
    assert [a.pedido_id for a in travadas(lista)] == [2, 1]


def test_resumo_por_area_soma_o_passivo():
    lista = [
        _amostra(pedido_id=1, estagio="Nao_Iniciado", dias_no_estagio=10),
        _amostra(pedido_id=2, estagio="Aguardando_Separacao", dias_no_estagio=20),
        _amostra(pedido_id=3, estagio="Aguardando_Enviar", dias_no_estagio=2),
        _amostra(pedido_id=4, estagio="Enviado", dias_no_estagio=99),
    ]
    resumo = resumo_por_area(lista)
    assert resumo["qualidade"]["travadas"] == 2
    assert resumo["qualidade"]["dias_acumulados"] == 30.0
    assert resumo["logistica"]["abertas"] == 1
    assert resumo["logistica"]["travadas"] == 0
    assert "comercial" not in resumo, "amostra enviada não é passivo de fábrica"


def test_resumo_itens_encurta_lista_longa():
    a = _amostra(itens=[{"descricao": f"Item {i}"} for i in range(5)])
    assert a.resumo_itens() == "Item 0, Item 1, Item 2 (+2)"
    assert _amostra(itens=[]).resumo_itens() == "sem itens"


# ─── Carga contra o PDV real ──────────────────────────────────────────────

pytest_live = pytest.mark.skipif(not get_pdv_api_key(), reason="PDV_API_KEY não configurada")


@pytest.fixture(scope="module")
def carregadas():
    return carregar_amostras(PDVClient(), incluir_itens=True)


@pytest_live
def test_carga_bate_com_a_contagem_da_api(carregadas):
    """Reconciliação: o motor não pode perder nem inventar amostra."""
    client = PDVClient()
    total_api = client.contar(
        "pedidos", {"tipo_pedido_id": client.id_do_catalogo("tipo_pedido", "Amostra")}
    )
    assert len(carregadas) == total_api


@pytest_live
def test_estagios_batem_estagio_a_estagio(carregadas):
    client = PDVClient()
    tipo = client.id_do_catalogo("tipo_pedido", "Amostra")
    for estagio in ("Nao_Iniciado", "Aguardando_Separacao", "Aguardando_Enviar", "Enviado"):
        esperado = client.contar(
            "pedidos", {"tipo_pedido_id": tipo, "status_amostra": estagio}
        )
        obtido = sum(1 for a in carregadas if a.estagio == estagio)
        assert obtido == esperado, f"{estagio}: motor={obtido} api={esperado}"


@pytest_live
def test_toda_amostra_viva_tem_area_e_tempo(carregadas):
    for a in carregadas:
        if a.encerrada or a.estagio is None:
            continue
        assert a.area is not None, f"amostra {a.numero_pedido} sem área"
        assert a.dias_no_estagio is not None, f"amostra {a.numero_pedido} sem tempo"


@pytest_live
def test_trilha_real_bate_com_o_historico_da_api(carregadas):
    """Para uma amostra com trilha, as etapas reproduzem o histórico do PDV."""
    com_trilha = [a for a in carregadas if len(a.trilha) >= 3]
    assert com_trilha, "esperava amostras com trilha no PDV"
    alvo = com_trilha[0]
    client = PDVClient()
    bruto = client.listar_tudo(
        "pedido-fase-historico", filtros={"pedido_id": alvo.pedido_id}, order="id"
    )
    assert len(alvo.trilha) == len(bruto)
    assert alvo.trilha[0].entrou_em.isoformat().startswith(bruto[0]["created_at"][:10])


@pytest_live
def test_amostra_enviada_nunca_aparece_como_travada(carregadas):
    assert not [a for a in travadas(carregadas) if a.encerrada]


@pytest_live
def test_passivo_por_area_e_coerente_com_as_travadas(carregadas):
    resumo = resumo_por_area(carregadas)
    assert sum(b["travadas"] for b in resumo.values()) == len(travadas(carregadas))


@pytest_live
def test_carga_incremental_e_subconjunto_da_completa(carregadas):
    parciais = carregar_amostras(PDVClient(), desde="2026-07-01", incluir_itens=False)
    ids_todas = {a.pedido_id for a in carregadas}
    assert {a.pedido_id for a in parciais} <= ids_todas
    assert len(parciais) < len(carregadas), "filtro por data deveria reduzir o conjunto"
