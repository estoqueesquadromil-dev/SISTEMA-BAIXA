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
        
        # OTIMIZAÇÃO: Obtém todas as abas de uma só vez (1 único pedido à API do Google Sheets)
        todas_abas = planilha_mov.worksheets()
        
        for aba in todas_abas:
            nome_aba = aba.title.strip()
            try:
                # Tenta converter o nome da aba para data (formato YYYY-MM-DD)
                data_aba = datetime.strptime(nome_aba, '%Y-%m-%d')
                
                # Verifica se está dentro do período selecionado
                if data_inicio <= data_aba <= data_fim:
                    registos = aba.get_all_records()
                    
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
            except ValueError:
                # Ignora abas que não estejam no formato de data (ex: abas de testes ou rascunhos)
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
    
    try:
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
        
        # Gera o ficheiro CSV compatível com Excel
        csv_data = df.to_csv(index=False, sep=';', encoding='utf-8-sig')
        
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename=inventario_{inicio_str}_a_{fim_str}.csv"}
        )
        
    except Exception as e:
        return f"Erro ao gerar relatório: {str(e)}", 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)








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
        data_hoje = dados.get('data') # formato YYYY-MM-DD
        itens_ordem = dados.get('itens', [])
        
        if not data_hoje or not itens_ordem:
            return jsonify({'sucesso': False, 'mensagem': 'Dados incompletos para salvar a ordem.'}), 400
            
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
            
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        
        # Verifica se já existe uma aba com a data do dia, senão cria uma nova
        try:
            aba_dia = planilha.worksheet(data_hoje)
        except:
            aba_dia = planilha.add_worksheet(title=data_hoje, rows="1000", cols="10")
            # Adiciona o cabeçalho na aba nova
            aba_dia.append_row(["Data", "Código Principal", "Quantidade Ordem", "Componente / Perfil", "Qtd Ajustada"])
            
        # Adiciona cada linha da ordem gerada à aba do dia
        for item in itens_ordem:
            aba_dia.append_row([
                item.get('Data', data_hoje),
                item.get('CodigoPrincipal', ''),
                item.get('QuantidadeOrdem', 0),
                item.get('Componente', ''),
                item.get('QtdAjustada', 0)
            ])
            
        return jsonify({'sucesso': True, 'mensagem': f'Ordem de produção registada com sucesso na aba {data_hoje}!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


# ==========================================
# NOVAS ROTAS PARA O MÓDULO "ORDENS CRIADAS"
# ==========================================

@app.route('/listar_ordens_criadas', methods=['GET'])
def listar_ordens_criadas():
    try:
        data_filtro = request.args.get('data', '').strip()
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
        
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        todas_abas = planilha.worksheets()
        
        ordens_unicas_dict = {} # Agrupa por ordem única para listar na tabela principal
        
        for aba in todas_abas:
            nome_aba = aba.title.strip()
            
            # Se o utilizador aplicou um filtro de data, ignora outras abas
            if data_filtro and nome_aba != data_filtro:
                continue
                
            try:
                # Valida se o título da aba é uma data válida (YYYY-MM-DD)
                datetime.strptime(nome_aba, '%Y-%m-%d')
            except ValueError:
                continue # Ignora abas como "dados" ou outras que não sejam datas
            
            registos = aba.get_all_records()
            for index, r in enumerate(registos):
                linha_planilha = index + 2
                codigo_principal = str(r.get('CodigoPrincipal') or r.get('Código Principal') or '').strip()
                qtd_ordem = r.get('QuantidadeOrdem') or r.get('Quantidade Ordem') or 0
                
                if not codigo_principal:
                    continue
                
                # Identificador único combinando a aba (data) e o código/linha para exclusão
                # Como uma ordem pode ter múltiplos componentes, agrupamos pelo Código Principal e Quantidade na data
                chave_unica = f"{nome_aba}_{codigo_principal}_{qtd_ordem}"
                
                if chave_unica not in ordens_unicas_dict:
                    ordens_unicas_dict[chave_unica] = {
                        "id": f"{nome_aba}|{linha_planilha}", # Guarda a aba e a primeira linha de referência
                        "data": nome_aba,
                        "codigoPrincipal": codigo_principal,
                        "quantidadeOrdem": qtd_ordem
                    }
                    
        lista_ordens = list(ordens_unicas_dict.values())
        return jsonify({'sucesso': True, 'ordens': lista_ordens})
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
        
        # Lê todas as linhas para garantir que removemos todos os componentes associados àquele Código Principal e Quantidade na mesma data
        linhas = aba.get_all_values()
        if not linhas or len(linhas) < 2:
            return jsonify({'sucesso': False, 'mensagem': 'Aba vazia.'})
            
        cabecalho = [c.upper().strip() for c in linhas[0]]
        idx_cod = cabecalho.index('CODIGOPRINCIPAL') if 'CODIGOPRINCIPAL' in cabecalho else (cabecalho.index('CÓDIGO PRINCIPAL') if 'CÓDIGO PRINCIPAL' in cabecalho else 1)
        idx_qtd = cabecalho.index('QUANTIDADEORDEM') if 'QUANTIDADEORDEM' in cabecalho else (cabecalho.index('QUANTIDADE ORDEM') if 'QUANTIDADE ORDEM' in cabecalho else 2)
        
        if linha_alvo - 1 < len(linhas):
            linha_referencia = linhas[linha_alvo - 1]
            cod_alvo = linha_referencia[idx_cod]
            qtd_alvo = linha_referencia[idx_qtd]
            
            # Remove de baixo para cima para não alterar os índices durante a remoção
            linhas_para_remover = []
            for idx in range(len(linhas) - 1, 0, -1):
                if linhas[idx][idx_cod] == cod_alvo and linhas[idx][idx_qtd] == qtd_alvo:
                    linhas_para_remover.append(idx + 1)
                    
            for l_num in linhas_para_remover:
                aba.delete_rows(l_num)
                
            return jsonify({'sucesso': True, 'mensagem': 'Ordem excluída com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Linha não encontrada na aba.'}), 404
            
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/exportar_excel_ordens', methods=['GET'])
def exportar_excel_ordens():
    try:
        data_filtro = request.args.get('data', '').strip()
        gc = conectar_google_sheets()
        if not gc:
            return "Erro de conexão com o Google Sheets", 500
            
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        todas_abas = planilha.worksheets()
        
        dados_excel = []
        chaves_vistas = set()
        
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
                
                if not codigo_principal:
                    continue
                
                # Chave única para evitar duplicados na mesma data
                chave_unica = (nome_aba, codigo_principal, str(quantidade_ordem))
                if chave_unica not in chaves_vistas:
                    chaves_vistas.add(chave_unica)
                    dados_excel.append({
                        "Data": nome_aba,
                        "Código Principal": codigo_principal,
                        "Quantidade Ordem": quantidade_ordem
                    })
                
        if not dados_excel:
            return "Nenhuma ordem encontrada para exportar.", 404
            
        df = pd.DataFrame(dados_excel)
        csv_data = df.to_csv(index=False, sep=';', encoding='utf-8-sig')
        
        nome_arquivo = f"ordens_{data_filtro}.csv" if data_filtro else "ordens_criadas.csv"
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
NOME_PLANILHA_PERDAS = 'Perdas'

@app.route('/salvar_lote_perdas', methods=['POST'])
def salvar_lote_perdas():
    dados = request.get_json()
    data_lancamento = dados.get('data') # Formato YYYY-MM-DD
    itens = dados.get('itens', [])

    if not data_lancamento or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou itens de perda não informados!'}), 400

    try:
        # Extrai o ano e mês para definir o nome da aba (ex: '2026-10')
        data_obj = datetime.strptime(data_lancamento, '%Y-%m-%d')
        nome_aba_mes = data_obj.strftime('%Y-%m')

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        try:
            planilha_perdas = gc.open(NOME_PLANILHA_PERDAS)
        except gspread.exceptions.SpreadsheetNotFound:
            # Se a planilha não existir, cria-a (Nota: pode necessitar de partilha prévia na conta de serviço)
            # Como alternativa segura, criamos com gc.create() ou abrimos
            return jsonify({'sucesso': False, 'mensagem': f'A planilha "{NOME_PLANILHA_PERDAS}" não foi encontrada no Google Drive. Crie-a manualmente.'}), 500

        try:
            sheet_mes = planilha_perdas.worksheet(nome_aba_mes)
        except gspread.exceptions.WorksheetNotFound:
            # Cria a aba mensal com as colunas solicitadas
            sheet_mes = planilha_perdas.add_worksheet(title=nome_aba_mes, rows="1000", cols="5")
            sheet_mes.append_row(["Data", "Tipo de Perda", "Quantidade", "Perfil", "Usuário/Registro"])

        linhas_para_adicionar = []
        for item in itens:
            tipo_perda = item.get('tipo', '')
            quantidade = int(item.get('quantidade', 0))
            perfil = item.get('perfil', 'Não') # 'Sim' ou 'Não'
            
            linhas_para_adicionar.append([data_lancamento, tipo_perda, quantidade, perfil, 'Sistema'])

        sheet_mes.append_rows(linhas_para_adicionar)
        return jsonify({'sucesso': True, 'mensagem': f'Perdas guardadas com sucesso na aba {nome_aba_mes} da planilha Perdas!'})

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
            # Valida se é aba de mês (YYYY-MM)
            try:
                datetime.strptime(nome_aba + '-01', '%Y-%m-%d')
            except ValueError:
                continue

            registos = aba.get_all_records()
            for r in registos:
                d = str(r.get('Data') or r.get('DATA') or '')
                t = str(r.get('Tipo de Perda') or r.get('TIPO DE PERDA') or '')
                q = int(r.get('Quantidade') or r.get('QUANTIDADE') or 0)
                p = str(r.get('Perfil') or r.get('PERFIL') or 'Não')

                # Aplicação de filtros
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

        # Calcula os totais por tipo para exibir no painel resumido
        totais_por_tipo = {}
        for item in todos_registos:
            tp = item['Tipo']
            totais_por_tipo[tp] = totais_por_tipo.get(tp, 0) + item['Quantidade']

        return jsonify({
            'sucesso': True,
            'itens': todos_registos,
            'totais': totais_por_tipo
        })

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao consultar perdas: {str(e)}'}), 500

