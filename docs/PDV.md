# PDV no velaplast-core — cliente + MCP

O PDV (`pdv.velaplast.com.br`) é o sistema do TI da Velaplast para acompanhamento de
pedidos e amostras. Ele expõe uma API externa **somente leitura** (`/api/ext/v1`).
Este módulo põe essa API dentro do `velaplast-core` e a publica como servidor MCP,
do mesmo jeito que o `sap-velaplast` — dá pra consultar o PDV conversando.

Nada aqui grava no PDV: a API só aceita `GET` e o usuário de banco por trás só tem `SELECT`.

## Como usar no chat

MCP registrado em user scope como **`pdv-velaplast`** (12 tools, prefixo `pdv_`).

| Tool | Para que serve |
|---|---|
| `pdv_ping` | testa conexão e chave |
| `pdv_recursos` | quais tabelas existem, campos e contagem atual |
| `pdv_pedidos` | busca pedidos por fase/tipo/cliente/vendedor/período/sem SAP |
| `pdv_pedido` | ficha completa: cabeçalho, itens, valor somado, histórico de fases, comentários, anexos |
| `pdv_pipeline` | funil: quantos em cada fase, dias parados, lista de travados |
| `pdv_amostras` | amostras por status de envio (`Enviado`, `Aguardando_Separacao`…) |
| `pdv_pendentes_sap` | a lacuna PDV × SAP, já separando o que não é faturável |
| `pdv_parceiros` | clientes/prospects com setor, estágio de venda e vendedor |
| `pdv_produtos` | produtos (bombona/tampa) com atributos resolvidos |
| `pdv_catalogos` | de/para de qualquer campo `_id` |
| `pdv_usuarios` | quem é quem no PDV, por departamento |
| `pdv_query` | consulta genérica (escape hatch) a qualquer recurso |

Perguntas que já funcionam direto:

- "O que está parado no PDV há mais de 5 dias?"
- "Quais amostras da Ihara ainda não foram enviadas?"
- "Me mostra o pedido 992 inteiro."
- "Quantos pedidos o Ademir lançou em julho?"
- "Tem pedido de venda concluído que nunca foi lançado no SAP?"

## Uso como biblioteca

```python
from velaplast_core.pdv import PDVClient

pdv = PDVClient()                       # lê PDV_API_URL / PDV_API_KEY do ambiente
pdv.contar("pedidos", {"fase": "concluido"})            # COUNT(*) sem trafegar linhas
linhas = pdv.listar_tudo("pedidos", filtros={"data_lancamento__gte": "2026-07-01"})
pdv.resolver("pedidos", linhas)         # tipo_pedido_id 193 → tipo_pedido "Amostra"
pdv.contar_por(linhas, "fase")          # GROUP BY feito aqui
```

Configuração (`.env`, nunca commitado — veja `.env.example`):

```
PDV_API_URL=https://pdv.velaplast.com.br/api/ext/v1
PDV_API_KEY=pdvk_...
```

## O que o cliente resolve por cima da API

A API é deliberadamente crua. As três lacunas e como são cobertas:

| Lacuna da API | Onde é resolvida |
|---|---|
| **Não tem JOIN** — campos `_id` vêm crus | `PDVClient.resolver()` acrescenta o nome legível ao lado do id, usando `catalogs`, `parceiros`, `users`, `departamentos` |
| **Não tem agregação** — nada de SUM/GROUP BY | `contar_por()` / `somar()` no cliente, depois de `listar_tudo()` paginar |
| **Teto de 1000 linhas/página** | `listar_tudo()` pagina até o fim (trava de segurança em 50 páginas) |

Além disso: rate limit local de 100 req/min (o teto da API), retry em 5xx, uma
retentativa em 429, cache in-memory de 60s e validação de campo **antes** de chamar
(filtro com campo inexistente vira erro claro, não resultado vazio silencioso).

## Modelo de dados (instância real, conferido em 2026-07-28)

15 recursos. `pedido-itens-livres` e `pedido-postergacoes` estão **vazios** na base real.

