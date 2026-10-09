import os
from flask import Flask, render_template, request, jsonify
import pandas as pd
import gspread

app = Flask(__name__)

NOME_PLANILHA_PRODUTOS = 'produtos_oficiais'
NOME_PLANILHA_MOVIMENTACAO = 'MOVIMENTACAO_NOMUS'

produtos_cache = []
codigos_validos_set = set()

def conectar_google_sheets():
    try:
        gc = gspread.service_account(filename='credenciais.json')
        return gc
    except Exception as e:
        print(f"[ERRO] Falha ao autenticar com o Google Sheets: {e}")
        return None

def carregar_base_produtos():
    global produtos_cache, codigos_validos_set
    try:
        gc = conectar_google_sheets()
        if not gc:
            return
        
        planilha = gc.open(NOME_PLANILHA_PRODUTOS)
        sheet = planilha.sheet1
        dados = sheet.get_all_records()
        df = pd.DataFrame(dados)
        
        produtos_cache = []
        codigos_validos_set = set()
        for _, row in df.iterrows():
            codigo = str(row.get('CODIGO', '')).strip()
            descricao = str(row.get('DESCRICAO', '')).strip()
            if codigo and codigo != 'nan':
                produtos_cache.append({'codigo': codigo, 'descricao': descricao})
                codigos_validos_set.add(codigo)
                
        print(f"[INFO] {len(produtos_cache)} produtos carregados com sucesso!")
    except Exception as e:
        print(f"[ERRO] Falha ao carregar produtos: {e}")

carregar_base_produtos()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/buscar_produtos', methods=['GET'])
def buscar_produtos():
    termo = request.args.get('termo', '').lower().strip()
    if not termo:
        return jsonify([])
    
    resultados = []
    for p in produtos_cache:
        if termo in p['codigo'].lower() or termo in p['descricao'].lower():
            resultados.append(p)
            if len(resultados) >= 15:
                break
    return jsonify(resultados)

@app.route('/salvar_lote', methods=['POST'])
def salvar_lote():
    dados = request.json
    data_lote = dados.get('data') # Ex: "2026-10-08"
    itens = dados.get('itens', [])

    if not data_lote or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou itens não informados!'})

    try:
        for item in itens:
            cod_item = str(item.get('CODIGO', '')).strip()
            if cod_item not in codigos_validos_set:
                return jsonify({
                    'sucesso': False, 
                    'mensagem': f'Bloqueado! O código "{cod_item}" não existe na lista oficial.'
                })

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        # Procura se já existe uma aba (worksheet) com o nome da data
        try:
            sheet_mov = planilha_mov.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            # Se não existir, cria uma nova aba com o nome da data
            sheet_mov = planilha_mov.add_worksheet(title=data_lote, rows="1000", cols="4")
            # Adiciona o cabeçalho na nova aba
            sheet_mov.append_row(["Data", "Código", "Descrição", "Quantidade"])

        linhas_para_adicionar = []
        for item in itens:
            codigo = item.get('CODIGO', '')
            descricao = item.get('DESCRICAO', '')
            quantidade = int(item.get('QUANTIDADE', 1))
            linhas_para_adicionar.append([data_lote, codigo, descricao, quantidade])

        # Adiciona os itens na aba correspondente à data
        sheet_mov.append_rows(linhas_para_adicionar)
        
        return jsonify({'sucesso': True, 'mensagem': f'Sucesso! Itens guardados na aba do dia {data_lote} em MOVIMENTACAO_NOMUS.'})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao processar: {str(e)}'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)






