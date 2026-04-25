"""Cliente SAP B1 unificado.

Classe `SAPClient` encapsula:
- HTTP com `requests.Session` + retry/backoff via `HTTPAdapter`
- Cache in-memory (MD5 do SQL, TTL 300s, máx 200 entradas)
- Validação de parâmetros (datas YYYY-MM-DD, card_code alfanumérico)
- Paginação automática por mês (SAP tem limite de 1000 linhas/query)
- Rate limit leve (token bucket ~100 req/min) opcional e thread-safe

REGRA INVIOLÁVEL: toda query que aceita período deve validar com
`_validate_date`. O chamador é responsável por incluir filtro de data
no SQL — o cliente não injeta automaticamente.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
import threading
import time
from datetime import date, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from velaplast_core.config import get_sap_api_url, get_sap_api_token

log = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────
CACHE_TTL_SECONDS = 300
CACHE_MAX_ENTRIES = 200
RETRY_TOTAL = 5
RETRY_BACKOFF = 0.3
RETRY_STATUS = (500, 502, 503, 504)

# Rate limit: SAP suporta 100 req/min. Token bucket simples.
RATE_LIMIT_PER_MINUTE = 100

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CARD_CODE_RE = re.compile(r"^[A-Za-z0-9_\-\.]{1,30}$")


# ─── Exceções ─────────────────────────────────────────────────────────────
class SAPError(Exception):
    """Erro genérico ao consultar a API SAP."""


class SAPValidationError(SAPError):
    """Erro de validação de parâmetros antes da query (data, card_code)."""


# ─── Rate limiter (token bucket simples) ──────────────────────────────────
class _TokenBucket:
    """Token bucket thread-safe (~N req/minuto)."""

    def __init__(self, per_minute: int = RATE_LIMIT_PER_MINUTE) -> None:
        self.capacity = float(per_minute)
        self.tokens = float(per_minute)
        self.refill_rate = per_minute / 60.0  # tokens/segundo
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> None:
        """Bloqueia até ter um token ou estourar timeout."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.refill_rate
            if time.monotonic() + wait > deadline:
                raise SAPError(f"rate limit: timeout aguardando token ({timeout}s)")
            time.sleep(min(wait, 0.5))


