"""Acesso ao MySQL: ligações, leitura para DataFrame, execução de scripts e inserção em lote."""
from decimal import Decimal

import numpy as np
import pandas as pd
import pymysql

from . import config


def ligar(base_dados=None):
    return pymysql.connect(
        **config.MYSQL,
        database=base_dados,
        charset="utf8mb4",
        autocommit=False,
    )


def ler(con, sql):
    """Executa um SELECT e devolve um DataFrame (Decimal convertido em float)."""
    with con.cursor() as cur:
        cur.execute(sql)
        colunas = [c[0] for c in cur.description]
        linhas = cur.fetchall()
    df = pd.DataFrame(list(linhas), columns=colunas)
    for c in df.columns:
        if len(df) and df[c].map(lambda v: isinstance(v, Decimal)).any():
            df[c] = df[c].astype(float)
    return df


def dividir_script(texto):
    """Divide um script SQL em instruções, respeitando texto entre plicas e comentários '--'."""
    instrucoes, atual = [], []
    i, n = 0, len(texto)
    em_texto = False
    while i < n:
        ch = texto[i]
        if em_texto:
            atual.append(ch)
            if ch == "\\" and i + 1 < n:
                atual.append(texto[i + 1])
                i += 2
                continue
            if ch == "'":
                if i + 1 < n and texto[i + 1] == "'":
                    atual.append("'")
                    i += 2
                    continue
                em_texto = False
        elif ch == "'":
            em_texto = True
            atual.append(ch)
        elif ch == "-" and texto.startswith("--", i):
            fim = texto.find("\n", i)
            i = n if fim == -1 else fim
            continue
        elif ch == ";":
            instrucao = "".join(atual).strip()
            if instrucao:
                instrucoes.append(instrucao)
            atual = []
        else:
            atual.append(ch)
        i += 1
    resto = "".join(atual).strip()
    if resto:
        instrucoes.append(resto)
    return instrucoes


def executar_script(con, caminho):
    texto = open(caminho, encoding="utf-8").read()
    with con.cursor() as cur:
        for instrucao in dividir_script(texto):
            cur.execute(instrucao)
    con.commit()


def _valor(v):
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    return v


def inserir(con, tabela, df):
    """Insere um DataFrame numa tabela, em lotes. Devolve o número de linhas inseridas."""
    if df.empty:
        return 0
    colunas = list(df.columns)
    sql = (f"INSERT INTO {tabela} ({', '.join(colunas)}) "
           f"VALUES ({', '.join(['%s'] * len(colunas))})")
    dados = [tuple(_valor(v) for v in linha) for linha in df.itertuples(index=False, name=None)]
    with con.cursor() as cur:
        for i in range(0, len(dados), config.TAMANHO_LOTE):
            cur.executemany(sql, dados[i:i + config.TAMANHO_LOTE])
    con.commit()
    return len(dados)
