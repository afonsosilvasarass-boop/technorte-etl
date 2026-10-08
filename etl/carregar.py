"""Carga: recria o repositório analítico, insere dimensões e factos e grava os metadados da execução."""
from datetime import datetime

import pandas as pd

from . import config, ligacao
from .regras import REGRAS
from .registo import log

# Ordem de carga: dimensões antes dos factos (respeita as chaves estrangeiras)
ORDEM = ["dim_data", "dim_loja", "dim_canal", "dim_produto", "dim_cliente", "dim_vendedor", "dim_campanha",
         "ponte_campanha_loja", "dim_motivo_devolucao", "dim_fonte_web",
         "fact_vendas", "fact_devolucoes", "fact_metas", "fact_trafego_web", "fact_satisfacao"]


def criar_dw():
    con = ligacao.ligar()
    try:
        ligacao.executar_script(con, config.PASTA_SQL / "01_criar_dw.sql")
    finally:
        con.close()


def iniciar_execucao(con):
    with con.cursor() as cur:
        cur.execute("INSERT INTO etl_execucao (inicio, estado) VALUES (%s, 'Em curso')", (datetime.now(),))
        execucao_id = cur.lastrowid
    con.commit()
    return execucao_id


def terminar_execucao(con, execucao_id, estado, mensagem=None):
    with con.cursor() as cur:
        cur.execute("UPDATE etl_execucao SET fim = %s, estado = %s, mensagem = %s WHERE execucao_id = %s",
                    (datetime.now(), estado, (mensagem or "")[:500], execucao_id))
    con.commit()


def carregar(con, tabelas, execucao_id, qualidade, contagens):
    inseridas = {}
    for nome in ORDEM:
        n = ligacao.inserir(con, nome, tabelas[nome])
        inseridas[nome] = n
        log.info("Carregada %-22s %8d linhas", nome, n)

    regras = pd.DataFrame([{"regra_id": k, **{c: v[c] for c in ("fonte", "problema", "regra", "justificacao", "acao")}}
                           for k, v in REGRAS.items()])
    ligacao.inserir(con, "dq_regra", regras)
    dq = qualidade.tabela()
    dq.insert(0, "execucao_id", execucao_id)
    ligacao.inserir(con, "dq_registo", dq)
    log.info("Carregada %-22s %8d linhas", "dq_registo", len(dq))

    cont = contagens.tabela()
    cont.insert(0, "execucao_id", execucao_id)
    ligacao.inserir(con, "etl_log_fonte", cont)
    return inseridas


def verificar(con):
    """Integridade referencial e controlos de reconciliação depois da carga."""
    verificacoes = {
        "vendas sem data": "SELECT COUNT(*) FROM fact_vendas f LEFT JOIN dim_data d USING (sk_data) WHERE d.sk_data IS NULL",
        "vendas sem produto": "SELECT COUNT(*) FROM fact_vendas f LEFT JOIN dim_produto p USING (sk_produto) WHERE p.sk_produto IS NULL",
        "vendas sem cliente": "SELECT COUNT(*) FROM fact_vendas f LEFT JOIN dim_cliente c USING (sk_cliente) WHERE c.sk_cliente IS NULL",
        "devoluções sem linha de venda": "SELECT COUNT(*) FROM fact_devolucoes d LEFT JOIN fact_vendas f USING (linha_id) WHERE f.linha_id IS NULL",
        "respostas sem loja": "SELECT COUNT(*) FROM fact_satisfacao s LEFT JOIN dim_loja l USING (sk_loja) WHERE l.sk_loja IS NULL",
        "linhas com custo fora da versão SCD2": (
            "SELECT COUNT(*) FROM fact_vendas f JOIN dim_produto p USING (sk_produto) JOIN dim_data d USING (sk_data) "
            "WHERE p.produto_id > 0 AND (d.data < p.valido_de OR d.data > p.valido_ate)"),
        "devolvido acima do vendido (linha)": (
            "SELECT COUNT(*) FROM (SELECT d.linha_id, SUM(d.quantidade) q FROM fact_devolucoes d GROUP BY d.linha_id) x "
            "JOIN fact_vendas f USING (linha_id) WHERE x.q > f.quantidade"),
    }
    problemas = 0
    with con.cursor() as cur:
        for nome, sql in verificacoes.items():
            cur.execute(sql)
            n = cur.fetchone()[0]
            problemas += n
            log.info("Verificação %-40s %s", nome, "OK" if n == 0 else f"{n} PROBLEMAS")
        cur.execute("""SELECT d.ano, ROUND(SUM(f.valor_venda), 2), COUNT(DISTINCT f.encomenda_id)
                       FROM fact_vendas f JOIN dim_data d USING (sk_data)
                       WHERE f.encomenda_valida = 1 GROUP BY d.ano ORDER BY d.ano""")
        for ano, valor, encs in cur.fetchall():
            log.info("Controlo %s: vendas de encomendas não canceladas = %s EUR em %s encomendas", ano, valor, encs)
    return problemas
