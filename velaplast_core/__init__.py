"""velaplast-core: biblioteca Velaplast compartilhada.

Módulos:
    sap      — cliente SAP B1 + queries canônicas
    pdv      — cliente do PDV (pedidos e amostras), read-only
    livemes  — cliente LiveMES (MES industrial)
    spotter  — cliente Spotter (Exact Sales CRM)
    finance  — fórmulas canônicas: faturamento NET, margem bruta, impostos
    ai       — AIEngine multi-provider (Claude, GPT-4o, Gemini, Groq)

Infra:
    db       — pool PG + fallback JSON
    auth     — JWT + decorators
    utils    — helpers comuns (timezone BRT, escrita atômica, etc.)
"""

__version__ = "0.1.0"
