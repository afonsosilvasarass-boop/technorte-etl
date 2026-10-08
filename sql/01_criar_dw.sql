-- ============================================================
-- TechNorte, Lda. — Repositório analítico (data warehouse)
-- E3 · Script de criação · MySQL 8+ / MariaDB 10.6+
-- Modelo dimensional em estrela (ver E1): 5 factos, 9 dimensões,
-- 1 tabela-ponte e 4 tabelas de metadados do ETL.
--
-- O carregamento é feito pelo ETL em Python (executar_etl.py), que executa
-- este script no início de cada execução (carga completa).
-- Para obter o script de criação + carregamento num só ficheiro:
--     python executar_etl.py --exportar-sql
--
-- A base de dados de origem (technorte_erp) nunca é alterada.
-- ============================================================
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS technorte_dw CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE technorte_dw;

-- Carga completa: os factos e as dimensões são recriados em cada execução.
-- As tabelas etl_execucao e etl_log_fonte guardam o histórico e não são apagadas.
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS fact_vendas, fact_devolucoes, fact_metas, fact_trafego_web, fact_satisfacao,
                     ponte_campanha_loja,
                     dim_data, dim_loja, dim_canal, dim_produto, dim_cliente, dim_vendedor,
                     dim_campanha, dim_motivo_devolucao, dim_fonte_web,
                     dq_regra, dq_registo;
SET FOREIGN_KEY_CHECKS = 1;

-- ------------------------------------------------------------
-- DIMENSÕES
-- ------------------------------------------------------------

-- Calendário 2024-01-01 a 2026-12-31 (inclui devoluções de julho/2026 e as metas de 2026)
CREATE TABLE dim_data (
  sk_data          INT          NOT NULL PRIMARY KEY COMMENT 'AAAAMMDD',
  data             DATE         NOT NULL UNIQUE,
  ano              SMALLINT     NOT NULL,
  semestre         TINYINT      NOT NULL,
  trimestre        TINYINT      NOT NULL,
  mes              TINYINT      NOT NULL,
  nome_mes         VARCHAR(10)  NOT NULL,
  mes_abrev        CHAR(3)      NOT NULL,
  ano_mes          CHAR(7)      NOT NULL COMMENT 'AAAA-MM',
  inicio_mes       DATE         NOT NULL,
  dia              TINYINT      NOT NULL,
  dia_semana       TINYINT      NOT NULL COMMENT '1 = segunda ... 7 = domingo',
  nome_dia         VARCHAR(15)  NOT NULL,
  semana_iso       TINYINT      NOT NULL,
  fim_de_semana    TINYINT(1)   NOT NULL,
  feriado          TINYINT(1)   NOT NULL,
  nome_feriado     VARCHAR(40)  NULL,
  tipo_feriado     VARCHAR(12)  NULL COMMENT 'Nacional | Facultativo',
  dia_util         TINYINT(1)   NOT NULL COMMENT 'Seg-Sex e não feriado nacional'
) ENGINE=InnoDB COMMENT='Dimensão calendário (fonte: gerada + feriados.csv)';

CREATE TABLE dim_loja (
  sk_loja          INT          NOT NULL PRIMARY KEY,
  loja_id          INT          NOT NULL UNIQUE COMMENT 'lojas.id do ERP',
  nome             VARCHAR(60)  NOT NULL,
  nome_curto       VARCHAR(30)  NOT NULL COMMENT 'Nome usado nas metas e no inquérito',
  tipo_loja        VARCHAR(10)  NOT NULL COMMENT 'Física | Online',
  cidade           VARCHAR(40)  NOT NULL,
  distrito         VARCHAR(40)  NOT NULL,
  regiao           VARCHAR(20)  NOT NULL,
  data_abertura    DATE         NOT NULL,
  area_m2          INT          NULL
) ENGINE=InnoDB COMMENT='Lojas físicas e loja online (SCD tipo 1)';

CREATE TABLE dim_canal (
  sk_canal         TINYINT      NOT NULL PRIMARY KEY,
  canal            VARCHAR(10)  NOT NULL UNIQUE
) ENGINE=InnoDB COMMENT='Canal de venda: Loja | Online';

