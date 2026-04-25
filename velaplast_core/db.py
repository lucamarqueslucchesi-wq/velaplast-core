"""velaplast_core.db — camada de persistência compartilhada.

Responsabilidades:
- Pool PostgreSQL `ThreadedConnectionPool(1, 10)` com lazy init thread-safe
- Primitivas genéricas: `conn` (context manager), `query`, `execute`, `init_db`
- JSON fallback transparente (`json_load`, `json_save`) para modo local/dev
- `pg_enabled()` helper para os projetos decidirem entre PG e JSON

Funções domain-specific (snapshots, thresholds, leads, etc.) permanecem no
`db.py` ou módulo equivalente do próprio projeto — aqui ficam apenas primitivas.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from velaplast_core.config import get_database_url

log = logging.getLogger(__name__)


# ─── Estado do pool (lazy, thread-safe) ───────────────────────────

_pool: Any = None
_pool_lock = threading.Lock()
_pool_init_tried = False


def _get_pool() -> Any:
    """Retorna o pool; cria sob demanda na primeira chamada. Double-checked locking."""
    global _pool, _pool_init_tried
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        if _pool_init_tried:
            return None
        _pool_init_tried = True
        dsn = get_database_url()
        if not dsn:
            log.info("DB: DATABASE_URL vazio — modo JSON fallback")
            return None
        try:
            from psycopg2 import pool as pg_pool
            _pool = pg_pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=dsn)
            log.info("DB: pool PostgreSQL criado (1-10 conexões)")
            return _pool
        except Exception as e:
            log.warning(f"DB: falha ao criar pool PostgreSQL: {e}")
            return None


def get_pool() -> Any:
    """Retorna o pool (ou None se `DATABASE_URL` não estiver configurado)."""
    return _get_pool()


def pg_enabled() -> bool:
    """True se `DATABASE_URL` está configurado (não garante que o pool conectou)."""
    return bool(get_database_url())


# ─── Context manager de conexão ───────────────────────────────────

@contextmanager
def conn() -> Iterator[Any]:
    """Checkout de uma conexão do pool com release automático.

    Uso:
        with conn() as c:
            with c.cursor() as cur:
                cur.execute("SELECT 1")

    Levanta `RuntimeError` se o pool não estiver disponível.
    """
    pool = _get_pool()
    if pool is None:
        raise RuntimeError("PostgreSQL indisponível (DATABASE_URL vazio ou pool falhou)")
    c = pool.getconn()
    try:
        yield c
    finally:
        try:
            pool.putconn(c)
        except Exception as e:
            log.warning(f"DB: erro ao devolver conexão ao pool: {e}")


# ─── Helpers de query ─────────────────────────────────────────────

def query(
    sql: str,
    params: Optional[tuple | list | dict] = None,
    one: bool = False,
) -> list[dict] | dict | None:
    """Executa SELECT e retorna lista de dicts (ou dict único se `one=True`).

    Usa `RealDictCursor`. Retorna `[]` (ou `None` se `one=True`) em erro/pg indisponível.
    """
    if not pg_enabled():
        return None if one else []
    try:
        from psycopg2.extras import RealDictCursor
        with conn() as c:
            with c.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                if one:
                    row = cur.fetchone()
                    return dict(row) if row else None
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        log.warning(f"DB query error: {e}")
        return None if one else []


def execute(sql: str, params: Optional[tuple | list | dict] = None) -> int:
    """Executa INSERT/UPDATE/DELETE com auto-commit. Retorna `rowcount` (-1 em erro)."""
    if not pg_enabled():
        return -1
    try:
        with conn() as c:
            try:
                with c.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.rowcount
                c.commit()
                return rows
            except Exception:
                try:
                    c.rollback()
                except Exception:
                    pass
                raise
    except Exception as e:
        log.warning(f"DB execute error: {e}")
        return -1


def init_db(schema_sql_path: str) -> bool:
    """Lê o arquivo SQL e executa (criação idempotente de tabelas).

    Retorna `True` em sucesso, `False` se PG indisponível ou schema inválido.
    Requer que o SQL use `CREATE TABLE IF NOT EXISTS` etc. para ser idempotente.
    """
    if not pg_enabled():
        log.info("DB: init_db() ignorado — PostgreSQL indisponível")
        return False
    if not os.path.isfile(schema_sql_path):
        log.error(f"DB: schema não encontrado em {schema_sql_path}")
        return False
    try:
        with open(schema_sql_path, encoding="utf-8") as f:
            schema_sql = f.read()
        with conn() as c:
            try:
                with c.cursor() as cur:
                    cur.execute(schema_sql)
                c.commit()
                log.info(f"DB: schema aplicado ({os.path.basename(schema_sql_path)})")
                return True
            except Exception as e:
                try:
                    c.rollback()
                except Exception:
                    pass
                log.error(f"DB: erro ao executar schema: {e}")
                return False
    except Exception as e:
        log.error(f"DB: falha ao ler/aplicar schema: {e}")
        return False


def close_pool() -> None:
    """Fecha todas as conexões do pool (útil em testes/shutdown)."""
    global _pool, _pool_init_tried
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception as e:
                log.warning(f"DB: erro ao fechar pool: {e}")
        _pool = None
        _pool_init_tried = False


# ─── JSON fallback transparente ───────────────────────────────────

def _json_path(name: str, data_dir: str) -> str:
    """Resolve caminho `data_dir/{name}.json`."""
    safe = name if name.endswith(".json") else f"{name}.json"
    return os.path.join(data_dir, safe)


def json_load(name: str, default: Any = None, data_dir: str = "data") -> Any:
    """Carrega `data_dir/{name}.json`. Retorna `default` se não existir ou falhar."""
    path = _json_path(name, data_dir)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"JSON read error ({path}): {e}")
    return default


def json_save(name: str, data: Any, data_dir: str = "data") -> bool:
    """Escrita atômica em `data_dir/{name}.json` via tempfile + `os.replace`.

    Cria `data_dir` se não existir. Retorna `True` em sucesso.
    """
    path = _json_path(name, data_dir)
    try:
        os.makedirs(data_dir, exist_ok=True)
        dir_name = os.path.dirname(os.path.abspath(path)) or "."
        tmp_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".tmp",
                dir=dir_name,
                delete=False,
                encoding="utf-8",
            ) as tmp:
                json.dump(data, tmp, ensure_ascii=False, indent=2, default=str)
                tmp_path = tmp.name
            os.replace(tmp_path, path)
            return True
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise
    except Exception as e:
        log.warning(f"JSON write error ({path}): {e}")
        return False
