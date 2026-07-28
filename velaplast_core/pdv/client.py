"""Cliente da API externa do PDV Velaplast (somente leitura).

`PDVClient` encapsula:
- HTTP com `requests.Session` + retry/backoff (5xx) via `HTTPAdapter`
- Rate limit local (token bucket ~100 req/min — o teto da API) thread-safe
- Cache in-memory curto (TTL 60s) — o PDV é operacional, dado envelhece rápido
- Paginação automática (`listar_tudo`) respeitando o teto de 1000 linhas/página
- Resolução de FKs (`resolver`), já que a API não faz JOIN
- Agregação local (`contar_por`), já que a API não tem GROUP BY

A API só aceita GET; nada aqui grava no PDV.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from velaplast_core.config import get_pdv_api_key, get_pdv_api_url

from .resources import (
    CATALOG_FK,
    LABEL_FIELD,
    LIMIT_MAXIMO,
    OPERADORES,
    RATE_LIMIT_POR_MINUTO,
    RECURSOS,
)

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 200
RETRY_TOTAL = 4
RETRY_BACKOFF = 0.5
RETRY_STATUS = (500, 502, 503, 504)
# Teto de segurança em listar_tudo: 50 páginas × 1000 = 50k linhas.
MAX_PAGINAS = 50

_FILTRO_RE = re.compile(r"^[a-z0-9_]+(__(" + "|".join(OPERADORES) + r"))?$")


class PDVError(Exception):
    """Erro ao consultar a API do PDV."""


class PDVValidationError(PDVError):
    """Parâmetro inválido detectado antes de chamar a API."""


class _TokenBucket:
    """Token bucket thread-safe (~N req/minuto)."""

    def __init__(self, per_minute: int = RATE_LIMIT_POR_MINUTO) -> None:
        self.capacity = float(per_minute)
        self.tokens = float(per_minute)
        self.refill_rate = per_minute / 60.0
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity, self.tokens + (now - self.last_refill) * self.refill_rate
                )
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.refill_rate
            if time.monotonic() + wait > deadline:
                raise PDVError(f"rate limit: timeout aguardando token ({timeout}s)")
            time.sleep(min(wait, 0.5))


class PDVClient:
    """Cliente HTTP read-only da API `/api/ext/v1` do PDV.

    Uso:
        pdv = PDVClient()
        r = pdv.listar("pedidos", filtros={"fase": "aguardando_correcao"}, limit=50)
        pedido = pdv.obter("pedidos", 1096)
    """

    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        # .strip() é obrigatório: chave copiada de .env com \r (CRLF) quebra o header.
        self.base_url = (url or get_pdv_api_url()).strip().rstrip("/")
        self.api_key = (api_key or get_pdv_api_key()).strip()
        if not self.api_key:
            raise PDVValidationError(
                "PDV_API_KEY não configurada (defina no .env ou passe api_key=)"
            )

        self.session = requests.Session()
        adapter = HTTPAdapter(
            max_retries=Retry(
                total=RETRY_TOTAL,
                backoff_factor=RETRY_BACKOFF,
                status_forcelist=RETRY_STATUS,
                allowed_methods=["GET"],
            )
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"X-API-Key": self.api_key, "Accept": "application/json"})

        self._bucket = _TokenBucket()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = threading.Lock()
        self._catalog_cache: dict[int, dict] | None = None
        self._label_cache: dict[str, dict[int, str]] = {}

    # ── cache ────────────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> Any | None:
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
                return json.loads(json.dumps(hit[1]))  # cópia defensiva
            if hit:
                self._cache.pop(key, None)
        return None

    def _cache_put(self, key: str, value: Any) -> None:
        with self._cache_lock:
            if len(self._cache) >= CACHE_MAX_ENTRIES:
                mais_velho = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
                self._cache.pop(mais_velho, None)
            self._cache[key] = (time.time(), value)

    def limpar_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()
        self._catalog_cache = None
        self._label_cache.clear()

    # ── validação ────────────────────────────────────────────────────────

    @staticmethod
    def _validar_recurso(recurso: str) -> str:
        if recurso not in RECURSOS:
            raise PDVValidationError(
                f"recurso '{recurso}' não existe. Disponíveis: {', '.join(RECURSOS)}"
            )
        return recurso

    @staticmethod
    def _validar_filtros(recurso: str, filtros: dict[str, Any]) -> dict[str, Any]:
        """Rejeita filtro cujo campo não existe no recurso — evita filtro silencioso errado.

        A API já devolve 422 nesse caso; validar aqui dá mensagem melhor e economiza
        uma requisição. Recursos com campos desconhecidos (tabelas vazias) passam direto.
        """
        campos = set(RECURSOS[recurso]["campos"])
        limpos: dict[str, Any] = {}
        for chave, valor in (filtros or {}).items():
            if valor is None:
                continue
            if not _FILTRO_RE.match(chave):
                raise PDVValidationError(
                    f"filtro '{chave}' malformado. Use campo ou campo__op "
                    f"(op ∈ {', '.join(OPERADORES)})"
                )
            campo = chave.split("__", 1)[0]
            if campos and campo not in campos:
                raise PDVValidationError(
                    f"campo '{campo}' não existe em '{recurso}'. "
                    f"Campos: {', '.join(sorted(campos))}"
                )
            if isinstance(valor, (list, tuple, set)):
                valor = ",".join(str(v) for v in valor)
            elif isinstance(valor, bool):
                valor = 1 if valor else 0
            limpos[chave] = valor
        return limpos

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, Any] | None = None, timeout: int = 30) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        chave_cache = f"{path}?{json.dumps(params, sort_keys=True, default=str)}"
        em_cache = self._cache_get(chave_cache)
        if em_cache is not None:
            return em_cache

        self._bucket.acquire()
        url = f"{self.base_url}/{path.lstrip('/')}" if path else self.base_url
        try:
            r = self.session.get(url, params=params, timeout=timeout)
        except requests.RequestException as e:
            raise PDVError(f"falha de rede ao chamar {url}: {e}") from e

        if r.status_code == 429:
            # A API corta em 100 req/min. Espera curta e uma única retentativa.
            time.sleep(2.0)
            r = self.session.get(url, params=params, timeout=timeout)

        if not r.ok:
            try:
                msg = r.json().get("error", r.text[:300])
            except ValueError:
                msg = r.text[:300]
            raise PDVError(f"HTTP {r.status_code} em {path}: {msg}")

        try:
            payload = r.json()
        except ValueError as e:
            raise PDVError(f"resposta não-JSON de {path}: {r.text[:200]}") from e

        self._cache_put(chave_cache, payload)
        return payload

    # ── leitura ──────────────────────────────────────────────────────────

    def recursos(self) -> list[dict]:
        """Lista os recursos expostos pela API (chamada real ao índice `/`)."""
        return self._get("", None).get("data", [])

    def listar(
        self,
        recurso: str,
        filtros: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
        order: str | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> dict:
        """Uma página de um recurso. Devolve `{"data": [...], "meta": {...}}`.

        `meta.total` é a contagem do filtro inteiro — use como COUNT(*).
        """
        self._validar_recurso(recurso)
        if not 1 <= int(limit) <= LIMIT_MAXIMO:
            raise PDVValidationError(f"limit deve estar entre 1 e {LIMIT_MAXIMO} (recebido {limit})")
        params = self._validar_filtros(recurso, filtros or {})
        if fields:
            params["fields"] = fields if isinstance(fields, str) else ",".join(fields)
        if order:
            params["order"] = order
        params["limit"] = int(limit)
        params["page"] = int(page)
        return self._get(recurso, params)

    def contar(self, recurso: str, filtros: dict[str, Any] | None = None) -> int:
        """COUNT(*) do filtro, sem trafegar linhas (limit=1 + fields=id)."""
        r = self.listar(recurso, filtros=filtros, fields="id", limit=1)
        return int(r.get("meta", {}).get("total", 0))

    def obter(self, recurso: str, registro_id: int) -> dict | None:
        """Um registro por id. `None` se não existir (404)."""
        self._validar_recurso(recurso)
        try:
            return self._get(f"{recurso}/{int(registro_id)}").get("data")
        except PDVError as e:
            if "HTTP 404" in str(e):
                return None
            raise

    def listar_tudo(
        self,
        recurso: str,
        filtros: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
        order: str | None = None,
        max_linhas: int | None = None,
    ) -> list[dict]:
        """Pagina até o fim (ou até `max_linhas`). Levanta erro se estourar MAX_PAGINAS."""
        todos: list[dict] = []
        pagina = 1
        while True:
            r = self.listar(
                recurso, filtros=filtros, fields=fields, order=order,
                limit=LIMIT_MAXIMO, page=pagina,
            )
            todos.extend(r.get("data", []))
            paginas = int(r.get("meta", {}).get("pages", 1) or 1)
            if max_linhas and len(todos) >= max_linhas:
                return todos[:max_linhas]
            if pagina >= paginas:
                return todos
            pagina += 1
            if pagina > MAX_PAGINAS:
                raise PDVError(
                    f"'{recurso}' tem mais de {MAX_PAGINAS * LIMIT_MAXIMO} linhas no filtro — "
                    "restrinja o filtro ou use max_linhas"
                )

    # ── de/para de FKs (a API não faz JOIN) ──────────────────────────────

    def catalogos(self) -> dict[int, dict]:
        """Todos os `catalogs` indexados por id (cacheado no processo)."""
        if self._catalog_cache is None:
            linhas = self.listar_tudo("catalogs")
            self._catalog_cache = {int(c["id"]): c for c in linhas}
        return self._catalog_cache

    def catalogos_por_tipo(self, tipo: str | None = None) -> list[dict]:
        """Catálogos filtrados por `type` (ex.: 'tipo_pedido', 'setor')."""
        vals = list(self.catalogos().values())
        return [c for c in vals if tipo is None or c.get("type") == tipo]

    def id_do_catalogo(self, tipo: str, nome: str) -> int | None:
        """Resolve nome → id de um catálogo (case-insensitive, tolera acento parcial)."""
        alvo = nome.strip().casefold()
        for c in self.catalogos_por_tipo(tipo):
            if str(c.get("name", "")).strip().casefold() == alvo:
                return int(c["id"])
        return None

    def _labels(self, recurso: str) -> dict[int, str]:
        """id → nome de um recurso destino (parceiros, users, departamentos...)."""
        if recurso not in self._label_cache:
            campo = LABEL_FIELD.get(recurso, "id")
            linhas = self.listar_tudo(recurso, fields=f"id,{campo}" if campo != "id" else "id")
            self._label_cache[recurso] = {
                int(x["id"]): x.get(campo) for x in linhas if x.get("id") is not None
            }
        return self._label_cache[recurso]

    def resolver(
        self,
        recurso: str,
        linhas: list[dict],
        campos: Iterable[str] | None = None,
    ) -> list[dict]:
        """Acrescenta `<campo sem _id>_nome` para cada FK conhecida das linhas.

        Ex.: `tipo_pedido_id: 193` vira também `tipo_pedido: "Amostra"`;
        `parceiro_id: 338` vira também `parceiro: "OXIQUIMICA"`.
        Não remove os ids — o id continua sendo a chave para drill-down.
        """
        mapa = CATALOG_FK.get(recurso, {})
        if not mapa or not linhas:
            return linhas
        alvo = set(campos) if campos else set(mapa)
        presentes = {c for c in alvo if c in mapa and any(c in ln for ln in linhas)}
        if not presentes:
            return linhas

        catalogos = self.catalogos() if any(mapa[c] == "catalogs" for c in presentes) else {}
        labels: dict[str, dict[int, str]] = {}
        for campo in presentes:
            destino = mapa[campo]
            if destino != "catalogs" and destino not in labels:
                labels[destino] = self._labels(destino)

        for linha in linhas:
            for campo in presentes:
                valor = linha.get(campo)
                if valor is None:
                    continue
                destino = mapa[campo]
                try:
                    chave = int(valor)
                except (TypeError, ValueError):
                    continue
                if destino == "catalogs":
                    nome = (catalogos.get(chave) or {}).get("name")
                else:
                    nome = labels[destino].get(chave)
                if nome is not None:
                    linha[campo[:-3] if campo.endswith("_id") else campo + "_nome"] = nome
        return linhas

    # ── agregação local (a API não tem GROUP BY) ─────────────────────────

    @staticmethod
    def contar_por(linhas: list[dict], campo: str) -> dict[str, int]:
        """GROUP BY + COUNT feito aqui. Nulos viram a chave '(nulo)'."""
        saida: dict[str, int] = {}
        for linha in linhas:
            chave = linha.get(campo)
            chave = "(nulo)" if chave is None else str(chave)
            saida[chave] = saida.get(chave, 0) + 1
        return dict(sorted(saida.items(), key=lambda kv: -kv[1]))

    @staticmethod
    def somar(linhas: list[dict], campo: str) -> float:
        """SUM feito aqui. Ignora valores não numéricos."""
        total = 0.0
        for linha in linhas:
            try:
                total += float(linha.get(campo) or 0)
            except (TypeError, ValueError):
                continue
        return round(total, 2)

    def ping(self) -> dict:
        """Testa credencial e conectividade. Devolve total de pedidos visível."""
        inicio = time.monotonic()
        total = self.contar("pedidos")
        return {
            "ok": True,
            "url": self.base_url,
            "pedidos_visiveis": total,
            "response_time_ms": round((time.monotonic() - inicio) * 1000, 1),
        }
