"""Transformação: aplica as regras de qualidade (regras.py) e constrói as dimensões e os factos.

Ordem: calendário -> lojas/canais -> produtos (SCD2 de custos) -> clientes (deduplicação)
-> encomendas -> linhas (fact_vendas) -> vendedores -> campanhas -> devoluções -> metas
-> inquérito -> tráfego. Cada passo regista as ocorrências em `q` (Qualidade) e as contagens
em `cont` (Contagens).
"""
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config
from .registo import log

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
MESES_ABREV = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# NUTS II simplificado; 'Lisboa' corresponde à região da loja de Lisboa
REGIAO_DISTRITO = {
    "Porto": "Norte", "Braga": "Norte", "Vila Real": "Norte", "Bragança": "Norte", "Viana do Castelo": "Norte",
    "Coimbra": "Centro", "Viseu": "Centro", "Aveiro": "Centro", "Leiria": "Centro", "Guarda": "Centro",
    "Castelo Branco": "Centro", "Lisboa": "Lisboa", "Setúbal": "Lisboa", "Santarém": "Centro",
    "Faro": "Algarve", "Beja": "Alentejo", "Évora": "Alentejo", "Portalegre": "Alentejo",
    "Madeira": "Madeira", "Açores": "Açores",
}
RESPONSABILIDADE_MOTIVO = {"Arrependimento": "Cliente"}  # os restantes motivos são da empresa
FIM_ABERTO = date(9999, 12, 31)
LIMITE_DATA = pd.Timestamp(config.FIM_PERIODO) + pd.Timedelta(days=1)


def sk_data(serie):
    """Data (ou datetime) -> chave AAAAMMDD."""
    s = pd.to_datetime(serie)
    return (s.dt.year * 10000 + s.dt.month * 100 + s.dt.day).astype(int)


def chaves_sequenciais(df, ordem, nome):
    df = df.sort_values(ordem).reset_index(drop=True)
    df.insert(0, nome, np.arange(1, len(df) + 1))
    return df


# ---------------------------------------------------------------------------------------
# Calendário
# ---------------------------------------------------------------------------------------
def dim_data(feriados, cont):
    datas = pd.date_range(config.INICIO_CALENDARIO, config.FIM_CALENDARIO, freq="D")
    d = pd.DataFrame({"data": datas})
    fer = feriados.drop_duplicates("data").rename(columns={"feriado": "nome_feriado", "tipo": "tipo_feriado"})
    d = d.merge(fer, on="data", how="left")
    d["sk_data"] = sk_data(d["data"])
    d["ano"] = d.data.dt.year
    d["semestre"] = np.where(d.data.dt.month <= 6, 1, 2)
    d["trimestre"] = d.data.dt.quarter
    d["mes"] = d.data.dt.month
    d["nome_mes"] = d.mes.map(lambda m: MESES[m - 1])
    d["mes_abrev"] = d.mes.map(lambda m: MESES_ABREV[m - 1])
    d["ano_mes"] = d.data.dt.strftime("%Y-%m")
    d["inicio_mes"] = d.data.values.astype("datetime64[M]")
    d["dia"] = d.data.dt.day
    d["dia_semana"] = d.data.dt.dayofweek + 1
    d["nome_dia"] = d.dia_semana.map(lambda x: DIAS[x - 1])
    d["semana_iso"] = d.data.dt.isocalendar().week.astype(int)
    d["fim_de_semana"] = (d.dia_semana >= 6).astype(int)
    d["feriado"] = d.nome_feriado.notna().astype(int)
    d["dia_util"] = ((d.dia_semana <= 5) & (d.tipo_feriado != "Nacional")).astype(int)
    d["data"] = d.data.dt.date
    d["inicio_mes"] = pd.to_datetime(d.inicio_mes).dt.date
    d = d.astype(object).where(d.notna(), None)
    cont.definir("feriados.csv", "dim_data", lidos=len(feriados), carregados=feriados.data.isin(datas).sum())
    return d[["sk_data", "data", "ano", "semestre", "trimestre", "mes", "nome_mes", "mes_abrev", "ano_mes",
              "inicio_mes", "dia", "dia_semana", "nome_dia", "semana_iso", "fim_de_semana", "feriado",
              "nome_feriado", "tipo_feriado", "dia_util"]]


# ---------------------------------------------------------------------------------------
# Lojas e canais
# ---------------------------------------------------------------------------------------
def dim_loja(lojas, cont):
    l = lojas.copy()
    l["tipo_loja"] = np.where(l.cidade.isna(), "Online", "Física")
    l["nome_curto"] = np.where(l.tipo_loja == "Online", "Online", l.nome.str.replace("TechNorte ", "", regex=False))
    l["cidade"] = l.cidade.fillna("Online")
    l["distrito"] = l.distrito.fillna("Online")
    l["data_abertura"] = pd.to_datetime(l.data_abertura).dt.date
    l = l.rename(columns={"id": "loja_id"})
    l = chaves_sequenciais(l, "loja_id", "sk_loja")
    cont.definir("ERP lojas", "dim_loja", lidos=len(lojas), carregados=len(l))
    return l[["sk_loja", "loja_id", "nome", "nome_curto", "tipo_loja", "cidade", "distrito", "regiao",
              "data_abertura", "area_m2"]]


def dim_canal(encomendas):
    canais = sorted(encomendas.canal.dropna().unique(), key=lambda c: (c != "Loja", c))
    return pd.DataFrame({"sk_canal": range(1, len(canais) + 1), "canal": canais})


# ---------------------------------------------------------------------------------------
# Produtos e custos (SCD tipo 2)
# ---------------------------------------------------------------------------------------
def _tipo_produto(nome, marca):
    resto = nome[len(marca):].strip() if marca and nome.startswith(marca) else nome
    partes = resto.split()
    return " ".join(partes[:-1]) if len(partes) > 1 else resto


