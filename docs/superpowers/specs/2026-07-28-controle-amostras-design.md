# Controle de amostras — PDV, CRM e Gestão

**Data:** 2026-07-28
**Repos afetados:** `velaplast-core` (motor), `velaplast-sap` (CRM), `velaplast-gestao`
**Origem:** amostras se perdem — ninguém vê que estão paradas e ninguém é cobrado.

## Problema, medido

O PDV registra a trilha de estágio da amostra (`pedido-fase-historico` grava
`amostra:<status>`), então a dor é mensurável:

- **22 amostras vivas, somando 1.091 dias parados.** Cinco passaram de 78 dias.
- Quando andam, andam rápido: mediana de 0,3 a 2,7 dias por estágio. **O problema
  não é lentidão, é abandono** — 13 das 22 nunca saíram de `Nao_Iniciado`.
- O CRM mostra estado errado em pelo menos 9 das suas 27 amostras: o vendedor
  registra no CRM, pede no PDV, e o CRM congela. Há amostras marcadas `a_enviar`
  no CRM que o PDV já deu como enviadas.
- O Gestão tem a tabela `amostras` desde 15/07 e **zero linhas** — o módulo existe,
  ninguém alimenta.

Nenhuma das três telas responde "isso está parado há quantos dias?".

## Princípio: cada dado tem um dono só

A fronteira é o momento do envio:

```
Nao_Iniciado → Aguardando_Separacao → Aguardando_Enviar → Enviado │ entregue → feedback
└──────────────── PDV manda, apps leem ────────────────────────┘ └── vendedor manda ──┘
```

O PDV sabe até "saiu daqui". Se chegou e o que o cliente achou, só o vendedor sabe.
Por isso `estágio` e datas de envio são **derivados** (read-only nos apps), enquanto
`entregue`, `resultado`, `data_feedback` e `notas` continuam escrita dos apps.
Nenhum campo tem dois donos — é isso que impede a divergência de voltar.

Divisão entre os apps:

| CRM (velaplast-sap) — o cliente | Gestão — a fábrica |
|---|---|
| feedback do cliente (aprovado/reprovado/ajuste) | responsável por área |
| vínculo com lead/empresa | tarefa de cobrança |
| "minhas amostras" do vendedor | painel de exceção (>5 dias) |

Os dois exibem estágio e dias parados, ambos vindos do PDV.

## 1. Motor compartilhado — `velaplast-core` v0.2.0

Módulo novo `velaplast_core/pdv/amostras.py`. Existe para que "dias parado" signifique
exatamente a mesma coisa nos dois apps — regra única, não copiada.

```python
ESTAGIOS = ["Nao_Iniciado", "Aguardando_Separacao", "Aguardando_Enviar",
            "Enviado", "Cancelado"]
ESTAGIOS_INTERNOS = ESTAGIOS[:3]        # onde a amostra trava
AREA_POR_ESTAGIO = {
    "Nao_Iniciado": "qualidade",
    "Aguardando_Separacao": "qualidade",
    "Aguardando_Enviar": "logistica",
    "Enviado": "comercial",
}
SLA_DIAS = 5
```

Funções:

- `carregar_amostras(client, desde=None) -> list[Amostra]` — amostras do PDV
  (`tipo_pedido = Amostra`) com parceiro, vendedor e itens resolvidos. `desde` filtra
  por `updated_at` para o sync incremental.
- `trilha(historico) -> list[Etapa]` — `(estagio, entrou_em, saiu_em, dias)` por etapa,
  a partir das linhas `amostra:*` de `pedido-fase-historico`. É o histórico por estágio.
- `dias_no_estagio(amostra, agora) -> float` — desde a última transição. **Fallback:**
  143 das 201 amostras são antigas e não têm trilha; nesses casos usa `updated_at`, e
  o registro sai marcado `trilha_parcial=True` para a tela não afirmar precisão que
  não tem.
- `area_responsavel(estagio)` e `esta_travada(amostra, sla)`.

`Amostra` é um dataclass simples: identidade no PDV (`pedido_id`, `numero_pedido`),
cliente (`parceiro_id`, `nome`, `cnpj`), vendedor (`usuario_id`, `nome`, `email`),
estágio atual, `estagio_desde`, `dias_no_estagio`, `itens` e `trilha`.

O módulo não conhece Postgres nem Flask — só o `PDVClient`. Testável isoladamente.

## 2. CRM (velaplast-sap)

### Schema

`crm_amostras` ganha:

