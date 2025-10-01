
# app.py
# Streamlit – Calendário de validação a partir de Excel "matriz de meses" (PT-BR)
# Entrada: 1ª coluna = nome do site; Demais colunas = "Mês Ano" (ex.: "Outubro 2025").
# Cada célula contém dias separados por vírgula (ex.: "10,12,13,20").
# Saída: Tabela editável de validação (Aprovada/Rejeitada/Pendente) + Calendário colorido + Exportar Excel.
# Visual: colore somente dias com passagem, mostra bolinhas/contagens por status e tooltip com sites.
# Stack: Excel (local ou URL raw do GitHub) + Streamlit. Sem banco, tudo em memória.
# Autor: ChatGPT – MIT License

import io
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import requests

st.set_page_config(page_title="Calendário de Passagens – Validação", page_icon="🛰️", layout="wide")
st.title("🛰️ Calendário de Validação de Passagens (Excel → Calendário)")
st.caption("Planilha no formato 'matriz de meses' → tabela de validação → calendário colorido → exportar Excel.")

PT_MESES: Dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

STATUS_OPCOES = ["Pendente", "Aprovada", "Rejeitada"]

if "df_validado" not in st.session_state:
    st.session_state.df_validado = None

def detectar_colunas_mes(df: pd.DataFrame) -> List[str]:
    cols_mes = []
    for c in df.columns:
        s = str(c).strip().replace('\xa0', ' ').lower()
        partes = s.split()
        if len(partes) == 2 and partes[0] in PT_MESES:
            try:
                _ = int(partes[1])
                cols_mes.append(c)
            except Exception:
                pass
    return cols_mes

def normalizar_planilha_matriz(df_raw: pd.DataFrame, col_site: Optional[str] = None) -> pd.DataFrame:
    df = df_raw.copy()
    df.columns = [str(c).strip().replace('\xa0', ' ') for c in df.columns]
    if col_site is None:
        col_site = df.columns[0]
    if col_site != "site_nome":
        df = df.rename(columns={col_site: "site_nome"})
    cols_mes = detectar_colunas_mes(df)
    if not cols_mes:
        raise ValueError("Não foram encontradas colunas no formato 'Mês Ano' (ex.: 'Outubro 2025').")
    reg = []
    for _, row in df.iterrows():
        site = row["site_nome"]
        for cm in cols_mes:
            dias_str = row[cm]
            if pd.isna(dias_str):
                continue
            mes_nome, ano_str = str(cm).strip().split()
            mes_num = PT_MESES.get(mes_nome.lower())
            ano = int(ano_str)
            dias = [d.strip() for d in str(dias_str).split(',') if d.strip() != ""]
            for d in dias:
                try:
                    di = int(d)
                    dt = pd.Timestamp(year=ano, month=mes_num, day=di)
                    reg.append({"site_nome": site, "data": dt.date(), "status": "Pendente", "observacao": "", "validador": "", "data_validacao": pd.NaT})
                except Exception:
                    continue
    df_expl = pd.DataFrame(reg)
    if df_expl.empty:
        raise ValueError("Nenhuma data válida foi encontrada nas células (confira se há dias como '10,12,13').")
    df_expl["yyyymm"] = pd.to_datetime(df_expl["data"]).dt.strftime("%Y-%m")
    return df_expl.sort_values(["data", "site_nome"]).reset_index(drop=True)

