"""velaplast_core.utils — helpers genéricos compartilhados.

Consolida os utilitários comuns a velaplast-sap, velaplast-commandcenter e
refugo-livemes. Mantém apenas funções GENÉRICAS (sem acoplamento a SAP,
LiveMES, Flask-request state, ou domínios específicos).

Helpers de timezone (BRT, hoje_brasil, agora_brasil, parse_ts) vivem em
`velaplast_core.config` e podem ser importados diretamente de lá.

Helpers de finanças (build_tax_map, build_tax_ratio_map) irão para
`velaplast_core.finance` em etapa futura — NÃO duplicar aqui.

Helpers do cliente SAP (rows_to_dicts depende de formato da API SAP, mas é
um adapter genérico o suficiente para ficar na core). Clients/fetch
permanecem em `velaplast_core.sap`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from velaplast_core.config import BRT, hoje_brasil

log = logging.getLogger(__name__)

__all__ = [
    # Numéricos
    "safe_div",
    "safe_float",
    "safe_int",
    # DB/JSON
    "rows_to_dicts",
    "atomic_json_write",
    "atomic_json_read",
    # Timestamp
    "iso_utc",
    # Período
    "parse_periodo",
    # Validação
    "validate_date_iso",
    "validate_card_code",
    # Refugo
    "extrair_motivo_refugo",
]


# ─────────────────────────────────────────────────────────────────────────────
# Numéricos
# ─────────────────────────────────────────────────────────────────────────────

def safe_div(numerator: Any, denominator: Any, default: float = 0.0) -> float:
    """Divisão segura — retorna `default` se denominador for 0/None/invalido."""
    try:
        num = float(numerator) if numerator is not None else 0.0
        den = float(denominator) if denominator is not None else 0.0
    except (TypeError, ValueError):
        return default
    return num / den if den else default


def safe_float(x: Any, default: float = 0.0) -> float:
    """Converte valor para float com fallback seguro."""
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def safe_int(x: Any, default: int = 0) -> int:
    """Converte valor para int com fallback seguro.

    Aceita strings numéricas com decimais (ex: '12.0' → 12) via float intermediário.
    """
    if x is None:
        return default
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return default


# ─────────────────────────────────────────────────────────────────────────────
# DB / JSON
# ─────────────────────────────────────────────────────────────────────────────

def rows_to_dicts(
    rows: Any,
    cols: Sequence[str] | None = None,
) -> list[dict]:
    """Converte cursor rows + column names em list de dicts.

    Aceita três formatos:
      1. Cursor DB: `rows` = iterable de tuplas/listas, `cols` = nomes das colunas
      2. SAP API: `rows` = dict com chaves 'dados' (lista) e 'colunas' (lista)
      3. Já dicts: `rows` = lista de dicts → retorna como está
    """
    # Formato SAP: {"dados": [...], "colunas": [...]}
    if isinstance(rows, dict):
        dados = rows.get("dados") or []
        if not dados:
            return []
        if isinstance(dados[0], dict):
            return list(dados)
        colunas = rows.get("colunas") or []
        return [dict(zip(colunas, row)) for row in dados]

    # Lista vazia ou None
    if not rows:
        return []

    # Lista de dicts
    first = rows[0] if hasattr(rows, "__getitem__") else next(iter(rows), None)
    if isinstance(first, dict):
        return list(rows)

    # Cursor rows + cols
    if cols is None:
        raise ValueError("rows_to_dicts: 'cols' é obrigatório quando rows não são dicts")
    return [dict(zip(cols, row)) for row in rows]


def atomic_json_write(path: str, data: Any, indent: int = 2) -> None:
    """Grava JSON de forma atômica: escreve em tempfile e renomeia.

    Previne corrupção se o processo crashar durante a escrita.
    """
    dir_name = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dir_name, exist_ok=True)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".tmp",
            dir=dir_name,
            delete=False,
            encoding="utf-8",
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=indent, default=str)
            tmp_path = tmp.name
        os.replace(tmp_path, path)
    except Exception as e:
        log.error("Erro ao gravar %s: %s", path, e)
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def atomic_json_read(path: str, default: Any = None) -> Any:
    """Lê JSON; retorna `default` se arquivo não existe ou está corrompido."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Falha ao ler JSON %s: %s — usando default", path, e)
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Timestamp
# ─────────────────────────────────────────────────────────────────────────────

def iso_utc(dt: datetime | None = None) -> str:
    """Retorna timestamp ISO 8601 em UTC.

    Se `dt` for None, usa `datetime.now(timezone.utc)`.
    Datetimes naive são assumidos em UTC.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Período
# ─────────────────────────────────────────────────────────────────────────────

def parse_periodo(
    inicio: str | None = None,
    fim: str | None = None,
) -> tuple[str, str]:
    """Normaliza par (inicio, fim) em strings YYYY-MM-DD.

    - Se ambos None: retorna 1º dia do mês atual até hoje (mês corrente).
    - Se apenas um faltando: preenche com hoje (fim) ou 30d atrás (inicio).
    - Valida formato YYYY-MM-DD; raise ValueError se inválido.
    """
    hoje = hoje_brasil()

    if inicio is None and fim is None:
        inicio = hoje.replace(day=1).strftime("%Y-%m-%d")
        fim = hoje.strftime("%Y-%m-%d")
        return inicio, fim

    if fim is None:
        fim = hoje.strftime("%Y-%m-%d")
    if inicio is None:
        inicio = (hoje - timedelta(days=30)).strftime("%Y-%m-%d")

    return validate_date_iso(inicio), validate_date_iso(fim)


# ─────────────────────────────────────────────────────────────────────────────
# Validação
# ─────────────────────────────────────────────────────────────────────────────

_DATE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# SAP CardCode: letras, dígitos, hífen, underscore, ponto. Até 32 chars.
_CARD_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def validate_date_iso(s: str) -> str:
    """Valida string no formato YYYY-MM-DD. Raise ValueError se inválido."""
    if not isinstance(s, str) or not _DATE_ISO_RE.match(s):
        raise ValueError(f"Data inválida (esperado YYYY-MM-DD): {s!r}")
    # Valida calendário real (ex: 2024-02-30 falha aqui)
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"Data inválida (calendário): {s!r}") from e
    return s


def validate_card_code(s: str) -> str:
    """Valida CardCode SAP (alfanumérico + `._-`). Raise ValueError se inválido."""
    if not isinstance(s, str) or not _CARD_CODE_RE.match(s):
        raise ValueError(f"CardCode inválido: {s!r}")
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Refugo
# ─────────────────────────────────────────────────────────────────────────────

def extrair_motivo_refugo(
    raw: dict,
    fields: Iterable[str],
    default: str = "Não especificado",
) -> str:
    """Testa cada campo em `fields` em ordem; retorna primeiro valor não-vazio.

    Genérico: o chamador passa os campos candidatos (ex: a lista
    SCRAP_REASON_FIELDS do projeto refugo-livemes).
    """
    if not isinstance(raw, dict):
        return default
    for field in fields:
        val = raw.get(field)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            return s
    return default