def dim_produto(produtos, categorias, custos, q, cont):
    p = produtos.copy()
    cat_nome = dict(zip(categorias.id, categorias.nome))

    # Q05 — categoria em falta: herdar a categoria dos produtos do mesmo tipo
    p["tipo"] = [_tipo_produto(n, m) for n, m in zip(p.nome, p.marca)]
    p["primeira_palavra"] = p.tipo.str.split().str[0]
    p["categoria_corrigida"] = 0
    conhecidos = p[p.categoria_id.notna()]
    for i in p.index[p.categoria_id.isna()]:
        cand = conhecidos[conhecidos.tipo == p.at[i, "tipo"]]
        if cand.empty:
            cand = conhecidos[conhecidos.primeira_palavra == p.at[i, "primeira_palavra"]]
        if cand.empty:
            log.warning("Produto %s sem categoria e sem produtos do mesmo tipo", p.at[i, "codigo"])
            continue
        nova = int(cand.categoria_id.mode().iloc[0])
        p.at[i, "categoria_id"] = nova
        p.at[i, "categoria_corrigida"] = 1
        q.registar("Q05", f"produto {p.at[i, 'id']}", "categoria_id", None, f"{nova} ({cat_nome[nova]})")
    p["categoria_id"] = p.categoria_id.fillna(0).astype(int)
    p["categoria"] = p.categoria_id.map(cat_nome).fillna("Sem categoria")

    # Q16 — códigos fora do padrão; Q18 — códigos sem produto
    c = custos.copy()
    c["linha_fonte"] = c.index + 5
    c["codigo_original"] = c.codigo.astype(str)
    c["codigo"] = c.codigo_original.str.strip().str.upper()
    for _, r in c[c.codigo != c.codigo_original].iterrows():
        q.registar("Q16", f"linha {r.linha_fonte}", "codigo", r.codigo_original, r.codigo)
    sem_produto = ~c.codigo.isin(p.codigo)
    for _, r in c[sem_produto].iterrows():
        q.registar("Q18", f"linha {r.linha_fonte}", "codigo", r.codigo_original, None, r.custo_unitario)
    c = c[~sem_produto]
    c["inicio_vigencia"] = pd.to_datetime(c.inicio_vigencia).dt.date
    c = c.merge(p[["id", "codigo"]], on="codigo").rename(columns={"id": "produto_id"})
    c = c.sort_values(["produto_id", "inicio_vigencia"]).drop_duplicates(["produto_id", "inicio_vigencia"], keep="last")

    # Margem média por categoria, com o custo mais recente de cada produto
    atual = c.groupby("produto_id").tail(1).merge(p[["id", "preco_venda", "categoria_id"]],
                                                   left_on="produto_id", right_on="id")
    atual["margem"] = 1 - atual.custo_unitario / atual.preco_venda
    margem_cat = atual.groupby("categoria_id").margem.mean()
    margem_global = float(atual.margem.mean())

    # Q17 — produtos sem custo: custo estimado com a margem média da categoria
    inicio_custos = c.inicio_vigencia.min()
    estimados = []
    for _, r in p[~p.id.isin(c.produto_id)].iterrows():
        m = float(margem_cat.get(r.categoria_id, margem_global))
        custo = round(r.preco_venda * (1 - m), 2)
        estimados.append({"produto_id": r.id, "custo_unitario": custo, "inicio_vigencia": inicio_custos,
                          "fornecedor": None, "custo_estimado": 1})
        q.registar("Q17", f"produto {r.id} ({r.codigo})", "custo_unitario", None,
                   f"{custo} (margem {m:.1%})")
    c["custo_estimado"] = 0
    versoes = pd.concat([c[["produto_id", "custo_unitario", "inicio_vigencia", "fornecedor", "custo_estimado"]],
                         pd.DataFrame(estimados)], ignore_index=True)
    versoes = versoes.sort_values(["produto_id", "inicio_vigencia"]).reset_index(drop=True)
    versoes["valido_de"] = versoes.inicio_vigencia
    proximo = versoes.groupby("produto_id").valido_de.shift(-1)
    versoes["valido_ate"] = [FIM_ABERTO if pd.isna(x) else x - timedelta(days=1) for x in proximo]
    versoes["versao"] = versoes.groupby("produto_id").cumcount() + 1
    versoes["versao_atual"] = (versoes.valido_ate == FIM_ABERTO).astype(int)

    d = versoes.merge(p.rename(columns={"id": "produto_id"}), on="produto_id")
    d = chaves_sequenciais(d, ["produto_id", "valido_de"], "sk_produto")
    desconhecido = pd.DataFrame([{
        "sk_produto": -1, "produto_id": -1, "codigo": "N/D", "nome": "Produto desconhecido", "marca": "N/D",
        "categoria_id": 0, "categoria": "Desconhecida", "preco_venda": None, "ativo": 0, "custo_unitario": None,
        "fornecedor": None, "custo_estimado": 1, "categoria_corrigida": 0, "valido_de": date(1900, 1, 1),
        "valido_ate": FIM_ABERTO, "versao": 1, "versao_atual": 1}])
    d = pd.concat([desconhecido, d], ignore_index=True)
    colunas = ["sk_produto", "produto_id", "codigo", "nome", "marca", "categoria_id", "categoria", "preco_venda",
               "ativo", "custo_unitario", "fornecedor", "custo_estimado", "categoria_corrigida",
               "valido_de", "valido_ate", "versao", "versao_atual"]
    cont.definir("ERP produtos", "dim_produto", lidos=len(produtos), corrigidos=p.categoria_corrigida.sum(),
                 carregados=len(d))
    cont.definir("custos_produtos.xlsx", "dim_produto", lidos=len(custos), rejeitados=sem_produto.sum(),
                 corrigidos=(custos.codigo.astype(str) != custos.codigo.astype(str).str.strip().str.upper()).sum(),
                 carregados=len(c))
    return d[colunas], margem_global


