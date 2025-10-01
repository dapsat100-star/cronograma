
# app.py — Calendário + validação (sim/não) + GitHub + Auto-load do último XLSX
import io
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import requests
import base64, json, datetime as dt

st.set_page_config(page_title="Calendário de Passagens – Validação", page_icon="🛰️", layout="wide")
st.title("🛰️ Calendário de Validação (checkbox sim/nao + GitHub)")
st.caption("Marque **sim/nao**, clique **Salvar** e o app comita o Excel no GitHub. Ao abrir, carrega o último snapshot automaticamente.")

# ===== GitHub: salvar snapshots =====
def _gh_headers():
    return {
        "Authorization": f"Bearer {st.secrets['github_token']}",
        "Accept": "application/vnd.github+json",
    }

def gh_get_file_sha(path: str):
    repo = st.secrets["github_repo"]
    branch = st.secrets.get("github_branch", "main")
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    r = requests.get(url, headers=_gh_headers(), timeout=20)
    return r.json().get("sha") if r.status_code == 200 else None

def gh_put_file(path: str, content_bytes: bytes, message: str, sha: Optional[str] = None):
    repo = st.secrets["github_repo"]
    branch = st.secrets.get("github_branch", "main")
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Falha ao salvar no GitHub ({r.status_code}): {r.text}")
    return r.json()

def gh_save_snapshot(xls_bytes: bytes, author: Optional[str] = None) -> dict:
    root = st.secrets.get("gh_data_root", "data/validado")
    now = dt.datetime.utcnow()
    yyyy = now.strftime("%Y"); mm = now.strftime("%m"); stamp = now.strftime("%Y%m%d-%H%M%S")
    excel_rel_path = f"{root}/{yyyy}/{mm}/validado-{stamp}.xlsx"
    gh_put_file(excel_rel_path, xls_bytes, f"[streamlit] snapshot {stamp} (autor={author or 'anon'})", None)
    latest = {"saved_at_utc": now.isoformat()+"Z", "author": author or "", "path": excel_rel_path}
    latest_path = f"{root}/latest.json"
    sha_old = gh_get_file_sha(latest_path)
    gh_put_file(latest_path, json.dumps(latest, ensure_ascii=False, indent=2).encode("utf-8"),
                f"[streamlit] update latest -> {excel_rel_path}", sha_old)
    return latest

# ===== AUTO-LOAD: último XLSX salvo no GitHub =====
def _gh_repo():    return st.secrets["github_repo"]
def _gh_branch():  return st.secrets.get("github_branch", "main")
def _gh_root():    return st.secrets.get("gh_data_root", "data/validado")

def _list_contents(path: str):
    url = f"https://api.github.com/repos/{_gh_repo()}/contents/{path}?ref={_gh_branch()}"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def _list_all_xlsx(path: str):
    items = _list_contents(path)
    files = []
    for it in items:
        if it["type"] == "file" and it["name"].lower().endswith(".xlsx"):
            files.append(it["path"])
        elif it["type"] == "dir":
            files.extend(_list_all_xlsx(it["path"]))
    return files

def load_latest_snapshot_df() -> Optional[pd.DataFrame]:
    try:
        all_files = _list_all_xlsx(_gh_root())
        if not all_files:
            return None
        all_files.sort(reverse=True)  # timestamp no nome => ordem desc
        latest = all_files[0]
        raw = f"https://raw.githubusercontent.com/{_gh_repo()}/{_gh_branch()}/{latest}"
        df = pd.read_excel(raw)
        keep = ["site_nome","data","status","observacao","validador","data_validacao"]
        df = df[[c for c in keep if c in df.columns]].copy()
        df["data"] = pd.to_datetime(df["data"], errors="coerce").dt.date
        if "data_validacao" in df.columns:
            df["data_validacao"] = pd.to_datetime(df["data_validacao"], errors="coerce")
        else:
            df["data_validacao"] = pd.NaT
        df["observacao"] = df.get("observacao","").astype(str)
        df["validador"]  = df.get("validador","").astype(str)
        df["status"]     = df.get("status","Pendente").astype(str)
        df["yyyymm"] = pd.to_datetime(df["data"]).dt.strftime("%Y-%m")
        return df.sort_values(["data","site_nome"]).reset_index(drop=True)
    except Exception as e:
        st.warning(f"Não consegui carregar o último snapshot do GitHub: {e}")
        return None

# ===== Meses em PT =====
PT_MESES: Dict[str, int] = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}

# ===== Session state =====
if "df_validado" not in st.session_state:
    st.session_state.df_validado = None
if "temp_edits" not in st.session_state:
    st.session_state.temp_edits = None