-- SCD tipo 2: uma linha por versão do custo de aquisição (custos_produtos.xlsx)
CREATE TABLE dim_produto (
  sk_produto          INT           NOT NULL PRIMARY KEY,
  produto_id          INT           NOT NULL COMMENT 'produtos.id do ERP (-1 = desconhecido)',
  codigo              VARCHAR(10)   NOT NULL,
  nome                VARCHAR(80)   NOT NULL,
  marca               VARCHAR(30)   NOT NULL,
  categoria_id        INT           NOT NULL,
  categoria           VARCHAR(40)   NOT NULL,
  preco_venda         DECIMAL(10,2) NULL     COMMENT 'Preço de tabela atual, sem IVA (SCD1)',
  ativo               TINYINT(1)    NOT NULL,
  custo_unitario      DECIMAL(10,2) NULL     COMMENT 'Custo de aquisição em vigor na versão',
  fornecedor          VARCHAR(40)   NULL,
  custo_estimado      TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '1 = sem custo registado; estimado pela margem média da categoria',
  categoria_corrigida TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '1 = categoria em falta no ERP, atribuída pelo ETL',
  valido_de           DATE          NOT NULL,
  valido_ate          DATE          NOT NULL COMMENT '9999-12-31 = versão atual',
  versao              TINYINT       NOT NULL,
  versao_atual        TINYINT(1)    NOT NULL,
  UNIQUE KEY uq_produto_versao (produto_id, valido_de),
  KEY ix_produto (produto_id)
) ENGINE=InnoDB COMMENT='Produtos com histórico de custo (SCD tipo 2)';

-- Sem dados pessoais diretos (nome, e-mail, telefone, NIF): não são necessários à análise (RGPD, minimização)
CREATE TABLE dim_cliente (
  sk_cliente          INT           NOT NULL PRIMARY KEY,
  cliente_id          INT           NOT NULL UNIQUE COMMENT 'clientes.id do registo sobrevivente',
  ids_origem          VARCHAR(40)   NOT NULL COMMENT 'Todos os clientes.id fundidos neste cliente',
  tipo                VARCHAR(12)   NOT NULL COMMENT 'Particular | Empresa',
  genero              CHAR(1)       NULL,
  cidade              VARCHAR(40)   NULL,
  distrito            VARCHAR(40)   NULL,
  regiao              VARCHAR(20)   NULL,
  distrito_com_loja   TINYINT(1)    NOT NULL COMMENT '1 = existe loja física no distrito do cliente',
  data_registo        DATE          NULL,
  ano_nascimento      SMALLINT      NULL,
  faixa_etaria        VARCHAR(10)   NOT NULL,
  data_primeira_compra DATE         NULL COMMENT 'Primeira encomenda não cancelada no ERP',
  registo_apos_compra TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '1 = data de registo posterior à primeira compra',
  duplicado_fundido   TINYINT(1)    NOT NULL DEFAULT 0
) ENGINE=InnoDB COMMENT='Clientes deduplicados (SCD tipo 1)';

CREATE TABLE dim_vendedor (
  sk_vendedor         INT           NOT NULL PRIMARY KEY,
  vendedor_id         INT           NOT NULL UNIQUE COMMENT 'vendedores.id do ERP (0 = sem vendedor)',
  nome                VARCHAR(80)   NOT NULL,
  loja                VARCHAR(30)   NOT NULL,
  data_admissao       DATE          NULL,
  ativo               TINYINT(1)    NOT NULL,
  admissao_inconsistente TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = vendas anteriores à data de admissão'
) ENGINE=InnoDB COMMENT='Vendedores das lojas físicas (SCD tipo 1)';

CREATE TABLE dim_campanha (
  sk_campanha         INT           NOT NULL PRIMARY KEY,
  campanha_id         INT           NOT NULL UNIQUE COMMENT 'ID em campanhas_marketing.xlsx (0 = sem campanha)',
  nome                VARCHAR(60)   NOT NULL,
  canal               VARCHAR(15)   NOT NULL COMMENT 'Loja | Online | Loja + Online',
  data_inicio         DATE          NULL,
  data_fim            DATE          NULL,
  duracao_dias        SMALLINT      NULL,
  orcamento           DECIMAL(12,2) NULL,
  lojas_abrangidas    VARCHAR(80)   NULL COMMENT 'Texto original da fonte'
) ENGINE=InnoDB COMMENT='Campanhas de marketing';