# ---------------------------------------------------------------------------------------
# Clientes: deduplicação (o resto dos atributos é tratado depois das encomendas)
# ---------------------------------------------------------------------------------------
def deduplicar_clientes(clientes, q):
    c = clientes.copy().sort_values("id").reset_index(drop=True)
    c["email_n"] = c.email.str.strip().str.lower()
    pai = {i: i for i in c.id}

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for chave in ("nif", "email_n"):
        for _, ids in c[c[chave].notna()].groupby(chave).id:
            ids = list(ids)
            for outro in ids[1:]:
                a, b = raiz(ids[0]), raiz(outro)
                if a != b:
                    pai[max(a, b)] = min(a, b)
    c["sobrevivente"] = c.id.map(raiz)
    mapa = dict(zip(c.id, c.sobrevivente))

    linhas = []
    for sob, grupo in c.groupby("sobrevivente", sort=True):
        grupo = grupo.sort_values("id")
        reg = grupo.iloc[0].copy()
        for col in ["telefone", "cidade", "distrito", "data_nascimento", "genero", "tipo"]:
            if pd.isna(reg[col]):
                valores = grupo[col].dropna()
                if not valores.empty:
                    reg[col] = valores.iloc[0]
        reg["data_registo"] = grupo.data_registo.dropna().min() if grupo.data_registo.notna().any() else None
        reg["ids_origem"] = ";".join(str(i) for i in grupo.id)
        reg["duplicado_fundido"] = int(len(grupo) > 1)
        linhas.append(reg)
        for _, dup in grupo.iloc[1:].iterrows():
            q.registar("Q01", f"cliente {dup.id}", "id", dup.id, sob)
    return pd.DataFrame(linhas).reset_index(drop=True), mapa


def _canonico(valores):
    """Grafia mais frequente de cada cidade (chave: minúsculas, sem espaços, 'V.' -> 'Vila')."""
    s = valores.dropna().str.strip()
    chave = s.str.casefold().str.replace(r"^v\.\s*", "vila ", regex=True)
    freq = pd.DataFrame({"chave": chave, "grafia": s}).value_counts().reset_index()
    canon = freq.drop_duplicates("chave").set_index("chave").grafia
    return lambda v: None if pd.isna(v) else canon[re.sub(r"^v\.\s*", "vila ", v.strip().casefold())]


def dim_cliente(clientes_dedup, total_lidos, primeira_compra, distritos_loja, q, cont):
    c = clientes_dedup.copy()
    # Q02 — cidades
    canon = _canonico(c.cidade)
    nova = c.cidade.map(canon)
    for _, r in c[(c.cidade != nova) & c.cidade.notna()].iterrows():
        q.registar("Q02", f"cliente {r.id}", "cidade", r.cidade, canon(r.cidade))
    c["cidade"] = nova
    c["distrito"] = c.distrito.str.strip()

    # Q03 — data de registo no futuro
    c["data_registo"] = pd.to_datetime(c.data_registo)
    c["data_primeira_compra"] = c.id.map(primeira_compra)
    futuro = c.data_registo > pd.Timestamp(config.FIM_PERIODO)
    for i in c.index[futuro]:
        novo = c.at[i, "data_primeira_compra"]
        q.registar("Q03", f"cliente {c.at[i, 'id']}", "data_registo", c.at[i, "data_registo"].date(),
                   None if pd.isna(novo) else pd.Timestamp(novo).date())
        c.at[i, "data_registo"] = pd.Timestamp(novo) if pd.notna(novo) else pd.NaT

    # Q30 — registo depois da primeira compra
    c["registo_apos_compra"] = (c.data_registo > pd.to_datetime(c.data_primeira_compra)).astype(int)
    for _, r in c[c.registo_apos_compra == 1].iterrows():
        q.registar("Q30", f"cliente {r.id}", "data_registo", r.data_registo.date(),
                   f"1.ª compra {r.data_primeira_compra}")

    # Q04 — data de nascimento sentinela
    c["data_nascimento"] = pd.to_datetime(c.data_nascimento)
    sentinela = c.data_nascimento == pd.Timestamp("1900-01-01")
    for _, r in c[sentinela].iterrows():
        q.registar("Q04", f"cliente {r.id}", "data_nascimento", "1900-01-01", None)
    c.loc[sentinela, "data_nascimento"] = pd.NaT

    ref = pd.Timestamp(config.FIM_PERIODO)
    idade = (ref - c.data_nascimento).dt.days // 365.25
    c["faixa_etaria"] = pd.cut(idade, [0, 24, 34, 44, 54, 64, 200],
                               labels=["<25", "25-34", "35-44", "45-54", "55-64", "65+"]).astype(str)
    c.loc[c.data_nascimento.isna(), "faixa_etaria"] = "Desconhecida"
    c["ano_nascimento"] = c.data_nascimento.dt.year.astype("Int64")
    c["regiao"] = c.distrito.map(REGIAO_DISTRITO).fillna("Outra")
    c["distrito_com_loja"] = c.distrito.isin(distritos_loja).astype(int)
    c["data_registo"] = c.data_registo.dt.date
    c["data_primeira_compra"] = pd.to_datetime(c.data_primeira_compra).dt.date
    c = c.rename(columns={"id": "cliente_id"})
    c = chaves_sequenciais(c, "cliente_id", "sk_cliente")
    corrigidos = len({r["registo"] for r in q.registos if r["regra_id"] in ("Q02", "Q03", "Q04")})
    cont.definir("ERP clientes", "dim_cliente", lidos=total_lidos, rejeitados=total_lidos - len(c),
                 corrigidos=corrigidos, carregados=len(c))
    cols = ["sk_cliente", "cliente_id", "ids_origem", "tipo", "genero", "cidade", "distrito", "regiao",
            "distrito_com_loja", "data_registo", "ano_nascimento", "faixa_etaria", "data_primeira_compra",
            "registo_apos_compra", "duplicado_fundido"]
    c = c[cols].astype(object)
    return c.where(pd.notna(c), None)


