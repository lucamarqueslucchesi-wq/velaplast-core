"""Query helpers por tabela SAP B1.

Funções de módulo (não métodos de classe) que recebem um `SAPClient` como
primeiro argumento. Escopo MVP: as 4 queries da cadeia MP → Qualidade que
vivem no projeto `velaplast-commandcenter`.

Cadeia de dados:

    OCRD (Fornecedor)
      └─ OPOR/POR1  (Pedido de Compra)  ── purchase_orders()
        └─ OPDN/PDN1 (Entrada NF / BatchNum) ── goods_receipts()
          └─ OWOR/WOR1 (OP consome MP, ItemType=4) ── production_orders()
            └─ OQCL/QCL1 (Testes QC) ── quality_tests()

Todas as funções:
- Validam `inicio`/`fim` (YYYY-MM-DD) — REGRA INVIOLÁVEL
- Paginam por mês via `_query_paginated` (limite 1000 rows/query)
- Retornam `list[dict]` (linhas com nome de coluna → valor)
"""

from __future__ import annotations

from .client import SAPClient


def _rows_to_dicts(result: dict) -> list[dict]:
    """Converte `{colunas, dados}` do SAP em lista de dicts."""
    cols = result.get("colunas") or []
    dados = result.get("dados") or []
    if not cols:
        return []
    return [dict(zip(cols, row)) for row in dados]


# ─── Pedidos de Compra ────────────────────────────────────────────────────
def purchase_orders(
    sap: SAPClient,
    inicio: str,
    fim: str,
    card_code: str | None = None,
) -> list[dict]:
    """Pedidos de Compra (OPOR/POR1) com linhas de item.

    Filtro opcional por `card_code` (fornecedor). Usa GROUP BY no lugar
    de DISTINCT (SAP API não suporta DISTINCT).
    """
    inicio = sap._validate_date(inicio) or ""
    fim = sap._validate_date(fim) or ""
    card = sap._validate_card_code(card_code)

    card_filter = f" AND T0.\"CardCode\" = '{card}'" if card else ""

    sql_template = f"""
    SELECT T0."DocNum", T0."DocDate", T0."CardCode", T0."CardName",
           T1."ItemCode", T1."Dscription", T1."Quantity", T1."Price", T1."LineTotal"
    FROM OPOR T0 INNER JOIN POR1 T1 ON T0."DocEntry" = T1."DocEntry"
    WHERE T0."DocDate" BETWEEN '{{data_inicio}}' AND '{{data_fim}}'{card_filter}
    GROUP BY T0."DocNum", T0."DocDate", T0."CardCode", T0."CardName",
             T1."ItemCode", T1."Dscription", T1."Quantity", T1."Price", T1."LineTotal"
    """
    result = sap._query_paginated(sql_template, inicio, fim)
    return _rows_to_dicts(result)


# ─── Entradas NF (Goods Receipt) ──────────────────────────────────────────
def goods_receipts(
    sap: SAPClient,
    inicio: str,
    fim: str,
    batch_num: str | None = None,
) -> list[dict]:
    """Entradas de Mercadoria / Goods Receipt (OPDN/PDN1).

    `BatchNum` é o link entre o lote recebido e o fornecedor — chave
    para rastreabilidade MP → Qualidade. Filtro opcional por `batch_num`.
    """
    inicio = sap._validate_date(inicio) or ""
    fim = sap._validate_date(fim) or ""

    batch_filter = ""
    if batch_num is not None:
        # Batch num: alfanumérico + hífen/underscore/ponto
        batch_clean = str(batch_num).strip()
        if not batch_clean or not all(c.isalnum() or c in "-_." for c in batch_clean):
            from .client import SAPValidationError
            raise SAPValidationError(f"batch_num inválido: {batch_num!r}")
        batch_filter = f" AND T1.\"BatchNum\" = '{batch_clean}'"

    sql_template = f"""
    SELECT T0."DocNum", T0."DocDate", T0."CardCode", T0."CardName",
           T1."ItemCode", T1."Dscription", T1."Quantity", T1."BaseEntry", T1."BatchNum"
    FROM OPDN T0 INNER JOIN PDN1 T1 ON T0."DocEntry" = T1."DocEntry"
    WHERE T0."DocDate" BETWEEN '{{data_inicio}}' AND '{{data_fim}}'{batch_filter}
    GROUP BY T0."DocNum", T0."DocDate", T0."CardCode", T0."CardName",
             T1."ItemCode", T1."Dscription", T1."Quantity", T1."BaseEntry", T1."BatchNum"
    """
    result = sap._query_paginated(sql_template, inicio, fim)
    return _rows_to_dicts(result)


# ─── Ordens de Produção ───────────────────────────────────────────────────
def production_orders(sap: SAPClient, inicio: str, fim: str) -> list[dict]:
    """Ordens de Produção + componentes de MP (OWOR/WOR1, ItemType=4).

    Filtro `ItemType=4` traz só componentes de matéria-prima consumidos
    na OP (exclui sub-produtos e mão-de-obra).
    """
    inicio = sap._validate_date(inicio) or ""
    fim = sap._validate_date(fim) or ""

    sql_template = """
    SELECT T0."DocNum", T0."Status", T0."ItemCode", T0."ProdName",
           T0."PlannedQty", T0."CmpltQty", T0."RjctQty", T0."PostDate",
           T1."ItemCode" as "CompItemCode", T1."ItemName" as "CompItemName",
           T1."BaseQty", T1."IssuedQty"
    FROM OWOR T0 INNER JOIN WOR1 T1 ON T0."DocEntry" = T1."DocEntry"
    WHERE T0."PostDate" BETWEEN '{data_inicio}' AND '{data_fim}' AND T1."ItemType" = 4
    GROUP BY T0."DocNum", T0."Status", T0."ItemCode", T0."ProdName",
             T0."PlannedQty", T0."CmpltQty", T0."RjctQty", T0."PostDate",
             T1."ItemCode", T1."ItemName", T1."BaseQty", T1."IssuedQty"
    """
    result = sap._query_paginated(sql_template, inicio, fim)
    return _rows_to_dicts(result)


# ─── Testes de Qualidade ──────────────────────────────────────────────────
def quality_tests(sap: SAPClient, inicio: str, fim: str) -> list[dict]:
    """Testes de Qualidade (OQCL/QCL1).

    Cabeçalho em OQCL (Status = 'P' aprovado / 'F' reprovado) + linhas
    de parâmetro em QCL1 (Param, MinVal, MaxVal, Result).
    """
    inicio = sap._validate_date(inicio) or ""
    fim = sap._validate_date(fim) or ""

    sql_template = """
    SELECT T0."DocNum", T0."CreateDate", T0."ItemCode", T0."Status",
           T1."Param", T1."MinVal", T1."MaxVal", T1."Result", T1."Remark"
    FROM OQCL T0 INNER JOIN QCL1 T1 ON T0."DocEntry" = T1."DocEntry"
    WHERE T0."CreateDate" BETWEEN '{data_inicio}' AND '{data_fim}'
    GROUP BY T0."DocNum", T0."CreateDate", T0."ItemCode", T0."Status",
             T1."Param", T1."MinVal", T1."MaxVal", T1."Result", T1."Remark"
    """
    result = sap._query_paginated(sql_template, inicio, fim)
    return _rows_to_dicts(result)
