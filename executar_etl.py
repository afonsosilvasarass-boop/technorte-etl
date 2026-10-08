"""ETL TechNorte — executa o processo completo, do início ao fim, sem intervenção manual.

    python executar_etl.py                  extrai, transforma e carrega o repositório technorte_dw
    python executar_etl.py --importar-erp   importa primeiro o dump do ERP (dados/technorte_erp.sql), se faltar
    python executar_etl.py --exportar-sql   no fim, gera saida/E3_DW_<nome>.sql (criação + carregamento)
    python executar_etl.py --sem-carga      só extrai e transforma (verificação das regras, sem escrever no MySQL)
"""
import argparse
import sys
import time

import pymysql

from etl import carregar, config, extrair, ligacao
from etl.registo import Contagens, Qualidade, configurar_log, fechar_log, log
from etl.transformar import transformar


def importar_erp(forcar=False):
    con = ligacao.ligar()
    try:
        with con.cursor() as cur:
            cur.execute("SHOW DATABASES LIKE %s", (config.BD_ERP,))
            existe = cur.fetchone() is not None
        if existe and not forcar:
            log.info("Base de dados %s já existe; importação do dump não necessária", config.BD_ERP)
            return
        log.info("A importar %s para o MySQL...", config.FICHEIROS["erp"].name)
        ligacao.executar_script(con, config.FICHEIROS["erp"])
        log.info("ERP importado")
    finally:
        con.close()


def extrair_tudo(cont):
    log.info("Extração")
    fontes = {
        "erp": extrair.erp(),
        "metas": extrair.metas(),
        "custos": extrair.custos(),
        "campanhas": extrair.campanhas(),
        "inquerito": extrair.inquerito(),
        "trafego": extrair.trafego(),
        "feriados": extrair.feriados(),
    }
    for tabela, df in fontes["erp"].items():
        log.info("  ERP %-18s %8d registos lidos", tabela, len(df))
    for nome in ("custos", "campanhas", "inquerito", "trafego", "feriados"):
        log.info("  %-22s %8d registos lidos", config.FICHEIROS[nome].name, len(fontes[nome]))
    for ano, df in fontes["metas"].items():
        log.info("  metas_vendas.xlsx [%s]  %8d linhas (com totais e notas)", ano, len(df))
    return fontes


def resumo(cont, q):
    log.info("Resumo por fonte (lidos / rejeitados / corrigidos / carregados):")
    for _, r in cont.tabela().iterrows():
        log.info("  %-26s -> %-26s %7d %7d %7d %7d", r.fonte, r.destino, r.lidos, r.rejeitados,
                 r.corrigidos, r.carregados)
    log.info("Regras de qualidade aplicadas:")
    for _, r in q.resumo().iterrows():
        log.info("  %s %-26s %-10s %5d ocorrências  %12.2f EUR", r.regra_id, r.fonte, r.acao,
                 r.ocorrencias, r.valor_afetado)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--importar-erp", action="store_true", help="importa o dump do ERP se a base não existir")
    p.add_argument("--reimportar-erp", action="store_true", help="reimporta sempre o dump do ERP")
    p.add_argument("--exportar-sql", action="store_true", help="gera o script E3 com os dados carregados")
    p.add_argument("--sem-carga", action="store_true", help="não escreve no MySQL de destino")
    p.add_argument("--nome", default="SaraSilva", help="nome usado no ficheiro E3 (E3_DW_<nome>.sql)")
    args = p.parse_args()

    caminho_log = configurar_log()
    inicio = time.time()
    log.info("ETL TechNorte — início | MySQL %s:%s | ERP=%s | DW=%s",
             config.MYSQL["host"], config.MYSQL["port"], config.BD_ERP, config.BD_DW)
    cont, q = Contagens(), Qualidade()
    con, execucao_id = None, None
    try:
        if args.importar_erp or args.reimportar_erp:
            importar_erp(forcar=args.reimportar_erp)
        fontes = extrair_tudo(cont)
        log.info("Transformação")
        tabelas = transformar(fontes, q, cont)
        if args.sem_carga:
            resumo(cont, q)
            log.info("Modo --sem-carga: nada foi escrito no MySQL")
            return 0
        log.info("Carga")
        carregar.criar_dw()
        con = ligacao.ligar(config.BD_DW)
        execucao_id = carregar.iniciar_execucao(con)
        carregar.carregar(con, tabelas, execucao_id, q, cont)
        problemas = carregar.verificar(con)
        resumo(cont, q)
        estado = "Sucesso" if problemas == 0 else "Avisos"
        carregar.terminar_execucao(con, execucao_id, estado, f"{problemas} problemas de integridade")
        if args.exportar_sql:
            destino = config.PASTA_SAIDA / f"E3_DW_{args.nome}.sql"
            from etl.exportar import exportar
            exportar(destino)
            log.info("Script E3 gerado: %s", destino)
        log.info("ETL terminado (%s) em %.1f s — execução n.º %s", estado, time.time() - inicio, execucao_id)
        return 0
    except pymysql.err.OperationalError as erro:
        log.error("Não foi possível ligar ao MySQL: %s. Confirme o ficheiro .env (ver .env.example).", erro)
        return 2
    except Exception as erro:
        log.exception("ETL interrompido: %s", erro)
        if con is not None and execucao_id is not None:
            carregar.terminar_execucao(con, execucao_id, "Erro", str(erro))
        return 1
    finally:
        if con is not None:
            con.close()
        fechar_log(caminho_log)


if __name__ == "__main__":
    sys.exit(main())
