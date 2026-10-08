"""Registo da execução: log em ficheiro, contagens por fonte e ocorrências de qualidade dos dados."""
import logging
import shutil
from datetime import datetime

import pandas as pd

from . import config
from .regras import REGRAS

log = logging.getLogger("etl")


def configurar_log():
    config.PASTA_LOGS.mkdir(exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = config.PASTA_LOGS / f"etl_{carimbo}.log"
    formato = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for h in (logging.FileHandler(caminho, encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(formato)
        log.addHandler(h)
    return caminho


def fechar_log(caminho):
    for h in log.handlers:
        h.flush()
    shutil.copyfile(caminho, config.PASTA_LOGS / "ultima_execucao.log")


class Contagens:
    """Lidos, rejeitados, corrigidos e carregados por par fonte -> destino."""

    def __init__(self):
        self.linhas = {}

    def definir(self, fonte, destino, lidos=None, rejeitados=None, corrigidos=None, carregados=None):
        reg = self.linhas.setdefault((fonte, destino),
                                     {"lidos": 0, "rejeitados": 0, "corrigidos": 0, "carregados": 0})
        for chave, valor in (("lidos", lidos), ("rejeitados", rejeitados),
                             ("corrigidos", corrigidos), ("carregados", carregados)):
            if valor is not None:
                reg[chave] = int(valor)

    def tabela(self):
        return pd.DataFrame(
            [{"fonte": f, "destino": d, **v} for (f, d), v in self.linhas.items()],
            columns=["fonte", "destino", "lidos", "rejeitados", "corrigidos", "carregados"],
        )


class Qualidade:
    """Junta as ocorrências de cada regra de qualidade (uma linha por registo afetado)."""

    def __init__(self):
        self.registos = []

    def registar(self, regra_id, registo, campo=None, original=None, final=None, valor=None):
        if regra_id not in REGRAS:
            raise KeyError(f"Regra de qualidade desconhecida: {regra_id}")
        self.registos.append({
            "regra_id": regra_id,
            "fonte": REGRAS[regra_id]["fonte"],
            "registo": str(registo),
            "campo": campo,
            "valor_original": None if original is None or pd.isna(original) else str(original)[:100],
            "valor_final": None if final is None or pd.isna(final) else str(final)[:100],
            "valor_afetado": None if valor is None or pd.isna(valor) else round(float(valor), 2),
        })

    def contar(self, regra_id):
        return sum(1 for r in self.registos if r["regra_id"] == regra_id)

    def tabela(self):
        return pd.DataFrame(self.registos, columns=[
            "regra_id", "fonte", "registo", "campo", "valor_original", "valor_final", "valor_afetado"])

    def resumo(self):
        df = self.tabela()
        linhas = []
        for rid, r in REGRAS.items():
            sub = df[df.regra_id == rid]
            linhas.append({"regra_id": rid, "fonte": r["fonte"], "acao": r["acao"],
                           "ocorrencias": len(sub),
                           "valor_afetado": round(sub.valor_afetado.dropna().sum(), 2)})
        return pd.DataFrame(linhas)