def montar_calendario(df_mes: pd.DataFrame, mes_ano: str, only_color_with_events: bool = True, show_badges: bool = True) -> go.Figure:
    primeiro = pd.to_datetime(f"{mes_ano}-01")
    ultimo = (primeiro + pd.offsets.MonthEnd(1))
    dias = pd.date_range(primeiro, ultimo, freq="D")
    if df_mes.empty:
        agg = pd.DataFrame(columns=["data","aprovadas","rejeitadas","pendentes","sites"])
    else:
        agg = (df_mes.assign(data=pd.to_datetime(df_mes["data"]).dt.date)
                     .groupby("data")
                     .agg(aprovadas=("status", lambda s: (s == "Aprovada").sum()),
                          rejeitadas=("status", lambda s: (s == "Rejeitada").sum()),
                          pendentes=("status", lambda s: (s == "Pendente").sum()),
                          sites=("site_nome", lambda s: sorted(set(s))))
                     .reset_index())
    info_map = {row["data"]: row for _, row in agg.iterrows()}

    def cor_do_dia(d: pd.Timestamp) -> str:
        inf = info_map.get(d.date())
        if inf is None:
            return "#ECEFF1" if only_color_with_events else "#B0BEC5"
        if inf["rejeitadas"] > 0:
            return "#c62828"
        if inf["pendentes"] > 0 and inf["aprovadas"] == 0:
            return "#B0BEC5"
        return "#2e7d32"

    def weekday_dom(d: pd.Timestamp) -> int:
        return (d.weekday() + 1) % 7
    grid = np.full((6, 7), None, dtype=object)
    week = 0
    for d in dias:
        col = weekday_dom(d)
        if col == 0 and d.day != 1:
            week += 1
        grid[week, col] = d
    fig = go.Figure()
    for r in range(6):
        for c in range(7):
            d = grid[r, c]
            if d is None:
                continue
            fill = cor_do_dia(d)
            fig.add_shape(type="rect", x0=c, x1=c+1, y0=5-r, y1=6-r, line=dict(width=1, color="#90A4AE"), fillcolor=fill)
            fig.add_annotation(x=c+0.05, y=5-r+0.85, text=str(d.day), showarrow=False, xanchor="left", yanchor="top", font=dict(size=12))
            inf = info_map.get(d.date())
            if show_badges and (inf is not None):
                y0 = 5-r+0.18
                badges = []
                if inf["aprovadas"] > 0: badges.append(("●", "#2e7d32"))
                if inf["rejeitadas"] > 0: badges.append(("●", "#c62828"))
                if inf["pendentes"] > 0: badges.append(("●", "#607D8B"))
                x0 = c+0.08
                for ch, colr in badges:
                    fig.add_annotation(x=x0, y=y0, text=f"<span style='color:{colr}'>{ch}</span>", showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=12))
                    x0 += 0.12
                txt_cnt = f"{inf['aprovadas']}A/{inf['rejeitadas']}R/{inf['pendentes']}P"
                fig.add_annotation(x=c+0.95, y=5-r+0.18, text=txt_cnt, showarrow=False, xanchor="right", yanchor="bottom", font=dict(size=10))
            if inf is not None:
                sites_txt = ", ".join(inf["sites"]) if inf["sites"] else "-"
                hover = (f"{d.strftime('%Y-%m-%d')}<br>"
                         f"Aprovadas: {inf['aprovadas']} | Rejeitadas: {inf['rejeitadas']} | Pendentes: {inf['pendentes']}<br>"
                         f"Sites: {sites_txt}")
                fig.add_trace(go.Scatter(x=[c+0.5], y=[5-r+0.5], mode="markers", marker=dict(size=1, color="rgba(0,0,0,0)"), hovertemplate=hover, showlegend=False))
    fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
    fig.update_layout(height=460, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="white", plot_bgcolor="white")
    return fig

def exportar_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df_exp = df.copy()
    df_exp["data"] = pd.to_datetime(df_exp["data"]).dt.strftime("%Y-%m-%d")
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df_exp.to_excel(writer, index=False, sheet_name="validacao")
    buf.seek(0)
    return buf.read()

with st.expander("📥 Carregar planilha", expanded=True):
    c1, c2 = st.columns([2,1])
    with c1:
        up = st.file_uploader("Envie seu Excel (.xlsx)", type=["xlsx"])
    with c2:
        url_raw = st.text_input("...ou cole a URL 'raw' do GitHub", placeholder="https://raw.githubusercontent.com/usuario/repo/main/cronograma.xlsx")
    col_site_hint = st.text_input("Nome da coluna do site (opcional)", value="") or None
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Carregar do upload"):
            if not up:
                st.warning("Envie um arquivo .xlsx.")
            else:
                try:
                    df_raw = pd.read_excel(up)
                    df_raw.columns = [str(c).strip().replace('\xa0', ' ') for c in df_raw.columns]
                    st.session_state.df_validado = normalizar_planilha_matriz(df_raw, col_site_hint)
                    st.success("Planilha carregada!")
                except Exception as e:
                    st.error(f"Erro: {e}")
    with b2:
        if st.button("Carregar da URL GitHub"):
            if not url_raw:
                st.warning("Informe a URL raw do GitHub.")
            else:
                try:
                    r = requests.get(url_raw, timeout=20); r.raise_for_status()
                    df_raw = pd.read_excel(io.BytesIO(r.content))
                    df_raw.columns = [str(c).strip().replace('\xa0', ' ') for c in df_raw.columns]
                    st.session_state.df_validado = normalizar_planilha_matriz(df_raw, col_site_hint)
                    st.success("Planilha carregada da URL!")
                except Exception as e:
                    st.error(f"Erro: {e}")
    with b3:
        if st.button("Gerar exemplo sintético"):
            exemplo = pd.DataFrame({
                "Site": ["UPGN Cabiunas", "UPGN Cacimbas", "P-68"],
                "Outubro 2025": ["10,12,13,20", "10,12,13,21", "10,12,13,22"],
                "Novembro 2025": ["10,12,13,21", "10,12,13,22", "10,12,13,23"],
                "Dezembro 2025": ["10,12,13,22", "10,12,13,23", "10,12,13,24"],
            })
            st.session_state.df_validado = normalizar_planilha_matriz(exemplo, col_site="Site")
            st.success("Exemplo carregado!")