CREATE TABLE ponte_campanha_loja (
  sk_campanha         INT           NOT NULL,
  sk_loja             INT           NOT NULL,
  PRIMARY KEY (sk_campanha, sk_loja),
  CONSTRAINT fk_pcl_campanha FOREIGN KEY (sk_campanha) REFERENCES dim_campanha(sk_campanha),
  CONSTRAINT fk_pcl_loja     FOREIGN KEY (sk_loja)     REFERENCES dim_loja(sk_loja)
) ENGINE=InnoDB COMMENT='Lojas abrangidas por cada campanha (derivado do texto livre)';

CREATE TABLE dim_motivo_devolucao (
  sk_motivo           TINYINT       NOT NULL PRIMARY KEY,
  motivo              VARCHAR(40)   NOT NULL UNIQUE,
  responsabilidade    VARCHAR(20)   NOT NULL COMMENT 'Empresa | Cliente'
) ENGINE=InnoDB;

CREATE TABLE dim_fonte_web (
  sk_fonte            TINYINT       NOT NULL PRIMARY KEY,
  fonte               VARCHAR(20)   NOT NULL UNIQUE,
  tipo_fonte          VARCHAR(12)   NOT NULL COMMENT 'Paga | Não paga'
) ENGINE=InnoDB;

-- ------------------------------------------------------------
-- FACTOS
-- ------------------------------------------------------------

-- Grão: uma linha de encomenda
CREATE TABLE fact_vendas (
  linha_id            INT           NOT NULL PRIMARY KEY COMMENT 'linhas_encomenda.id',
  encomenda_id        INT           NOT NULL COMMENT 'Dimensão degenerada',
  sk_data             INT           NOT NULL,
  hora                TINYINT       NOT NULL,
  sk_loja             INT           NOT NULL,
  sk_canal            TINYINT       NOT NULL,
  sk_cliente          INT           NOT NULL,
  sk_vendedor         INT           NOT NULL,
  sk_produto          INT           NOT NULL,
  sk_campanha         INT           NOT NULL,
  estado              VARCHAR(12)   NOT NULL COMMENT 'Concluída | Cancelada | Devolvida',
  encomenda_valida    TINYINT(1)    NOT NULL COMMENT '1 = não cancelada',
  metodo_pagamento    VARCHAR(15)   NULL,
  quantidade          INT           NOT NULL,
  preco_unitario      DECIMAL(10,2) NOT NULL COMMENT 'Preço praticado, já com desconto',
  desconto_pct        DECIMAL(5,2)  NOT NULL,
  preco_tabela        DECIMAL(10,2) NULL,
  valor_venda         DECIMAL(12,2) NOT NULL COMMENT 'quantidade x preco_unitario',
  valor_desconto      DECIMAL(12,2) NOT NULL COMMENT 'quantidade x (preco_tabela - preco_unitario)',
  custo_unitario      DECIMAL(10,2) NOT NULL COMMENT 'Custo em vigor na data da encomenda',
  cmv                 DECIMAL(12,2) NOT NULL COMMENT 'quantidade x custo_unitario',
  primeira_encomenda  TINYINT(1)    NOT NULL COMMENT '1 = primeira encomenda válida do cliente',
  dq_flag             VARCHAR(40)   NULL COMMENT 'Regras de qualidade aplicadas a esta linha',
  KEY ix_fv_encomenda (encomenda_id),
  CONSTRAINT fk_fv_data      FOREIGN KEY (sk_data)     REFERENCES dim_data(sk_data),
  CONSTRAINT fk_fv_loja      FOREIGN KEY (sk_loja)     REFERENCES dim_loja(sk_loja),
  CONSTRAINT fk_fv_canal     FOREIGN KEY (sk_canal)    REFERENCES dim_canal(sk_canal),
  CONSTRAINT fk_fv_cliente   FOREIGN KEY (sk_cliente)  REFERENCES dim_cliente(sk_cliente),
  CONSTRAINT fk_fv_vendedor  FOREIGN KEY (sk_vendedor) REFERENCES dim_vendedor(sk_vendedor),
  CONSTRAINT fk_fv_produto   FOREIGN KEY (sk_produto)  REFERENCES dim_produto(sk_produto),
  CONSTRAINT fk_fv_campanha  FOREIGN KEY (sk_campanha) REFERENCES dim_campanha(sk_campanha)
) ENGINE=InnoDB COMMENT='Vendas ao nível da linha de encomenda';