# ---------------------------------------------------------------------------------------
# Encomendas
# ---------------------------------------------------------------------------------------
def tratar_encomendas(encomendas, linhas, mapa_cliente, q):
    e = encomendas.copy().sort_values("id").reset_index(drop=True)
    e["data_hora"] = pd.to_datetime(e.data_hora)
    e["cliente_id"] = e.cliente_id.map(mapa_cliente)
    e["dq"] = [[] for _ in range(len(e))]

    # Q07 — ano impossível: corrigir pelo ano das encomendas vizinhas
    fora = (e.data_hora < pd.Timestamp(config.INICIO_PERIODO)) | (e.data_hora >= LIMITE_DATA)
    validas = e.data_hora.where(~fora)
    anterior, seguinte = validas.shift(1).ffill(), validas.shift(-1).bfill()
    excluir = set()
    for i in e.index[fora]:
        original, corrigida = e.at[i, "data_hora"], None
        for ano in sorted({anterior[i].year, seguinte[i].year} - {np.nan}):
            try:
                cand = original.replace(year=int(ano))
            except ValueError:
                continue
            if anterior[i] - pd.Timedelta(days=1) <= cand <= seguinte[i] + pd.Timedelta(days=1):
                corrigida = cand
                break
        if corrigida is None:
            excluir.add(e.at[i, "id"])
            q.registar("Q07", f"encomenda {e.at[i, 'id']}", "data_hora", original, "Excluída")
        else:
            e.at[i, "data_hora"] = corrigida
            e.at[i, "dq"].append("Q07")
            q.registar("Q07", f"encomenda {e.at[i, 'id']}", "data_hora", original, corrigida)

    # Q08 — encomendas duplicadas (cabeçalho e linhas iguais)
    assinatura = (linhas.sort_values(["encomenda_id", "produto_id", "id"])
                  .assign(t=lambda x: x.produto_id.astype(str) + "x" + x.quantidade.astype(str)
                          + "@" + x.preco_unitario.round(2).astype(str))
                  .groupby("encomenda_id").t.agg("|".join))
    e["assinatura"] = e.id.map(assinatura).fillna("")
    chave = ["data_hora", "cliente_id", "loja_id", "vendedor_id", "canal", "estado", "metodo_pagamento",
             "campanha_id", "assinatura"]
    k = e[chave].fillna(-1)
    duplicada = k.duplicated(keep="first")
    sobrevivente = e.groupby(k.apply(tuple, axis=1)).id.transform("min")
    valor_enc = (linhas.quantidade * linhas.preco_unitario).groupby(linhas.encomenda_id).sum()
    mapa_dup = {}
    for i in e.index[duplicada]:
        mapa_dup[e.at[i, "id"]] = sobrevivente[i]
        q.registar("Q08", f"encomenda {e.at[i, 'id']}", "id", e.at[i, "id"], f"duplicada de {sobrevivente[i]}",
                   valor_enc.get(e.at[i, "id"], 0))
    excluir |= set(mapa_dup)
    e = e[~e.id.isin(excluir)].reset_index(drop=True)
    return e, mapa_dup, excluir


def primeira_compra(encomendas):
    validas = encomendas[encomendas.estado != "Cancelada"].sort_values(["data_hora", "id"])
    primeiras = validas.drop_duplicates("cliente_id")
    return dict(zip(primeiras.cliente_id, primeiras.data_hora.dt.date)), set(primeiras.id)


# ---------------------------------------------------------------------------------------
# Campanhas
# ---------------------------------------------------------------------------------------
def dim_campanha(campanhas, lojas, q, cont):
    c = campanhas.copy()
    c["campanha_id"] = c.campanha_id.astype(int)
    c["data_inicio"] = pd.to_datetime(c.data_inicio).dt.date
    c["data_fim"] = pd.to_datetime(c.data_fim).dt.date
    c["duracao_dias"] = [(f - i).days + 1 for i, f in zip(c.data_inicio, c.data_fim)]
    c["lojas_abrangidas"] = c.lojas_abrangidas.astype(str).str.strip()
    c = chaves_sequenciais(c, "campanha_id", "sk_campanha")

    nomes = {n.casefold(): sk for n, sk in zip(lojas.nome_curto, lojas.sk_loja)}
    ponte = []
    for _, r in c.iterrows():
        abertas = lojas[lojas.data_abertura <= r.data_inicio]
        if r.canal == "Loja":
            por_canal = abertas[abertas.tipo_loja == "Física"]
        elif r.canal == "Online":
            por_canal = abertas[abertas.tipo_loja == "Online"]
        else:
            por_canal = abertas
        texto = r.lojas_abrangidas.casefold()
        if texto == "todas":
            sks = list(por_canal.sk_loja)
        elif texto.startswith("todas exceto"):
            excluidas = {nomes.get(x.strip()) for x in re.split(r"[;,]", texto.replace("todas exceto", ""))}
            sks = [s for s in por_canal.sk_loja if s not in excluidas]
        else:
            sks = []
            for nome in re.split(r"[;,]", texto):
                sk = nomes.get(nome.strip())
                if sk is None:
                    log.warning("Campanha %s: loja '%s' não reconhecida", r.campanha_id, nome.strip())
                elif sk in set(abertas.sk_loja):
                    sks.append(sk)
        ponte += [{"sk_campanha": r.sk_campanha, "sk_loja": s} for s in sks]
        lista = ", ".join(lojas.set_index("sk_loja").loc[sks, "nome_curto"])
        q.registar("Q21", f"campanha {r.campanha_id}", "lojas_abrangidas", r.lojas_abrangidas, lista)
        if r.data_fim > config.FIM_PERIODO:
            q.registar("Q22", f"campanha {r.campanha_id}", "data_fim", r.data_fim, None, r.orcamento)
    sem = pd.DataFrame([{"sk_campanha": 0, "campanha_id": 0, "nome": "Sem campanha", "canal": "N/A",
                         "data_inicio": None, "data_fim": None, "duracao_dias": None, "orcamento": None,
                         "lojas_abrangidas": None}])
    d = pd.concat([sem, c], ignore_index=True)
    cols = ["sk_campanha", "campanha_id", "nome", "canal", "data_inicio", "data_fim", "duracao_dias",
            "orcamento", "lojas_abrangidas"]
    d = d[cols].astype(object)
    d = d.where(pd.notna(d), None)
    cont.definir("campanhas_marketing.xlsx", "dim_campanha", lidos=len(campanhas), corrigidos=len(c),
                 carregados=len(d))
    cont.definir("campanhas_marketing.xlsx", "ponte_campanha_loja", lidos=len(campanhas), carregados=len(ponte))
    return d, pd.DataFrame(ponte)


