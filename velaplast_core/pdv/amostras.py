"""Motor de amostras — a regra de "há quanto tempo isso está parado".

Existe para que CRM e Gestão calculem estágio, trilha e tempo parado com a MESMA
regra. Se cada app tivesse a sua, voltaríamos ao problema que este módulo resolve:
dois sistemas afirmando coisas diferentes sobre a mesma amostra.

O PDV é a fonte: o estágio da amostra e sua trilha saem de `pedidos.status_amostra`
e das linhas `amostra:*` de `pedido-fase-historico`. Este módulo só lê.

Fronteira de responsabilidade (ver docs/superpowers/specs/2026-07-28-controle-amostras-design.md):
o PDV manda até `Enviado`; entrega e feedback do cliente são dos apps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from velaplast_core.config import BRT, agora_brasil, parse_ts

from .client import PDVClient

log = logging.getLogger(__name__)

# ─── Domínio ──────────────────────────────────────────────────────────────

ESTAGIOS = [
    "Nao_Iniciado",
    "Aguardando_Separacao",
    "Aguardando_Enviar",
    "Enviado",
    "Cancelado",
]

#: Estágios em que a amostra ainda está dentro de casa — onde ela trava.
ESTAGIOS_INTERNOS = ["Nao_Iniciado", "Aguardando_Separacao", "Aguardando_Enviar"]

#: Quem responde por destravar cada estágio. Medido no histórico do PDV:
#: Qualidade tira de Nao_Iniciado e de Aguardando_Separacao; Logística envia.
AREA_POR_ESTAGIO = {
    "Nao_Iniciado": "qualidade",
    "Aguardando_Separacao": "qualidade",
    "Aguardando_Enviar": "logistica",
    "Enviado": "comercial",
    "Cancelado": None,
}

#: Dias parada em estágio interno a partir dos quais a amostra vira exceção.
#: ~7x a mediana observada — quem cumpre o processo nunca aparece na lista.
SLA_DIAS = 5

#: Prefixo com que o PDV grava as transições de amostra em pedido-fase-historico.
PREFIXO_HISTORICO = "amostra:"

#: Estágio atribuído quando o PDV passa a usar um valor que não conhecemos.
#: Não quebra nada: cai no painel sem área, para investigação.
ESTAGIO_DESCONHECIDO = "outro"

#: O PDV encerra amostra cancelada gravando a fase do pedido ('cancelado'),
#: sem o prefixo `amostra:` e em minúscula. É o mesmo estágio.
ALIAS_ESTAGIO = {"cancelado": "Cancelado"}


def _limpar_estagio(valor: str | None) -> str | None:
    """'amostra:Enviado' → 'Enviado'. Devolve None para entrada vazia."""
    if not valor:
        return None
    texto = str(valor)
    if texto.startswith(PREFIXO_HISTORICO):
        return texto[len(PREFIXO_HISTORICO):]
    return texto


def normalizar_estagio(valor: str | None) -> str | None:
    """Estágio válido, ou ESTAGIO_DESCONHECIDO se o PDV inventar um novo."""
    limpo = _limpar_estagio(valor)
    if limpo is None:
        return None
    limpo = ALIAS_ESTAGIO.get(limpo, limpo)
    return limpo if limpo in ESTAGIOS else ESTAGIO_DESCONHECIDO


def area_responsavel(estagio: str | None) -> str | None:
    """Área que responde por destravar este estágio (None se não se aplica)."""
    return AREA_POR_ESTAGIO.get(estagio or "")


def _ts(valor) -> datetime | None:
    """Timestamp do PDV ('YYYY-MM-DD HH:MM:SS', BRT sem tz) → datetime com tz."""
    if not valor:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=BRT)
    texto = str(valor).replace(" ", "T")
    marcador = datetime.min.replace(tzinfo=BRT)
    resultado = parse_ts(texto, fallback=marcador)
    return None if resultado == marcador else resultado


# ─── Estruturas ───────────────────────────────────────────────────────────


@dataclass
class Etapa:
    """Uma passagem por um estágio. `saiu_em` None = é onde a amostra está agora."""

    estagio: str
    entrou_em: datetime
    saiu_em: datetime | None
    dias: float
    por_usuario_id: int | None = None
    por_usuario: str | None = None

    def to_dict(self) -> dict:
        return {
            "estagio": self.estagio,
            "entrou_em": self.entrou_em.isoformat() if self.entrou_em else None,
            "saiu_em": self.saiu_em.isoformat() if self.saiu_em else None,
            "dias": self.dias,
            "por_usuario_id": self.por_usuario_id,
            "por_usuario": self.por_usuario,
        }


@dataclass
class Amostra:
    """Uma amostra do PDV, com estágio atual e trilha já calculados."""

    pedido_id: int
    numero_pedido: int | None
    estagio: str | None
    estagio_desde: datetime | None
    dias_no_estagio: float | None
    #: True quando não há trilha e o tempo veio de `updated_at` — a tela não deve
    #: afirmar precisão que o dado não tem.
    trilha_parcial: bool
    parceiro_id: int | None
    parceiro: str | None
    cnpj: str | None
    usuario_id: int | None
    vendedor: str | None
    vendedor_email: str | None
    data_lancamento: str | None
    criado_em: datetime | None
    atualizado_em: datetime | None
    fase_pedido: str | None
    itens: list[dict] = field(default_factory=list)
    trilha: list[Etapa] = field(default_factory=list)

    @property
    def area(self) -> str | None:
        return area_responsavel(self.estagio)

    @property
    def encerrada(self) -> bool:
        return self.estagio in ("Enviado", "Cancelado")

    def esta_travada(self, sla_dias: int = SLA_DIAS) -> bool:
        """Parada além do SLA em estágio interno. Encerrada nunca trava."""
        if self.estagio not in ESTAGIOS_INTERNOS:
            return False
        return (self.dias_no_estagio or 0) >= sla_dias

    def resumo_itens(self) -> str:
        """Uma linha com os produtos, para título de tarefa e lista."""
        nomes = [i["descricao"] for i in self.itens if i.get("descricao")]
        if not nomes:
            return f"{len(self.itens)} item(ns)" if self.itens else "sem itens"
        return ", ".join(nomes[:3]) + (f" (+{len(nomes) - 3})" if len(nomes) > 3 else "")

    def to_dict(self) -> dict:
        return {
            "pedido_id": self.pedido_id,
            "numero_pedido": self.numero_pedido,
            "estagio": self.estagio,
            "estagio_desde": self.estagio_desde.isoformat() if self.estagio_desde else None,
            "dias_no_estagio": self.dias_no_estagio,
            "trilha_parcial": self.trilha_parcial,
            "area": self.area,
            "travada": self.esta_travada(),
            "encerrada": self.encerrada,
            "parceiro_id": self.parceiro_id,
            "parceiro": self.parceiro,
            "cnpj": self.cnpj,
            "usuario_id": self.usuario_id,
            "vendedor": self.vendedor,
            "vendedor_email": self.vendedor_email,
            "data_lancamento": self.data_lancamento,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
            "fase_pedido": self.fase_pedido,
            "resumo_itens": self.resumo_itens(),
            "itens": self.itens,
            "trilha": [e.to_dict() for e in self.trilha],
        }


# ─── Trilha ───────────────────────────────────────────────────────────────


def trilha(historico: Iterable[dict], agora: datetime | None = None) -> list[Etapa]:
    """Linhas de `pedido-fase-historico` de UMA amostra → etapas com duração.

    Considera só as transições de amostra (`amostra:*`) e o `cancelado` do pedido,
    que é como o PDV encerra uma amostra cancelada. A última etapa fica com
    `saiu_em=None` e a duração conta até `agora`.
    """
    agora = agora or agora_brasil()
    linhas = []
    for h in historico:
        estagio = normalizar_estagio(h.get("fase_nova"))
        quando = _ts(h.get("created_at"))
        if estagio is None or quando is None:
            continue
        linhas.append((quando, estagio, h))
    linhas.sort(key=lambda x: x[0])

    etapas: list[Etapa] = []
    for i, (quando, estagio, h) in enumerate(linhas):
        saiu = linhas[i + 1][0] if i + 1 < len(linhas) else None
        fim = saiu or agora
        etapas.append(
            Etapa(
                estagio=estagio,
                entrou_em=quando,
                saiu_em=saiu,
                dias=round((fim - quando).total_seconds() / 86400, 2),
                por_usuario_id=h.get("user_id"),
                por_usuario=h.get("user") or h.get("usuario"),
            )
        )
    return etapas


def dias_no_estagio(
    etapas: list[Etapa],
    fallback_desde: datetime | None,
    agora: datetime | None = None,
) -> tuple[float | None, datetime | None, bool]:
    """Tempo no estágio atual → `(dias, desde, trilha_parcial)`.

    Com trilha, mede desde a última transição. Sem trilha (amostras antigas do
    PDV), cai em `fallback_desde` (`updated_at`) e devolve `trilha_parcial=True`.
    """
    agora = agora or agora_brasil()
    if etapas:
        ultima = etapas[-1]
        return (
            round((agora - ultima.entrou_em).total_seconds() / 86400, 2),
            ultima.entrou_em,
            False,
        )
    if fallback_desde:
        return (
            round((agora - fallback_desde).total_seconds() / 86400, 2),
            fallback_desde,
            True,
        )
    return None, None, True


# ─── Carga ────────────────────────────────────────────────────────────────


#: Atributos que, juntos, formam o nome do produto. O PDV não guarda um nome
#: pronto: `produto_vendido_como` é a ORIGEM (producao_interna/revenda) e vem
#: preenchido em menos de 7% dos cadastros — usar aquele campo como nome põe
#: "revenda" na tela no lugar de "Bombona 20 Litros".
ATRIBUTOS_DO_NOME = ("descricao_id", "volume_embalagem_id", "peso_id", "cor_id")


def _nome_do_produto(registro: dict, catalogos: dict[int, dict]) -> str | None:
    """Monta 'Frasco 1 Litro 105 g Branca' a partir dos atributos de catálogo."""
    partes: list[str] = []
    for campo in ATRIBUTOS_DO_NOME:
        valor = registro.get(campo)
        if not valor:
            continue
        nome = (catalogos.get(int(valor)) or {}).get("name")
        if nome:
            partes.append(str(nome))
    montado = " ".join(partes).strip()
    if montado:
        rcp = registro.get("rcp")
        return f"{montado} (RCP {rcp})" if rcp else montado
    # Sem atributos, cai no que houver de identificação.
    return registro.get("rcp") or registro.get("sku") or None


def _indexar_itens(client: PDVClient, ids: set[int]) -> dict[int, list[dict]]:
    """Itens (normais + personalizados) agrupados por pedido.

    Busca em lote e agrupa aqui: 201 amostras via `pedido_id__in` estouraria a URL,
    e uma chamada por amostra estouraria o rate limit.
    """
    por_pedido: dict[int, list[dict]] = {}

    catalogos = client.catalogos()

    normais = client.listar_tudo(
        "pedido-itens",
        fields="id,pedido_id,produto_id,quantidade_pedida,quantidade_real,valor_unitario,data_entrega_desejada",
    )
    produtos = {
        int(p["id"]): p
        for p in client.listar_tudo(
            "produtos",
            fields="id,rcp,sku,tipo,descricao_id,peso_id,cor_id,volume_embalagem_id",
        )
    }
    for item in normais:
        pid = item.get("pedido_id")
        if pid not in ids:
            continue
        prod = produtos.get(int(item["produto_id"])) if item.get("produto_id") else None
        por_pedido.setdefault(pid, []).append({
            "origem": "produto",
            "produto_id": item.get("produto_id"),
            "descricao": _nome_do_produto(prod or {}, catalogos),
            "tipo": (prod or {}).get("tipo"),
            "rcp": (prod or {}).get("rcp"),
            "quantidade": item.get("quantidade_real") or item.get("quantidade_pedida"),
            "valor_unitario": item.get("valor_unitario"),
        })

    personalizados = client.listar_tudo(
        "pedido-itens-personalizados",
        fields=(
            "id,pedido_id,tipo,sku,rcp,quantidade_pedida,quantidade_real,valor_unitario,"
            "descricao_id,peso_id,cor_id,volume_embalagem_id"
        ),
    )
    for item in personalizados:
        pid = item.get("pedido_id")
        if pid not in ids:
            continue
        por_pedido.setdefault(pid, []).append({
            "origem": "personalizado",
            "produto_id": None,
            "descricao": _nome_do_produto(item, catalogos),
            "tipo": item.get("tipo"),
            "rcp": item.get("rcp"),
            "quantidade": item.get("quantidade_real") or item.get("quantidade_pedida"),
            "valor_unitario": item.get("valor_unitario"),
        })
    return por_pedido


def carregar_amostras(
    client: PDVClient | None = None,
    desde: str | None = None,
    incluir_itens: bool = True,
    agora: datetime | None = None,
) -> list[Amostra]:
    """Todas as amostras do PDV com estágio, trilha e tempo parado calculados.

    Args:
        client: PDVClient; criado na hora se omitido.
        desde: 'YYYY-MM-DD' — só amostras com `updated_at` a partir dessa data
            (sync incremental). Sem valor, carrega tudo.
        incluir_itens: False economiza 3 requisições quando só interessa o estágio.
        agora: injetável para teste determinístico.
    """
    client = client or PDVClient()
    agora = agora or agora_brasil()

    tipo_id = client.id_do_catalogo("tipo_pedido", "Amostra")
    if tipo_id is None:
        raise ValueError("catálogo 'tipo_pedido' não tem o valor 'Amostra' no PDV")

    filtros: dict = {"tipo_pedido_id": tipo_id}
    if desde:
        filtros["updated_at__gte"] = desde
    pedidos = client.listar_tudo("pedidos", filtros=filtros, order="id")
    client.resolver("pedidos", pedidos, campos=["parceiro_id", "usuario_id"])

    ids = {int(p["id"]) for p in pedidos}
    if not ids:
        return []

    # Histórico e cadastros em lote — evita uma chamada por amostra.
    historico_por_pedido: dict[int, list[dict]] = {}
    for h in client.listar_tudo("pedido-fase-historico", order="id"):
        pid = h.get("pedido_id")
        if pid in ids:
            historico_por_pedido.setdefault(pid, []).append(h)

    usuarios = {int(u["id"]): u for u in client.listar_tudo("users", fields="id,name,email")}
    parceiros = {
        int(p["id"]): p for p in client.listar_tudo("parceiros", fields="id,nome_empresa,cnpj")
    }
    itens_por_pedido = _indexar_itens(client, ids) if incluir_itens else {}

    amostras: list[Amostra] = []
    for pedido in pedidos:
        pid = int(pedido["id"])
        historico = historico_por_pedido.get(pid, [])
        for h in historico:
            u = usuarios.get(h.get("user_id") or -1)
            if u:
                h["user"] = u.get("name")
        etapas = trilha(historico, agora=agora)

        # O estágio vale o que o cabeçalho diz; a trilha só mede o tempo.
        estagio = normalizar_estagio(pedido.get("status_amostra"))
        if estagio is None and pedido.get("fase") == "cancelado":
            estagio = "Cancelado"
        if etapas and estagio and etapas[-1].estagio != estagio:
            # Cabeçalho e trilha discordam: o cabeçalho manda, mas o tempo perde
            # a referência da transição — trata como sem trilha.
            log.debug("amostra %s: cabeçalho=%s trilha=%s", pid, estagio, etapas[-1].estagio)
            etapas_para_tempo: list[Etapa] = []
        else:
            etapas_para_tempo = etapas

        dias, desde_quando, parcial = dias_no_estagio(
            etapas_para_tempo, _ts(pedido.get("updated_at")), agora=agora
        )
        parceiro = parceiros.get(int(pedido["parceiro_id"])) if pedido.get("parceiro_id") else None
        usuario = usuarios.get(int(pedido["usuario_id"])) if pedido.get("usuario_id") else None

        amostras.append(
            Amostra(
                pedido_id=pid,
                numero_pedido=pedido.get("numero_pedido"),
                estagio=estagio,
                estagio_desde=desde_quando,
                dias_no_estagio=dias,
                trilha_parcial=parcial,
                parceiro_id=pedido.get("parceiro_id"),
                parceiro=(parceiro or {}).get("nome_empresa") or pedido.get("parceiro"),
                cnpj=(parceiro or {}).get("cnpj"),
                usuario_id=pedido.get("usuario_id"),
                vendedor=(usuario or {}).get("name") or pedido.get("usuario"),
                vendedor_email=(usuario or {}).get("email"),
                data_lancamento=pedido.get("data_lancamento"),
                criado_em=_ts(pedido.get("created_at")),
                atualizado_em=_ts(pedido.get("updated_at")),
                fase_pedido=pedido.get("fase"),
                itens=itens_por_pedido.get(pid, []),
                trilha=etapas,
            )
        )
    return amostras


def travadas(amostras: Iterable[Amostra], sla_dias: int = SLA_DIAS) -> list[Amostra]:
    """Amostras paradas além do SLA, da mais parada para a menos."""
    presas = [a for a in amostras if a.esta_travada(sla_dias)]
    return sorted(presas, key=lambda a: -(a.dias_no_estagio or 0))


def resumo_por_area(amostras: Iterable[Amostra], sla_dias: int = SLA_DIAS) -> dict[str, dict]:
    """Passivo por área: quantas estão paradas e quantos dias somam."""
    saida: dict[str, dict] = {}
    for a in amostras:
        if a.encerrada or a.area is None:
            continue
        bloco = saida.setdefault(a.area, {"area": a.area, "abertas": 0, "travadas": 0, "dias_acumulados": 0.0})
        bloco["abertas"] += 1
        if a.esta_travada(sla_dias):
            bloco["travadas"] += 1
            bloco["dias_acumulados"] += a.dias_no_estagio or 0
    for bloco in saida.values():
        bloco["dias_acumulados"] = round(bloco["dias_acumulados"], 1)
    return saida


def tempo_medio_por_estagio(amostras: Iterable[Amostra]) -> dict[str, dict]:
    """Mediana/média/máximo de dias por estágio, só de etapas já concluídas."""
    import statistics

    coletado: dict[str, list[float]] = {}
    for a in amostras:
        for etapa in a.trilha:
            if etapa.saiu_em is not None:
                coletado.setdefault(etapa.estagio, []).append(etapa.dias)
    return {
        estagio: {
            "n": len(vals),
            "mediana": round(statistics.median(vals), 2),
            "media": round(statistics.mean(vals), 2),
            "maximo": round(max(vals), 2),
        }
        for estagio, vals in coletado.items()
    }