# Se não há planilha na sessão, tenta auto-carregar do GitHub
if st.session_state.df_validado is None:
    with st.spinner("Carregando último arquivo salvo no GitHub..."):
        df_auto = load_latest_snapshot_df()
    if df_auto is not None:
        st.session_state.df_validado = df_auto
        st.success("✅ Último snapshot carregado automaticamente do GitHub.")

# ===== Utils (para upload inicial de matriz mês/ano) =====
def detectar_colunas_mes(df: pd.DataFrame) -> List[str]:
    cols_mes = []
    for c in df.columns:
        s = str(c).strip().replace("\xa0", " ").lower()
        partes = s.split()
        if len(partes) == 2 and partes[0] in PT_MESES:
            try:
                _ = int(partes[1]); cols_mes.append(c)
            except Exception:
                pass
    return cols_mes

def normalizar_planilha_matriz(df_raw: pd.DataFrame, col_site: Optional[str] = None) -> pd.DataFrame:
    df = df_raw.copy()
    df.columns = [str(c).strip().replace("\xa0", " ") for c in df.columns]
    if col_site is None:
        col_site = df.columns[0]
    if col_site != "site_nome":
        df = df.rename(columns={col_site: "site_nome"})
    cols_mes = detectar_colunas_mes(df)
    if not cols_mes:
        raise ValueError("Não foram encontradas colunas no formato 'Mês Ano' (ex.: 'Outubro 2025').")
    registros = []
    for _, row in df.iterrows():
        site = row["site_nome"]
        for cm in cols_mes:
            dias_str = row[cm]
            if pd.isna(dias_str) or str(dias_str).strip() == "":
                continue
            mes_nome, ano_str = str(cm).strip().split()
            mes_num = PT_MESES.get(mes_nome.lower()); ano = int(ano_str)
            dias = [d.strip() for d in str(dias_str).split(",") if d.strip() != ""]
            for d in dias:
                try:
                    di = int(d)
                    dt_ = pd.Timestamp(year=ano, month=mes_num, day=di)
                    registros.append({
                        "site_nome": site, "data": dt_.date(), "status": "Pendente",
                        "observacao": "", "validador": "", "data_validacao": pd.NaT
                    })
                except Exception:
                    continue
    df_expl = pd.DataFrame(registros)
    if df_expl.empty:
        raise ValueError("Nenhuma data válida foi encontrada (ex.: células '10,12,13').")
    df_expl["yyyymm"] = pd.to_datetime(df_expl["data"]).dt.strftime("%Y-%m")
    return df_expl.sort_values(["data", "site_nome"]).reset_index(drop=True)

# ===== Entrada (upload / url / exemplo) =====
with st.expander("📥 Carregar planilha", expanded=True):
    c1, c2 = st.columns([2,1])
    with c1: up = st.file_uploader("Envie seu Excel (.xlsx)", type=["xlsx"])
    with c2: url_raw = st.text_input("...ou cole a URL 'raw' do GitHub", placeholder="https://raw.githubusercontent.com/usuario/repo/main/cronograma.xlsx")
    col_site_hint = st.text_input("Nome da coluna do site (opcional)", value="") or None
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Carregar do upload"):
            if not up: st.warning("Envie um arquivo .xlsx.")
            else:
                try:
                    df_raw = pd.read_excel(up)
                    df_raw.columns = [str(c).strip().replace("\xa0", " ") for c in df_raw.columns]
                    st.session_state.df_validado = normalizar_planilha_matriz(df_raw, col_site_hint); st.session_state.temp_edits = None
                    st.success("Planilha carregada!")
                except Exception as e: st.error(f"Erro: {e}")
    with b2:
        if st.button("Carregar da URL GitHub"):
            if not url_raw: st.warning("Informe a URL raw do GitHub.")
            else:
                try:
                    r = requests.get(url_raw, timeout=20); r.raise_for_status()
                    df_raw = pd.read_excel(io.BytesIO(r.content))
                    df_raw.columns = [str(c).strip().replace("\xa0", " ") for c in df_raw.columns]
                    st.session_state.df_validado = normalizar_planilha_matriz(df_raw, col_site_hint); st.session_state.temp_edits = None
                    st.success("Planilha carregada da URL!")
                except Exception as e: st.error(f"Erro: {e}")
    with b3:
        if st.button("Gerar exemplo sintético"):
            exemplo = pd.DataFrame({
                "Site": ["UPGN Cabiunas", "UPGN Cacimbas", "P-68"],
                "Outubro 2025": ["10,12,13,20", "10,12,13,21", "10,12,13,22"],
                "Novembro 2025": ["10,12,13,21", "10,12,13,22", "10,12,13,23"],
                "Dezembro 2025": ["10,12,13,22", "10,12,13,23", "10,12,13,24"],
            })
            st.session_state.df_validado = normalizar_planilha_matriz(exemplo, col_site="Site"); st.session_state.temp_edits = None
            st.success("Exemplo carregado!")

