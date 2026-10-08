"""Configuração do ETL: ligação ao MySQL, pastas e parâmetros do período de análise.

Os valores de ligação vêm do ficheiro .env (ver .env.example). Nada de palavras-passe no código.
"""
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

MYSQL = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
}
BD_ERP = os.getenv("BD_ERP", "technorte_erp")
BD_DW = os.getenv("BD_DW", "technorte_dw")

PASTA_DADOS = Path(os.getenv("PASTA_DADOS", RAIZ / "dados"))
PASTA_SQL = RAIZ / "sql"
PASTA_LOGS = RAIZ / "logs"
PASTA_SAIDA = RAIZ / "saida"

FICHEIROS = {
    "erp": PASTA_DADOS / "technorte_erp.sql",
    "metas": PASTA_DADOS / "metas_vendas.xlsx",
    "custos": PASTA_DADOS / "custos_produtos.xlsx",
    "campanhas": PASTA_DADOS / "campanhas_marketing.xlsx",
    "inquerito": PASTA_DADOS / "inquerito_satisfacao.xlsx",
    "trafego": PASTA_DADOS / "trafego_web.csv",
    "feriados": PASTA_DADOS / "feriados.csv",
}

# Período de análise (enunciado, secção 3) e data de extração das fontes
INICIO_PERIODO = date(2024, 1, 1)
FIM_PERIODO = date(2026, 6, 30)
# O calendário vai até ao fim de 2026: há devoluções em julho de 2026 e metas para todo o ano
INICIO_CALENDARIO = date(2024, 1, 1)
FIM_CALENDARIO = date(2026, 12, 31)

TAMANHO_LOTE = 5000
