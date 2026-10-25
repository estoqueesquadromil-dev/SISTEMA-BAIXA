import os
from flask import Flask, render_template, request, jsonify, Response
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
        
        import uuid
        try:
            sheet_mov = planilha_mov.worksheet(data_lote)
        except gspread.exceptions.WorksheetNotFound:
            # Cria a aba com 7 colunas para incluir o ID na primeira coluna
            sheet_mov = planilha_mov.add_worksheet(title=data_lote, rows="1000", cols="7")
            sheet_mov.append_row(["ID", "Data", "Código", "Quantidade", "Tipo", "Status", "Inventario Feito"])

        linhas_para_adicionar = []
        for item in itens:
            codigo = item.get('CODIGO', '')
            quantidade = int(item.get('QUANTIDADE', 1))
            tipo = item.get('TIPO', 'Baixa Diaria')
            
            # Ajuste: Se o tipo for Aproveitamento de Sucata, o status começa vazio ("")
            if tipo == 'Aproveitamento de Sucata':
                status = ''
            else:
                status = 'Não'
                
            inventario_feito = 'Não'
            
            # Gera um ID único curto para esta linha específica
            row_id = str(uuid.uuid4())[:8]
            
            linhas_para_adicionar.append([row_id, data_lote, codigo, quantidade, tipo, status, inventario_feito])

        sheet_mov.append_rows(linhas_para_adicionar)
        
        return jsonify({'sucesso': True, 'mensagem': f'Sucesso! Itens guardados na aba do dia {data_lote} em MOVIMENTACAO_NOMUS.'})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao processar: {str(e)}'})


@app.route('/atualizar_status_lote_por_id', methods=['POST'])
def atualizar_status_lote_por_id():
    try:
        dados = request.get_json()
        if not dados:
            return jsonify({'sucesso': False, 'mensagem': 'Dados inválidos enviados.'}), 400
            
        data_consulta = str(dados.get('data', '')).strip()
        ids_selecionados = [str(i).strip() for i in dados.get('ids', [])]
        novo_status = str(dados.get('status', 'Não')).strip()
        
        if not data_consulta or not ids_selecionados:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum item selecionado.'}), 400
            
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            aba = planilha.worksheet(data_consulta)
        except Exception:
            return jsonify({'sucesso': False, 'mensagem': f'Aba da data {data_consulta} não encontrada.'}), 400
            
        linhas_planilha = aba.get_all_values()
        if not linhas_planilha or len(linhas_planilha) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'A planilha está vazia.'}), 400
            
        cabecalhos = [str(c).strip().upper() for c in linhas_planilha[0]]
        
        if "ID" not in cabecalhos or "STATUS" not in cabecalhos:
            return jsonify({'sucesso': False, 'mensagem': 'Colunas ID ou STATUS não encontradas na planilha.'}), 400
            
        col_id_idx = cabecalhos.index("ID") + 1
        col_status_idx = cabecalhos.index("STATUS") + 1
        
        cell_updates = []
        contador = 0
        
        for idx_l, linha in enumerate(linhas_planilha):
            if idx_l == 0:
                continue
                
            id_celula = str(linha[col_id_idx - 1]).strip() if len(linha) >= col_id_idx else ""
            
            if id_celula in ids_selecionados:
                cell_address = gspread.utils.rowcol_to_a1(idx_l + 1, col_status_idx)
                cell_updates.append({
                    'range': cell_address,
                    'values': [[novo_status]]
                })
                contador += 1
                
        # Envia todas as alterações num único pacote para o Google Sheets (Evita o erro 429)
        if cell_updates:
            aba.batch_update(cell_updates)
                
        return jsonify({'sucesso': True, 'mensagem': f'Status atualizado com sucesso em {contador} item(ns)!'})
        
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro interno no servidor: {str(e)}'}), 500




