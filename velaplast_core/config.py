"""velaplast_core.config — configuração compartilhada.

Responsabilidades:
- Timezone BRT canônico (`BRT`, `hoje_brasil`, `agora_brasil`, `parse_ts`)
- Getters lazy de env vars comuns (DATABASE_URL, SAP_API_*, JWT secret, AI keys, LiveMES, Spotter)
- `setup_logging` helper padronizado

Env vars específicas de um projeto (ex: WORK_CENTERS_FALLBACK, EVOLUTION_API_KEY,
REFUGO_API_URL, WHATSAPP_*) permanecem no `config.py` do próprio projeto.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ─── Timezone canônico (BRT = America/Sao_Paulo) ──────────────────

BRT = ZoneInfo("America/Sao_Paulo")


def hoje_brasil() -> date:
    """Data atual em timezone BRT."""
    return datetime.now(BRT).date()


def agora_brasil() -> datetime:
    """Datetime atual em timezone BRT."""
    return datetime.now(BRT)


def parse_ts(ts_str: str, fallback: Optional[datetime] = None) -> datetime:
    """Parse ISO timestamp assumindo BRT se naive. Retorna `fallback` ou datetime.min-BRT se falhar."""
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BRT)
        return dt
    except (ValueError, TypeError):
        return fallback if fallback is not None else datetime.min.replace(tzinfo=BRT)


# ─── Env var getters (lazy; re-lêem o ambiente a cada chamada) ─────

def get_database_url() -> str:
    return os.getenv("DATABASE_URL", "")


def get_sap_api_url() -> str:
    return os.getenv("SAP_API_URL", "http://link.velaplast.com.br:8877/query")


def get_sap_api_token() -> str:
    return os.getenv("SAP_API_TOKEN", "")


def get_jwt_secret() -> str:
    """JWT secret. Aceita `JWT_SECRET` (preferido) ou `SECRET_KEY` (legado)."""
    return os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY") or ""


def get_jwt_expiry_hours() -> int:
    return int(os.getenv("JWT_EXPIRY_HOURS", "24"))


def get_ai_keys() -> dict[str, str]:
    """Retorna chaves dos 4 providers AI (valor vazio = não configurado)."""
    return {
        "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
        "openai": os.getenv("OPENAI_API_KEY", ""),
        "gemini": os.getenv("GEMINI_API_KEY", ""),
        "groq": os.getenv("GROQ_API_KEY", ""),
    }


def get_livemes_config() -> dict[str, str]:
    """Config do cliente LiveMES."""
    return {
        "url": os.getenv("LIVEMES_URL", "https://app.livemes.com/integration"),
        "token": os.getenv("LIVEMES_TOKEN", ""),
    }


def get_spotter_token() -> str:
    return os.getenv("SPOTTER_TOKEN", "")


def get_pdv_api_url() -> str:
    """URL base da API externa (read-only) do PDV."""
    return os.getenv("PDV_API_URL", "https://pdv.velaplast.com.br/api/ext/v1")


def get_pdv_api_key() -> str:
    """Chave `X-API-Key` do PDV. Vazio = não configurado."""
    return os.getenv("PDV_API_KEY", "")


# ─── Logging setup ────────────────────────────────────────────────

def setup_logging(
    name: str,
    level: int = logging.INFO,
    log_file: Optional[str] = None,
) -> logging.Logger:
    """Configura handlers de logging (stdout + file opcional) e retorna logger nomeado."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )
    return logging.getLogger(name)