# ─── Cliente ──────────────────────────────────────────────────────────────
class SAPClient:
    """Cliente HTTP para a Query API do SAP B1 (endpoint custom Velaplast).

    Uso:
        sap = SAPClient()
        result = sap.query("SELECT * FROM OITM WHERE \"ItemCode\" = 'X'")
        # result = {status, colunas, dados, total_linhas, response_time_ms}
    """

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = url or get_sap_api_url()
        self.token = token or get_sap_api_token()

        self._session = requests.Session()
        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=RETRY_STATUS,
            allowed_methods=frozenset(["POST", "GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=retry,
        )
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        # Cache por instância (simples; queries idênticas reaproveitam em 5min)
        self._cache: dict[str, tuple[dict, float]] = {}
        self._cache_lock = threading.Lock()

        self._rate_limiter = _TokenBucket(RATE_LIMIT_PER_MINUTE)

        if not self.token:
            log.warning("SAPClient: token não configurado — queries vão falhar")

    # ── Cache ────────────────────────────────────────────────────────────
    @staticmethod
    def _cache_key(sql: str) -> str:
        """Normaliza whitespace do SQL e gera chave MD5."""
        normalized = " ".join(sql.split())
        return hashlib.md5(normalized.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> dict | None:
        """Retorna cópia do resultado cacheado se ainda fresco, senão None."""
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and (time.time() - entry[1]) < CACHE_TTL_SECONDS:
                return copy.deepcopy(entry[0])
            if entry:
                del self._cache[key]
        return None

    def _cache_put(self, key: str, value: dict) -> None:
        """Armazena resultado no cache. Evict TTL-expirados e aplica cap duro."""
        with self._cache_lock:
            now = time.time()
            expired = [k for k, (_, ts) in self._cache.items() if now - ts >= CACHE_TTL_SECONDS]
            for k in expired:
                del self._cache[k]
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                # Remove os 50 mais antigos por timestamp
                oldest = sorted(self._cache.items(), key=lambda kv: kv[1][1])[:50]
                for k, _ in oldest:
                    del self._cache[k]
            self._cache[key] = (value, now)

    # ── Validação ────────────────────────────────────────────────────────
    @staticmethod
    def _validate_date(s: str | None) -> str | None:
        """Valida string de data no formato YYYY-MM-DD. Passa None adiante."""
        if s is None:
            return None
        value = str(s)[:10]
        if not _DATE_RE.fullmatch(value):
            raise SAPValidationError(f"data inválida (esperado YYYY-MM-DD): {s!r}")
        return value

    @staticmethod
    def _validate_card_code(s: str | None) -> str | None:
        """Valida CardCode do SAP (alfanumérico + `_-.`, até 30 chars)."""
        if s is None:
            return None
        if not _CARD_CODE_RE.match(s):
            raise SAPValidationError(f"card_code inválido: {s!r}")
        return s

    # ── Paginação ────────────────────────────────────────────────────────
    @staticmethod
    def _month_chunks(inicio: str, fim: str) -> list[tuple[str, str]]:
        """Divide [inicio, fim] em chunks mensais (limite de 1000 linhas/query)."""
        dt_start = date.fromisoformat(str(inicio)[:10])
        dt_end = date.fromisoformat(str(fim)[:10])
        if dt_end < dt_start:
            raise SAPValidationError(f"período inválido: fim ({fim}) < início ({inicio})")
        chunks: list[tuple[str, str]] = []
        cursor = dt_start
        while cursor <= dt_end:
            if cursor.month == 12:
                month_end = date(cursor.year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
            chunk_end = min(month_end, dt_end)
            chunks.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
            cursor = chunk_end + timedelta(days=1)
        return chunks

    def _query_paginated(
        self,
        sql_template: str,
        inicio: str,
        fim: str,
        timeout: int = 30,
        **kwargs: object,
    ) -> dict:
        """Executa query paginada por mês.

        `sql_template` deve conter os placeholders `{data_inicio}` e
        `{data_fim}`. Demais placeholders podem vir em **kwargs.
        Retorna dict agregando `dados` de todos os chunks.
        """
        inicio = self._validate_date(inicio) or ""
        fim = self._validate_date(fim) or ""
        chunks = self._month_chunks(inicio, fim)

        if len(chunks) <= 1:
            sql = sql_template.format(data_inicio=inicio, data_fim=fim, **kwargs)
            return self.query(sql, timeout=timeout)

        all_dados: list = []
        colunas: list | None = None
        total_ms = 0
        for c_inicio, c_fim in chunks:
            sql = sql_template.format(data_inicio=c_inicio, data_fim=c_fim, **kwargs)
            result = self.query(sql, timeout=timeout)
            if not result:
                continue
            if colunas is None:
                colunas = result.get("colunas", [])
            all_dados.extend(result.get("dados") or [])
            total_ms += int(result.get("response_time_ms") or 0)

        return {
            "status": "ok",
            "colunas": colunas or [],
            "dados": all_dados,
            "total_linhas": len(all_dados),
            "response_time_ms": total_ms,
        }

    # ── HTTP ─────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def query(self, sql: str, timeout: int = 30) -> dict:
        """Executa query SQL contra o SAP.

        Retorna: `{status, colunas, dados, total_linhas, response_time_ms}`.

        IMPORTANTE: o chamador DEVE incluir filtro de data no SQL. Resultados
        são cacheados por `CACHE_TTL_SECONDS` segundos (chave = MD5 do SQL).
        """
        if not self.token:
            raise SAPError("SAP_API_TOKEN não configurado")

        ck = self._cache_key(sql)
        cached = self._cache_get(ck)
        if cached is not None:
            log.debug("SAP cache HIT (%s)", ck[:8])
            return cached

        self._rate_limiter.acquire(timeout=timeout)

        t0 = time.time()
        try:
            resp = self._session.post(
                self.url,
                json={"sql": sql},
                headers=self._headers(),
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout as e:
            raise SAPError(f"SAP timeout ({timeout}s)") from e
        except requests.exceptions.RequestException as e:
            raise SAPError(f"SAP request error: {e}") from e
        except ValueError as e:
            raise SAPError("SAP retornou resposta inválida (não-JSON)") from e

        elapsed_ms = int((time.time() - t0) * 1000)
        # Garante campo padronizado de timing
        if "response_time_ms" not in data:
            data["response_time_ms"] = elapsed_ms

        total = data.get("total_linhas", "?")
        log.debug("SAP query OK: %s rows em %sms (key=%s)", total, elapsed_ms, ck[:8])
        if elapsed_ms > 5000:
            log.warning("SAP query lenta: %sms — %s", elapsed_ms, sql[:120])

        self._cache_put(ck, data)
        return data