# ---------------------------------------------------------------------------------------
# Factos de vendas
# ---------------------------------------------------------------------------------------
def fact_vendas(linhas, enc, excluidas, produtos_erp, d_produto, margem_global, chaves, devolucoes,
                ponte, d_campanha, primeiras_ids, q, cont):
    l = linhas.copy()
    lidos = len(l)
    l = l[~l.encomenda_id.isin(excluidas)]
    rej_encomenda = lidos - len(l)

    # Q12 — quantidade <= 0
    neg = l.quantidade <= 0
    for _, r in l[neg].iterrows():
        q.registar("Q12", f"linha {r.id}", "quantidade", r.quantidade, "Excluída", r.quantidade * r.preco_unitario)
    l = l[~neg].copy()
    l["dq"] = [[] for _ in range(len(l))]

    # Q11 — produto inexistente
    preco_tab = dict(zip(produtos_erp.id, produtos_erp.preco_venda))
    desconhecido = ~l.produto_id.isin(produtos_erp.id)
    for i in l.index[desconhecido]:
        q.registar("Q11", f"linha {l.at[i, 'id']}", "produto_id", l.at[i, "produto_id"], "-1 (desconhecido)",
                   l.at[i, "quantidade"] * l.at[i, "preco_unitario"])
        l.at[i, "dq"].append("Q11")

    # Q13 — preço zero
    zero = (l.preco_unitario <= 0) & ~desconhecido
    for i in l.index[zero]:
        novo = round(preco_tab[l.at[i, "produto_id"]] * (1 - l.at[i, "desconto_pct"] / 100), 2)
        q.registar("Q13", f"linha {l.at[i, 'id']}", "preco_unitario", l.at[i, "preco_unitario"], novo,
                   novo * l.at[i, "quantidade"])
        l.at[i, "preco_unitario"] = novo
        l.at[i, "dq"].append("Q13")

    # Atributos da encomenda
    e = enc.copy()
    e["data"] = e.data_hora.dt.normalize()
    l = l.merge(e[["id", "data", "data_hora", "cliente_id", "loja_id", "vendedor_id", "canal", "estado",
                   "metodo_pagamento", "campanha_id", "dq"]].rename(columns={"id": "encomenda_id", "dq": "dq_enc"}),
                on="encomenda_id")

    # Custo em vigor na data da encomenda (SCD2)
    versoes = d_produto[d_produto.produto_id > 0][["sk_produto", "produto_id", "custo_unitario",
                                                   "valido_de", "valido_ate"]].copy()
    versoes["valido_de"] = pd.to_datetime(versoes.valido_de)
    versoes["valido_ate"] = pd.to_datetime(versoes.valido_ate.where(versoes.valido_ate != FIM_ABERTO,
                                                                     date(2262, 1, 1)))
    l["linha_ord"] = np.arange(len(l))
    m = l[["linha_ord", "produto_id", "data"]].merge(versoes, on="produto_id", how="left")
    m = m[(m.data >= m.valido_de) & (m.data <= m.valido_ate)].drop_duplicates("linha_ord")
    l = l.merge(m[["linha_ord", "sk_produto", "custo_unitario"]], on="linha_ord", how="left")
    sem_versao = l.sk_produto.isna() & l.produto_id.isin(produtos_erp.id)
    if sem_versao.any():  # venda anterior ao primeiro custo conhecido: usar a versão mais antiga
        log.warning("%d linhas anteriores ao primeiro custo do produto; usada a versão mais antiga", sem_versao.sum())
        primeira = versoes.sort_values("valido_de").drop_duplicates("produto_id").set_index("produto_id")
        l.loc[sem_versao, "sk_produto"] = l.loc[sem_versao, "produto_id"].map(primeira.sk_produto)
        l.loc[sem_versao, "custo_unitario"] = l.loc[sem_versao, "produto_id"].map(primeira.custo_unitario)
    desconhecido = l.sk_produto.isna()
    l.loc[desconhecido, "sk_produto"] = -1
    l.loc[desconhecido, "custo_unitario"] = (l.loc[desconhecido, "preco_unitario"] * (1 - margem_global)).round(2)

    l["preco_tabela"] = l.produto_id.map(preco_tab)
    l["valor_venda"] = (l.quantidade * l.preco_unitario).round(2)
    l["valor_desconto"] = (l.quantidade * (l.preco_tabela - l.preco_unitario)).round(2).fillna(0)
    l["cmv"] = (l.quantidade * l.custo_unitario).round(2)

    # Q10 — campanha incoerente com a data ou a loja
    camp = d_campanha[d_campanha.campanha_id > 0].set_index("campanha_id")
    abrangidas = set(zip(ponte.sk_campanha, ponte.sk_loja))
    enc_camp = e[e.campanha_id.notna()]
    for _, r in enc_camp.iterrows():
        cid = int(r.campanha_id)
        if cid not in camp.index:
            q.registar("Q10", f"encomenda {r.id}", "campanha_id", cid, "campanha inexistente")
            continue
        cp = camp.loc[cid]
        motivo = None
        if not (cp.data_inicio <= r.data_hora.date() <= cp.data_fim):
            motivo = "fora das datas"
        elif (cp.sk_campanha, chaves["loja"][r.loja_id]) not in abrangidas:
            motivo = "loja não abrangida"
        if motivo:
            q.registar("Q10", f"encomenda {r.id}", "campanha_id", cid, motivo)
            l.loc[l.encomenda_id == r.id, "dq_enc"] = l.loc[l.encomenda_id == r.id, "dq_enc"].map(lambda x: x + ["Q10"])

    # Q09 — Devolvida sem devolução
    sem_dev = e[(e.estado == "Devolvida") & ~e.id.isin(devolucoes.encomenda_id)]
    for _, r in sem_dev.iterrows():
        q.registar("Q09", f"encomenda {r.id}", "estado", "Devolvida", "sem registo de devolução")
    l.loc[l.encomenda_id.isin(sem_dev.id), "dq_enc"] = l.loc[l.encomenda_id.isin(sem_dev.id), "dq_enc"].map(
        lambda x: x + ["Q09"])

    l["sk_data"] = sk_data(l.data)
    l["hora"] = l.data_hora.dt.hour
    l["sk_loja"] = l.loja_id.map(chaves["loja"])
    l["sk_canal"] = l.canal.map(chaves["canal"])
    l["sk_cliente"] = l.cliente_id.map(chaves["cliente"])
    l["sk_vendedor"] = l.vendedor_id.fillna(0).astype(int).map(chaves["vendedor"])
    l["sk_campanha"] = l.campanha_id.fillna(0).astype(int).map(chaves["campanha"]).fillna(0)
    l["encomenda_valida"] = (l.estado != "Cancelada").astype(int)
    l["primeira_encomenda"] = l.encomenda_id.isin(primeiras_ids).astype(int)
    l["dq_flag"] = [",".join(sorted(set(a + b))) or None for a, b in zip(l.dq, l.dq_enc)]
    l = l.rename(columns={"id": "linha_id"})
    cols = ["linha_id", "encomenda_id", "sk_data", "hora", "sk_loja", "sk_canal", "sk_cliente", "sk_vendedor",
            "sk_produto", "sk_campanha", "estado", "encomenda_valida", "metodo_pagamento", "quantidade",
            "preco_unitario", "desconto_pct", "preco_tabela", "valor_venda", "valor_desconto", "custo_unitario",
            "cmv", "primeira_encomenda", "dq_flag"]
    f = l[cols].copy()
    for col in ["sk_produto", "sk_campanha", "sk_vendedor"]:
        f[col] = f[col].astype(int)
    cont.definir("ERP linhas_encomenda", "fact_vendas", lidos=lidos, rejeitados=lidos - len(f),
                 corrigidos=zero.sum(), carregados=len(f))
    log.info("fact_vendas: %d linhas rejeitadas por pertencerem a encomendas excluídas (Q07/Q08), %d por Q12",
             rej_encomenda, neg.sum())
    return f, l[["linha_id", "encomenda_id", "produto_id", "data"]]