if st.session_state.df_validado is None:
    st.info("Carregue seu Excel para continuar."); st.stop()

# ===== Filtros =====
with st.sidebar:
    st.header("Filtros")
    dfv = st.session_state.df_validado
    sites = sorted(dfv["site_nome"].unique())
    site_sel = st.multiselect("Sites", options=sites, default=sites)
    meses = sorted(dfv["yyyymm"].unique())
    mes_ano = st.selectbox("Mês", options=meses, index=max(0, len(meses)-1))
    st.checkbox("Colorir só dias com passagem", value=True, key="only_color_with_events")
    st.checkbox("Mostrar bolinhas/contagem", value=True, key="show_badges")
    st.text_input("Seu nome (autor do commit)", key="usuario_logado", placeholder="ex.: Márcio")

mask = st.session_state.df_validado["site_nome"].isin(site_sel) & (st.session_state.df_validado["yyyymm"] == mes_ano)
fdf = st.session_state.df_validado.loc[mask].copy().sort_values(["data", "site_nome"]) if not st.session_state.df_validado.empty else st.session_state.df_validado.copy()

# ===== Editor com checkboxes =====
st.subheader("Tabela de passagens para validar")
view = fdf[["site_nome", "data", "status", "observacao", "validador", "data_validacao"]].copy()
view["data"] = pd.to_datetime(view["data"]).dt.strftime("%Y-%m-%d")
view["sim"] = (view["status"] == "Aprovada")
view["nao"] = (view["status"] == "Rejeitada")
view["observacao"] = view["observacao"].astype("string")
view["validador"] = view["validador"].astype("string")
view["data_validacao"] = view["data_validacao"].apply(lambda x: "" if pd.isna(x) else pd.to_datetime(x).strftime("%Y-%m-%d %H:%M:%S")).astype("string")

edited = st.data_editor(
    view,
    num_rows="fixed",
    width='stretch',
    column_config={
        "sim": st.column_config.CheckboxColumn(label="Confirmar (sim)", help="Marca como Aprovada"),
        "nao": st.column_config.CheckboxColumn(label="Rejeitar (nao)", help="Marca como Rejeitada"),
        "status": st.column_config.TextColumn(disabled=True),
        "data": st.column_config.TextColumn(disabled=True),
        "site_nome": st.column_config.TextColumn(disabled=True),
        "data_validacao": st.column_config.TextColumn(disabled=True),
        "observacao": st.column_config.TextColumn(width="medium"),
        "validador": st.column_config.TextColumn(width="small"),
    },
    disabled=["status", "data", "site_nome", "data_validacao"],
    key="editor_v3",
)

col_save1, col_save2 = st.columns([1,6])
with col_save1:
    save_clicked = st.button("💾 Salvar alterações", type="primary")
with col_save2:
    if st.session_state.temp_edits is not None:
        st.caption("Há edições não salvas. Clique em **Salvar alterações** para aplicar.")

if st.session_state.temp_edits is None or not edited.equals(st.session_state.temp_edits):
    st.session_state.temp_edits = edited.copy()

