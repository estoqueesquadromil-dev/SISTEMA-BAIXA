import os
from flask import Flask, render_template, request, jsonify
import pandas as pd
import gspread
from datetime import datetime, timedelta

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
    data_lote = dados.get('data')
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
        
        try:
            sheet_mov = planilha_mov.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            # Cria a aba com 6 colunas para incluir o 'Inventario Feito'
            sheet_mov = planilha_mov.add_worksheet(title=data_lote, rows="1000", cols="6")
            sheet_mov.append_row(["Data", "Código", "Quantidade", "Tipo", "Status", "Inventario Feito"])

        linhas_para_adicionar = []
        for item in itens:
            codigo = item.get('CODIGO', '')
            quantidade = int(item.get('QUANTIDADE', 1))
            tipo = item.get('TIPO', 'Baixa Diaria')
            status = 'Não'
            inventario_feito = 'Não'
            
            linhas_para_adicionar.append([data_lote, codigo, quantidade, tipo, status, inventario_feito])

        sheet_mov.append_rows(linhas_para_adicionar)
        
        return jsonify({'sucesso': True, 'mensagem': f'Sucesso! Itens guardados na aba do dia {data_lote} em MOVIMENTACAO_NOMUS.'})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao processar: {str(e)}'})

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
                linha_encontrada = idx + 1
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
    codigos = [str(c).strip() for c in dados.get('codigos', [])]
    novo_status = dados.get('status')

    if not data_mov or not codigos or not novo_status:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia ou sem dados.'})
        
        cabecalho = [col.upper().strip() for col in linhas[0]]
        
        if 'STATUS' not in cabecalho:
            idx_status = len(cabecalho) + 1
            if idx_status > sheet_mov.col_count:
                sheet_mov.add_cols(1)
            sheet_mov.update_cell(1, idx_status, 'Status')
            cabecalho.append('STATUS')
            linhas = sheet_mov.get_all_values()
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
        cell_updates = []
        
        for row_idx, row in enumerate(linhas[1:], start=2):
            if len(row) >= idx_codigo:
                val_codigo = str(row[idx_codigo - 1]).strip()
                if val_codigo in codigos:
                    cell_address = gspread.utils.rowcol_to_a1(row_idx, idx_status)
                    cell_updates.append({
                        'range': cell_address,
                        'values': [[novo_status]]
                    })
                    atualizados += 1

        if cell_updates:
            sheet_mov.batch_update(cell_updates)

        return jsonify({'sucesso': True, 'mensagem': f'Status atualizado para "{novo_status}" em {atualizados} itens com sucesso!'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao atualizar status: {str(e)}'})


# --- ROTAS PARA A ABA DE INVENTÁRIO ---

@app.route('/consultar_inventario_periodo', methods=['GET'])
def consultar_inventario_periodo():
    inicio_str = request.args.get('inicio')
    fim_str = request.args.get('fim')
    
    if not inicio_str or not fim_str:
        return jsonify({'sucesso': False, 'mensagem': 'Datas de início e fim são obrigatórias.'}), 400
    
    try:
        data_inicio = datetime.strptime(inicio_str, '%Y-%m-%d')
        data_fim = datetime.strptime(fim_str, '%Y-%m-%d')
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        todos_itens = []
        delta = timedelta(days=1)
        atual = data_inicio
        
        while atual <= data_fim:
            nome_aba = atual.strftime('%Y-%m-%d')
            
            try:
                sheet_mov = planilha_mov.worksheet(nome_aba)
                registos = sheet_mov.get_all_records()
                
                for index, r in enumerate(registos):
                    linha_planilha = index + 2
                    
                    item_formatado = {
                        "Linha": linha_planilha,
                        "Aba": nome_aba,
                        "Data": r.get('Data') or r.get('DATA') or nome_aba,
                        "Código": r.get('Código') or r.get('CODIGO') or r.get('Codigo') or '',
                        "Quantidade": r.get('Quantidade') or r.get('QUANTIDADE') or 1,
                        "Tipo": r.get('Tipo') or r.get('TIPO') or 'Baixa Diaria',
                        "Status": r.get('Status') or r.get('STATUS') or 'Não',
                        "InventarioFeito": r.get('Inventario Feito') or r.get('INVENTARIO FEITO') or r.get('Inventario') or 'Não'
                    }
                    todos_itens.append(item_formatado)
            except Exception:
                # Se a aba do dia não existir na planilha, passa para o dia seguinte
                pass
                
            atual += delta
            
        return jsonify({'sucesso': True, 'itens': todos_itens})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/atualizar_inventario', methods=['POST'])
def atualizar_inventario():
    dados = request.json
    aba = dados.get('aba')
    linha = dados.get('linha')
    novo_status = dados.get('inventarioFeito')
    
    if not aba or not linha or not novo_status:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos.'}), 400
        
    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(aba)
        
        cabecalho = [col.upper().strip() for col in sheet_mov.row_values(1)]
        coluna_idx = None
        
        for idx, col in enumerate(cabecalho):
            if col in ['INVENTARIO FEITO', 'INVENTARIO']:
                coluna_idx = idx + 1
                break
                
        # Se a coluna "Inventario Feito" não existir na aba, cria-a na coluna 6
        if not coluna_idx:
            coluna_idx = 6
            if coluna_idx > sheet_mov.col_count:
                sheet_mov.add_cols(1)
            sheet_mov.update_cell(1, coluna_idx, 'Inventario Feito')
            
        sheet_mov.update_cell(linha, coluna_idx, novo_status)
        
        return jsonify({'sucesso': True, 'mensagem': 'Inventário atualizado com sucesso na planilha!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/exportar_excel_inventario', methods=['GET'])
def exportar_excel_inventario():
    inicio_str = request.args.get('inicio')
    fim_str = request.args.get('fim')
    
    try:
        # Reaproveita a lógica de busca do período para exportar
        data_inicio = datetime.strptime(inicio_str, '%Y-%m-%d')
        data_fim = datetime.strptime(fim_str, '%Y-%m-%d')
        
        gc = conectar_google_sheets()
        if not gc:
            return "Erro de conexão com o Google Sheets", 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        todos_itens = []
        delta = timedelta(days=1)
        atual = data_inicio
        
        while atual <= data_fim:
            nome_aba = atual.strftime('%Y-%m-%d')
            try:
                sheet_mov = planilha_mov.worksheet(nome_aba)
                registos = sheet_mov.get_all_records()
                for r in registos:
                    tipo = str(r.get('Tipo') or r.get('TIPO') or '')
                    status = str(r.get('Status') or r.get('STATUS') or '').strip().lower()
                    
                    is_sucata = 'sucata' in tipo.lower() or 'aproveitamento' in tipo.lower()
                    is_outros = 'corte' in tipo.lower() or 'perfil' in tipo.lower() or 'outros' in tipo.lower()
                    
                    # Aplica a mesma regra de filtro da aba Inventário
                    if is_sucata and status != 'sim':
                        continue
                    if not is_sucata and not is_outros:
                        continue
                        
                    todos_itens.append({
                        "Data": r.get('Data') or r.get('DATA') or nome_aba,
                        "Código": r.get('Código') or r.get('CODIGO') or '',
                        "Quantidade": r.get('Quantidade') or r.get('QUANTIDADE') or 1,
                        "Tipo": tipo,
                        "Classificação": 'Entrada' if is_sucata else 'Baixa',
                        "Inventario Feito": r.get('Inventario Feito') or r.get('INVENTARIO FEITO') or 'Não'
                    })
            except Exception:
                pass
            atual += delta
            
        if not todos_itens:
            return "Nenhum dado encontrado para exportar no período selecionado.", 404
            
        df = pd.DataFrame(todos_itens)
        
        # Gera o ficheiro Excel em memória e envia para download
        filepath = f"inventario_{inicio_str}_a_{fim_str}.xlsx"
        df.to_excel(filepath, index=False)
        
        from flask import send_file
        return send_file(filepath, as_attachment=True)
        
    except Exception as e:
        return f"Erro ao gerar Excel: {str(e)}", 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
