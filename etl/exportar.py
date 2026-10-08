"""Gera o E3 completo: script de criação + INSERTs com os dados carregados no repositório analítico."""
from datetime import date, datetime
from decimal import Decimal

from . import config, ligacao
from .carregar import ORDEM

TABELAS_EXTRA = ["dq_regra", "dq_registo"]
LINHAS_POR_INSERT = 1000


def _sql(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, datetime):
        return f"'{v:%Y-%m-%d %H:%M:%S}'"
    if isinstance(v, date):
        return f"'{v:%Y-%m-%d}'"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def exportar(caminho):
    config.PASTA_SAIDA.mkdir(exist_ok=True)
    ddl = open(config.PASTA_SQL / "01_criar_dw.sql", encoding="utf-8").read()
    con = ligacao.ligar(config.BD_DW)
    try:
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(ddl)
            f.write("\n-- ============================================================\n"
                    "-- CARREGAMENTO (gerado por executar_etl.py --exportar-sql a partir do repositório carregado)\n"
                    f"-- {datetime.now():%Y-%m-%d %H:%M}\n"
                    "-- ============================================================\n"
                    "SET FOREIGN_KEY_CHECKS = 0;\n")
            with con.cursor() as cur:
                for tabela in ORDEM + TABELAS_EXTRA:
                    colunas_sql = "id" if tabela == "dq_registo" else None
                    cur.execute(f"SELECT * FROM {tabela}" + (f" ORDER BY {colunas_sql}" if colunas_sql else ""))
                    colunas = [c[0] for c in cur.description]
                    linhas = cur.fetchall()
                    f.write(f"\n-- {tabela}: {len(linhas)} linhas\n")
                    for i in range(0, len(linhas), LINHAS_POR_INSERT):
                        bloco = linhas[i:i + LINHAS_POR_INSERT]
                        valores = ",\n".join("(" + ", ".join(_sql(v) for v in linha) + ")" for linha in bloco)
                        f.write(f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES\n{valores};\n")
            f.write("\nSET FOREIGN_KEY_CHECKS = 1;\n")
    finally:
        con.close()
    return caminho
