"""Extração: lê cada fonte tal como está, sem correções (as correções são feitas em transformar.py)."""
import pandas as pd

from . import config, ligacao

TABELAS_ERP = ["lojas", "categorias", "produtos", "clientes", "vendedores",
               "encomendas", "linhas_encomenda", "devolucoes"]


def erp():
    """Lê as 8 tabelas do ERP (só leitura)."""
    con = ligacao.ligar(config.BD_ERP)
    try:
        return {t: ligacao.ler(con, f"SELECT * FROM {t} ORDER BY id") for t in TABELAS_ERP}
    finally:
        con.close()


def metas():
    """Uma folha por ano; cabeçalho na 4.ª linha; para na linha 'Total' (ignora totais e notas)."""
    folhas = {}
    for folha in pd.ExcelFile(config.FICHEIROS["metas"]).sheet_names:
        df = pd.read_excel(config.FICHEIROS["metas"], sheet_name=folha, header=3)
        df = df.rename(columns={df.columns[0]: "Loja"})
        folhas[int(folha)] = df
    return folhas


def custos():
    df = pd.read_excel(config.FICHEIROS["custos"], sheet_name="Custos", header=3)
    df.columns = ["codigo", "custo_unitario", "inicio_vigencia", "fornecedor"]
    return df.dropna(how="all")


def campanhas():
    df = pd.read_excel(config.FICHEIROS["campanhas"], header=2)
    df.columns = ["campanha_id", "nome", "canal", "data_inicio", "data_fim", "orcamento", "lojas_abrangidas"]
    return df.dropna(how="all")


def inquerito():
    df = pd.read_excel(config.FICHEIROS["inquerito"], header=3)
    df.columns = ["data_resposta", "encomenda_id", "loja", "nota", "recomendaria", "comentario"]
    # n.º da linha no Excel (cabeçalho na linha 4) = identificador da resposta na fonte
    df.insert(0, "resposta_id", df.index + 5)
    return df.dropna(subset=["encomenda_id"])


def trafego():
    """CSV com definições regionais portuguesas: ';' como separador, vírgula decimal, datas dd/mm/aaaa."""
    df = pd.read_csv(config.FICHEIROS["trafego"], sep=";", decimal=",", thousands=".",
                     encoding="utf-8-sig", dtype={"data": str})
    df["data"] = pd.to_datetime(df["data"], format="%d/%m/%Y")
    return df


def feriados():
    df = pd.read_csv(config.FICHEIROS["feriados"], sep=",", encoding="utf-8")
    df["data"] = pd.to_datetime(df["data"], format="%Y-%m-%d")
    return df
