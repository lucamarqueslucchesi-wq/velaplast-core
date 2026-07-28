"""Mapa dos recursos da API externa do PDV.

Descoberto contra a instância REAL (https://pdv.velaplast.com.br/api/ext/v1)
em 2026-07-28 — não é o "padrão do Laravel", é o que a API devolve de fato.

`RECURSOS` lista os 15 recursos expostos com seus campos. `CATALOG_FK` mapeia
os campos `_id` para a origem que os resolve (a API não faz JOIN). `FASES` e
`STATUS_AMOSTRA` são os domínios observados nos dados.
"""

from __future__ import annotations

# ─── Recursos e campos (instância real, 2026-07-28) ───────────────────────

RECURSOS: dict[str, dict] = {
    "pedidos": {
        "descricao": "Cabeçalho do pedido/amostra/cotação. Entidade central do PDV.",
        "campos": [
            "id", "parceiro_id", "numero_pedido", "pedido_cliente", "uso_produto_id",
            "nota_triangular", "data_lancamento", "tipo_frete_id", "forma_pagamento_id",
            "condicoes_pagamento_id", "usuario_id", "ultima_atualizacao", "tipo_pedido_id",
            "comentarios", "anexo", "endereco_entrega_id", "fase", "fase_iniciada_em",
            "motivo_rejeicao", "aprovado_cliente", "num_sap", "created_at", "updated_at",
            "status_amostra", "com_rcp", "pedido_origem_id",
        ],
    },
    "pedido-itens": {
        "descricao": "Itens de pedido ligados a um produto já cadastrado (produto_id).",
        "campos": [
            "id", "pedido_id", "produto_id", "linha_pedido_cliente", "quantidade_pedida",
            "valor_unitario", "data_entrega_desejada", "quantidade_real", "data_entrega_real",
            "created_at", "updated_at",
        ],
    },
    "pedido-itens-personalizados": {
        "descricao": "Itens de pedido descritos por atributos (sem produto cadastrado).",
        "campos": [
            "id", "pedido_id", "tipo", "sku", "rcp", "produto_vendido_como",
            "linha_pedido_cliente", "quantidade_pedida", "valor_unitario",
            "data_entrega_desejada", "quantidade_real", "data_entrega_real",
            "created_at", "updated_at", "descricao_id", "peso_id", "cor_id",
            "especificacao_id", "modelo_gargalo_id", "postico_fundo_id",
            "volume_embalagem_id", "diametro_gargalo_id", "postico_rodape_id",
            "material_id", "embalagem_certificada_id", "postico_advertencia_id",
            "grupo_terrestre_id", "postico_ombro_id", "grupo_maritimo_id",
            "certificado_maritimo_id", "certificado_terrestre_id",
        ],
    },
    "pedido-itens-livres": {
        "descricao": "Itens livres. VAZIO na instância real (0 linhas em 2026-07-28).",
        "campos": [],
    },
    "pedido-fase-historico": {
        "descricao": "Trilha de transições de fase do pedido (quem mudou, quando, por quê).",
        "campos": ["id", "pedido_id", "fase_anterior", "fase_nova", "user_id", "motivo", "created_at"],
    },
    "pedido-postergacoes": {
        "descricao": "Postergações de entrega. VAZIO na instância real (0 linhas em 2026-07-28).",
        "campos": [],
    },
    "pedido-comentarios": {
        "descricao": "Comentários lançados no pedido.",
        "campos": ["id", "pedido_id", "user_id", "comentario", "created_at", "updated_at"],
    },
    "parceiros": {
        "descricao": "Clientes/prospects. `codigo` é o código de negócio; `usuario_id` é o vendedor dono.",
        "campos": [
            "id", "codigo", "nome_empresa", "nome_contato", "setor_id", "tamanho_id", "cep",
            "endereco", "numero", "municipio", "bairro", "uf", "latitude", "longitude", "cnpj",
            "telefone", "sub_setor_id", "estagio_venda_id", "usuario_id", "email_contato",
            "concorrencia", "padrao_compra_id", "isencao_fiscal", "data_designacao", "website",
            "ultima_alteracao", "dias_entrega", "comentarios", "anexo", "created_at", "updated_at",
        ],
    },
    "parceiro-comentarios": {
        "descricao": "Comentários lançados no cadastro do parceiro.",
        "campos": ["id", "parceiro_id", "user_id", "comentario", "created_at", "updated_at"],
    },
    "produtos": {
        "descricao": "Produtos por parceiro (bombona/tampa), descritos por atributos de catálogo.",
        "campos": [
            "id", "parceiro_id", "is_generico", "tipo", "descricao_id", "peso_id", "cor_id",
            "especificacao_id", "sku", "rcp", "modelo_gargalo_id", "postico_fundo_id",
            "volume_embalagem_id", "diametro_gargalo_id", "postico_rodape_id", "material_id",
            "produto_vendido_como", "embalagem_certificada_id", "postico_advertencia_id",
            "grupo_terrestre_id", "postico_ombro_id", "grupo_maritimo_id",
            "certificado_maritimo_id", "certificado_terrestre_id", "created_at", "updated_at",
            "usuario_id",
        ],
    },
    "enderecos": {
        "descricao": "Endereços de entrega vinculados ao parceiro.",
        "campos": [
            "id", "parceiro_id", "cep", "endereco", "numero", "bairro", "uf", "cidade",
            "dias_entrega", "latitude", "longitude", "complemento", "created_at", "updated_at",
        ],
    },
    "catalogs": {
        "descricao": "Tabela única de domínios (de/para de TODO campo `_id`). Chaves: type, scope, entity, name.",
        "campos": ["id", "type", "scope", "entity", "name", "created_at", "updated_at"],
    },
    "departamentos": {
        "descricao": "Departamentos dos usuários do PDV.",
        "campos": ["id", "nome", "created_at", "updated_at"],
    },
    "anexos": {
        "descricao": "Anexos polimórficos (anexable_type + anexable_id apontam pra Pedido/Parceiro/...).",
        "campos": [
            "id", "anexable_id", "anexable_type", "nome", "path", "mime", "size",
            "fase_anexada", "created_at", "updated_at",
        ],
    },
    "users": {
        "descricao": "Usuários do PDV (vendedores, PCP, logística...). Senha/token nunca são expostos.",
        "campos": ["id", "codigo_acesso", "name", "email", "departamento_id", "created_at", "updated_at"],
    },
}