def dim_vendedor(vendedores, lojas, enc, q, cont):
    v = vendedores.copy()
    loja_nome = dict(zip(lojas.loja_id, lojas.nome_curto))
    v["loja"] = v.loja_id.map(loja_nome)
    v["data_admissao"] = pd.to_datetime(v.data_admissao).dt.date
    primeira = enc.groupby("vendedor_id").data_hora.min().dt.date
    v["primeira_venda"] = v.id.map(primeira)
    v["admissao_inconsistente"] = (v.primeira_venda < v.data_admissao).astype(int)
    for _, r in v[v.admissao_inconsistente == 1].iterrows():
        q.registar("Q06", f"vendedor {r.id}", "data_admissao", r.data_admissao, f"1.ª venda {r.primeira_venda}")
    v = v.rename(columns={"id": "vendedor_id"})
    v = chaves_sequenciais(v, "vendedor_id", "sk_vendedor")
    sem = pd.DataFrame([{"sk_vendedor": 0, "vendedor_id": 0, "nome": "Sem vendedor (online)", "loja": "Online",
                         "data_admissao": None, "ativo": 1, "admissao_inconsistente": 0}])
    d = pd.concat([sem, v], ignore_index=True)
    cols = ["sk_vendedor", "vendedor_id", "nome", "loja", "data_admissao", "ativo", "admissao_inconsistente"]
    cont.definir("ERP vendedores", "dim_vendedor", lidos=len(vendedores), carregados=len(d))
    d = d[cols].astype(object)
    return d.where(pd.notna(d), None)


def fact_devolucoes(devolucoes, mapa_dup, linhas_fact, f_vendas, enc, chaves, motivos, q, cont):
    d = devolucoes.copy()
    d["encomenda_id"] = d.encomenda_id.map(lambda x: mapa_dup.get(x, x))
    alvo = linhas_fact.drop_duplicates(["encomenda_id", "produto_id"])
    d = d.merge(alvo[["linha_id", "encomenda_id", "produto_id"]], on=["encomenda_id", "produto_id"], how="left")
    sem_linha = d.linha_id.isna()
    for _, r in d[sem_linha].iterrows():
        q.registar("Q14", f"devolução {r.id}", "encomenda_id", r.encomenda_id, "Excluída")
    d = d[~sem_linha].copy()
    d["linha_id"] = d.linha_id.astype(int)
    d["data"] = pd.to_datetime(d.data)
    for _, r in d[d.data > pd.Timestamp(config.FIM_PERIODO)].iterrows():
        q.registar("Q15", f"devolução {r.id}", "data", r.data.date(), None)
    fv = f_vendas.set_index("linha_id")
    d = d.join(fv[["sk_data", "sk_loja", "sk_canal", "sk_cliente", "sk_produto", "preco_unitario", "custo_unitario"]],
               on="linha_id", rsuffix="_v")
    data_enc = enc.set_index("id").data_hora.dt.normalize()
    d["sk_data_encomenda"] = d.sk_data
    d["sk_data"] = sk_data(d.data)
    d["sk_motivo"] = d.motivo.map(chaves["motivo"])
    d["valor_devolvido"] = (d.quantidade * d.preco_unitario).round(2)
    d["cmv_devolvido"] = (d.quantidade * d.custo_unitario).round(2)
    d["dias_ate_devolucao"] = (d.data - d.encomenda_id.map(data_enc)).dt.days
    d = d.rename(columns={"id": "devolucao_id"})
    cols = ["devolucao_id", "encomenda_id", "linha_id", "sk_data", "sk_data_encomenda", "sk_loja", "sk_canal",
            "sk_cliente", "sk_produto", "sk_motivo", "quantidade", "valor_devolvido", "cmv_devolvido",
            "dias_ate_devolucao"]
    cont.definir("ERP devolucoes", "fact_devolucoes", lidos=len(devolucoes), rejeitados=sem_linha.sum(),
                 carregados=len(d))
    return d[cols]


def dim_motivo(devolucoes):
    motivos = sorted(devolucoes.motivo.dropna().unique())
    return pd.DataFrame({"sk_motivo": range(1, len(motivos) + 1), "motivo": motivos,
                         "responsabilidade": [RESPONSABILIDADE_MOTIVO.get(m, "Empresa") for m in motivos]})


