import streamlit as st
import pandas as pd
import gspread
from datetime import date

# Configuração da página para ocupar o ecrã inteiro (ótimo para tablets)
st.set_page_config(page_title="Inventário Nomus", layout="centered")

NOME_PLANILHA_PRODUTOS = "PRODUTOS_NOMUS"
NOME_PLANILHA_MOVIMENTACAO = "MOVIMENTACAO_NOMUS"

# Função para conectar ao Google Sheets utilizando os Secrets do Streamlit
@st.cache_resource
def conectar_google_sheets():
    try:
        # No Streamlit Cloud, usamos st.secrets para as credenciais
        # Localmente, pode usar o ficheiro credenciais.json se preferir testar no PC
        if "gcp_service_account" in st.secrets:
            gc = gspread.service_account_from_dict(st.secrets["gcp_service_account"])
        else:
            gc = gspread.service_account(filename="credenciais.json")
            
        sheet_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO).sheet1
        sheet_prod = gc.open(NOME_PLANILHA_PRODUTOS).sheet1
        return sheet_mov, sheet_prod
    except Exception as e:
        st.error(f"Erro ao conectar ao Google Sheets: {e}")
        return None, None

sheet_movimentacao, sheet_produtos = conectar_google_sheets()

# Carregar produtos para cache
@st.cache_data(ttl=600) # Atualiza o cache a cada 10 minutos
def carregar_produtos():
    if sheet_produtos is None:
        return [], set()
    try:
        registos = sheet_produtos.get_all_records()
        cache = []
        validos = set()
        for row in registos:
            codigo = str(row.get('CODIGO', '')).strip()
            descricao = str(row.get('DESCRICAO', '')).strip()
            if codigo and codigo != 'nan':
                cache.append({'codigo': codigo, 'descricao': descricao})
                validos.add(codigo)
        return cache, validos
    except Exception as e:
        st.error(f"Erro ao carregar produtos: {e}")
        return [], set()

produtos_cache, codigos_validos_set = carregar_produtos()

# Interface Visual do Inventário
st.title("📦 Sistema de Inventário Nomus")
st.markdown("Registo de movimentações direto na nuvem para acesso via tablet.")

# Seleção da Data do Lote
data_lote = st.date_input("Data do Lote", value=date.today())

# Inicializar carrinho/lote na sessão do Streamlit
if 'itens_lote' not in st.session_state:
    st.session_state.itens_lote = []

st.divider()
st.subheader("Adicionar Produto")

# Campo de pesquisa de produtos
termo_busca = st.text_input("Pesquisar por Código ou Descrição:")

produto_selecionado = None
if termo_busca and produtos_cache:
    filtrados = [
        p for p in produtos_cache 
        if termo_busca.lower() in p['codigo'].lower() or termo_busca.lower() in p['descricao'].lower()
    ][:10] # Limita a 10 resultados
    
    if filtrados:
        opcoes = [f"{p['codigo']} - {p['descricao']}" for p in filtrados]
        escolha = st.selectbox("Selecione o produto correspondente:", opcoes)
        if escolha:
            codigo_escolhido = escolha.split(" - ")[0]
            produto_selecionado = next((p for p in filtrados if p['codigo'] == codigo_escolhido), None)

# Quantidade
quantidade = st.number_input("Quantidade", min_value=1, value=1, step=1)

if st.button("Adicionar ao Lote", type="primary"):
    if produto_selecionado:
        item = {
            'CODIGO': produto_selecionado['codigo'],
            'DESCRICAO': produto_selecionado['descricao'],
            'QUANTIDADE': int(quantidade)
        }
        st.session_state.itens_lote.append(item)
        st.success(f"Adicionado: {produto_selecionado['codigo']} ({quantidade} un.)")
    else:
        st.warning("Selecione um produto válido da lista antes de adicionar.")

# Mostrar itens já adicionados no lote atual
if st.session_state.itens_lote:
    st.divider()
    st.subheader("Itens no Lote Atual")
    df_lote = pd.DataFrame(st.session_state.itens_lote)
    st.dataframe(df_lote, use_container_width=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Enviar Lote para a Nuvem"):
            try:
                linhas_para_inserir = [
                    [str(data_lote), item['CODIGO'], item['QUANTIDADE'], item['DESCRICAO']]
                    for item in st.session_state.itens_lote
                ]
                sheet_movimentacao.append_rows(linhas_para_inserir)
                st.success("Lote enviado com sucesso para o Google Sheets!")
                st.session_state.itens_lote = [] # Limpa o lote após enviar
            except Exception as e:
                st.error(f"Erro ao salvar no Google Sheets: {e}")
                
    with col2:
        if st.button("Limpar Lote"):
            st.session_state.itens_lote = []
            st.rerun()