-- Grão: uma linha de devolução (produto devolvido de uma encomenda)
CREATE TABLE fact_devolucoes (
  devolucao_id        INT           NOT NULL PRIMARY KEY COMMENT 'devolucoes.id',
  encomenda_id        INT           NOT NULL,
  linha_id            INT           NOT NULL COMMENT 'Linha de encomenda devolvida',
  sk_data             INT           NOT NULL COMMENT 'Data da devolução',
  sk_data_encomenda   INT           NOT NULL,
  sk_loja             INT           NOT NULL,
  sk_canal            TINYINT       NOT NULL,
  sk_cliente          INT           NOT NULL,
  sk_produto          INT           NOT NULL,
  sk_motivo           TINYINT       NOT NULL,
  quantidade          INT           NOT NULL,
  valor_devolvido     DECIMAL(12,2) NOT NULL COMMENT 'quantidade x preco_unitario da linha',
  cmv_devolvido       DECIMAL(12,2) NOT NULL COMMENT 'quantidade x custo_unitario da linha',
  dias_ate_devolucao  SMALLINT      NOT NULL,
  CONSTRAINT fk_fd_data      FOREIGN KEY (sk_data)           REFERENCES dim_data(sk_data),
  CONSTRAINT fk_fd_data_enc  FOREIGN KEY (sk_data_encomenda) REFERENCES dim_data(sk_data),
  CONSTRAINT fk_fd_loja      FOREIGN KEY (sk_loja)           REFERENCES dim_loja(sk_loja),
  CONSTRAINT fk_fd_canal     FOREIGN KEY (sk_canal)          REFERENCES dim_canal(sk_canal),
  CONSTRAINT fk_fd_cliente   FOREIGN KEY (sk_cliente)        REFERENCES dim_cliente(sk_cliente),
  CONSTRAINT fk_fd_produto   FOREIGN KEY (sk_produto)        REFERENCES dim_produto(sk_produto),
  CONSTRAINT fk_fd_motivo    FOREIGN KEY (sk_motivo)         REFERENCES dim_motivo_devolucao(sk_motivo),
  CONSTRAINT fk_fd_linha     FOREIGN KEY (linha_id)          REFERENCES fact_vendas(linha_id)
) ENGINE=InnoDB COMMENT='Devoluções';

-- Grão: loja x mês
CREATE TABLE fact_metas (
  sk_loja             INT           NOT NULL,
  sk_data             INT           NOT NULL COMMENT 'Primeiro dia do mês',
  meta_valor          DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (sk_loja, sk_data),
  CONSTRAINT fk_fm_loja FOREIGN KEY (sk_loja) REFERENCES dim_loja(sk_loja),
  CONSTRAINT fk_fm_data FOREIGN KEY (sk_data) REFERENCES dim_data(sk_data)
) ENGINE=InnoDB COMMENT='Metas mensais de vendas por loja (2025 e 2026)';

-- Grão: dia x fonte de aquisição
CREATE TABLE fact_trafego_web (
  sk_data             INT           NOT NULL,
  sk_fonte            TINYINT       NOT NULL,
  sessoes             INT           NOT NULL,
  utilizadores        INT           NOT NULL,
  taxa_conversao_pct  DECIMAL(5,2)  NOT NULL,
  conversoes          DECIMAL(10,2) NOT NULL COMMENT 'sessoes x taxa_conversao_pct / 100',
  receita_estimada    DECIMAL(12,2) NOT NULL COMMENT 'Estimada pela plataforma web (não é a receita do ERP)',
  PRIMARY KEY (sk_data, sk_fonte),
  CONSTRAINT fk_ft_data  FOREIGN KEY (sk_data)  REFERENCES dim_data(sk_data),
  CONSTRAINT fk_ft_fonte FOREIGN KEY (sk_fonte) REFERENCES dim_fonte_web(sk_fonte)
) ENGINE=InnoDB COMMENT='Tráfego diário do site';