# ---------------------------------------------------------------------------------------
# Metas
# ---------------------------------------------------------------------------------------
def fact_metas(folhas, lojas, q, cont):
    nomes = {n.casefold(): sk for n, sk in zip(lojas.nome_curto, lojas.sk_loja)}
    linhas, lidos, rejeitados = [], 0, 0
    for ano, df in folhas.items():
        df = df.copy()
        df["Loja"] = df.Loja.astype(str).str.strip()
        fim = df.index[df.Loja.str.casefold() == "total"]
        corpo = df.loc[: fim[0] - 1] if len(fim) else df
        corpo = corpo[corpo.Loja.ne("nan")]
        total = df.loc[fim[0]] if len(fim) else None
        meses = list(df.columns[1:13])
        valores = corpo[meses].apply(pd.to_numeric, errors="coerce")
        # Q20 — controlo dos totais
        if "Total" in df.columns:
            for loja, soma, tot in zip(corpo.Loja, valores.sum(axis=1), corpo["Total"]):
                if pd.notna(tot) and abs(soma - tot) > 0.5:
                    q.registar("Q20", f"{ano} {loja}", "Total", tot, soma, tot - soma)
        if total is not None:
            for mes, soma in zip(meses, valores.sum()):
                if abs(soma - float(total[mes])) > 0.5:
                    q.registar("Q20", f"{ano} Total {mes}", mes, total[mes], soma, float(total[mes]) - soma)
        for (_, r), (_, val) in zip(corpo.iterrows(), valores.iterrows()):
            sk = nomes.get(r.Loja.casefold())
            for n, mes in enumerate(meses, start=1):
                lidos += 1
                if sk is None:
                    rejeitados += 1
                    log.warning("Metas %s: loja '%s' não reconhecida", ano, r.Loja)
                    continue
                if pd.isna(val[mes]):
                    rejeitados += 1
                    q.registar("Q19", f"{r.Loja} {ano}-{n:02d}", "meta", None, "sem meta")
                    continue
                linhas.append({"sk_loja": sk, "sk_data": ano * 10000 + n * 100 + 1, "meta_valor": float(val[mes])})
    f = pd.DataFrame(linhas)
    cont.definir("metas_vendas.xlsx", "fact_metas", lidos=lidos, rejeitados=rejeitados, carregados=len(f))
    return f


# ---------------------------------------------------------------------------------------
# Inquérito de satisfação
# ---------------------------------------------------------------------------------------
def fact_satisfacao(inq, enc, mapa_dup, chaves, lojas, q, cont):
    s = inq.copy()
    lidos = len(s)
    # Q23 (1.ª parte) — duplicados exatos
    dup = s.drop(columns="resposta_id").duplicated(keep="first")
    for _, r in s[dup].iterrows():
        q.registar("Q23", f"linha {r.resposta_id}", "encomenda_id", r.encomenda_id, "Excluída (cópia exata)")
    s = s[~dup]
    # Q24 — nota fora da escala
    nota = pd.to_numeric(s.nota, errors="coerce")
    fora = nota.isna() | (nota < 0) | (nota > 10) | (nota != nota.round())
    for _, r in s[fora].iterrows():
        q.registar("Q24", f"linha {r.resposta_id}", "nota", r.nota, "Excluída")
    s = s[~fora].copy()
    s["nota"] = s.nota.astype(int)
    # Q28 — encomenda inexistente ou excluída
    s["encomenda_id"] = s.encomenda_id.astype(int).map(lambda x: mapa_dup.get(x, x))
    e = enc.set_index("id")
    orfa = ~s.encomenda_id.isin(e.index)
    for _, r in s[orfa].iterrows():
        q.registar("Q28", f"linha {r.resposta_id}", "encomenda_id", r.encomenda_id, "Excluída")
    s = s[~orfa]
    # Q23 (2.ª parte) — mais de uma resposta válida para a mesma encomenda
    dup = s.sort_values("resposta_id").duplicated("encomenda_id", keep="first")
    for _, r in s[dup].iterrows():
        q.registar("Q23", f"linha {r.resposta_id}", "encomenda_id", r.encomenda_id, "Excluída (encomenda repetida)")
    s = s[~dup].copy()
    s["dq"] = [[] for _ in range(len(s))]

    # Q25 — data de resposta impossível
    s["data_resposta"] = pd.to_datetime(s.data_resposta)
    s["data_enc"] = s.encomenda_id.map(e.data_hora).dt.normalize()
    excluir = []
    for i, r in s.iterrows():
        dias = (r.data_resposta - r.data_enc).days
        if 0 <= dias <= 30:
            continue
        nova = None
        for ano in (r.data_enc.year, r.data_enc.year + 1):
            try:
                cand = r.data_resposta.replace(year=ano)
            except ValueError:
                continue
            if 0 <= (cand - r.data_enc).days <= 30:
                nova = cand
                break
        q.registar("Q25", f"linha {r.resposta_id}", "data_resposta", r.data_resposta.date(),
                   nova.date() if nova is not None else "Excluída")
        if nova is None:
            excluir.append(i)
        else:
            s.at[i, "data_resposta"] = nova
            s.at[i, "dq"].append("Q25")
    s = s.drop(index=excluir)

    # Q26/Q27 — loja em texto livre: prevalece a loja da encomenda no ERP
    curto = dict(zip(lojas.loja_id, lojas.nome_curto))
    s["loja_erp"] = s.encomenda_id.map(e.loja_id).map(curto)
    texto = s.loja.astype(str).str.strip()
    normal = texto.str.casefold().str.replace(r"^loja\s+", "", regex=True)
    for i, r in s.iterrows():
        if normal[i] != r.loja_erp.casefold():
            q.registar("Q27", f"linha {r.resposta_id}", "loja", r.loja, r.loja_erp)
            s.at[i, "dq"].append("Q27")
        elif r.loja != r.loja_erp:
            q.registar("Q26", f"linha {r.resposta_id}", "loja", r.loja, r.loja_erp)
            s.at[i, "dq"].append("Q26")

    s["classe_nps"] = np.select([s.nota >= 9, s.nota >= 7], ["Promotor", "Passivo"], "Detrator")
    s["promotor"] = (s.nota >= 9).astype(int)
    s["detrator"] = (s.nota <= 6).astype(int)
    s["recomendaria"] = s.recomendaria.map({"Sim": 1, "Não": 0})
    s["sk_data"] = sk_data(s.data_resposta)
    s["sk_data_encomenda"] = sk_data(s.data_enc)
    s["sk_loja"] = s.encomenda_id.map(e.loja_id).map(chaves["loja"])
    s["sk_canal"] = s.encomenda_id.map(e.canal).map(chaves["canal"])
    s["sk_cliente"] = s.encomenda_id.map(e.cliente_id).map(chaves["cliente"])
    s["loja_texto_fonte"] = s.loja
    s["comentario"] = s.comentario.where(s.comentario.notna(), None)
    s["dq_flag"] = [",".join(x) or None for x in s.dq]
    cols = ["resposta_id", "encomenda_id", "sk_data", "sk_data_encomenda", "sk_loja", "sk_canal", "sk_cliente",
            "nota", "classe_nps", "promotor", "detrator", "recomendaria", "comentario", "loja_texto_fonte", "dq_flag"]
    f = s[cols].astype(object)
    f = f.where(pd.notna(f), None)
    corrigidos = sum(1 for x in s.dq if "Q25" in x or "Q26" in x)
    cont.definir("inquerito_satisfacao.xlsx", "fact_satisfacao", lidos=lidos, rejeitados=lidos - len(f),
                 corrigidos=corrigidos, carregados=len(f))
    return f


