"""pdv-velaplast MCP server — expõe a API read-only do PDV como tools MCP.

O PDV é o sistema do TI da Velaplast para acompanhamento de pedidos e amostras.
A API dele não faz JOIN nem agregação; este servidor resolve as duas coisas do
lado de cá (via `PDVClient.resolver` e `contar_por`/`somar`), para que uma
pergunta de negócio vire uma tool só.

Run: python mcp/server.py
Registro (user scope):
    claude mcp add --scope user pdv-velaplast \
        "<core>/venv/bin/python" "<core>/mcp/server.py"
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass

from mcp.server.fastmcp import FastMCP  # noqa: E402

from velaplast_core.config import agora_brasil, parse_ts  # noqa: E402
from velaplast_core.pdv import PDVClient, PDVError  # noqa: E402
from velaplast_core.pdv.resources import (  # noqa: E402
    FASES,
    FASES_EM_ANDAMENTO,
    RECURSOS,
    STATUS_AMOSTRA,
)

mcp = FastMCP("pdv-velaplast")
client = PDVClient()

# Campos devolvidos por padrão nas listagens (evita payload gigante no chat).
CAMPOS_PEDIDO = (
    "id,numero_pedido,parceiro_id,usuario_id,tipo_pedido_id,fase,fase_iniciada_em,"
    "data_lancamento,num_sap,status_amostra,com_rcp,pedido_cliente,motivo_rejeicao"
)
CAMPOS_PARCEIRO = (
    "id,codigo,nome_empresa,cnpj,municipio,uf,usuario_id,setor_id,estagio_venda_id,"
    "tamanho_id,email_contato,telefone"
)
CAMPOS_PRODUTO = "id,parceiro_id,tipo,sku,rcp,produto_vendido_como,descricao_id,peso_id,cor_id,is_generico"


def _err(e: Exception) -> dict:
    return {"status": "error", "error": str(e)[:500]}


def _ok(dados, **extra) -> dict:
    saida = {"status": "ok", "rows": len(dados) if isinstance(dados, list) else 1, "data": dados}
    saida.update(extra)
    return saida


def _dias_na_fase(pedido: dict) -> Optional[float]:
    """Dias corridos desde a última mudança de fase (None se o campo estiver vazio)."""
    ts = pedido.get("fase_iniciada_em")
    if not ts:
        return None
    inicio = parse_ts(str(ts).replace(" ", "T"))
    return round((agora_brasil() - inicio).total_seconds() / 86400, 1)


def _filtros_periodo(campo: str, de: Optional[str], ate: Optional[str]) -> dict:
    f = {}
    if de:
        f[f"{campo}__gte"] = de
    if ate:
        f[f"{campo}__lte"] = ate
    return f


# ── Introspecção ──────────────────────────────────────────────────────────


@mcp.tool()
def pdv_ping() -> dict:
    """Testa a conexão e a chave da API do PDV. Use quando algo parecer fora do ar."""
    try:
        return {"status": "ok", **client.ping()}
    except PDVError as e:
        return _err(e)


@mcp.tool()
def pdv_recursos(recurso: Optional[str] = None) -> dict:
    """Lista os recursos (tabelas) do PDV com seus campos, ou detalha um recurso.

    Sem argumento: os 15 recursos com descrição e contagem atual de linhas.
    Com `recurso`: campos, FKs resolvíveis e domínios (fases, status de amostra).

    Args:
        recurso: nome do recurso, ex. 'pedidos', 'parceiros', 'produtos'.
    """
    try:
        if recurso:
            if recurso not in RECURSOS:
                return {"status": "error", "error": f"recurso '{recurso}' não existe",
                        "disponiveis": list(RECURSOS)}
            info = dict(RECURSOS[recurso])
            info["recurso"] = recurso
            info["total_linhas"] = client.contar(recurso) if info["campos"] else 0
            from velaplast_core.pdv.resources import CATALOG_FK

            info["fks_resolvidas"] = CATALOG_FK.get(recurso, {})
            if recurso == "pedidos":
                info["fases"] = FASES
                info["status_amostra"] = STATUS_AMOSTRA
            return {"status": "ok", "data": info}

        saida = []
        for nome, meta in RECURSOS.items():
            saida.append({
                "recurso": nome,
                "descricao": meta["descricao"],
                "total_linhas": client.contar(nome) if meta["campos"] else 0,
                "campos": meta["campos"],
            })
        return _ok(saida)
    except PDVError as e:
        return _err(e)


@mcp.tool()
def pdv_catalogos(tipo: Optional[str] = None) -> dict:
    """De/para dos campos `_id` do PDV (tabela `catalogs`).

    Sem argumento: lista os tipos disponíveis com a contagem de valores.
    Com `tipo`: os valores daquele domínio (id + nome).

    Args:
        tipo: ex. 'tipo_pedido', 'setor', 'estagio_venda', 'condicoes_pagamento', 'material'.
    """
    try:
        todos = list(client.catalogos().values())
        if tipo:
            vals = [
                {"id": c["id"], "name": c["name"], "scope": c.get("scope"), "entity": c.get("entity")}
                for c in todos
                if c.get("type") == tipo
            ]
            if not vals:
                tipos = sorted({str(c.get("type")) for c in todos})
                return {"status": "error", "error": f"tipo '{tipo}' não existe", "tipos": tipos}
            return _ok(sorted(vals, key=lambda x: str(x["name"])))

        resumo: dict[str, int] = {}
        for c in todos:
            t = str(c.get("type"))
            resumo[t] = resumo.get(t, 0) + 1
        return _ok(
            [{"tipo": t, "valores": n} for t, n in sorted(resumo.items())],
            dica="chame de novo com tipo='...' para ver os valores",
        )
    except PDVError as e:
        return _err(e)


@mcp.tool()
def pdv_usuarios(departamento: Optional[str] = None) -> dict:
    """Usuários do PDV (vendedores, PCP, logística, fiscal...) com seu departamento.

    Args:
        departamento: filtra pelo nome do departamento, ex. 'Comercial', 'PCP', 'Logistica'.
    """
    try:
        users = client.listar_tudo("users", order="name")
        client.resolver("users", users)
        if departamento:
            alvo = departamento.strip().casefold()
            users = [u for u in users if str(u.get("departamento") or "").casefold() == alvo]
        return _ok(users)
    except PDVError as e:
        return _err(e)


# ── Pedidos ───────────────────────────────────────────────────────────────


@mcp.tool()
def pdv_pedidos(
    fase: Optional[str] = None,
    em_andamento: bool = False,
    tipo: Optional[str] = None,
    cliente: Optional[str] = None,
    vendedor: Optional[str] = None,
    data_de: Optional[str] = None,
    data_ate: Optional[str] = None,
    sem_sap: bool = False,
    num_sap: Optional[str] = None,
    numero_pedido: Optional[int] = None,
    com_rcp: Optional[bool] = None,
    limit: int = 100,
    order: str = "-id",
) -> dict:
    """Busca pedidos do PDV já com nomes resolvidos (cliente, vendedor, tipo) e idade na fase.

    Todos os filtros combinam com AND. `rows` é a página; `total` é a contagem do
    filtro inteiro — use `total` para responder "quantos".

    Args:
        fase: uma das fases do fluxo (ex. 'aguardando_correcao', 'concluido', 'inserido_sap').
        em_andamento: True traz só o que não está concluído nem cancelado (ignora `fase`).
        tipo: 'Pedido', 'Pedido Urgente', 'Amostra' ou 'Cotação'.
        cliente: parte do nome da empresa (busca parcial) ou o id do parceiro.
        vendedor: parte do nome do usuário dono do pedido.
        data_de / data_ate: recorte por data_lancamento, formato YYYY-MM-DD (inclusivos).
        sem_sap: True traz só pedidos ainda não lançados no SAP (num_sap nulo).
        num_sap: número do documento no SAP (busca exata).
        numero_pedido: número do pedido no PDV (diferente do id interno).
        com_rcp: True/False para filtrar pedidos com/sem RCP.
        limit: linhas na página (1..1000).
        order: ex. '-id' (mais recentes), 'data_lancamento', '-fase_iniciada_em'.
    """
    try:
        filtros: dict = {}
        if em_andamento:
            filtros["fase__in"] = ",".join(FASES_EM_ANDAMENTO)
        elif fase:
            if fase not in FASES:
                return {"status": "error", "error": f"fase '{fase}' inválida", "fases": FASES}
            filtros["fase"] = fase
        if tipo:
            tipo_id = client.id_do_catalogo("tipo_pedido", tipo)
            if tipo_id is None:
                opcoes = [c["name"] for c in client.catalogos_por_tipo("tipo_pedido")]
                return {"status": "error", "error": f"tipo '{tipo}' inválido", "tipos": opcoes}
            filtros["tipo_pedido_id"] = tipo_id
        if cliente:
            ids = _resolver_parceiros(cliente)
            if not ids:
                return {"status": "ok", "rows": 0, "total": 0, "data": [],
                        "aviso": f"nenhum parceiro casa com '{cliente}'"}
            filtros["parceiro_id__in"] = ",".join(str(i) for i in ids)
        if vendedor:
            ids = _resolver_usuarios(vendedor)
            if not ids:
                return {"status": "ok", "rows": 0, "total": 0, "data": [],
                        "aviso": f"nenhum usuário casa com '{vendedor}'"}
            filtros["usuario_id__in"] = ",".join(str(i) for i in ids)
        filtros.update(_filtros_periodo("data_lancamento", data_de, data_ate))
        if sem_sap:
            filtros["num_sap__null"] = 1
        if num_sap:
            filtros["num_sap"] = num_sap
        if numero_pedido is not None:
            filtros["numero_pedido"] = int(numero_pedido)
        if com_rcp is not None:
            filtros["com_rcp"] = 1 if com_rcp else 0

        r = client.listar("pedidos", filtros=filtros, fields=CAMPOS_PEDIDO, order=order, limit=limit)
        linhas = r.get("data", [])
        client.resolver("pedidos", linhas)
        for ln in linhas:
            ln["dias_na_fase"] = _dias_na_fase(ln)
        meta = r.get("meta", {})
        return _ok(
            linhas,
            total=meta.get("total"),
            paginas=meta.get("pages"),
            filtros_aplicados=filtros,
        )
    except (PDVError, ValueError) as e:
        return _err(e)


@mcp.tool()
def pdv_pedido(
    pedido_id: Optional[int] = None,
    numero_pedido: Optional[int] = None,
    incluir_historico: bool = True,
    incluir_comentarios: bool = True,
) -> dict:
    """Ficha completa de um pedido: cabeçalho resolvido, itens com valores, histórico e comentários.

    Informe `pedido_id` (id interno) OU `numero_pedido` (o número que o usuário vê).
    Soma o valor total do pedido a partir dos itens — a API do PDV não agrega.

    Args:
        pedido_id: id interno do pedido.
        numero_pedido: número do pedido exibido no PDV.
        incluir_historico: trilha de mudanças de fase.
        incluir_comentarios: comentários lançados no pedido.
    """
    try:
        if pedido_id is None and numero_pedido is None:
            return {"status": "error", "error": "informe pedido_id ou numero_pedido"}
        if pedido_id is None:
            achados = client.listar(
                "pedidos", filtros={"numero_pedido": int(numero_pedido)}, fields="id", limit=5
            ).get("data", [])
            if not achados:
                return {"status": "error", "error": f"pedido nº {numero_pedido} não encontrado"}
            if len(achados) > 1:
                return {"status": "error",
                        "error": f"nº {numero_pedido} casa com {len(achados)} registros",
                        "ids": [a["id"] for a in achados]}
            pedido_id = int(achados[0]["id"])

        cab = client.obter("pedidos", pedido_id)
        if not cab:
            return {"status": "error", "error": f"pedido id={pedido_id} não encontrado"}
        client.resolver("pedidos", [cab])
        cab["dias_na_fase"] = _dias_na_fase(cab)

        itens = client.listar_tudo("pedido-itens", filtros={"pedido_id": pedido_id})
        client.resolver("pedido-itens", itens, campos=["produto_id"])
        personalizados = client.listar_tudo(
            "pedido-itens-personalizados", filtros={"pedido_id": pedido_id}
        )
        client.resolver("pedido-itens-personalizados", personalizados)

        def _valor(lista):
            return round(
                sum(
                    float(i.get("quantidade_real") or i.get("quantidade_pedida") or 0)
                    * float(i.get("valor_unitario") or 0)
                    for i in lista
                ),
                2,
            )

        saida = {
            "pedido": cab,
            "itens": itens,
            "itens_personalizados": personalizados,
            "totais": {
                "qtd_itens": len(itens) + len(personalizados),
                "valor_itens": _valor(itens),
                "valor_itens_personalizados": _valor(personalizados),
                "valor_total": round(_valor(itens) + _valor(personalizados), 2),
                "criterio": "quantidade_real (ou pedida, se real vazia) × valor_unitario",
            },
        }
        if incluir_historico:
            hist = client.listar_tudo(
                "pedido-fase-historico", filtros={"pedido_id": pedido_id}, order="id"
            )
            client.resolver("pedido-fase-historico", hist, campos=["user_id"])
            saida["historico_fases"] = hist
        if incluir_comentarios:
            coms = client.listar_tudo(
                "pedido-comentarios", filtros={"pedido_id": pedido_id}, order="id"
            )
            client.resolver("pedido-comentarios", coms, campos=["user_id"])
            saida["comentarios"] = coms
        anexos = client.listar_tudo("anexos", filtros={"anexable_id": pedido_id})
        saida["anexos"] = [a for a in anexos if str(a.get("anexable_type", "")).endswith("Pedido")]
        return {"status": "ok", "data": saida}
    except (PDVError, ValueError) as e:
        return _err(e)


@mcp.tool()
def pdv_pipeline(tipo: Optional[str] = None, dias_travado: int = 3) -> dict:
    """Quadro do funil: quantos pedidos em cada fase, quanto tempo parados e quais travaram.

    A API do PDV não tem GROUP BY — a contagem é feita aqui sobre a base paginada.

    Args:
        tipo: restringe a 'Pedido', 'Pedido Urgente', 'Amostra' ou 'Cotação'.
        dias_travado: a partir de quantos dias na mesma fase o pedido entra na lista de travados.
    """
    try:
        filtros: dict = {"fase__in": ",".join(FASES_EM_ANDAMENTO)}
        if tipo:
            tipo_id = client.id_do_catalogo("tipo_pedido", tipo)
            if tipo_id is None:
                return {"status": "error", "error": f"tipo '{tipo}' inválido"}
            filtros["tipo_pedido_id"] = tipo_id

        abertos = client.listar_tudo("pedidos", filtros=filtros, fields=CAMPOS_PEDIDO, order="id")
        client.resolver("pedidos", abertos)
        for p in abertos:
            p["dias_na_fase"] = _dias_na_fase(p)

        por_fase: dict[str, dict] = {}
        for p in abertos:
            f = str(p.get("fase"))
            b = por_fase.setdefault(f, {"fase": f, "pedidos": 0, "dias_max": 0.0, "dias_medio": 0.0})
            b["pedidos"] += 1
            d = p.get("dias_na_fase") or 0
            b["dias_max"] = max(b["dias_max"], d)
            b["dias_medio"] += d
        for b in por_fase.values():
            b["dias_medio"] = round(b["dias_medio"] / b["pedidos"], 1)

        travados = sorted(
            [p for p in abertos if (p.get("dias_na_fase") or 0) >= dias_travado],
            key=lambda p: -(p.get("dias_na_fase") or 0),
        )
        ordem = {f: i for i, f in enumerate(FASES)}
        return {
            "status": "ok",
            "em_andamento": len(abertos),
            "por_fase": sorted(por_fase.values(), key=lambda b: ordem.get(b["fase"], 99)),
            "travados": travados[:50],
            "criterio_travado": f">= {dias_travado} dias na mesma fase",
            "nota": "fases concluido/cancelado ficam fora; pedidos com fase nula não entram em nenhum grupo",
        }
    except PDVError as e:
        return _err(e)


@mcp.tool()
def pdv_amostras(
    status: Optional[str] = None,
    cliente: Optional[str] = None,
    vendedor: Optional[str] = None,
    data_de: Optional[str] = None,
    data_ate: Optional[str] = None,
    limit: int = 200,
) -> dict:
    """Acompanhamento de amostras: pedidos do tipo Amostra com seu status de envio.

    Sem filtro de status, devolve o resumo por status + as amostras em aberto
    (tudo que não foi enviado nem cancelado).

    Args:
        status: 'Nao_Iniciado', 'Aguardando_Separacao', 'Aguardando_Enviar', 'Enviado', 'Cancelado'.
        cliente: parte do nome da empresa.
        vendedor: parte do nome do vendedor.
        data_de / data_ate: recorte por data_lancamento (YYYY-MM-DD).
        limit: teto de linhas devolvidas.
    """
    try:
        tipo_id = client.id_do_catalogo("tipo_pedido", "Amostra")
        filtros: dict = {"tipo_pedido_id": tipo_id}
        if status:
            if status not in STATUS_AMOSTRA:
                return {"status": "error", "error": f"status '{status}' inválido",
                        "status_validos": STATUS_AMOSTRA}
            filtros["status_amostra"] = status
        if cliente:
            ids = _resolver_parceiros(cliente)
            if not ids:
                return {"status": "ok", "rows": 0, "data": [], "aviso": f"nenhum parceiro casa com '{cliente}'"}
            filtros["parceiro_id__in"] = ",".join(str(i) for i in ids)
        if vendedor:
            ids = _resolver_usuarios(vendedor)
            if not ids:
                return {"status": "ok", "rows": 0, "data": [], "aviso": f"nenhum usuário casa com '{vendedor}'"}
            filtros["usuario_id__in"] = ",".join(str(i) for i in ids)
        filtros.update(_filtros_periodo("data_lancamento", data_de, data_ate))

        todas = client.listar_tudo("pedidos", filtros=filtros, fields=CAMPOS_PEDIDO, order="-id")
        client.resolver("pedidos", todas)
        for a in todas:
            a["dias_na_fase"] = _dias_na_fase(a)

        resumo = client.contar_por(todas, "status_amostra")
        em_aberto = [a for a in todas if a.get("status_amostra") not in ("Enviado", "Cancelado")]
        dados = todas if status else em_aberto
        return {
            "status": "ok",
            "total_amostras": len(todas),
            "por_status": resumo,
            "rows": len(dados[:limit]),
            "data": dados[:limit],
            "nota": "status_amostra nulo = amostra sem status preenchido no PDV",
        }
    except PDVError as e:
        return _err(e)


# ── Parceiros e produtos ──────────────────────────────────────────────────


def _resolver_parceiros(busca: str) -> list[int]:
    """Nome parcial (ou id) → lista de parceiro_id."""
    busca = str(busca).strip()
    if busca.isdigit():
        return [int(busca)]
    achados = client.listar_tudo(
        "parceiros", filtros={"nome_empresa__like": busca}, fields="id,nome_empresa"
    )
    return [int(p["id"]) for p in achados]


def _resolver_usuarios(busca: str) -> list[int]:
    """Nome parcial (ou id) → lista de user_id."""
    busca = str(busca).strip()
    if busca.isdigit():
        return [int(busca)]
    achados = client.listar_tudo("users", filtros={"name__like": busca}, fields="id,name")
    return [int(u["id"]) for u in achados]


@mcp.tool()
def pdv_parceiros(
    busca: Optional[str] = None,
    uf: Optional[str] = None,
    cnpj: Optional[str] = None,
    vendedor: Optional[str] = None,
    setor: Optional[str] = None,
    estagio_venda: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """Busca clientes/prospects cadastrados no PDV, com setor, estágio e vendedor resolvidos.

    Args:
        busca: parte do nome da empresa.
        uf: sigla do estado, ex. 'SP'.
        cnpj: busca parcial por CNPJ.
        vendedor: parte do nome do vendedor dono da conta.
        setor: ex. 'Agroquímicos', 'Químicos', 'Alimentício'.
        estagio_venda: ex. 'Ativo (Comprou em 2025)', 'Em Desenvolvimento/Negociação'.
        limit: linhas na página (1..1000).
    """
    try:
        filtros: dict = {}
        if busca:
            filtros["nome_empresa__like"] = busca
        if uf:
            filtros["uf"] = uf.upper()
        if cnpj:
            filtros["cnpj__like"] = "".join(ch for ch in cnpj if ch.isdigit()) or cnpj
        if vendedor:
            ids = _resolver_usuarios(vendedor)
            if not ids:
                return {"status": "ok", "rows": 0, "data": [], "aviso": f"nenhum usuário casa com '{vendedor}'"}
            filtros["usuario_id__in"] = ",".join(str(i) for i in ids)
        for campo, valor in (("setor_id", setor), ("estagio_venda_id", estagio_venda)):
            if valor:
                cid = client.id_do_catalogo(campo[:-3], valor)
                if cid is None:
                    opcoes = [c["name"] for c in client.catalogos_por_tipo(campo[:-3])]
                    return {"status": "error", "error": f"{campo[:-3]} '{valor}' inválido", "opcoes": opcoes}
                filtros[campo] = cid

        r = client.listar("parceiros", filtros=filtros, fields=CAMPOS_PARCEIRO,
                          order="nome_empresa", limit=limit)
        linhas = r.get("data", [])
        client.resolver("parceiros", linhas)
        return _ok(linhas, total=r.get("meta", {}).get("total"), filtros_aplicados=filtros)
    except PDVError as e:
        return _err(e)


@mcp.tool()
def pdv_produtos(
    busca: Optional[str] = None,
    rcp: Optional[str] = None,
    sku: Optional[str] = None,
    cliente: Optional[str] = None,
    tipo: Optional[str] = None,
    limit: int = 100,
) -> dict:
    """Busca produtos cadastrados no PDV (bombonas/tampas) com atributos de catálogo resolvidos.

    Args:
        busca: parte do texto de 'produto_vendido_como'.
        rcp: código RCP (busca parcial).
        sku: SKU (busca parcial).
        cliente: parte do nome do parceiro dono do produto, ou o id.
        tipo: 'bombona' ou 'tampa'.
        limit: linhas na página (1..1000).
    """
    try:
        filtros: dict = {}
        if busca:
            filtros["produto_vendido_como__like"] = busca
        if rcp:
            filtros["rcp__like"] = rcp
        if sku:
            filtros["sku__like"] = sku
        if tipo:
            filtros["tipo"] = tipo
        if cliente:
            ids = _resolver_parceiros(cliente)
            if not ids:
                return {"status": "ok", "rows": 0, "data": [], "aviso": f"nenhum parceiro casa com '{cliente}'"}
            filtros["parceiro_id__in"] = ",".join(str(i) for i in ids)

        r = client.listar("produtos", filtros=filtros, fields=CAMPOS_PRODUTO, order="-id", limit=limit)
        linhas = r.get("data", [])
        client.resolver("produtos", linhas)
        return _ok(linhas, total=r.get("meta", {}).get("total"), filtros_aplicados=filtros)
    except PDVError as e:
        return _err(e)


@mcp.tool()
def pdv_pendentes_sap(dias_min: int = 0, limit: int = 200) -> dict:
    """Pedidos que ainda não têm número do SAP — a lacuna entre o PDV e o SAP.

    Amostra e Cotação não geram documento no SAP por natureza, então saem separadas
    do que é lacuna de verdade: pedido de venda ('Pedido' / 'Pedido Urgente') sem num_sap.
    `divergencias` é o grupo que merece investigação — venda encerrada e nunca lançada.

    Args:
        dias_min: só lista pedidos parados há pelo menos N dias na fase atual.
        limit: teto de linhas devolvidas em cada grupo.
    """
    try:
        linhas = client.listar_tudo(
            "pedidos", filtros={"num_sap__null": 1}, fields=CAMPOS_PEDIDO, order="-id"
        )
        client.resolver("pedidos", linhas)
        for p in linhas:
            p["dias_na_fase"] = _dias_na_fase(p)
        if dias_min:
            linhas = [p for p in linhas if (p.get("dias_na_fase") or 0) >= dias_min]

        vendas = [p for p in linhas if p.get("tipo_pedido") in ("Pedido", "Pedido Urgente")]
        nao_faturaveis = [p for p in linhas if p.get("tipo_pedido") in ("Amostra", "Cotação")]
        pendentes = [p for p in vendas if p.get("fase") in FASES_EM_ANDAMENTO]
        divergencias = [p for p in vendas if p.get("fase") == "concluido"]
        canceladas = [p for p in vendas if p.get("fase") == "cancelado"]
        sem_fase = [p for p in linhas if p.get("fase") not in FASES]
        return {
            "status": "ok",
            "total_sem_num_sap": len(linhas),
            "por_tipo": client.contar_por(linhas, "tipo_pedido"),
            "por_fase": client.contar_por(linhas, "fase"),
            "vendas_pendentes": pendentes[:limit],
            "divergencias_venda_concluida_sem_sap": divergencias[:limit],
            "vendas_canceladas_sem_sap": len(canceladas),
            "nao_faturaveis_amostra_cotacao": len(nao_faturaveis),
            "sem_fase": sem_fase[:limit],
            "nota": (
                "Amostra/Cotação sem num_sap é o comportamento esperado. "
                "Pedidos com fase nula são registros antigos e não entram em nenhum grupo de fase."
            ),
        }
    except PDVError as e:
        return _err(e)


# ── Escape hatch ──────────────────────────────────────────────────────────


@mcp.tool()
def pdv_query(
    recurso: str,
    filtros: Optional[str] = None,
    fields: Optional[str] = None,
    order: Optional[str] = None,
    limit: int = 100,
    page: int = 1,
    resolver_nomes: bool = True,
) -> dict:
    """Consulta genérica a qualquer recurso do PDV. Use quando as tools específicas não bastarem.

    Args:
        recurso: nome do recurso (veja `pdv_recursos`).
        filtros: JSON com os filtros, ex. '{"fase":"concluido","data_lancamento__gte":"2026-07-01"}'.
                 Operadores: neq, gt, gte, lt, lte, like, in, nin, null.
        fields: colunas separadas por vírgula. Vazio = todas.
        order: ex. '-id', 'uf,-created_at'.
        limit: 1..1000.
        page: página (use com meta.pages para varrer tudo).
        resolver_nomes: acrescenta o nome legível de cada campo `_id`.
    """
    try:
        f = json.loads(filtros) if filtros else {}
        if not isinstance(f, dict):
            return {"status": "error", "error": "filtros deve ser um objeto JSON"}
        r = client.listar(recurso, filtros=f, fields=fields, order=order, limit=limit, page=page)
        linhas = r.get("data", [])
        if resolver_nomes:
            client.resolver(recurso, linhas)
        meta = r.get("meta", {})
        return _ok(linhas, total=meta.get("total"), pagina=meta.get("page"), paginas=meta.get("pages"))
    except json.JSONDecodeError as e:
        return {"status": "error", "error": f"filtros não é JSON válido: {e}"}
    except (PDVError, ValueError) as e:
        return _err(e)


if __name__ == "__main__":
    mcp.run()