-- Grão: uma resposta ao inquérito
CREATE TABLE fact_satisfacao (
  resposta_id         INT           NOT NULL PRIMARY KEY COMMENT 'N.º da linha na fonte',
  encomenda_id        INT           NOT NULL,
  sk_data             INT           NOT NULL COMMENT 'Data da resposta',
  sk_data_encomenda   INT           NOT NULL,
  sk_loja             INT           NOT NULL COMMENT 'Loja da encomenda no ERP',
  sk_canal            TINYINT       NOT NULL,
  sk_cliente          INT           NOT NULL,
  nota                TINYINT       NOT NULL COMMENT '0-10',
  classe_nps          VARCHAR(10)   NOT NULL COMMENT 'Promotor (9-10) | Passivo (7-8) | Detrator (0-6)',
  promotor            TINYINT(1)    NOT NULL,
  detrator            TINYINT(1)    NOT NULL,
  recomendaria        TINYINT(1)    NULL,
  comentario          VARCHAR(200)  NULL,
  loja_texto_fonte    VARCHAR(40)   NULL COMMENT 'Loja como escrita no inquérito',
  dq_flag             VARCHAR(40)   NULL,
  UNIQUE KEY uq_fs_encomenda (encomenda_id),
  CONSTRAINT fk_fs_data     FOREIGN KEY (sk_data)           REFERENCES dim_data(sk_data),
  CONSTRAINT fk_fs_data_enc FOREIGN KEY (sk_data_encomenda) REFERENCES dim_data(sk_data),
  CONSTRAINT fk_fs_loja     FOREIGN KEY (sk_loja)           REFERENCES dim_loja(sk_loja),
  CONSTRAINT fk_fs_canal    FOREIGN KEY (sk_canal)          REFERENCES dim_canal(sk_canal),
  CONSTRAINT fk_fs_cliente  FOREIGN KEY (sk_cliente)        REFERENCES dim_cliente(sk_cliente)
) ENGINE=InnoDB COMMENT='Respostas ao inquérito de satisfação pós-compra';

-- ------------------------------------------------------------
-- METADADOS DO ETL E QUALIDADE DOS DADOS
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl_execucao (
  execucao_id         INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
  inicio              DATETIME      NOT NULL,
  fim                 DATETIME      NULL,
  estado              VARCHAR(12)   NOT NULL COMMENT 'Em curso | Sucesso | Erro',
  mensagem            VARCHAR(500)  NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS etl_log_fonte (
  execucao_id         INT           NOT NULL,
  fonte               VARCHAR(40)   NOT NULL,
  destino             VARCHAR(40)   NOT NULL,
  lidos               INT           NOT NULL,
  rejeitados          INT           NOT NULL,
  corrigidos          INT           NOT NULL,
  carregados          INT           NOT NULL,
  PRIMARY KEY (execucao_id, fonte, destino),
  CONSTRAINT fk_log_exec FOREIGN KEY (execucao_id) REFERENCES etl_execucao(execucao_id)
) ENGINE=InnoDB;

CREATE TABLE dq_regra (
  regra_id            VARCHAR(6)    NOT NULL PRIMARY KEY,
  fonte               VARCHAR(40)   NOT NULL,
  problema            VARCHAR(200)  NOT NULL,
  regra               VARCHAR(300)  NOT NULL,
  justificacao        VARCHAR(300)  NOT NULL,
  acao                VARCHAR(12)   NOT NULL COMMENT 'Corrigido | Excluído | Sinalizado'
) ENGINE=InnoDB COMMENT='Catálogo das regras de qualidade aplicadas';

CREATE TABLE dq_registo (
  id                  INT           NOT NULL AUTO_INCREMENT PRIMARY KEY,
  execucao_id         INT           NOT NULL,
  regra_id            VARCHAR(6)    NOT NULL,
  fonte               VARCHAR(40)   NOT NULL,
  registo             VARCHAR(40)   NOT NULL COMMENT 'Identificador do registo na fonte',
  campo               VARCHAR(40)   NULL,
  valor_original      VARCHAR(100)  NULL,
  valor_final         VARCHAR(100)  NULL,
  valor_afetado       DECIMAL(12,2) NULL COMMENT 'Valor em euros afetado, quando aplicável',
  KEY ix_dq_regra (regra_id),
  CONSTRAINT fk_dq_regra FOREIGN KEY (regra_id) REFERENCES dq_regra(regra_id)
) ENGINE=InnoDB COMMENT='Um registo por ocorrência de cada regra na última execução';