| Coluna | Para quê |
|---|---|
| `pdv_pedido_id INTEGER UNIQUE` | identidade no PDV; o `UNIQUE` é o que torna o sync idempotente |
| `pdv_numero_pedido INTEGER` | o número que o usuário vê |
| `origem TEXT DEFAULT 'crm'` | `crm` (nativa, prospecção) ou `pdv` (espelho) |
| `pdv_estagio TEXT` | estágio cru do PDV |
| `pdv_estagio_desde TIMESTAMPTZ` | quando entrou no estágio |
| `pdv_trilha_parcial BOOLEAN` | true quando o estágio veio de `updated_at`, sem trilha |
| `pdv_match TEXT` | `auto`, `confirmado` ou `sugerido` (casamento com nativa) |
| `pdv_sincronizado_em TIMESTAMPTZ` | última sincronização |

### Mapeamento de estágio

Alimenta as colunas que já existem, para não quebrar dashboard nem tela:

| Estágio no PDV | `status_envio` |
|---|---|
| Nao_Iniciado, Aguardando_Separacao, Aguardando_Enviar | `a_enviar` |
| Enviado | `enviada` |
| Cancelado | `cancelada` *(valor novo no domínio)* |
| — | `entregue` só por marcação do vendedor |

`data_envio` recebe a data da transição para `Enviado`. `data_entrega`,
`data_feedback` e `resultado` seguem manuais.

### Regras

- Amostra com `origem='pdv'`: estágio, `status_envio` e `data_envio` são read-only na
  API (PUT que tente alterá-los devolve 400 com "estágio é do PDV"). Feedback continua
  aberto.
- Amostra com `origem='crm'`: comportamento atual, intocado.
- O dashboard existente ganha `travadas` (parada >5 dias em estágio interno).

### Telas

- `AmostrasSection` (card da empresa) e a lista de amostras passam a mostrar o estágio
  real, **"parada há N dias"** e um selo de origem. Para amostra do PDV, o seletor de
  estágio fica travado com link "ver no PDV"; o bloco de feedback fica aberto.
- Bloco "confirme se é a mesma amostra" quando `pdv_match='sugerido'`, com dois botões:
  é a mesma (funde) / são diferentes (mantém as duas).

## 3. Gestão

### Schema

`amostras` ganha as mesmas colunas de espelho (`pdv_*`, `origem`), mais:

```sql
CREATE TABLE amostras_areas (
    area TEXT PRIMARY KEY,              -- qualidade | logistica | comercial
    responsavel_id INTEGER REFERENCES users(id),
    sla_dias INTEGER NOT NULL DEFAULT 5
);
```

Semeada com o que o histórico do PDV mostra: `qualidade` → Tiago Custódio,
`logistica` → responsável da Logística, `comercial` → vendedor da própria amostra
(responsavel_id nulo significa "usa o vendedor da amostra").

O `CHECK` de `status_envio` passa a aceitar `cancelada`.

### Painel de exceção