@app.route('/consultar_movimentacao', methods=['GET'])
def consultar_movimentacao():
    data_consulta = request.args.get('data', '').strip()
    if not data_consulta:
        return jsonify({'sucesso': False, 'mensagem': 'Data não informada!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            sheet_mov = planilha_mov.worksheet(data_consulta)
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({'sucesso': True, 'itens': [], 'mensagem': f'Nenhuma baixa encontrada para o dia {data_consulta}.'})

        dados = sheet_mov.get_all_records()
        return jsonify({'sucesso': True, 'itens': dados})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao consultar: {str(e)}'})



@app.route('/excluir_movimentacao', methods=['POST'])
def excluir_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    codigo = str(dados.get('codigo')).strip()

    if not data_mov or not codigo:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou código não informados!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
        
        cabecalho = linhas[0]
        idx_codigo = -1
        for i, col in enumerate(cabecalho):
            if col.upper() in ['CODIGO', 'CÓDIGO']:
                idx_codigo = i
                break
        
        if idx_codigo == -1:
            return jsonify({'sucesso': False, 'mensagem': 'Coluna de código não encontrada.'})

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            if str(linhas[idx][idx_codigo]).strip() == codigo:
                linha_encontrada = idx + 1 # No gspread as linhas começam em 1
                break

        if linha_encontrada != -1:
            sheet_mov.delete_rows(linha_encontrada)
            return jsonify({'sucesso': True, 'mensagem': 'Item excluído com sucesso da planilha!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado na planilha.'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao excluir: {str(e)}'})


@app.route('/editar_movimentacao', methods=['POST'])
def editar_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    codigo = str(dados.get('codigo')).strip()
    nova_quantidade = dados.get('quantidade')

    if not data_mov or not codigo or nova_quantidade is None:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
        
        cabecalho = linhas[0]
        idx_codigo = -1
        idx_qtd = -1
        for i, col in enumerate(cabecalho):
            if col.upper() in ['CODIGO', 'CÓDIGO']:
                idx_codigo = i
            elif col.upper() in ['QUANTIDADE']:
                idx_qtd = i
        
        if idx_codigo == -1 or idx_qtd == -1:
            return jsonify({'sucesso': False, 'mensagem': 'Colunas necessárias não encontradas.'})

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            if str(linhas[idx][idx_codigo]).strip() == codigo:
                linha_encontrada = idx + 1
                break

        if linha_encontrada != -1:
            # Atualiza a célula correspondente (coluna no gspread é idx_qtd + 1)
            sheet_mov.update_cell(linha_encontrada, idx_qtd + 1, nova_quantidade)
            return jsonify({'sucesso': True, 'mensagem': 'Quantidade atualizada com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado na planilha.'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao editar: {str(e)}'})




@app.route('/atualizar_status_lote', methods=['POST'])
def atualizar_status_lote():
    dados = request.json
    data_mov = dados.get('data')
    codigos = dados.get('codigos', []) # Lista de códigos selecionados
    novo_status = dados.get('status') # 'Sim' ou 'Não'

    if not data_mov or not codigos or not novo_status:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
        
        cabecalho = [col.upper().strip() for col in linhas[0]]
        
        # Garante que existe coluna de Status, senão avisa ou cria
        if 'STATUS' not in cabecalho:
            # Se não existir, podemos adicionar a coluna no cabeçalho na primeira linha livre
            idx_status = len(cabecalho) + 1
            sheet_mov.update_cell(1, idx_status, 'Status')
            cabecalho.append('STATUS')
        else:
            idx_status = cabecalho.index('STATUS') + 1

        idx_codigo = -1
        for i, col in enumerate(cabecalho):
            if col in ['CODIGO', 'CÓDIGO']:
                idx_codigo = i + 1
                break

        if idx_codigo == -1:
            return jsonify({'sucesso': False, 'mensagem': 'Coluna de código não encontrada.'})

        atualizados = 0
        for idx in range(2, len(linhas) + 1):
            val_codigo = str(sheet_mov.cell(idx, idx_codigo).value).strip()
            if val_codigo in [str(c).strip() for c in codigos]:
                sheet_mov.update_cell(idx, idx_status, novo_status)
                atualizados += 1

        return jsonify({'sucesso': True, 'mensagem': f'Status atualizado para "{novo_status}" em {atualizados} itens com sucesso!'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao atualizar status: {str(e)}'})
