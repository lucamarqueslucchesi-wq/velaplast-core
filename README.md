# velaplast-core

Biblioteca Python compartilhada com clientes de dados (SAP B1, LiveMES, Spotter), fórmulas financeiras canônicas, AI multi-provider e infraestrutura comum (pool PG, JWT, utils).

## Instalação local (editable)

Nos apps do monorepo:

```bash
pip install -e ../velaplast-core
```

Isso permite que mudanças em `velaplast_core/` reflitam imediatamente em todos os apps sem reinstalação.

## Uso

```python
from velaplast_core.pdv import PDVClient
from velaplast_core.sap import SAPClient
from velaplast_core.finance import build_tax_map, calcular_faturamento_net
from velaplast_core.livemes import LiveMESClient
from velaplast_core.ai import AIEngine
from velaplast_core.db import pool_get
from velaplast_core.auth import login_required, role_required
```

## Status

🚧 **Em migração** — ver `../tasks/todo.md` para etapas.

| Módulo | Status |
|---|---|
| `pdv/` | ✅ pronto — cliente + MCP `pdv-velaplast`, ver [docs/PDV.md](docs/PDV.md) |
| `sap/` | 🔜 |
| `livemes/` | 🔜 |
| `spotter/` | 🔜 |
| `finance/` | 🔜 |
| `ai/` | 🔜 |
| `db.py` | 🔜 |
| `auth.py` | 🔜 |
| `utils.py` | 🔜 |

## Testes

```bash
pip install -e ".[dev]"
pytest
```
