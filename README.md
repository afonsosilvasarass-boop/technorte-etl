# TechNorte — ETL e repositório analítico

Projeto final da UFCD 10804 (Projeto de Business Intelligence) · Sara Silva

Processo ETL em Python que lê **todas** as fontes da TechNorte e carrega o repositório analítico
`technorte_dw` em MySQL (modelo dimensional em estrela, documentado no E1):

| Fonte | Formato | Destino no repositório |
|---|---|---|
| `technorte_erp` (dump `technorte_erp.sql`) | MySQL | `dim_loja`, `dim_produto`, `dim_cliente`, `dim_vendedor`, `dim_canal`, `dim_motivo_devolucao`, `fact_vendas`, `fact_devolucoes` |
| `custos_produtos.xlsx` | Excel | `dim_produto` (histórico de custos, SCD tipo 2) |
| `metas_vendas.xlsx` | Excel | `fact_metas` |
| `campanhas_marketing.xlsx` | Excel | `dim_campanha`, `ponte_campanha_loja` |
| `inquerito_satisfacao.xlsx` | Excel | `fact_satisfacao` |
| `trafego_web.csv` | CSV (`;`, vírgula decimal) | `dim_fonte_web`, `fact_trafego_web` |
| `feriados.csv` | CSV | `dim_data` |

A base de dados do ERP é tratada como sistema de origem: o ETL **só lê** dela. O repositório analítico
é uma base de dados separada (`technorte_dw`).

## Requisitos

- MySQL 8+ (testado com MySQL 9.6) ou MariaDB 10.6+
- Python 3.11+ (testado com 3.14)

## Reexecutar do início ao fim

```bash
# 1. Ambiente Python
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

# 2. Ligação ao MySQL
copy .env.example .env          # e preencher MYSQL_USER / MYSQL_PASSWORD

# 3. ETL completo (importa o ERP se ainda não existir no MySQL)
python executar_etl.py --importar-erp --exportar-sql
```

O passo 3 pode ser repetido as vezes que for preciso: cada execução recria as tabelas do repositório
(carga completa) e acrescenta uma linha ao histórico em `etl_execucao`.

Opções de `executar_etl.py`:

| Opção | Efeito |
|---|---|
| `--importar-erp` | importa `dados/technorte_erp.sql` se a base `technorte_erp` não existir |
| `--reimportar-erp` | importa sempre o dump (apaga e recria `technorte_erp`) |
| `--exportar-sql` | no fim, gera `saida/E3_DW_SaraSilva.sql` (criação + carregamento, sem precisar do Python) |
| `--sem-carga` | só extrai e transforma; mostra as contagens e as regras sem escrever no MySQL |

Em alternativa ao `--importar-erp`, o ERP pode ser importado à mão:
`mysql -u root -p < dados/technorte_erp.sql`.

## Quando chegarem dados novos

1. Substituir os ficheiros em `dados/` (mesmos nomes e estrutura) ou atualizar a base `technorte_erp`.
2. Correr `python executar_etl.py` (com `--reimportar-erp` se o ERP chegar como novo dump).
3. Ver o resumo no fim do log (`logs/ultima_execucao.log`): contagens por fonte e regras de qualidade.
4. No Power BI, **Atualizar**.

Se o período mudar, ajustar `INICIO_PERIODO`, `FIM_PERIODO` e `FIM_CALENDARIO` em `etl/config.py`.

## Estrutura

```
executar_etl.py      programa principal (orquestração, log, tratamento de erros)
etl/config.py        ligação (.env), pastas e período de análise
etl/extrair.py       leitura das fontes, sem correções
etl/regras.py        catálogo das 30 regras de qualidade (problema, regra, justificação, destino)
etl/transformar.py   aplicação das regras, chaves substitutas, SCD tipo 2, factos
etl/carregar.py      criação do repositório, carga, verificação de integridade
etl/exportar.py      geração do E3 completo (DDL + INSERT)
etl/ligacao.py       acesso ao MySQL
sql/01_criar_dw.sql  E3 — script de criação do repositório analítico
dados/               pacote de dados fornecido pelo formador
logs/                log de cada execução; ultima_execucao.log = a mais recente
```

## Log de execução

Cada execução escreve `logs/etl_AAAAMMDD_HHMMSS.log` (e uma cópia em `logs/ultima_execucao.log`) com:

- registos **lidos** por fonte;
- por par fonte → tabela: **lidos, rejeitados, corrigidos e carregados**;
- ocorrências e valor em euros afetado por cada regra de qualidade;
- resultado das verificações de integridade referencial e totais de controlo por ano.

As mesmas contagens ficam no MySQL, em `etl_log_fonte` (por execução), e cada ocorrência de qualidade
fica em `dq_registo`, ligada ao catálogo `dq_regra` — é daqui que sai a página "Qualidade dos dados"
do dashboard.

## Dimensões que variam no tempo

- **`dim_produto` — SCD tipo 2.** O custo de aquisição muda ao longo do tempo (`custos_produtos.xlsx`
  tem datas de vigência). Cada versão de custo é uma linha com `valido_de`/`valido_ate`; a venda liga à
  versão em vigor na data da encomenda, por isso a margem de 2024 usa o custo de 2024.
- **`dim_cliente`, `dim_vendedor`, `dim_loja` — SCD tipo 1.** O ERP só guarda o estado atual (cidade,
  `ativo`): não há histórico de alterações para reconstruir. Guardar versões a partir de agora exigiria
  cargas incrementais com fotografias periódicas; fica como evolução recomendada (ver E1).
- **`ponte_campanha_loja`** só inclui lojas já abertas no início de cada campanha (Lisboa abriu em
  março de 2025), para "Todas" não significar coisas diferentes em anos diferentes.

## Dados pessoais

`dim_cliente` não tem nome, e-mail, telefone nem NIF: são usados só durante a deduplicação e não são
necessários à análise (RGPD, princípio da minimização).