if st.session_state.df_validado is None:
    st.info("Carregue seu Excel para continuar.")
    st.stop()

with st.sidebar:
    st.header("Filtros")
    dfv = st.session_state.df_validado
    sites = sorted(dfv["site_nome"].unique())
    site_sel = st.multiselect("Sites", options=sites, default=sites)
    meses = sorted(dfv["yyyymm"].unique())
    mes_ano = st.selectbox("Mês", options=meses, index=max(0, len(meses)-1))
    only_color_with_events = st.checkbox("Colorir só dias com passagem", value=True)
    show_badges = st.checkbox("Mostrar bolinhas/contagem", value=True)

mask = dfv["site_nome"].isin(site_sel) & (dfv["yyyymm"] == mes_ano)
fdf = dfv.loc[mask].copy().sort_values(["data", "site_nome"]) if not dfv.empty else dfv.copy()

st.subheader("Tabela de passagens para validar")
editavel = fdf[["site_nome", "data", "status", "observacao", "validador", "data_validacao"]].copy()
editavel["data"] = pd.to_datetime(editavel["data"]).dt.strftime("%Y-%m-%d")
editavel["status"] = editavel["status"].astype("string")
editavel["observacao"] = editavel["observacao"].astype("string")
editavel["validador"] = editavel["validador"].astype("string")
editavel["data_validacao"] = editavel["data_validacao"].apply(lambda x: "" if pd.isna(x) else pd.to_datetime(x).strftime("%Y-%m-%d %H:%M:%S")).astype("string")

edited = st.data_editor(
    editavel,
    num_rows="fixed",
    use_container_width=True,
    column_config={
        "status": st.column_config.SelectboxColumn(label="Status", options=STATUS_OPCOES),
        "observacao": st.column_config.TextColumn(width="medium"),
        "validador": st.column_config.TextColumn(width="small"),
        "data_validacao": st.column_config.TextColumn(help="Preenchido ao aprovar/rejeitar"),
    },
    key="editor_v2",
)

if not edited.equals(editavel):
    base = st.session_state.df_validado
    edited_tmp = edited.copy()
    edited_tmp["data"] = pd.to_datetime(edited_tmp["data"]).dt.date
    keys = ["site_nome", "data"]
    upd_cols = ["status", "observacao", "validador"]
    base = base.drop(columns=upd_cols, errors="ignore").merge(edited_tmp[keys + upd_cols], on=keys, how="left")
    mudou = base["status"].isin(["Aprovada", "Rejeitada"]) & base["data_validacao"].isna()
    agora = pd.Timestamp.utcnow()
    base.loc[mudou, "data_validacao"] = agora
    st.session_state.df_validado = base
    dfv = base
    mask = dfv["site_nome"].isin(site_sel) & (dfv["yyyymm"] == mes_ano)
    fdf = dfv.loc[mask].copy().sort_values(["data", "site_nome"]) if not dfv.empty else dfv.copy()

st.subheader("Calendário do mês selecionado")
fig = montar_calendario(fdf, mes_ano, only_color_with_events=only_color_with_events, show_badges=show_badges)
st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

st.markdown("---")
st.subheader("Exportar")
colA, colB = st.columns([1,2])
with colA:
    nome_arquivo = st.text_input("Nome do arquivo", value="passagens_validado.xlsx")
with colB:
    xlsb = exportar_excel(st.session_state.df_validado)
    st.download_button("Baixar Excel validado", data=xlsb, file_name=nome_arquivo, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with st.expander("ℹ️ Notas e dicas", expanded=False):
    st.markdown(
        "- Aceita meses em **português** (Janeiro ... Dezembro) no cabeçalho, com **ano** (ex.: `Outubro 2025`).\n"
        "- As células devem conter **dias separados por vírgula** (p.ex. `10,12,13`). Espaços são ignorados.\n"
        "- A validação registra automaticamente a **data/hora UTC** em `data_validacao` ao marcar **Aprovada** ou **Rejeitada**.\n"
        "- Para usar com **GitHub**, clique em **Raw** no arquivo `.xlsx` e cole a URL aqui para carregar; depois exporte e faça o upload manual do validado.\n"
        "- Se preferir manter histórico, suba os validados com sufixo de data (ex.: `validado_2025-10-01.xlsx`)."
    )

st.success("Pronto! Coloque este app no seu GitHub e rode `streamlit run app.py`.")