# ─── De/para dos campos `_id` (a API não faz JOIN) ────────────────────────
# valor "catalogs" = resolve pelo id em /catalogs; outro valor = recurso destino.

CATALOG_FK: dict[str, dict[str, str]] = {
    "pedidos": {
        "parceiro_id": "parceiros",
        "usuario_id": "users",
        "endereco_entrega_id": "enderecos",
        "pedido_origem_id": "pedidos",
        "tipo_pedido_id": "catalogs",
        "uso_produto_id": "catalogs",
        "tipo_frete_id": "catalogs",
        "forma_pagamento_id": "catalogs",
        "condicoes_pagamento_id": "catalogs",
    },
    "pedido-itens": {
        "pedido_id": "pedidos",
        "produto_id": "produtos",
    },
    "pedido-itens-personalizados": {
        "pedido_id": "pedidos",
        "descricao_id": "catalogs", "peso_id": "catalogs", "cor_id": "catalogs",
        "especificacao_id": "catalogs", "modelo_gargalo_id": "catalogs",
        "postico_fundo_id": "catalogs", "volume_embalagem_id": "catalogs",
        "diametro_gargalo_id": "catalogs", "postico_rodape_id": "catalogs",
        "material_id": "catalogs", "embalagem_certificada_id": "catalogs",
        "postico_advertencia_id": "catalogs", "grupo_terrestre_id": "catalogs",
        "postico_ombro_id": "catalogs", "grupo_maritimo_id": "catalogs",
        "certificado_maritimo_id": "catalogs", "certificado_terrestre_id": "catalogs",
    },
    "pedido-fase-historico": {"pedido_id": "pedidos", "user_id": "users"},
    "pedido-comentarios": {"pedido_id": "pedidos", "user_id": "users"},
    "parceiro-comentarios": {"parceiro_id": "parceiros", "user_id": "users"},
    "parceiros": {
        "usuario_id": "users",
        "setor_id": "catalogs", "sub_setor_id": "catalogs", "tamanho_id": "catalogs",
        "estagio_venda_id": "catalogs", "padrao_compra_id": "catalogs",
    },
    "produtos": {
        "parceiro_id": "parceiros", "usuario_id": "users",
        "descricao_id": "catalogs", "peso_id": "catalogs", "cor_id": "catalogs",
        "especificacao_id": "catalogs", "modelo_gargalo_id": "catalogs",
        "postico_fundo_id": "catalogs", "volume_embalagem_id": "catalogs",
        "diametro_gargalo_id": "catalogs", "postico_rodape_id": "catalogs",
        "material_id": "catalogs", "embalagem_certificada_id": "catalogs",
        "postico_advertencia_id": "catalogs", "grupo_terrestre_id": "catalogs",
        "postico_ombro_id": "catalogs", "grupo_maritimo_id": "catalogs",
        "certificado_maritimo_id": "catalogs", "certificado_terrestre_id": "catalogs",
    },
    "enderecos": {"parceiro_id": "parceiros"},
    "users": {"departamento_id": "departamentos"},
}

# Campo que serve de "nome" ao resolver cada recurso destino.
LABEL_FIELD: dict[str, str] = {
    "parceiros": "nome_empresa",
    "users": "name",
    "departamentos": "nome",
    "catalogs": "name",
    "produtos": "rcp",
    "enderecos": "endereco",
    "pedidos": "numero_pedido",
}

# ─── Domínios observados ──────────────────────────────────────────────────

FASES: list[str] = [
    "aguardando_alinhar_quantidades",
    "aguardando_validacao_datas_pcp",
    "aguardando_validacao_datas_logistica",
    "aguardando_validacao_vendedor",
    "aguardando_inserir_sap",
    "inserido_sap",
    "aguardando_correcao",
    "concluido",
    "cancelado",
]

# Fases em que o pedido ainda está em curso (nem concluído, nem cancelado).
FASES_EM_ANDAMENTO: list[str] = [f for f in FASES if f not in ("concluido", "cancelado")]

STATUS_AMOSTRA: list[str] = [
    "Nao_Iniciado",
    "Aguardando_Separacao",
    "Aguardando_Enviar",
    "Enviado",
    "Cancelado",
]

# Operadores aceitos no querystring (campo__operador=valor).
OPERADORES: list[str] = ["eq", "neq", "gt", "gte", "lt", "lte", "like", "in", "nin", "null"]

LIMIT_MAXIMO = 1000
LIMIT_PADRAO = 100
#: Teto do servidor é 100/min por chave. Ficamos abaixo de propósito: a chave é
#: compartilhada entre os workers do app e o MCP, e encostar no teto vira 429.
RATE_LIMIT_POR_MINUTO = 80
#: Requisições que podem sair "de uma vez". Mantém a média acima, sem rajada.
RAJADA_MAXIMA = 8
