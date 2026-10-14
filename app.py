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
        
        col_id_idx = 1 # Por padrão o ID fica na coluna 1
        col_status_idx = None
        
        for i, h in enumerate(cabecalhos):
            if h == "ID":
                col_id_idx = i + 1
            if "STATUS" in h:
                col_status_idx = i + 1
                
        if not col_status_idx:
            col_status_idx = 6 
            
        contador = 0
        
        for idx_l, linha in enumerate(linhas_planilha):
            if idx_l == 0:
                continue
                
            id_celula = str(linha[col_id_idx - 1]).strip() if len(linha) >= col_id_idx else ""
            
            if id_celula in ids_selecionados:
                aba.update_cell(idx_l + 1, col_status_idx, novo_status)
                contador += 1
                
        return jsonify({'sucesso': True, 'mensagem': f'Status atualizado com sucesso em {contador} item(ns)!'})
        
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro interno no servidor: {str(e)}'}), 500




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
            aba_dia = planilha.add_worksheet(title=data_hoje, rows="1000", cols="5")
            # Adiciona o cabeçalho limpo na aba nova (apenas 3 colunas)
            aba_dia.append_row(["Data", "Código Principal", "Quantidade Ordem"])
            
        # Agrupa os itens para garantir que salvamos apenas uma linha única por Código Principal/Ordem
        ordens_unicas = {}
        for item in itens_ordem:
            cod_prin = item.get('CodigoPrincipal', '')
            qtd_ord = item.get('QuantidadeOrdem', 0)
            if cod_prin:
                ordens_unicas[cod_prin] = qtd_ord

        # Adiciona apenas uma linha limpa por ordem única na aba do dia
        for cod_prin, qtd_ord in ordens_unicas.items():
            aba_dia.append_row([
                data_hoje,
                cod_prin,
                qtd_ord
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
    try:
        data_filtro = request.args.get('data', '').strip()
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500
        
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        todas_abas = planilha.worksheets()
        lista_ordens = []
        
        for aba in todas_abas:
            nome_aba = aba.title.strip()
            
            if data_filtro and nome_aba != data_filtro:
                continue
                
            try:
                datetime.strptime(nome_aba, '%Y-%m-%d')
            except ValueError:
                continue 
            
            registos = aba.get_all_records()
            for index, r in enumerate(registos):
                linha_planilha = index + 2
                codigo_principal = str(r.get('CodigoPrincipal') or r.get('Código Principal') or '').strip()
                qtd_ordem = r.get('QuantidadeOrdem') or r.get('Quantidade Ordem') or 0
                status_feito = str(r.get('Status') or r.get('Feito') or '').strip().lower() in ['true', 'sim', '1', 'yes', 'feito']
                
                if not codigo_principal:
                    continue
                
                lista_ordens.append({
                    "id": f"{nome_aba}|{linha_planilha}",
                    "data": nome_aba,
                    "codigoPrincipal": codigo_principal,
                    "quantidadeOrdem": qtd_ordem,
                    "feito": status_feito
                })
                
        return jsonify({'sucesso': True, 'ordens': lista_ordens})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


# Nova rota para alternar o status de "Feito" diretamente na linha da planilha
@app.route('/alternar_status_ordem', methods=['POST'])
def alternar_status_ordem():
    try:
        dados = request.get_json()
        id_ordem = dados.get('id', '')
        feito_val = dados.get('feito', False) # True ou False
        
        if '|' not in id_ordem:
            return jsonify({'sucesso': False, 'mensagem': 'ID inválido.'}), 400
            
        nome_aba, linha_str = id_ordem.split('|')
        linha_alvo = int(linha_str)
        
        gc = conectar_google_sheets()
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        aba = planilha.worksheet(nome_aba)
        
        # Procura qual é a coluna correspondente a "Status" ou "Feito", se não existir adiciona
        cabecalhos = aba.row_values(1)
        if "Status" not in cabecalhos:
            # Adiciona a coluna Status na frente (ex: coluna D)
            aba.update_cell(1, 4, "Status")
            cabecalhos = aba.row_values(1)
            
        col_idx = cabecalhos.index("Status") + 1
        aba.update_cell(linha_alvo, col_idx, "Sim" if feito_val else "Não")
        
        return jsonify({'sucesso': True, 'mensagem': 'Status atualizado com sucesso na planilha!'})
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
    data_lancamento = dados.get('data') # Formato YYYY-MM-DD
    itens = dados.get('itens', [])

    if not data_lancamento or not itens:
        return jsonify({'sucesso': False, 'mensagem': 'Data ou itens de perda não informados!'}), 400

    try:
        data_obj = datetime.strptime(data_lancamento, '%Y-%m-%d')
        nome_aba_mes = data_obj.strftime('%Y-%m')

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        try:
            planilha_perdas = gc.open(NOME_PLANILHA_PERDAS)
        except gspread.exceptions.SpreadsheetNotFound:
            return jsonify({'sucesso': False, 'mensagem': f'A planilha "{NOME_PLANILHA_PERDAS}" não foi encontrada no Google Drive.'}), 500

        try:
            sheet_mes = planilha_perdas.worksheet(nome_aba_mes)
        except gspread.exceptions.WorksheetNotFound:
            sheet_mes = planilha_perdas.add_worksheet(title=nome_aba_mes, rows="1000", cols="6")
            sheet_mes.append_row(["Data", "Tipo de Perda", "Quantidade", "Perfil", "Usuário/Registro", "Qtd_Sistema"])

        linhas_para_adicionar = []
        for item in itens:
            tipo_perda = item.get('tipo', '')
            
            try:
                raw_val = str(item.get('quantidade', 0)).replace(',', '.').strip()
                val_float = float(raw_val)
            except (ValueError, TypeError):
                val_float = 0.0
                
            # Coluna C (Quantidade pura normal para os seus dashboards e somas)
            quantidade_pura = val_float
            
            # Coluna F (Quantidade protegida com apóstrofe e vírgula para o sistema ler sem erros)
            quantidade_protegida = f"'{val_float:.3f}".replace('.', ',')
                
            perfil = item.get('perfil', 'Não')
            
            # Ordem: Data, Tipo de Perda, Quantidade (C), Perfil, Usuário/Registro, Qtd_Sistema (F)
            linhas_para_adicionar.append([data_lancamento, tipo_perda, quantidade_pura, perfil, 'Sistema', quantidade_protegida])

        # RAW obriga o Google Sheets a aceitar o apóstrofe na Coluna F sem tentar convertê-lo
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
    dados = request.get_json()
    data_reg = dados.get('data')
    tipo_reg = dados.get('tipo')
    quantidade_reg = str(dados.get('quantidade', '')).strip()
    perfil_reg = str(dados.get('perfil', '')).strip()
    
    if not data_reg or not tipo_reg:
        return jsonify({'sucesso': False, 'mensagem': 'Dados insuficientes para exclusão.'}), 400

    try:
        data_obj = datetime.strptime(data_reg, '%Y-%m-%d')
        nome_aba_mes = data_obj.strftime('%Y-%m')

        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão com o Google Sheets.'}), 500

        planilha_perdas = gc.open(NOME_PLANILHA_PERDAS)
        try:
            sheet_mes = planilha_perdas.worksheet(nome_aba_mes)
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({'sucesso': False, 'mensagem': 'Aba do mês não encontrada.'}), 404

        registos = sheet_mes.get_all_records()
        
        linha_para_apagar = None
        for i, r in enumerate(registos):
            d = str(r.get('Data') or r.get('DATA') or '').strip()
            t = str(r.get('Tipo de Perda') or r.get('TIPO DE PERDA') or '').strip()
            
            # Pega a quantidade da coluna de sistema (F) ou quantidade pura (C)
            q = str(r.get('Qtd_Sistema') or r.get('QTD_SISTEMA') or r.get('Quantidade') or r.get('QUANTIDADE') or '').replace("'", "").strip()
            p = str(r.get('Perfil') or r.get('PERFIL') or '').strip()
            
            # Compara todos os campos para garantir que é exatamente o item escolhido
            if d == data_reg and t.lower() == tipo_reg.lower() and q == quantidade_reg and p.lower() == perfil_reg.lower():
                linha_para_apagar = i + 2  # i + 2 porque o get_all_records exclui o cabeçalho
                break

        # Fallback de segurança caso não ache exato por todos os campos (ex: divergência de formatação decimal)
        if not linha_para_apagar:
            for i, r in enumerate(registos):
                d = str(r.get('Data') or r.get('DATA') or '').strip()
                t = str(r.get('Tipo de Perda') or r.get('TIPO DE PERDA') or '').strip()
                if d == data_reg and t.lower() == tipo_reg.lower():
                    linha_para_apagar = i + 2
                    break

        if linha_para_apagar:
            sheet_mes.delete_rows(linha_para_apagar)
            return jsonify({'sucesso': True, 'mensagem': 'Perda excluída com sucesso!'})
        else:
            return jsonify({'sucesso': False, 'mensagem': 'Registo não encontrado na planilha.'}), 404

    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': f'Erro ao excluir: {str(e)}'}), 500




# ==========================================
# ROTAS DO SISTEMA DE LIXEIRA E ORDENS CRIADAS
# ==========================================

@app.route('/mover_para_lixeira', methods=['POST'])
def mover_para_lixeira():
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
        
        # 1. Obtém os dados da linha na aba original do dia
        aba_origem = planilha.worksheet(nome_aba)
        linhas = aba_origem.get_all_values()
        
        if linha_alvo > len(linhas):
            return jsonify({'sucesso': False, 'mensagem': 'Linha não encontrada na aba.'}), 404
            
        dados_linha = linhas[linha_alvo - 1] # Index base 0
        
        # 2. Verifica se a aba "LIXEIRA" existe, senão cria-a automaticamente
        try:
            aba_lixeira = planilha.worksheet("LIXEIRA")
        except gspread.exceptions.WorksheetNotFound:
            aba_lixeira = planilha.add_worksheet(title="LIXEIRA", rows="1000", cols="5")
            aba_lixeira.append_row(["Data Original", "Código Principal", "Quantidade Ordem"])
            
        # 3. Adiciona na lixeira e remove da aba original
        aba_lixeira.append_row(dados_linha)
        aba_origem.delete_rows(linha_alvo)
        
        return jsonify({'sucesso': True, 'mensagem': 'Ordem movida para a lixeira com sucesso!'})
    except Exception as e:
        return jsonify({'sucesso': False, 'mensagem': str(e)}), 500


@app.route('/listar_lixeira', methods=['GET'])
def listar_lixeira():
    try:
        gc = conectar_google_sheets()
        if not gc:
            return jsonify({'sucesso': False, 'mensagem': 'Erro de conexão.'}), 500
            
        planilha = gc.open(PLANILHA_CRIAR_ORDENS)
        try:
            aba_lixeira = planilha.worksheet("LIXEIRA")
        except gspread.exceptions.WorksheetNotFound:
            return jsonify({'sucesso': True, 'ordens': []})
            
        registos = aba_lixeira.get_all_records()
        lista_lixeira = []
        
        for index, r in enumerate(registos):
            linha_planilha = index + 2
            data_orig = str(r.get('Data') or r.get('Data Original') or '').strip()
            codigo = str(r.get('CodigoPrincipal') or r.get('Código Principal') or '').strip()
            qtd = r.get('QuantidadeOrdem') or r.get('Quantidade Ordem') or 0
            
            if not codigo:
                continue
                
            lista_lixeira.append({
                "id": f"LIXEIRA|{linha_planilha}",
                "data": data_orig,
                "codigoPrincipal": codigo,
                "quantidadeOrdem": qtd
            })
            
        return jsonify({'sucesso': True, 'ordens': lista_lixeira})
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