`pedidos` é o centro: 737 linhas, sendo 378 `Pedido`, 201 `Amostra`, 80 `Cotação`,
78 `Pedido Urgente`. O `tipo_pedido_id` é que separa venda de amostra — não existe
tabela de amostras separada.

Fases do fluxo, na ordem:

```
aguardando_alinhar_quantidades → aguardando_validacao_datas_pcp →
aguardando_validacao_datas_logistica → aguardando_validacao_vendedor →
aguardando_inserir_sap → inserido_sap → concluido
                          (desvios: aguardando_correcao, cancelado)
```

Status de amostra: `Nao_Iniciado`, `Aguardando_Separacao`, `Aguardando_Enviar`,
`Enviado`, `Cancelado`.

### Armadilhas confirmadas na base

- **22 pedidos têm `fase` nula** (registros antigos). Filtrar por fase os exclui de
  qualquer grupo — as contagens por fase não somam o total.
- **`numero_pedido` ≠ `id`.** O número que o usuário vê é o `numero_pedido`;
  o `id` é interno. `pdv_pedido` aceita os dois.
- **`parceiros.nome_empresa` é nome fantasia**, não razão social. Ex.: "Spraytec Brasil"
  no PDV é "LATINA AGRO INDUSTRIA E COMERCIO DE FERTILIZANTES LTDA" (C00293) no SAP —
  mesma empresa, mesma cidade (Maringá/PR). Não confunda com erro de mapeamento.
- **Amostra e Cotação nunca recebem `num_sap`** — por natureza, não geram documento
  no SAP. Só `Pedido` e `Pedido Urgente` devem ter número.
- Pedir `?limit>1000` devolve **422**, não corta silenciosamente.

## Evidência de auditoria (2026-07-28)

Rodado contra a base real, não mock. `pytest tests/test_pdv_live.py` — 24 testes verdes.

**Reconciliação interna** (contra a própria API, para provar que a camada não mente):
- `meta.total` do universo (737) == soma dos totais por tipo de pedido (378+201+80+78).
- `contar_por(fase)` feito no cliente == `meta.total` que a API devolve fase a fase.
- `listar_tudo("pedidos")` traz 737 ids únicos, sem duplicar nem perder na paginação.
- O nome que o `resolver()` cola em `parceiro` == o que `/parceiros/{id}` devolve.

**Reconciliação externa** (contra o SAP, fonte independente do PDV):
- 8 `num_sap` amostrados existem como pedido de venda (`ORDR`) no SAP: 3624, 3631,
  3634, 3637, 3638, 3639, 3640, 3643 — 8/8, e o cliente bate em todos.
- O valor total que `pdv_pedido` soma bate **ao centavo** com o `DocTotal` do SAP em
  4/4 conferidos: 219.699,36 (#994) · 88.300,80 (#983) · 9.190,94 (#975) · 8.877,50 (#966).
  Critério da soma: `quantidade_real` (ou `pedida`, se real vazia) × `valor_unitario`.

**Sanity de negócio:**
- 193 pedidos concluídos sem `num_sap` são **158 amostras (todas já enviadas) + 35 cotações**.
  Pedidos de venda concluídos sem SAP: **zero** — a integração PDV→SAP não tem buraco.
- 46 pedidos param na fase `inserido_sap` (média 44 dias, máximo 229 — #344 PRENTISS,
  SAP 3255). Foram lançados no SAP mas ninguém fechou a fase no PDV: é higiene de
  processo, não erro de dado. Aparecem em `pdv_pipeline` como travados.

## Manutenção

O mapa de campos vive em `velaplast_core/pdv/resources.py`. Se o TI liberar uma tabela
nova ou mudar campo, `pdv_recursos` acusa a divergência e o teste
`test_todos_os_recursos_respondem` quebra — atualize o mapa lá.

Servidor MCP: `mcp/server.py`. Depende de `mcp==1.27.0` (a 2.0 moveu o `FastMCP` de
lugar e quebra o import), `requests` e `python-dotenv`, todos no `venv/` do core.