A página Amostras (hoje vazia) vira o painel: agrupado por área, ordenado por dias
parados, com o SLA marcado. Cabeçalho com o passivo total ("22 amostras paradas,
1.091 dias acumulados"), porque é o número que faz alguém agir.

### Cobrança

Reaproveita o motor de tarefas que já existe (`_sincronizar_cobranca`), com um
destinatário diferente: a tarefa vai para o **responsável da área**, não para o
vendedor — quem trava é a fábrica.

- Amostra em estágio interno há mais de `sla_dias` → tarefa para o responsável da
  área, dedup por `origem_tipo='amostra'` + `origem_id`.
- Amostra que sai do estágio interno → tarefa concluída automaticamente.
- **Carga inicial não abre tarefa retroativa individual.** Registra o passivo no
  painel e abre **uma tarefa de mutirão por área** ("destravar N amostras paradas há
  mais de 5 dias — ver painel"). A partir do segundo sync, cada amostra que cruzar o
  SLA abre a sua. Sem isso, a estreia despeja ~11 tarefas no colo de uma pessoa e o
  recurso nasce sendo ignorado.
- A regra de cobrança de feedback que já existe (entregue sem retorno) continua
  valendo para o vendedor, inalterada.

## 4. Sincronização

Uma vez por dia, de madrugada, em cada app — cada um no seu banco, ambos lendo a
mesma fonte, então o espelho duplicado não gera divergência.

- **Como roda:** no CRM, no loop de background que já existe (`app.py`), com marcador
  de data no Postgres — mesmo padrão do `ltm_warm_date`, que garante uma execução por
  cluster e não por worker. No Gestão, o mesmo padrão, num loop leve criado para isso.
- **Botão "Sincronizar agora"** (admin) nos dois apps: sem ele, quem lança uma amostra
  às 9h não a vê no app o dia inteiro.
- **Volume:** 201 amostras, ~6 requisições por ciclo completo. O teto da API é 100/min.
- **Falha do PDV não derruba nada:** o sync registra o erro, mantém o espelho anterior
  e a tela mostra "última sincronização em <data>". O dado velho é sinalizado, nunca
  apagado.

## 5. Carga inicial

1. **Casamento automático** — amostra nativa do CRM e amostra do PDV do mesmo cliente
   (CNPJ, ou raiz para filial), criadas dentro de uma janela de 7 dias: funde,
   `pdv_match='auto'`, o PDV assume o estágio. Cobre os casos SOLOHUMICS (3), AMBIOS
   (2) e BRQ (4).
2. **Ambíguo** — mesmo cliente, fora da janela: `pdv_match='sugerido'`, vendedor
   confirma na tela.
3. **Sem par** — continua nativa, intocada (prospecção pura).
4. **Cliente sem cadastro no CRM** — 9 parceiros, 39 amostras. Entram numa fila de
   aprovação (`pdv_empresas_pendentes`: nome, cnpj, cidade, uf, amostras_vinculadas).
   **A amostra aparece do mesmo jeito**, com o nome vindo do PDV; a aprovação cria a
   empresa e liga o histórico, mas não segura a amostra. Uma amostra parada há 144
   dias não pode ficar invisível esperando aprovação de cadastro.

### Cobertura esperada

44 dos 53 clientes com amostra casam com `crm_empresas` (40 por CNPJ cheio, 4 por
raiz) → 162 das 201 amostras vinculadas na estreia. 17 dos 26 usuários do PDV casam
com `users` por e-mail.

### Pendência de cadastro

O usuário que mais envia amostra (23 das 34 transições para `Enviado`) não tem conta
no CRM. Sem conta, não recebe tarefa nem notificação. Ou se cria a conta, ou a
Logística aponta outro responsável em `amostras_areas` — a tela permite os dois.

## 6. Testes

Contra dados reais, não mock (o PDV é read-only: ler em teste é seguro).

**Motor (core):**
- `trilha()` reconstrói as 4 etapas de uma amostra com histórico completo.
- `dias_no_estagio()` bate com o cálculo manual sobre `pedido-fase-historico`.
- Amostra sem trilha cai no fallback e sai marcada `trilha_parcial`.
- `esta_travada()` responde ao SLA configurado, não a um número fixo.

**CRM:**
- Sync é idempotente: rodar duas vezes não duplica (garantido pelo `UNIQUE`).
- PUT que tenta mudar estágio de amostra `origem='pdv'` devolve 400.
- PUT de feedback em amostra do PDV funciona.
- Amostra nativa continua editável como hoje (regressão).
- Casamento automático funde SOLOHUMICS/AMBIOS/BRQ e não funde o resto.

**Gestão:**
- Painel lista só o que passou do SLA, agrupado pela área certa de cada estágio.
- Tarefa de cobrança vai para o responsável da área, não para o vendedor.
- Carga inicial abre uma tarefa por área, não uma por amostra.
- Amostra que sai do estágio interno fecha a tarefa.

**Reconciliação (definição de pronto):** o total de amostras por estágio nos dois apps
tem que bater com a contagem direta na API do PDV, estágio a estágio. Sem isso, é
"gerado, não auditado".

## 7. Riscos

| Risco | Tratamento |
|---|---|
| PDV fora do ar na madrugada | espelho anterior é mantido; tela mostra a data da última sincronização; botão manual |
| Casamento automático errado esconde uma amostra | janela estreita (mesmo cliente + 7 dias); o ambíguo vai para confirmação humana; a fusão é reversível |
| Responsável de área sem conta | tela de áreas permite trocar; enquanto nulo, a tarefa vai para o líder |
| Enxurrada de tarefas na estreia | mutirão por área, individual só a partir do segundo sync |
| Estágio novo aparece no PDV | estágio desconhecido não quebra: cai como "outro", sem área, e aparece no painel para investigação |

## Fora de escopo

- Escrever no PDV (a API é read-only por definição).
- Mexer no fluxo de amostra de lead do CRM, que funciona.
- Notificação por e-mail/WhatsApp: a tarefa e o painel já notificam pelos canais que
  cada app tem hoje.
