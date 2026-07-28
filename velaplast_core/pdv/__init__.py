"""Cliente do PDV Velaplast (sistema de acompanhamento de pedidos e amostras).

Expõe `PDVClient` (cliente HTTP read-only sobre a API externa `/api/ext/v1`),
as exceções `PDVError` / `PDVValidationError`, e o mapa de recursos descoberto
na instância real (`RECURSOS`).

A API do PDV é somente-leitura, não tem JOIN e não tem agregação — o cliente
resolve as duas lacunas: `resolver_catalogos()` faz o de/para dos campos `_id`
e `listar_tudo()` pagina até o fim para que somatórios sejam feitos aqui.
"""

from .amostras import (
    AREA_POR_ESTAGIO,
    ESTAGIOS,
    ESTAGIOS_INTERNOS,
    SLA_DIAS,
    Amostra,
    Etapa,
    area_responsavel,
    carregar_amostras,
    dias_no_estagio,
    normalizar_estagio,
    resumo_por_area,
    tempo_medio_por_estagio,
    travadas,
    trilha,
)
from .client import PDVClient, PDVError, PDVValidationError
from .resources import CATALOG_FK, FASES, RECURSOS, STATUS_AMOSTRA

__all__ = [
    "PDVClient",
    "PDVError",
    "PDVValidationError",
    "RECURSOS",
    "CATALOG_FK",
    "FASES",
    "STATUS_AMOSTRA",
    # motor de amostras
    "Amostra",
    "Etapa",
    "ESTAGIOS",
    "ESTAGIOS_INTERNOS",
    "AREA_POR_ESTAGIO",
    "SLA_DIAS",
    "carregar_amostras",
    "trilha",
    "dias_no_estagio",
    "area_responsavel",
    "normalizar_estagio",
    "travadas",
    "resumo_por_area",
    "tempo_medio_por_estagio",
]