if save_clicked:
    base = st.session_state.df_validado
    e = st.session_state.temp_edits.copy()
    e["data"] = pd.to_datetime(e["data"]).dt.date

    def decide(row):
        if row.get("nao", False): return "Rejeitada"
        if row.get("sim", False): return "Aprovada"
        return "Pendente"

    e["status_novo"] = e.apply(decide, axis=1)
    keys = ["site_nome", "data"]
    upd_cols = ["status_novo", "observacao", "validador"]
    merged = base.drop(columns=["observacao", "validador"], errors="ignore").merge(e[keys + upd_cols], on=keys, how="left")
    mask_new = ~merged["status_novo"].isna()
    merged.loc[mask_new, "status"] = merged.loc[mask_new, "status_novo"]
    merged = merged.drop(columns=["status_novo"])
    mudou = merged["status"].isin(["Aprovada", "Rejeitada"]) & merged["data_validacao"].isna()
    merged.loc[mudou, "data_validacao"] = pd.Timestamp.now(tz="UTC").tz_convert(None)
    st.session_state.df_validado = merged
    st.session_state.temp_edits = None
    st.success("Alterações salvas!")
    # publicar no GitHub
    try:
        xlsb = (lambda df: (lambda b: b.getvalue())( (lambda buf: (df.assign(data=pd.to_datetime(df["data"]).dt.strftime("%Y-%m-%d"),
                                                                                 data_validacao=pd.to_datetime(df["data_validacao"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S").fillna(""))
                                                               .astype({c:str for c in set(df.columns)-{"data","data_validacao"}})
                                                              ).to_excel(pd.ExcelWriter(buf:=io.BytesIO(), engine="xlsxwriter"), index=False, sheet_name="validacao") or buf )( )))(st.session_state.df_validado)
    except Exception:
        # versão legível se a lambda acima for confusa:
        def exportar_excel(df: pd.DataFrame) -> bytes:
            cols = ["site_nome", "data", "status", "observacao", "validador", "data_validacao"]
            cols = [c for c in cols if c in df.columns]
            df_exp = df[cols].copy()
            df_exp["data"] = pd.to_datetime(df_exp["data"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
            dv = pd.to_datetime(df_exp["data_validacao"], errors="coerce")
            df_exp["data_validacao"] = dv.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
            for c in set(df_exp.columns) - {"data", "data_validacao"}:
                df_exp[c] = df_exp[c].fillna("").astype(str)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
                df_exp.to_excel(writer, index=False, sheet_name="validacao")
            buf.seek(0); return buf.read()
        xlsb = exportar_excel(st.session_state.df_validado)

    try:
        usuario = st.session_state.get("usuario_logado", "")
        meta = gh_save_snapshot(xlsb, author=usuario)
        st.info(f"Salvo no GitHub: `{meta['path']}` (UTC: {meta['saved_at_utc']})")
    except Exception as e:
        st.warning(f"Salvou localmente, mas falhou ao publicar no GitHub: {e}")

    # recalc fdf
    mask = st.session_state.df_validado["site_nome"].isin(site_sel) & (st.session_state.df_validado["yyyymm"] == mes_ano)
    fdf = st.session_state.df_validado.loc[mask].copy().sort_values(["data", "site_nome"]) if not st.session_state.df_validado.empty else st.session_state.df_validado.copy()

# ===== Ações em lote =====
st.markdown("### ⚙️ Ações em lote por dia")
dias_disponiveis = sorted(pd.to_datetime(fdf["data"]).dt.date.unique())
if dias_disponiveis:
    d_sel = st.selectbox("Dia", options=dias_disponiveis, format_func=lambda d: d.strftime("%Y-%m-%d"))
    cA, cB, _ = st.columns([1,1,6])
    with cA:
        if st.button("✅ Aprovar tudo do dia"):
            base = st.session_state.df_validado
            idx = (pd.to_datetime(base["data"]).dt.date == d_sel) & base["site_nome"].isin(site_sel) & (base["yyyymm"] == mes_ano)
            base.loc[idx, "status"] = "Aprovada"
            base.loc[idx & base["data_validacao"].isna(), "data_validacao"] = pd.Timestamp.now(tz="UTC").tz_convert(None)
            st.session_state.df_validado = base
            st.success(f"Aprovado tudo em {d_sel}.")
            try:
                usuario = st.session_state.get("usuario_logado", "")
                meta = gh_save_snapshot(xlsb=(lambda df: io.BytesIO((df.assign(data=pd.to_datetime(df["data"]).dt.strftime("%Y-%m-%d"),
                                                                                     data_validacao=pd.to_datetime(df["data_validacao"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S").fillna(""))
                                                                        ).to_csv(index=False).encode())).getvalue(), author=usuario)  # fallback rápido
            except Exception:
                pass
    with cB:
        if st.button("⛔ Rejeitar tudo do dia"):
            base = st.session_state.df_validado
            idx = (pd.to_datetime(base["data"]).dt.date == d_sel) & base["site_nome"].isin(site_sel) & (base["yyyymm"] == mes_ano)
            base.loc[idx, "status"] = "Rejeitada"
            base.loc[idx & base["data_validacao"].isna(), "data_validacao"] = pd.Timestamp.now(tz="UTC").tz_convert(None)
            st.session_state.df_validado = base
            st.success(f"Rejeitado tudo em {d_sel}.")

else:
    st.caption("Sem passagens no mês/site(s) filtrados.")

# ===== Calendário =====
def montar_calendario(df_mes: pd.DataFrame, mes_ano: str,
                      only_color_with_events: bool = True,
                      show_badges: bool = True) -> go.Figure:
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
        if inf["rejeitadas"] > 0: return "#c62828"
        if inf["pendentes"] > 0 and inf["aprovadas"] == 0: return "#B0BEC5"
        return "#2e7d32"

    def weekday_dom(d: pd.Timestamp) -> int: return (d.weekday() + 1) % 7
    grid = np.full((6, 7), None, dtype=object); week = 0
    for d in dias:
        col = weekday_dom(d)
        if col == 0 and d.day != 1: week += 1
        grid[week, col] = d

    fig = go.Figure()
    for r in range(6):
        for c in range(7):
            d = grid[r, c]
            if d is None: continue
            fill = cor_do_dia(d)
            fig.add_shape(type="rect", x0=c, x1=c+1, y0=5-r, y1=6-r, line=dict(width=1, color="#90A4AE"), fillcolor=fill)
            fig.add_annotation(x=c+0.05, y=5-r+0.85, text=str(d.day), showarrow=False, xanchor="left", yanchor="top", font=dict(size=12))
            inf = info_map.get(d.date())
            if show_badges and (inf is not None):
                y0 = 5-r+0.18; badges = []
                if inf["aprovadas"] > 0: badges.append(("●", "#2e7d32"))
                if inf["rejeitadas"] > 0: badges.append(("●", "#c62828"))
                if inf["pendentes"] > 0: badges.append(("●", "#607D8B"))
                x0 = c+0.08
                for ch, colr in badges:
                    fig.add_annotation(x=x0, y=y0, text=f"<span style='color:{colr}'>{ch}</span>", showarrow=False, xanchor="left", yanchor="bottom", font=dict(size=12)); x0 += 0.12
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

st.subheader("Calendário do mês selecionado")
fig = montar_calendario(fdf, mes_ano, only_color_with_events=st.session_state.get("only_color_with_events", True),
                        show_badges=st.session_state.get("show_badges", True))
st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})

# ===== Exportar manual =====
st.markdown("---"); st.subheader("Exportar")
colA, colB = st.columns([1,2])
with colA: nome_arquivo = st.text_input("Nome do arquivo", value="passagens_validado.xlsx")
with colB:
    # export seguro (mesmo formato dos snapshots)
    def exportar_excel(df: pd.DataFrame) -> bytes:
        cols = ["site_nome", "data", "status", "observacao", "validador", "data_validacao"]
        cols = [c for c in cols if c in df.columns]
        df_exp = df[cols].copy()
        df_exp["data"] = pd.to_datetime(df_exp["data"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
        dv = pd.to_datetime(df_exp["data_validacao"], errors="coerce")
        df_exp["data_validacao"] = dv.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
        for c in set(df_exp.columns) - {"data", "data_validacao"}:
            df_exp[c] = df_exp[c].fillna("").astype(str)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            df_exp.to_excel(writer, index=False, sheet_name="validacao")
        buf.seek(0)
        return buf.read()
    xlsb = exportar_excel(st.session_state.df_validado)
    st.download_button("Baixar Excel validado", data=xlsb, file_name=nome_arquivo,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ===== Diagnóstico GitHub =====
with st.expander("🔧 Diagnóstico GitHub", expanded=False):
    has_token  = "github_token"  in st.secrets
    has_repo   = "github_repo"   in st.secrets
    has_branch = "github_branch" in st.secrets
    has_root   = "gh_data_root"  in st.secrets
    st.write("Secrets:", {"github_token": has_token, "github_repo": has_repo, "github_branch": has_branch, "gh_data_root": has_root})
    if has_repo:   st.write("Repo:", st.secrets["github_repo"])
    if has_branch: st.write("Branch:", st.secrets["github_branch"])
    if has_root:   st.write("Raiz:", st.secrets["gh_data_root"])
    if st.button("🔎 Testar conexão e gravar ping.txt"):
        try:
            now = dt.datetime.utcnow(); stamp = now.strftime("%Y%m%d-%H%M%S")
            path = f"{st.secrets.get('gh_data_root','data/validado')}/diagnostics/ping-{stamp}.txt"
            url  = f"https://api.github.com/repos/{st.secrets['github_repo']}/contents/{path}"
            payload = {"message": f"diagnostics: ping {stamp}",
                       "content": base64.b64encode(f'ping {stamp}\n'.encode("utf-8")).decode("utf-8"),
                       "branch": st.secrets.get("github_branch","main")}
            r = requests.put(url, headers=_gh_headers(), json=payload, timeout=30)
            st.write("HTTP:", r.status_code)
            try: st.json(r.json())
            except Exception: st.write(r.text)
            if r.status_code in (200, 201):
                st.success("✅ Consegui gravar no GitHub. O fluxo de salvar Excel deve funcionar também.")
            else:
                st.error("❌ Não consegui gravar. Veja o JSON acima (repo/branch/token).")
        except Exception as e:
            st.exception(e)