@app.route('/consultar_movimentacao', methods=['GET'])
def consultar_movimentacao():
    inicio_str = request.args.get('inicio')
    fim_str = request.args.get('fim')
    
    if not inicio_str or not fim_str:
        return jsonify({'sucesso': False, 'mensagem': 'Data de início e fim são obrigatórias.'}), 400
    
    try:
        data_inicio = datetime.strptime(inicio_str.strip(), '%Y-%m-%d')
        data_fim = datetime.strptime(fim_str.strip(), '%Y-%m-%d')
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        todas_abas = planilha_mov.worksheets()
        lista_itens = []

        for aba in todas_abas:
            nome_aba_original = aba.title.strip()
            aba_data_str = nome_aba_original.split(' ')[0]
            if 'T' in aba_data_str:
                aba_data_str = aba_data_str.split('T')[0]

            try:
                data_aba = datetime.strptime(aba_data_str, '%Y-%m-%d')
                if data_inicio <= data_aba <= data_fim:
                    linhas = aba.get_all_values()
                    if not linhas or len(linhas) < 2:
                        continue
                    
                    cabecalhos = [str(c).strip().upper() for c in linhas[0]]
                    
                    for index in range(1, len(linhas)):
                        row = linhas[index]
                        if not any(row):
                            continue
                        
                        row_id = ''
                        if "ID" in cabecalhos and len(row) > cabecalhos.index("ID"):
                            row_id = str(row[cabecalhos.index("ID")]).strip()
                        if not row_id:
                            row_id = f"{aba_data_str}_{index}"
                        
                        codigo_val = ''
                        for h in ["CÓDIGO", "CODIGO"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                codigo_val = str(row[cabecalhos.index(h)]).strip()
                                break
                        if not codigo_val and len(row) > 2:
                            codigo_val = str(row[2]).strip()

                        qtd_val = 1
                        for h in ["QUANTIDADE"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                try:
                                    qtd_val = int(float(str(row[cabecalhos.index(h)]).replace(',', '.')))
                                except:
                                    qtd_val = 1
                                break

                        tipo_val = 'Baixa Diaria'
                        for h in ["TIPO"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                tipo_val = str(row[cabecalhos.index(h)]).strip()
                                break
                        if tipo_val == 'Baixa Diaria' and len(row) > 4:
                            tipo_val = str(row[4]).strip()

                        # Lê o valor real do status sem forçar "Não" se estiver vazio
                        status_val = ''
                        for h in ["STATUS"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                status_val = str(row[cabecalhos.index(h)]).strip()
                                break

                        lista_itens.append({
                            "ID": row_id,
                            "Data": aba_data_str,
                            "Código": codigo_val,
                            "Quantidade": qtd_val,
                            "Tipo": tipo_val,
                            "Status": status_val,
                            "Aba": nome_aba_original,
                            "Linha": index + 1
                        })
            except ValueError:
                continue

        return jsonify({'sucesso': True, 'itens': lista_itens})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500





@app.route('/excluir_movimentacao', methods=['POST'])
def excluir_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    row_id = str(dados.get('id', '')).strip()

    if not data_mov or not row_id:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou ID não informados!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
        
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        
        if "ID" not in cabecalhos:
            return jsonify({'sucesso': False, 'mensagem': 'Coluna ID não encontrada na planilha.'})
            
        col_id_idx = cabecalhos.index("ID") + 1

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            val_id = str(linhas[idx][col_id_idx - 1]).strip() if len(linhas[idx]) >= col_id_idx else ""
            if val_id == row_id:
                linha_encontrada = idx + 1
                break

        if linha_encontrada != -1:
            sheet_mov.delete_rows(linha_encontrada)
            return jsonify({'sucesso': True, 'mensagem': 'Item excluído com sucesso da planilha!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado pelo ID na planilha.'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao excluir: {str(e)}'})


@app.route('/editar_movimentacao', methods=['POST'])
def editar_movimentacao():
    dados = request.json
    data_mov = dados.get('data')
    row_id = str(dados.get('id', '')).strip()
    nova_quantidade = dados.get('quantidade')

    if not data_mov or not row_id or nova_quantidade is None:
        return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos!'})

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'})

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(data_mov)
        
        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
        
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        
        if "ID" not in cabecalhos or "QUANTIDADE" not in cabecalhos:
            return jsonify({'sucesso': False, 'mensagem': 'Colunas ID ou QUANTIDADE não encontradas.'})
            
        col_id_idx = cabecalhos.index("ID") + 1
        col_qtd_idx = cabecalhos.index("QUANTIDADE") + 1

        linha_encontrada = -1
        for idx in range(1, len(linhas)):
            val_id = str(linhas[idx][col_id_idx - 1]).strip() if len(linhas[idx]) >= col_id_idx else ""
            if val_id == row_id:
                linha_encontrada = idx + 1
                break

        if linha_encontrada != -1:
            sheet_mov.update_cell(linha_encontrada, col_qtd_idx, nova_quantidade)
            return jsonify({'sucesso': True, 'mensagem': 'Quantidade atualizada com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado pelo ID na planilha.'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao editar: {str(e)}'})




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
        todas_abas = planilha_mov.worksheets()
        
        for aba in todas_abas:
            nome_aba = aba.title.strip()
            try:
                data_aba = datetime.strptime(nome_aba, '%Y-%m-%d')
                
                if data_inicio <= data_aba <= data_fim:
                    linhas = aba.get_all_values()
                    if not linhas or len(linhas) < 2:
                        continue
                    
                    cabecalhos = [str(c).strip().upper() for c in linhas[0]]
                    
                    for index in range(1, len(linhas)):
                        row = linhas[index]
                        if not any(row):
                            continue
                        
                        linha_planilha = index + 1
                        
                        # ID
                        row_id = ''
                        if "ID" in cabecalhos and len(row) > cabecalhos.index("ID"):
                            row_id = row[cabecalhos.index("ID")]
                        if not row_id:
                            row_id = f"{nome_aba}_l{linha_planilha}"

                        # Código
                        codigo_val = ''
                        if "CÓDIGO" in cabecalhos and len(row) > cabecalhos.index("CÓDIGO"):
                            codigo_val = row[cabecalhos.index("CÓDIGO")]
                        elif "CODIGO" in cabecalhos and len(row) > cabecalhos.index("CODIGO"):
                            codigo_val = row[cabecalhos.index("CODIGO")]
                        elif len(row) > 2:
                            codigo_val = row[2]

                        # Quantidade
                        qtd_val = 1
                        if "QUANTIDADE" in cabecalhos and len(row) > cabecalhos.index("QUANTIDADE"):
                            qtd_val = row[cabecalhos.index("QUANTIDADE")]
                        elif len(row) > 3:
                            qtd_val = row[3]

                        # Tipo
                        tipo_val = 'Baixa Diaria'
                        if "TIPO" in cabecalhos and len(row) > cabecalhos.index("TIPO"):
                            tipo_val = row[cabecalhos.index("TIPO")]
                        elif len(row) > 4:
                            tipo_val = row[4]

                        # Status
                        status_val = 'Não'
                        if "STATUS" in cabecalhos and len(row) > cabecalhos.index("STATUS"):
                            status_val = row[cabecalhos.index("STATUS")]
                        elif len(row) > 5:
                            status_val = row[5]
                        
                        # Inventário Feito
                        inv_val = 'Não'
                        for h_nome in ['INVENTARIO FEITO', 'INVENTARIO']:
                            if h_nome in cabecalhos and len(row) > cabecalhos.index(h_nome):
                                inv_val = row[cabecalhos.index(h_nome)] or 'Não'
                                break
                        if inv_val == 'Não' and len(row) > 6:
                            inv_val = row[6] or 'Não'

                        # IVE
                        ive_val = 'Não'
                        if "IVE" in cabecalhos and len(row) > cabecalhos.index("IVE"):
                            ive_val = row[cabecalhos.index("IVE")] or 'Não'

                        # Status ERP
                        erp_val = ''
                        if "STATUS_ERP" in cabecalhos and len(row) > cabecalhos.index("STATUS_ERP"):
                            erp_val = row[cabecalhos.index("STATUS_ERP")] or ''

                        # Leitura da Coluna I (Coluna 9) para o Saldo ERP salvo
                        saldo_erp_val = ''
                        if len(row) >= 9:
                            saldo_erp_val = str(row[8]).strip()

                        item_formatado = {
                            "ID": row_id,
                            "Linha": linha_planilha,
                            "Aba": nome_aba,
                            "Data": nome_aba,
                            "Codigo": codigo_val,
                            "Quantidade": qtd_val,
                            "Tipo": tipo_val,
                            "Status": status_val,
                            "InventarioFeito": inv_val,
                            "IVE": ive_val,
                            "Status_ERP": erp_val,
                            "Saldo_ERP": saldo_erp_val
                        }
                        todos_itens.append(item_formatado)
            except ValueError:
                continue
            except Exception:
                pass
                
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
                
        # Se a coluna não existir, cria-a na coluna 6
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
    filtro_classificacao = request.args.get('classificacao', 'TODOS')
    filtro_status = request.args.get('status', 'TODOS')

    if not inicio_str or not fim_str:
        return "Datas não informadas.", 400

    try:
        data_inicio = datetime.strptime(inicio_str, '%Y-%m-%d')
        data_fim = datetime.strptime(fim_str, '%Y-%m-%d')

        gc = conectar_google_sheets()
        if not gc:
            return "Erro de conexão com o Google Sheets.", 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        delta = timedelta(days=1)
        datas_para_consultar = []
        atual = data_inicio
        while atual <= data_fim:
            datas_para_consultar.append(atual.strftime('%Y-%m-%d'))
            atual += delta

        import csv
        from io import StringIO
        from flask import Response

        output = StringIO()
        writer = csv.writer(output, delimiter=';')
        
        # Cabeçalho do CSV
        writer.writerow(['Data', 'Código', 'Quantidade', 'Tipo', 'Classificação', 'Inventário Feito'])

        for data_aba in datas_para_consultar:
            try:
                sheet_mov = planilha_mov.worksheet(data_aba)
                linhas = sheet_mov.get_all_values()
                if not linhas or len(linhas) < 2:
                    continue
                
                cabecalhos = [str(c).strip().upper() for c in linhas[0]]
                
                for idx in range(1, len(linhas)):
                    row = linhas[idx]
                    if not any(row):
                        continue
                    
                    codigo_val = ''
                    if "CÓDIGO" in cabecalhos and len(row) > cabecalhos.index("CÓDIGO"):
                        codigo_val = row[cabecalhos.index("CÓDIGO")]
                    elif "CODIGO" in cabecalhos and len(row) > cabecalhos.index("CODIGO"):
                        codigo_val = row[cabecalhos.index("CODIGO")]
                    elif len(row) > 2:
                        codigo_val = row[2]

                    qtd_val = "1"
                    if "QUANTIDADE" in cabecalhos and len(row) > cabecalhos.index("QUANTIDADE"):
                        qtd_val = row[cabecalhos.index("QUANTIDADE")]
                    elif len(row) > 3:
                        qtd_val = row[3]

                    tipo_val = ''
                    if "TIPO" in cabecalhos and len(row) > cabecalhos.index("TIPO"):
                        tipo_val = row[cabecalhos.index("TIPO")]
                    elif len(row) > 4:
                        tipo_val = row[4]

                    # Procura o status de inventário feito na linha
                    inv_val = 'Não'
                    for h_nome in ['INVENTARIO FEITO', 'INVENTARIO']:
                        if h_nome in cabecalhos and len(row) > cabecalhos.index(h_nome):
                            inv_val = row[cabecalhos.index(h_nome)] or 'Não'
                            break
                    if inv_val == 'Não' and len(row) > 6:
                        inv_val = row[6] or 'Não'

                    # Aplica a mesma regra de negócio da tela do inventário:
                    tipo_lower = str(tipo_val).strip().lower()
                    is_sucata = 'sucata' in tipo_lower or 'aproveitamento' in tipo_lower
                    is_ajuste_entrada = 'ajuste entrada' in tipo_lower
                    is_outros = 'corte' in tipo_lower or 'perfil' in tipo_lower or 'outros' in tipo_lower or 'baixa' in tipo_lower

                    # Se não for sucata, ajuste de entrada nem outros válidos, ignora
                    if not is_sucata and not is_ajuste_entrada and not is_outros:
                        continue
                    
                    # Sucata exige status de ERP 'sim', mas Ajuste Entrada entra livremente
                    status_erp = 'não'
                    for h_nome in ['STATUS']:
                        if h_nome in cabecalhos and len(row) > cabecalhos.index(h_nome):
                            status_erp = str(row[cabecalhos.index(h_nome)]).strip().lower()
                            break

                    if is_sucata and status_erp != 'sim':
                        continue

                    # Define a classificação: Sucata e Ajuste Entrada são Entrada; os demais são Baixa
                    classificacao_item = 'Entrada' if (is_sucata or is_ajuste_entrada) else 'Baixa'

                    # Aplica os filtros escolhidos pelo utilizador nos selects da tela
                    if filtro_classificacao != 'TODOS' and classificacao_item.upper() != filtro_classificacao.upper():
                        continue
                        
                    if filtro_status != 'TODOS' and str(inv_val).strip().capitalize() != str(filtro_status).strip().capitalize():
                        continue
                        
                    writer.writerow([data_aba, codigo_val, qtd_val, tipo_val, classificacao_item, inv_val])
            except Exception:
                continue

        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=inventario_{inicio_str}_a_{fim_str}.csv"}
        )

    except Exception as e:
        return f"Erro ao exportar Excel: {str(e)}", 500





# ==========================================
# MÓDULO DE CRIAÇÃO DE ORDENS
# ==========================================
PLANILHA_CRIAR_ORDENS = "DADOS-CRIAR-ORDENS"

@app.route('/buscar_dados_ordens', methods=['GET'])
def buscar_dados_ordens():
    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
        
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        # Abre especificamente a aba "dados" conforme solicitado
        try:
            aba = planilha.worksheet("dados")
        except:
            aba = planilha.sheet1 # Fallback caso a aba se chame de outra forma por defeito
            
        registos = aba.get_all_records()
        return jsonify({'sucesso': True, 'itens': registos})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500

@app.route('/salvar_ordem_producao', methods=['POST'])
def salvar_ordem_producao():
    try:
        dados = request.get_json()
        data_hoje = dados.get('data')
        itens_ordem = dados.get('itens', [])
        
        if not data_hoje or not itens_ordem:
            return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos para salvar a ordem.'}), 400
            
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        
        import uuid
        try:
            aba_dia = planilha.worksheet(data_hoje)
        except:
            aba_dia = planilha.add_worksheet(title=data_hoje, rows="1000", cols="5")
            # Adicionamos "ID" na primeira coluna da ordem
            aba_dia.append_row(["ID", "Data", "Código Principal", "Quantidade Ordem", "Status"])
            
        ordens_unicas = {}
        for item in itens_ordem:
            cod_prin = item.get('CodigoPrincipal', '')
            qtd_ord = item.get('QuantidadeOrdem', 0)
            if cod_prin:
                ordens_unicas[cod_prin] = qtd_ord

        for cod_prin, qtd_ord in ordens_unicas.items():
            row_id = str(uuid.uuid4())[:8] # ID único para a ordem
            aba_dia.append_row([
                row_id,
                data_hoje,
                cod_prin,
                qtd_ord,
                "Não"
            ])
            
        return jsonify({'sucesso': True, 'mensagem': f'Ordem de produção registada com sucesso na aba {data_hoje}!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500



# ==========================================
# NOVAS ROTAS PARA O MÓDULO "ORDENS CRIADAS"
# ==========================================

# ==========================================
# NOVAS ROTAS PARA O MÓDULO "ORDENS CRIADAS"
# ==========================================

@app.route('/listar_ordens_criadas', methods=['GET'])
def listar_ordens_criadas():
    inicio_str = request.args.get('inicio')
    fim_str = request.args.get('fim')
    
    if not inicio_str or not fim_str:
        return jsonify({'sucesso': False, 'mensagem': 'Data de início e fim são obrigatórias.'}), 400
    
    try:
        data_inicio = datetime.strptime(inicio_str.strip(), '%Y-%m-%d')
        data_fim = datetime.strptime(fim_str.strip(), '%Y-%m-%d')
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_ordens = gc.open(PLANILHA_CRIAR_ORDENS)
        todas_abas = planilha_ordens.worksheets()
        lista_ordens = []

        for aba in todas_abas:
            nome_aba_original = aba.title.strip()
            if nome_aba_original.upper() == "LIXEIRA":
                continue
                
            aba_data_str = nome_aba_original.split(' ')[0]
            if 'T' in aba_data_str:
                aba_data_str = aba_data_str.split('T')[0]

            try:
                data_aba = datetime.strptime(aba_data_str, '%Y-%m-%d')
                if data_inicio <= data_aba <= data_fim:
                    linhas = aba.get_all_values()
                    if not linhas or len(linhas) < 2:
                        continue
                    
                    cabecalhos = [str(c).strip().upper() for c in linhas[0]]
                    
                    for index in range(1, len(linhas)):
                        row = linhas[index]
                        if not any(row):
                            continue
                        
                        # Lê diretamente o ID único da coluna ID gerada na planilha
                        row_id = ''
                        if "ID" in cabecalhos and len(row) > cabecalhos.index("ID"):
                            row_id = str(row[cabecalhos.index("ID")]).strip()
                        
                        # Fallback de segurança caso a linha antiga não tenha ID
                        if not row_id:
                            row_id = f"{nome_aba_original}|{index + 1}"

                        codigo_principal = ''
                        for h in ["CÓDIGO PRINCIPAL", "CODIGO PRINCIPAL", "CÓDIGO", "CODIGO"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                codigo_principal = str(row[cabecalhos.index(h)]).strip()
                                break

                        qtd_ordem = 1
                        for h in ["QUANTIDADE ORDEM", "QUANTIDADE"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                try:
                                    qtd_ordem = int(float(str(row[cabecalhos.index(h)]).replace(',', '.')))
                                except:
                                    qtd_ordem = 1
                                break

                        feito_val = False
                        for h in ["FEITO", "STATUS"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                val = str(row[cabecalhos.index(h)]).strip()
                                if val.lower() in ['sim', 'true', '1', 'feito']:
                                    feito_val = True
                                break

                        lista_ordens.append({
                            "id": row_id,
                            "data": aba_data_str,
                            "codigoPrincipal": codigo_principal,
                            "quantidadeOrdem": qtd_ordem,
                            "feito": feito_val
                        })
            except ValueError:
                continue

        return jsonify({'sucesso': True, 'ordens': lista_ordens})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500





# Nova rota para alternar o status de "Feito" diretamente na linha da planilha
@app.route('/alternar_status_ordem', methods=['POST'])
def alternar_status_ordem():
    try:
        dados = request.get_json()
        id_ordem = str(dados.get('id', '')).strip()
        feito_val = dados.get('feito', False)
        
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        
        # Como o ID é único, precisamos de varrer as abas do dia ou procurar na aba correta. 
        # Se o id_ordem contiver a data ou se procurarmos em todas as abas de data:
        for aba in planilha.worksheets():
            if aba.title.upper() in ["LIXEIRA", "DADOS"]:
                continue
            linhas = aba.get_all_values()
            if not linhas or len(linhas) < 2:
                continue
            cabecalhos = [str(c).strip().upper() for c in linhas[0]]
            if "ID" not in cabecalhos:
                continue
            
            col_id_idx = cabecalhos.index("ID") + 1
            for idx, row in enumerate(linhas[1:], start=2):
                val_id = str(row[col_id_idx - 1]).strip() if len(row) >= col_id_idx else ""
                if val_id == id_ordem:
                    # Encontrou a linha exata! Atualiza o status
                    if "STATUS" not in cabecalhos and "FEITO" not in cabecalhos:
                        col_status_idx = len(cabecalhos) + 1
                        aba.update_cell(1, col_status_idx, "Status")
                    else:
                        col_status_idx = cabecalhos.index("STATUS") + 1 if "STATUS" in cabecalhos else cabecalhos.index("FEITO") + 1
                    
                    aba.update_cell(idx, col_status_idx, "Sim" if feito_val else "Não")
                    return jsonify({'sucesso': True, 'mensagem': 'Status atualizado com sucesso!'})
                    
        return jsonify({'sucesso': False, 'mensagem': 'ID da ordem não encontrado na planilha.'}), 404
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500




@app.route('/excluir_ordem_criada', methods=['POST'])
def excluir_ordem_criada():
    try:
        dados = request.get_json()
        id_ordem = dados.get('id', '') # Formato recebido: "YYYY-MM-DD|linha"
        
        if '|' not in id_ordem:
            return jsonify({'sucesso': False, 'mensagem': 'ID de ordem inválido.'}), 400
            
        nome_aba, linha_str = id_ordem.split('|')
        linha_alvo = int(linha_str)
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        aba = planilha.worksheet(nome_aba)
        
        # Como agora listamos linha a linha, removemos especificamente a linha exata apontada pelo ID
        linhas = aba.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
            
        if linha_alvo <= len(linhas):
            # Deleta a linha exata correspondente na planilha
            aba.delete_rows(linha_alvo)
            return jsonify({'sucesso': True, 'mensagem': 'Ordem excluída com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Linha não encontrada na aba.'}), 404
            
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/exportar_excel_ordens', methods=['GET'])
def exportar_excel_ordens():
    try:
        data_filtro = request.args.get('data', '').strip()
        status_filtro = request.args.get('status', 'todos').strip().lower()
        
        gc = conectar_google_sheets()
        if not gc:
            return "Erro de conexão com o Google Sheets", 500
            
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        todas_abas = planilha.worksheets()
        
        dados_excel = []
        
        for aba in todas_abas:
            nome_aba = aba.title.strip()
            if data_filtro and nome_aba != data_filtro:
                continue
            try:
                datetime.strptime(nome_aba, '%Y-%m-%d')
            except ValueError:
                continue
                
            registos = aba.get_all_records()
            for r in registos:
                codigo_principal = str(r.get('CodigoPrincipal') or r.get('Código Principal') or '').strip()
                quantidade_ordem = r.get('QuantidadeOrdem') or r.get('Quantidade Ordem') or 0
                status_feito = str(r.get('Status') or r.get('Feito') or '').strip().lower() in ['true', 'sim', '1', 'yes', 'feito']
                
                if not codigo_principal:
                    continue
                
                # Aplica o filtro de status antes de adicionar ao Excel
                if status_filtro == 'pendentes' and status_feito:
                    continue
                if status_filtro == 'feitos' and not status_feito:
                    continue
                
                dados_excel.append({
                    "Data": nome_aba,
                    "Código Principal": codigo_principal,
                    "Quantidade Ordem": quantidade_ordem,
                    "Status": "Feito" if status_feito else "Pendente"
                })
                
        if not dados_excel:
            return "Nenhuma ordem encontrada com os filtros selecionados para exportar.", 404
            
        df = pd.DataFrame(dados_excel)
        csv_data = df.to_csv(index=False, sep=';', encoding='utf-8-sig')
        
        nome_arquivo = f"ordens_{data_filtro}_{status_filtro}.csv" if data_filtro else f"ordens_criadas_{status_filtro}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
        )
    except Exception as e:
        return f"Erro ao exportar: {str(e)}", 500


# ==========================================
# MÓDULO DE PERDAS (ABA kg-m)
# ==========================================
# ==========================================
# MÓDULO DE PERDAS (ABA kg-m)
# ==========================================
NOME_PLANILHA_PERDAS = 'Perdas'

@app.route('/salvar_lote_perdas', methods=['POST'])
def salvar_lote_perdas():
    dados = request.get_json()
    data_lancamento = dados.get('data')
    itens = dados.get('itens', [])

    if not data_lancamento or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou itens de perda não informados!'}), 400

    try:
        data_obj = datetime.strptime(data_lancamento, '%Y-%m-%d')
        nome_aba_mes = data_obj.strftime('%Y-%m')

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_perdas = gc.open(NOME_PLANILHA_PERDAS)

        import uuid
        try:
            sheet_mes = planilha_perdas.worksheet(nome_aba_mes)
        except gspread.exceptions.WorksheetNotFound:
            # Adicionamos "ID" na primeira coluna para rastreio exato
            sheet_mes = planilha_perdas.add_worksheet(title=nome_aba_mes, rows="1000", cols="7")
            sheet_mes.append_row(["ID", "Data", "Tipo de Perda", "Quantidade", "Perfil", "Usuário/Registro", "Qtd_Sistema"])

        linhas_para_adicionar = []
        for item in itens:
            tipo_perda = item.get('tipo', '')
            try:
                val_float = float(str(item.get('quantidade', 0)).replace(',', '.').strip())
            except (ValueError, TypeError):
                val_float = 0.0
                
            row_id = str(uuid.uuid4())[:8] # ID único curto
            quantidade_protegida = f"'{val_float:.3f}".replace('.', ',')
            perfil = item.get('perfil', 'Não')
            
            # Ordem exata com ID na coluna 1
            linhas_para_adicionar.append([row_id, data_lancamento, tipo_perda, val_float, perfil, 'Sistema', quantidade_protegida])

        sheet_mes.append_rows(linhas_para_adicionar, value_input_option='RAW')
        return jsonify({'sucesso': True, 'mensagem': f'Perdas guardadas com sucesso na aba {nome_aba_mes}!'})

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao salvar perdas: {str(e)}'}), 500





@app.route('/consultar_perdas', methods=['GET'])
def consultar_perdas():
    data_inicio = request.args.get('inicio', '').strip()
    data_fim = request.args.get('fim', '').strip()
    tipo_filtro = request.args.get('tipo', '').strip()
    perfil_filtro = request.args.get('perfil', '').strip()

    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_perdas = gc.open(NOME_PLANILHA_PERDAS)
        todas_abas = planilha_perdas.worksheets()

        todos_registos = []
        for aba in todas_abas:
            nome_aba = aba.title.strip()
            try:
                datetime.strptime(nome_aba + '-01', '%Y-%m-%d')
            except ValueError:
                continue

            registos = aba.get_all_records()
            for r in registos:
                d = str(r.get('Data') or r.get('DATA') or '')
                t = str(r.get('Tipo de Perda') or r.get('TIPO DE PERDA') or '')
                
                # BUSCA DIRETAMENTE DA COLUNA F ('Qtd_Sistema') PARA GARANTIR PRECISÃO TOTAL NO SISTEMA
                raw_qtd = r.get('Qtd_Sistema') or r.get('QTD_SISTEMA') or r.get('Quantidade') or r.get('QUANTIDADE') or 0
                try:
                    # Remove a plica, troca vírgula por ponto e converte para float
                    qtd_limpa = str(raw_qtd).replace("'", "").replace(',', '.').strip()
                    q = float(qtd_limpa)
                except ValueError:
                    q = 0.0

                p = str(r.get('Perfil') or r.get('PERFIL') or 'Não')

                if data_inicio and d < data_inicio:
                    continue
                if data_fim and d > data_fim:
                    continue
                if tipo_filtro and t.lower() != tipo_filtro.lower():
                    continue
                if perfil_filtro and p.lower() != perfil_filtro.lower():
                    continue

                todos_registos.append({
                    "Data": d,
                    "Tipo": t,
                    "Quantidade": q,
                    "Perfil": p
                })

        totais_por_tipo = {}
        for item in todos_registos:
            tp = item["Tipo"]
            qt = item["Quantidade"]
            totais_por_tipo[tp] = totais_por_tipo.get(tp, 0.0) + qt

        totais_formatados = {k: round(v, 3) for k, v in totais_por_tipo.items()}

        return jsonify({
            'sucesso': True, 
            'itens': todos_registos,
            'totais': totais_formatados
        })

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao consultar perdas: {str(e)}'}), 500



@app.route('/excluir_perda', methods=['POST'])
def excluir_perda():
    try:
        dados = request.get_json()
        id_item = str(dados.get('id', '')).strip()
        
        if not id_item:
            return jsonify({'sucesso': False, 'mensagem': 'ID não fornecido.'}), 400
            
        gc = conectar_google_sheets()
        
        # Identifica a planilha correta de perdas
        nome_planilha = globals().get('PLANILHA_PERDAS', globals().get('PLANILHA_PERDA', 'Perdas'))
        try:
            planilha = gc.open(nome_planilha)
        except:
            planilha = gc.open("Perdas")
            
        # Procura a aba correta (ex: aba PERDAS ou aba do mês atual)
        aba = None
        for w in planilha.worksheets():
            if w.title.upper() == "PERDAS" or w.title.startswith("2026-"):
                aba = w
                break
        if not aba:
            aba = planilha.sheet1
            
        linhas = aba.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum registo encontrado.'}), 404
            
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        col_id_idx = cabecalhos.index("ID") if "ID" in cabecalhos else 0
        
        encontrou_indice = None
        for idx in range(2, len(linhas) + 1):
            row = linhas[idx - 1]
            val_id = str(row[col_id_idx]).strip() if len(row) > col_id_idx else ""
            if val_id == id_item:
                encontrou_indice = idx
                break
                
        if encontrou_indice:
            aba.delete_rows(encontrou_indice)
            return jsonify({'sucesso': True, 'mensagem': 'Registo excluído com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Registo não encontrado pelo ID.'}), 404
            
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500s



# ==========================================
# ROTAS DO SISTEMA DE LIXEIRA E ORDENS CRIADAS
# ==========================================

@app.route('/mover_para_lixeira', methods=['POST'])
def mover_para_lixeira():
    try:
        dados = request.get_json()
        id_ordem = str(dados.get('id', '')).strip()
        
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        
        for aba in planilha.worksheets():
            if aba.title.upper() in ["LIXEIRA", "DADOS"]:
                continue
            linhas = aba.get_all_values()
            if not linhas or len(linhas) < 2:
                continue
            cabecalhos = [str(c).strip().upper() for c in linhas[0]]
            if "ID" not in cabecalhos:
                continue
            
            col_id_idx = cabecalhos.index("ID") + 1
            for idx, row in enumerate(linhas[1:], start=2):
                val_id = str(row[col_id_idx - 1]).strip() if len(row) >= col_id_idx else ""
                if val_id == id_ordem:
                    dados_linha = row
                    try:
                        aba_lixeira = planilha.worksheet("LIXEIRA")
                    except:
                        aba_lixeira = planilha.add_worksheet(title="LIXEIRA", rows="1000", cols="5")
                        aba_lixeira.append_row(["ID", "Data", "Código Principal", "Quantidade Ordem", "Status"])
                        
                    aba_lixeira.append_row(dados_linha)
                    aba.delete_rows(idx)
                    return jsonify({'sucesso': True, 'mensagem': 'Ordem movida para a lixeira com sucesso!'})
                    
        return jsonify({'sucesso': False, 'mensagem': 'ID da ordem não encontrado para mover.'}), 404
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500






@app.route('/excluir_definitivo', methods=['POST'])
def excluir_definitivo():
    try:
        dados = request.get_json()
        id_ordem = dados.get('id', '')
        _, linha_str = id_ordem.split('|')
        linha_alvo = int(linha_str)
        
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        aba_lixeira = planilha.worksheet("LIXEIRA")
        
        aba_lixeira.delete_rows(linha_alvo)
        return jsonify({'sucesso': True, 'mensagem': 'Ordem excluída permanentemente da lixeira!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500



@app.route('/atualizar_status_lote_por_indice', methods=['POST'])
def atualizar_status_lote_por_indice():
    try:
        dados = request.get_json()
        if not dados:
            return jsonify({'sucesso': False, 'mensagem': 'Dados inválidos enviados.'}), 400
            
        data_consulta = str(dados.get('data', '')).strip()
        itens_selecionados = dados.get('itens', [])
        novo_status = str(dados.get('status', 'Não')).strip()
        
        if not data_consulta or not itens_selecionados:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum item selecionado.'}), 400
            
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(PLANILHA_BAIXAS) if 'PLANILHA_BAIXAS' in globals() else gc.open("BAIXAS")
        
        try:
            aba = planilha.worksheet(data_consulta)
        except Exception:
            return jsonify({'sucesso': False, 'mensagem': f'Aba da data {data_consulta} não encontrada.'}), 400
            
        linhas_planilha = aba.get_all_values()
        if not linhas_planilha or len(linhas_planilha) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'A planilha está vazia.'}), 400
            
        cabecalhos = [str(c).strip().upper() for c in linhas_planilha[0]]
        
        col_status_idx = None
        for i, h in enumerate(cabecalhos):
            if "STATUS" in h:
                col_status_idx = i + 1
                break
        if not col_status_idx:
            col_status_idx = 7 
            
        contador = 0
        
        for idx_l, linha in enumerate(linhas_planilha):
            if idx_l == 0:
                continue
                
            codigo_celula = str(linha[2]).strip() if len(linha) > 2 else ""
            tipo_celula = str(linha[4]).strip() if len(linha) > 4 else ""
            
            for item in itens_selecionados:
                item_codigo = str(item.get('codigo', '')).strip()
                item_tipo = str(item.get('tipo', '')).strip()
                
                if codigo_celula == item_codigo and tipo_celula == item_tipo:
                    aba.update_cell(idx_l + 1, col_status_idx, novo_status)
                    contador += 1
                    break
                
        return jsonify({'sucesso': True, 'mensagem': f'Status atualizado com sucesso em {contador} item(ns)!'})
        
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro interno no servidor: {str(e)}'}), 500





@app.route('/validar_saldo_erp_inventario', methods=['POST'])
def validar_saldo_erp_inventario():
    try:
        dados = request.get_json()
        txt_saldos = dados.get('texto_saldos', '')
        itens_visiveis = dados.get('itens', [])
        
        if not txt_saldos.strip():
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum saldo informado.'}), 400

        saldos_dict = {}
        for linha in txt_saldos.strip().split('\n'):
            partes = linha.strip().split()
            if len(partes) >= 2:
                codigo = partes[0].strip().upper()
                try:
                    # Trata o formato do ERP: remove ponto de milhar e converte vírgula decimal
                    val_str = partes[1].strip()
                    if '.' in val_str and ',' in val_str:
                        val_str = val_str.replace('.', '').replace(',', '.')
                    elif ',' in val_str:
                        val_str = val_str.replace(',', '.')
                    elif '.' in val_str:
                        # Se for no formato "8.000" (milhar sem decimal)
                        val_str = val_str.replace('.', '')
                    
                    qtd = float(val_str)
                    saldos_dict[codigo] = saldos_dict.get(codigo, 0.0) + qtd
                except ValueError:
                    continue

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        for item in itens_visiveis:
            aba = item.get('Aba')
            linha = int(item.get('Linha'))
            codigo_ordem = str(item.get('Codigo', '')).strip().upper()
            
            try:
                qtd_str = str(item.get('Quantidade', '1')).strip()
                limpa_qtd = ''.join(c for c in qtd_str if c.isdigit() or c in '.,')
                qtd_ordem = float(limpa_qtd.replace('.', '').replace(',', '.')) if limpa_qtd else 1.0
            except ValueError:
                qtd_ordem = 1.0

            if codigo_ordem in saldos_dict:
                saldo_erp = saldos_dict[codigo_ordem]
                # Soma o Saldo ERP com a Quantidade do item
                total_calculado = saldo_erp + qtd_ordem
                
                # Formata como inteiro limpo com ponto separador de milhar (ex: 10.752)
                val_int = int(round(total_calculado))
                val_formatado = f"{val_int:,}".replace(",", ".")

                if total_calculado >= 0:
                    status_erp_str = f"Suficiente ({val_formatado})"
                else:
                    status_erp_str = f"Insuficiente ({val_formatado})"
            else:
                status_erp_str = "Insuficiente (0)"

            item['Status_ERP'] = status_erp_str

            # Atualiza na planilha do Google Sheets a coluna Status_ERP
            try:
                sheet_mov_aba = planilha_mov.worksheet(aba)
                cabecalho = [col.upper().strip() for col in sheet_mov_aba.row_values(1)]
                col_erp_idx = None
                for idx, col in enumerate(cabecalho):
                    if col == 'STATUS_ERP':
                        col_erp_idx = idx + 1
                        break
                if not col_erp_idx:
                    col_erp_idx = len(cabecalho) + 1
                    if col_erp_idx > sheet_mov_aba.col_count:
                        sheet_mov_aba.add_cols(col_erp_idx - sheet_mov_aba.col_count)
                    sheet_mov_aba.update_cell(1, col_erp_idx, 'STATUS_ERP')
                sheet_mov_aba.update_cell(linha, col_erp_idx, status_erp_str)
            except Exception:
                pass

        return jsonify({'sucesso': True, 'itens': itens_visiveis, 'mensagem': 'Validação de Saldo ERP concluída!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500





@app.route('/atualizar_ive', methods=['POST'])
def atualizar_ive():
    try:
        dados = request.get_json() if request.is_json else request.form
        ive_marcado = dados.get('ive_status', 'Não')
        aba_alvo = dados.get('aba')
        linha_alvo = dados.get('linha')
        
        if not aba_alvo or not linha_alvo:
            return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos.'}), 400

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        sheet_mov = planilha_mov.worksheet(aba_alvo)
        
        cabecalho = [col.upper().strip() for col in sheet_mov.row_values(1)]
        coluna_idx = None
        
        for idx, col in enumerate(cabecalho):
            if col == 'IVE':
                coluna_idx = idx + 1
                break
                
        # Se a coluna IVE não existir, cria-a garantindo que a aba tem colunas suficientes
        if not coluna_idx:
            coluna_idx = len(cabecalho) + 1
            if coluna_idx > sheet_mov.col_count:
                sheet_mov.add_cols(coluna_idx - sheet_mov.col_count)
            sheet_mov.update_cell(1, coluna_idx, 'IVE')
            
        sheet_mov.update_cell(int(linha_alvo), coluna_idx, "Sim" if ive_marcado == "Sim" else "Não")
        
        if request.is_json:
            return jsonify({'sucesso': True, 'mensagem': 'IVE atualizado com sucesso!'})
        else:
            return redirect(url_for('index'))
            
    except Exception as e:
        if request.is_json:
            return jsonify({'sucesso': False, 'mensagem': str(e)}), 500
        return f"Erro: {str(e)}", 500




@app.route('/validar_saldo_erp', methods=['POST'])
def validar_saldo_erp():
    txt_saldos = request.form.get('txt_saldos_erp', '')
    aba_origem = request.form.get('aba_origem', 'inventario')
    
    try:
        if txt_saldos.strip():
            saldos_dict = {}
            for linha in txt_saldos.strip().split('\n'):
                partes = linha.strip().split()
                if len(partes) >= 2:
                    codigo = partes[0].strip().upper()
                    try:
                        qtd = float(partes[1].replace(',', '.'))
                        saldos_dict[codigo] = saldos_dict.get(codigo, 0.0) + qtd
                    except ValueError:
                        continue

            gc = conectar_google_sheets()
            if gc:
                planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
                todas_abas = planilha_mov.worksheets()
                
                for aba in todas_abas:
                    # Verifica se a aba é uma data válida (ex: 2026-10-15)
                    try:
                        datetime.strptime(aba.title.strip(), '%Y-%m-%d')
                    except ValueError:
                        continue
                    
                    linhas = aba.get_all_values()
                    if not linhas or len(linhas) < 2:
                        continue
                        
                    cabecalhos = [str(c).strip().upper() for c in linhas[0]]
                    if "CÓDIGO" not in cabecalhos and "CODIGO" not in cabecalhos:
                        continue
                        
                    col_codigo_idx = cabecalhos.index("CÓDIGO") + 1 if "CÓDIGO" in cabecalhos else cabecalhos.index("CODIGO") + 1
                    col_qtd_idx = cabecalhos.index("QUANTIDADE") + 1 if "QUANTIDADE" in cabecalhos else 4
                    
                    # Identifica ou cria a coluna Status_ERP se não existir
                    if "STATUS_ERP" not in cabecalhos:
                        col_erp_idx = len(cabecalhos) + 1
                        if col_erp_idx > aba.col_count:
                            aba.add_cols(col_erp_idx - aba.col_count)
                        aba.update_cell(1, col_erp_idx, "Status_ERP")
                    else:
                        col_erp_idx = cabecalhos.index("STATUS_ERP") + 1
                        
                    cell_updates = []
                    for idx_l, linha_vals in enumerate(linhas[1:], start=2):
                        if not any(linha_vals):
                            continue
                        
                        # Proteção contra linhas curtas
                        if len(linha_vals) < (col_codigo_idx - 1) or len(linha_vals) < (col_qtd_idx - 1):
                            continue
                            
                        codigo_item = str(linha_vals[col_codigo_idx - 1]).strip().upper()
                        
                        try:
                            qtd_str = str(linha_vals[col_qtd_idx - 1]).strip()
                            limpa_qtd = ''.join(c for c in qtd_str if c.isdigit() or c in '.,')
                            qtd_ordem = float(limpa_qtd.replace(',', '.')) if limpa_qtd else 1.0
                        except ValueError:
                            qtd_ordem = 1.0
                            
                        if codigo_item in saldos_dict:
                            saldo_erp = saldos_dict[codigo_item]
                            status_str = f"Suficiente ({int(saldo_erp)})" if saldo_erp >= qtd_ordem else f"Insuficiente ({int(saldo_erp)})"
                        else:
                            status_str = "Insuficiente (0)"
                            
                        cell_updates.append({
                            'range': gspread.utils.rowcol_to_a1(idx_l, col_erp_idx),
                            'values': [[status_str]]
                        })
                        
                    if cell_updates:
                        aba.batch_update(cell_updates)
                        
        flash("Validação de Saldo do ERP concluída com sucesso!", "success")
    except Exception as e:
        print(f"Erro ao validar saldo ERP: {e}")
        flash(f"Erro ao processar validação ERP: {e}", "danger")
        
    return redirect(url_for('index'))



@app.route('/salvar_suficiente_erp', methods=['POST'])
def salvar_suficiente_erp():
    try:
        dados = request.get_json()
        itens = dados.get('itens', [])
        
        if not itens:
            return jsonify({'sucesso': True, 'mensagem': 'Nenhum item para salvar.'})
            
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        atualizacoes_por_aba = {}
        for item in itens:
            nome_aba = item.get('aba')
            linha_idx = int(item.get('linha'))
            valor_suficiente = item.get('valor')
            
            if not nome_aba:
                continue
                
            if nome_aba not in atualizacoes_por_aba:
                atualizacoes_por_aba[nome_aba] = []
            atualizacoes_por_aba[nome_aba].append((linha_idx, valor_suficiente))
            
        for nome_aba, lista_valores in atualizacoes_por_aba.items():
            try:
                worksheet = planilha_mov.worksheet(nome_aba)
            except Exception:
                continue
            
            # Garante que a aba tem pelo menos 9 colunas (Coluna I)
            if worksheet.col_count < 9:
                worksheet.add_cols(9 - worksheet.col_count)
                
            cell_updates = []
            for linha_idx, valor in lista_valores:
                try:
                    # Extrai apenas os dígitos do valor recebido para evitar qualquer sujeira de texto ou notação
                    s_val = str(valor)
                    # Se vier no formato "Suficiente (10.752)" ou similar, extrai os números
                    apenas_digitos = ''.join(c for c in s_val if c.isdigit())
                    if apenas_digitos:
                        val_limpo = int(apenas_digitos)
                    else:
                        val_limpo = 0
                except (ValueError, TypeError):
                    val_limpo = 0

                cell_address = gspread.utils.rowcol_to_a1(linha_idx, 9)
                cell_updates.append({
                    'range': cell_address,
                    'values': [[val_limpo]]
                })
                
            if cell_updates:
                worksheet.batch_update(cell_updates, value_input_option='USER_ENTERED')
            
        return jsonify({'sucesso': True, 'mensagem': 'Valores suficientes salvos na coluna I com sucesso!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500




@app.route('/exportar_excel_consultar_baixas', methods=['GET'])
def exportar_excel_consultar_baixas():
    data_consulta = request.args.get('data', '').strip()
    filtro_tipo = request.args.get('tipo', 'TODOS').strip().lower()
    filtro_status_erp = request.args.get('status_erp', 'TODOS').strip().lower()

    if not data_consulta:
        return "Data não informada.", 400

    try:
        gc = conectar_google_sheets()
        if not gc:
            return "Erro de conexão com o Google Sheets.", 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        
        try:
            sheet_mov = planilha_mov.worksheet(data_consulta)
        except gspread.exceptions.WorksheetNotFound:
            return f"Nenhuma baixa encontrada para o dia {data_consulta}.", 404

        linhas = sheet_mov.get_all_values()
        if not linhas or len(linhas) < 2:
            return "A planilha está vazia.", 404

        cabecalhos = [str(c).strip().upper() for c in linhas[0]]

        import csv
        from io import StringIO
        from flask import Response

        output = StringIO()
        writer = csv.writer(output, delimiter=';')
        
        # Cabeçalho do CSV correspondente à aba de Consulta
        writer.writerow(['Data', 'Código', 'Quantidade', 'Tipo', 'Status ERP', 'Status Baixa'])

        for idx in range(1, len(linhas)):
            row = linhas[idx]
            if not any(row):
                continue
            
            # Extração segura dos campos
            data_val = row[cabecalhos.index("DATA")] if "DATA" in cabecalhos and len(row) > cabecalhos.index("DATA") else data_consulta
            codigo_val = row[cabecalhos.index("CÓDIGO")] if "CÓDIGO" in cabecalhos and len(row) > cabecalhos.index("CÓDIGO") else (row[cabecalhos.index("CODIGO")] if "CODIGO" in cabecalhos and len(row) > cabecalhos.index("CODIGO") else "")
            qtd_val = row[cabecalhos.index("QUANTIDADE")] if "QUANTIDADE" in cabecalhos and len(row) > cabecalhos.index("QUANTIDADE") else "1"
            tipo_val = row[cabecalhos.index("TIPO")] if "TIPO" in cabecalhos and len(row) > cabecalhos.index("TIPO") else "Baixa Diaria"
            status_val = row[cabecalhos.index("STATUS")] if "STATUS" in cabecalhos and len(row) > cabecalhos.index("STATUS") else "Não"
            
            # Status ERP (se existir coluna)
            status_erp_val = "Não informado"
            if "STATUS_ERP" in cabecalhos and len(row) > cabecalhos.index("STATUS_ERP"):
                status_erp_val = row[cabecalhos.index("STATUS_ERP")] or "Não informado"

            # Aplicação dos filtros do utilizador
            if filtro_tipo != 'todos' and tipo_val.strip().lower() != filtro_tipo:
                continue

            if filtro_status_erp != 'todos':
                if filtro_status_erp == 'ok' and 'ok' not in status_erp_val.lower():
                    continue
                if filtro_status_erp == 'insuficiente' and 'insuficiente' not in status_erp_val.lower():
                    continue

            writer.writerow([data_val, codigo_val, qtd_val, tipo_val, status_erp_val, status_val])

        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment;filename=consultar_baixas_{data_consulta}.csv"}
        )

    except Exception as e:
        return f"Erro ao exportar Excel: {str(e)}", 500





@app.route('/consultar_baguetes_periodo', methods=['GET'])
def consultar_baguetes_periodo():
    inicio_str = request.args.get('inicio')
    fim_str = request.args.get('fim')
    
    if not inicio_str or not fim_str:
        return jsonify({'sucesso': False, 'mensagem': 'Datas de início e fim obrigatórias.'}), 400
    
    try:
        data_inicio = datetime.strptime(inicio_str.strip(), '%Y-%m-%d')
        data_fim = datetime.strptime(fim_str.strip(), '%Y-%m-%d')
        
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_mov = gc.open(NOME_PLANILHA_MOVIMENTACAO)
        lista_perdas = []
        lista_sucatas = []
        todas_abas = planilha_mov.worksheets()
        
        tipos_perdas_validos = ['baixa diaria', 'perfil maior', 'perfil menor', 'erro no corte', 'outros']

        prefixos_map = {
            "B219P-": "BJ03",
            "B219-": "BJ05",
            "B072-": "BJ06L",
            "B708-": "BJ06L",
            "B120-": "BJ06L",
            "B249-": "BB01B",
            "B248-": "BB01B",
            "B221-": "BJ05",
            "B013-": "BJ03"
        }

        for aba in todas_abas:
            nome_aba = aba.title.strip()
            
            # Limpa eventuais horários ou caracteres extras no nome da aba (ex: "2026-10-19 00:00:00" -> "2026-10-19")
            if ' ' in nome_aba:
                nome_aba = nome_aba.split(' ')[0]
            if 'T' in nome_aba:
                nome_aba = nome_aba.split('T')[0]

            try:
                # Tenta converter a aba para data
                data_aba = datetime.strptime(nome_aba, '%Y-%m-%d')
                
                # Verifica se está dentro do período selecionado
                if data_inicio <= data_aba <= data_fim:
                    linhas = aba.get_all_values()
                    if not linhas or len(linhas) < 2:
                        continue
                    
                    cabecalhos = [str(c).strip().upper() for c in linhas[0]]
                    
                    for index in range(1, len(linhas)):
                        row = linhas[index]
                        if not any(row):
                            continue
                        
                        # Extrai Código
                        codigo_val = ''
                        for h in ["CÓDIGO", "CODIGO"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                codigo_val = str(row[cabecalhos.index(h)]).strip()
                                break
                        if not codigo_val and len(row) > 2:
                            codigo_val = str(row[2]).strip()

                        if not codigo_val.upper().startswith('B'):
                            continue

                        # Extrai Tipo
                        tipo_val = 'Baixa Diaria'
                        for h in ["TIPO"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                tipo_val = str(row[cabecalhos.index(h)]).strip()
                                break
                        if tipo_val == 'Baixa Diaria' and len(row) > 4:
                            tipo_val = str(row[4]).strip()

                        tipo_lower = tipo_val.lower()
                        is_sucata = 'aproveitamento de sucata' in tipo_lower
                        is_perda = any(p in tipo_lower for p in tipos_perdas_validos)

                        if not is_sucata and not is_perda:
                            continue

                        # Identifica o Perfil verificando os prefixos
                        codigo_up = codigo_val.upper()
                        perfil_atribuido = None
                        for pref, prof in prefixos_map.items():
                            if codigo_up.startswith(pref):
                                perfil_atribuido = prof
                                break

                        if not perfil_atribuido:
                            continue

                        # Extrai Quantidade
                        qtd_val = 1.0
                        for h in ["QUANTIDADE"]:
                            if h in cabecalhos and len(row) > cabecalhos.index(h):
                                try:
                                    qtd_val = float(str(row[cabecalhos.index(h)]).replace(',', '.'))
                                except:
                                    qtd_val = 1.0
                                break

                        # Extrai Medida (parte final após o último hífen, dividida por 10 para ajustar a vírgula ex: 218 -> 21.8)
                        medida_val = 0.0
                        if '-' in codigo_val:
                            partes = codigo_val.split('-')
                            parte_final = partes[-1].strip()
                            try:
                                medida_val = float(parte_final.replace(',', '.')) / 10.0
                            except:
                                medida_val = 0.0

                        item_dict = {
                            "data": nome_aba,
                            "codigo": codigo_val,
                            "quantidade": qtd_val,
                            "tipo": tipo_val,
                            "perfil": perfil_atribuido,
                            "medida": medida_val
                        }

                        if is_sucata:
                            lista_sucatas.append(item_dict)
                        else:
                            lista_perdas.append(item_dict)

            except ValueError:
                # Ignora abas que não têm formato de data (ex: "Resumo", "Página1", etc.)
                continue

        return jsonify({'sucesso': True, 'perdas': lista_perdas, 'sucatas': lista_sucatas})
    
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500




@app.route('/atualizar_status_lote', methods=['POST'])
def atualizar_status_lote():
    try:
        dados = request.get_json()
        ids = dados.get('ids', [])
        feito_val = dados.get('feito', False)
        
        if not ids:
            return jsonify({'sucesso': False, 'mensagem': 'Nenhum ID fornecido.'}), 400
            
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        
        ids_restantes = set(ids)
        
        for aba in planilha.worksheets():
            if not ids_restantes:
                break
            if aba.title.upper() in ["LIXEIRA", "DADOS"]:
                continue
                
            linhas = aba.get_all_values()
            if not linhas or len(linhas) < 2:
                continue
                
            cabecalhos = [str(c).strip().upper() for c in linhas[0]]
            if "ID" not in cabecalhos:
                continue
                
            col_id_idx = cabecalhos.index("ID") + 1
            col_status_idx = None
            for h in ["STATUS", "FEITO"]:
                if h in cabecalhos:
                    col_status_idx = cabecalhos.index(h) + 1
                    break
            if not col_status_idx:
                col_status_idx = len(cabecalhos) + 1
                aba.update_cell(1, col_status_idx, "Status")
                
            for idx, row in enumerate(linhas[1:], start=2):
                val_id = str(row[col_id_idx - 1]).strip() if len(row) >= col_id_idx else ""
                if val_id in ids_restantes:
                    aba.update_cell(idx, col_status_idx, "Sim" if feito_val else "Não")
                    ids_restantes.remove(val_id)
                    
        return jsonify({'sucesso': True, 'mensagem': 'Lote atualizado com sucesso!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/listar_lixeira', methods=['GET'])
def listar_lixeira():
    try:
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        try:
            aba_lixeira = planilha.worksheet("LIXEIRA")
        except:
            return jsonify({'sucesso': True, 'itens': []})
            
        linhas = aba_lixeira.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': True, 'itens': []})
            
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        itens = []
        
        for idx, row in enumerate(linhas[1:], start=2):
            if not any(row):
                continue
            
            # Ajuste exato dos índices para corresponder à sua planilha:
            # Coluna A (0): ID
            # Coluna B (1): Data
            # Coluna C (2): Código Principal
            # Coluna D (3): Quantidade Ordem
            val_id = row[cabecalhos.index("ID")] if "ID" in cabecalhos and len(row) > cabecalhos.index("ID") else (row[0] if len(row) > 0 else str(idx))
            val_data = row[1] if len(row) > 1 else ""                  # Coluna B
            val_codigo = row[2] if len(row) > 2 else ""              # Coluna C
            val_qtd = row[3] if len(row) > 3 else ""                     # Coluna D

            itens.append({
                "linha": idx,
                "id": str(val_id).strip(),
                "data": val_data,
                "codigoPrincipal": val_codigo,
                "quantidadeOrdem": val_qtd
            })
            
        return jsonify({'sucesso': True, 'itens': itens})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500




@app.route('/remover_definitivo_lixeira', methods=['POST'])
def remover_definitivo_lixeira():
    try:
        dados = request.get_json()
        id_item = str(dados.get('id', '')).strip()
        
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        aba_lixeira = planilha.worksheet("LIXEIRA")
        
        linhas = aba_lixeira.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Lixeira vazia.'}), 404
            
        cabecalhos = [str(c).strip().upper() for c in linhas[0]]
        col_id_idx = cabecalhos.index("ID") if "ID" in cabecalhos else 0
        
        for idx, row in enumerate(linhas[1:], start=2):
            val_id = str(row[col_id_idx]).strip() if len(row) > col_id_idx else ""
            if val_id == id_item:
                aba_lixeira.delete_rows(idx)
                return jsonify({'sucesso': True, 'mensagem': 'Item excluído definitivamente!'})
                
        return jsonify({'sucesso': False, 'mensagem': 'Item não encontrado na lixeira.'}), 404
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500
