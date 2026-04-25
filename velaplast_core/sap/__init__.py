"""Cliente SAP B1 unificado para projetos Velaplast.

Expõe `SAPClient` (cliente HTTP com cache + validação + paginação) e as
exceções `SAPError` / `SAPValidationError`. Funções de query específicas
por tabela (purchase_orders, goods_receipts, production_orders,
quality_tests) ficam em `velaplast_core.sap.queries`.
"""

from .client import SAPClient, SAPError, SAPValidationError

__all__ = ["SAPClient", "SAPError", "SAPValidationError"]