# ---------------------------------------------------------------------------------------
# Tráfego web
# ---------------------------------------------------------------------------------------
def dim_fonte_web(trafego):
    fontes = sorted(trafego.fonte.str.strip().unique())
    return pd.DataFrame({"sk_fonte": range(1, len(fontes) + 1), "fonte": fontes,
                         "tipo_fonte": ["Paga" if f == "Pago" else "Não paga" for f in fontes]})


def fact_trafego(trafego, d_fonte, q, cont):
    t = trafego.copy()
    lidos = len(t)
    t["fonte"] = t.fonte.str.strip()
    dup = t.duplicated(["data", "fonte"], keep="first")
    if dup.any():
        log.warning("Tráfego: %d linhas repetidas para o mesmo dia e fonte foram ignoradas", dup.sum())
    t = t[~dup]
    todos = pd.date_range(config.INICIO_PERIODO, config.FIM_PERIODO, freq="D")
    for dia in sorted(set(todos) - set(t.data)):
        q.registar("Q29", dia.date(), "data", None, "sem registo")
    t = t[(t.data >= pd.Timestamp(config.INICIO_CALENDARIO)) & (t.data <= pd.Timestamp(config.FIM_CALENDARIO))]
    t["sk_data"] = sk_data(t.data)
    t["sk_fonte"] = t.fonte.map(dict(zip(d_fonte.fonte, d_fonte.sk_fonte)))
    t["conversoes"] = (t.sessoes * t.taxa_conversao_pct / 100).round(2)
    cols = ["sk_data", "sk_fonte", "sessoes", "utilizadores", "taxa_conversao_pct", "conversoes", "receita_estimada"]
    cont.definir("trafego_web.csv", "fact_trafego_web", lidos=lidos, rejeitados=lidos - len(t), carregados=len(t))
    return t[cols]


# ---------------------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------------------
def transformar(fontes, q, cont):
    erp = fontes["erp"]
    t = {}
    t["dim_data"] = dim_data(fontes["feriados"], cont)
    t["dim_loja"] = dim_loja(erp["lojas"], cont)
    t["dim_canal"] = dim_canal(erp["encomendas"])
    t["dim_produto"], margem_global = dim_produto(erp["produtos"], erp["categorias"], fontes["custos"], q, cont)

    clientes_dedup, mapa_cliente = deduplicar_clientes(erp["clientes"], q)
    enc, mapa_dup, excluidas = tratar_encomendas(erp["encomendas"], erp["linhas_encomenda"], mapa_cliente, q)
    cont.definir("ERP encomendas", "fact_vendas (encomendas)", lidos=len(erp["encomendas"]),
                 rejeitados=len(excluidas), corrigidos=q.contar("Q07") - sum(
                     1 for r in q.registos if r["regra_id"] == "Q07" and r["valor_final"] == "Excluída"),
                 carregados=len(enc))
    compra, primeiras_ids = primeira_compra(enc)
    distritos_loja = set(t["dim_loja"].loc[t["dim_loja"].tipo_loja == "Física", "distrito"])
    t["dim_cliente"] = dim_cliente(clientes_dedup, len(erp["clientes"]), compra, distritos_loja, q, cont)
    t["dim_vendedor"] = dim_vendedor(erp["vendedores"], t["dim_loja"], enc, q, cont)
    t["dim_campanha"], t["ponte_campanha_loja"] = dim_campanha(fontes["campanhas"], t["dim_loja"], q, cont)
    t["dim_motivo_devolucao"] = dim_motivo(erp["devolucoes"])
    t["dim_fonte_web"] = dim_fonte_web(fontes["trafego"])

    chaves = {
        "loja": dict(zip(t["dim_loja"].loja_id, t["dim_loja"].sk_loja)),
        "canal": dict(zip(t["dim_canal"].canal, t["dim_canal"].sk_canal)),
        "cliente": dict(zip(t["dim_cliente"].cliente_id, t["dim_cliente"].sk_cliente)),
        "vendedor": dict(zip(t["dim_vendedor"].vendedor_id, t["dim_vendedor"].sk_vendedor)),
        "campanha": dict(zip(t["dim_campanha"].campanha_id, t["dim_campanha"].sk_campanha)),
        "motivo": dict(zip(t["dim_motivo_devolucao"].motivo, t["dim_motivo_devolucao"].sk_motivo)),
    }
    t["fact_vendas"], linhas_fact = fact_vendas(
        erp["linhas_encomenda"], enc, excluidas, erp["produtos"], t["dim_produto"], margem_global, chaves,
        erp["devolucoes"], t["ponte_campanha_loja"], t["dim_campanha"], primeiras_ids, q, cont)
    t["fact_devolucoes"] = fact_devolucoes(erp["devolucoes"], mapa_dup, linhas_fact, t["fact_vendas"], enc,
                                           chaves, t["dim_motivo_devolucao"], q, cont)
    t["fact_metas"] = fact_metas(fontes["metas"], t["dim_loja"], q, cont)
    t["fact_trafego_web"] = fact_trafego(fontes["trafego"], t["dim_fonte_web"], q, cont)
    t["fact_satisfacao"] = fact_satisfacao(fontes["inquerito"], enc, mapa_dup, chaves, t["dim_loja"], q, cont)
    return t
