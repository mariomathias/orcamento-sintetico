import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from pathlib import Path
import sys
from decimal import Decimal, getcontext, ROUND_DOWN


# ============================================================
# CONFIGURAÇÃO
# ============================================================

def obter_pasta_programa():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


PASTA_PROGRAMA = obter_pasta_programa()
DB_PATH = PASTA_PROGRAMA / "seinfra.db"
SINAPI_DB_PATH = PASTA_PROGRAMA / "sinapi_2026_08.db"
USER_DB_PATH = PASTA_PROGRAMA / "usuario.db"
SINAPI_REFERENCIA = "08/2026"

# Precisão alta para os cálculos hierárquicos do SINAPI.
# O arredondamento fica restrito à apresentação (tela/Excel).
getcontext().prec = 40
DECIMAL_ZERO = Decimal("0")
DECIMAL_CEM = Decimal("100")

def decimal_exato(valor):
    """Converte valores para Decimal sem arredondar."""
    if valor is None or valor == "":
        return DECIMAL_ZERO
    return Decimal(str(valor))


def truncar_2_casas(valor):
    """Mantém somente 2 casas decimais, sem arredondamento.

    Exemplo:
        12.349 -> 12.34
        12.341 -> 12.34

    O valor original do banco não é alterado. Esta função é usada somente
    quando um valor monetário passa a participar do cálculo de uma composição,
    de um subtotal ou de um total.
    """
    return decimal_exato(valor).quantize(Decimal("0.01"), rounding=ROUND_DOWN)



# ============================================================
# BANCO
# ============================================================

def conectar():
    conexao = sqlite3.connect(DB_PATH)
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao

def conectar_sinapi():
    conexao = sqlite3.connect(SINAPI_DB_PATH)
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def conectar_usuario():
    """Conecta ao banco persistente do usuário.

    Este arquivo deve ser preservado entre atualizações do aplicativo.
    """
    conexao = sqlite3.connect(USER_DB_PATH)
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def contexto_sinapi():
    # A referência SINAPI do sistema usa CE como base.
    # Quando um insumo não possui preço no CE, o preço de SP é usado como fallback.
    return "CE", "COM_DESONERACAO"


def banco_selecionado(nome_combo):
    combo = globals().get(nome_combo)
    return (combo.get().strip().upper() if combo else "SEINFRA") or "SEINFRA"


def obter_preco_personalizado(banco, codigo):
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()
    if not banco or not codigo:
        return None

    con = conectar_usuario()
    try:
        r = con.execute("""
            SELECT preco
            FROM precos_personalizados
            WHERE banco=? AND codigo_insumo=? AND ativo=1
        """, (banco, codigo)).fetchone()
        return decimal_exato(r[0]) if r else None
    finally:
        con.close()


def obter_precos_personalizados_banco(banco):
    banco = (banco or "").strip().upper()
    con = conectar_usuario()
    try:
        return {
            str(codigo).upper(): decimal_exato(preco)
            for codigo, preco in con.execute("""
                SELECT codigo_insumo, preco
                FROM precos_personalizados
                WHERE banco=? AND ativo=1
            """, (banco,)).fetchall()
        }
    finally:
        con.close()


def salvar_preco_personalizado(banco, codigo, preco, referencia_origem=""):
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()
    preco = truncar_2_casas(preco)

    con = conectar_usuario()
    try:
        con.execute("""
            INSERT INTO precos_personalizados(
                banco, codigo_insumo, preco, referencia_origem, ativo
            )
            VALUES(?,?,?,?,1)
            ON CONFLICT(banco, codigo_insumo) DO UPDATE SET
                preco=excluded.preco,
                referencia_origem=excluded.referencia_origem,
                ativo=1,
                atualizado_em=CURRENT_TIMESTAMP
        """, (banco, codigo, str(preco), referencia_origem or ""))
        con.commit()
    finally:
        con.close()


def restaurar_preco_oficial(banco, codigo):
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()
    con = conectar_usuario()
    try:
        con.execute("""
            DELETE FROM precos_personalizados
            WHERE banco=? AND codigo_insumo=?
        """, (banco, codigo))
        con.commit()
    finally:
        con.close()


def obter_insumo_por_banco(banco, codigo):
    """Retorna dados do insumo e distingue preço oficial do preço utilizado."""
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()

    if banco == "SEINFRA":
        con = conectar()
        try:
            r = con.execute("""
                SELECT codigo, descricao, unidade, preco
                FROM insumos
                WHERE codigo=?
            """, (codigo,)).fetchone()
        finally:
            con.close()
        if not r:
            return None
        oficial = truncar_2_casas(r[3] or 0)
        personalizado = obter_preco_personalizado("SEINFRA", codigo)
        utilizado = truncar_2_casas(personalizado if personalizado is not None else oficial)
        return {
            "banco": "SEINFRA", "codigo": r[0], "descricao": r[1] or "",
            "unidade": r[2] or "", "categoria": "",
            "preco_oficial": oficial, "preco_utilizado": utilizado,
            "personalizado": personalizado is not None,
        }

    if banco == "SINAPI":
        con = conectar_sinapi()
        try:
            r = con.execute("""
                SELECT i.codigo, i.descricao, i.unidade, i.classificacao,
                       COALESCE(MAX(CASE WHEN p.uf='CE' THEN p.preco END),0),
                       COALESCE(MAX(CASE WHEN p.uf='SP' THEN p.preco END),0)
                FROM insumos i
                LEFT JOIN precos_insumos p
                  ON p.insumo_codigo=i.codigo
                 AND p.regime='COM_DESONERACAO'
                 AND p.referencia=?
                 AND p.uf IN ('CE','SP')
                WHERE i.codigo=?
                GROUP BY i.codigo, i.descricao, i.unidade, i.classificacao
            """, (SINAPI_REFERENCIA, codigo)).fetchone()
        finally:
            con.close()
        if not r:
            return None

        preco_ce = decimal_exato(r[4])
        preco_sp = decimal_exato(r[5])
        oficial = preco_sp if preco_ce <= DECIMAL_ZERO and preco_sp > DECIMAL_ZERO else preco_ce
        oficial = truncar_2_casas(oficial)
        personalizado = obter_preco_personalizado("SINAPI", codigo)
        utilizado = truncar_2_casas(personalizado if personalizado is not None else oficial)

        return {
            "banco": "SINAPI", "codigo": r[0], "descricao": r[1] or "",
            "unidade": r[2] or "", "categoria": categoria_sintetica_sinapi(r[3]),
            "preco_oficial": oficial, "preco_utilizado": utilizado,
            "personalizado": personalizado is not None,
            "preco_ce": preco_ce, "preco_sp": preco_sp,
        }

    if banco in ("PRÓPRIO", "PROPRIO"):
        con = conectar_usuario()
        try:
            r = con.execute("""
                SELECT codigo, descricao, unidade, categoria, preco
                FROM insumos_proprios
                WHERE codigo=?
            """, (codigo,)).fetchone()
        finally:
            con.close()
        if not r:
            return None
        preco = truncar_2_casas(r[4] or 0)
        return {
            "banco": "PRÓPRIO", "codigo": r[0], "descricao": r[1] or "",
            "unidade": r[2] or "", "categoria": r[3] or "",
            "preco_oficial": preco, "preco_utilizado": preco,
            "personalizado": False,
        }

    return None


def aplicar_preco_insumo(banco, codigo, novo_preco):
    """Altera o preço utilizado sem modificar bancos oficiais.

    SEINFRA/SINAPI: grava override em usuario.db.
    PRÓPRIO: altera o próprio cadastro do usuário.
    """
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()
    novo_preco = truncar_2_casas(novo_preco)

    if novo_preco < DECIMAL_ZERO:
        raise ValueError("O preço não pode ser negativo.")

    if banco in ("PRÓPRIO", "PROPRIO"):
        con = conectar_usuario()
        try:
            if not con.execute(
                "SELECT 1 FROM insumos_proprios WHERE codigo=?", (codigo,)
            ).fetchone():
                raise ValueError(f"Insumo próprio {codigo} não encontrado.")
            con.execute("""
                UPDATE insumos_proprios
                SET preco=?, atualizado_em=CURRENT_TIMESTAMP
                WHERE codigo=?
            """, (str(novo_preco), codigo))
            con.commit()
        finally:
            con.close()
    elif banco in ("SEINFRA", "SINAPI"):
        referencia = SINAPI_REFERENCIA if banco == "SINAPI" else ""
        salvar_preco_personalizado(banco, codigo, novo_preco, referencia)
    else:
        raise ValueError("Banco inválido.")


def atualizar_dependencias_apos_preco():
    if codigo_composicao_atual:
        banco_atual, codigo_atual = (
            codigo_composicao_atual
            if isinstance(codigo_composicao_atual, tuple)
            else ("SEINFRA", codigo_composicao_atual)
        )
        combo_banco_consulta.set(banco_atual)
        consultar_composicao(codigo_atual, silencioso=True)

    if itens_orcamento:
        recalcular_orcamento()

    if 'atualizar_listas_banco_proprio' in globals():
        atualizar_listas_banco_proprio()


def categoria_sintetica_sinapi(classificacao):
    """Agrupa a classificação original SINAPI nas 3 categorias do orçamento.

    A classificação original do banco não é alterada. Esta conversão é usada
    somente nos cálculos e na visualização do aplicativo.

    Regras:
      - MAO DE OBRA + ENCARGOS COMPLEMENTARES -> MAO DE OBRA
      - MATERIAL + SERVIÇOS + ESPECIAIS       -> MATERIAL
      - EQUIPAMENTO (...)                     -> mantém EQUIPAMENTO (...)
    """
    classificacao = (classificacao or "").strip().upper()

    if classificacao in ("MAO DE OBRA", "ENCARGOS COMPLEMENTARES"):
        return "MAO DE OBRA"

    if classificacao in ("MATERIAL", "SERVIÇOS", "ESPECIAIS"):
        return "MATERIAL"

    if classificacao.startswith("EQUIPAMENTO"):
        return classificacao

    return classificacao or "MATERIAL"


# ============================================================
# FORMATAÇÃO
# ============================================================

def formatar_moeda(valor):
    """Mostra apenas 2 casas sem alterar o valor interno.

    Para evitar arredondamento visual, a exibição é truncada em 2 casas.
    O valor usado nos cálculos continua com toda a precisão disponível.
    """
    valor_exibicao = truncar_2_casas(valor)
    return (
        f"R$ {valor_exibicao:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def converter_numero(texto):
    texto = str(texto).strip().replace("R$", "").replace("%", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return float(texto)


def formatar_quantidade(valor):
    return (
        f"{valor:,.4f}"
        .rstrip("0")
        .rstrip(".")
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def formatar_percentual(valor):
    valor_exibicao = truncar_2_casas(valor)
    return f"{valor_exibicao:.2f}%".replace(".", ",")


# ============================================================
# ESTADO
# ============================================================

codigo_composicao_atual = None
insumo_consulta_atual = None

# Metadados das linhas mostradas na consulta de composição.
# Permite editar diretamente o preço de linhas que representam insumos.
metadados_linhas_composicao = {}

orcamento_salvo_atual_id = None
orcamento_salvo_atual_nome = None

# Estrutura do orçamento:
# categorias[nome] = {
#     "iid": iid_da_categoria,
#     "subcategorias": {
#         nome_subcategoria: {"iid": iid_da_subcategoria}
#     }
# }
categorias_orcamento = {}

# Cada composição adicionada é independente, portanto o mesmo
# código pode aparecer em pilares, vigas, lajes etc.
itens_orcamento = {}



def criar_calculadora_sinapi(cur):
    """Cria uma calculadora hierárquica SINAPI usando CE e fallback de SP.

    Regra de preço:
      - usa o preço do CE quando ele for maior que zero;
      - se CE estiver zerado/ausente, usa SP;
      - se CE e SP estiverem zerados/ausentes, usa 0.

    O percentual atribuído a SP (%AS) é calculado pelo VALOR:
      valor proveniente de SP / valor total calculado * 100.
    """
    regime = "COM_DESONERACAO"

    cur.execute("""
        SELECT i.codigo, i.classificacao, i.descricao, i.unidade,
               COALESCE(MAX(CASE WHEN p.uf='CE' THEN p.preco END), 0) AS preco_ce,
               COALESCE(MAX(CASE WHEN p.uf='SP' THEN p.preco END), 0) AS preco_sp
        FROM insumos i
        LEFT JOIN precos_insumos p
          ON p.insumo_codigo=i.codigo
         AND p.regime=? AND p.referencia=?
         AND p.uf IN ('CE','SP')
        GROUP BY i.codigo, i.classificacao, i.descricao, i.unidade
    """, (regime, SINAPI_REFERENCIA))
    insumos = {}
    precos_personalizados = obter_precos_personalizados_banco("SINAPI")

    for codigo, classificacao, descricao, unidade, preco_ce, preco_sp in cur.fetchall():
        preco_ce = decimal_exato(preco_ce)
        preco_sp = decimal_exato(preco_sp)
        usa_sp = preco_ce <= DECIMAL_ZERO and preco_sp > DECIMAL_ZERO
        preco_oficial = preco_sp if usa_sp else preco_ce
        preco_personalizado = precos_personalizados.get(str(codigo).upper())
        preco = preco_personalizado if preco_personalizado is not None else preco_oficial
        classificacao_original = classificacao or "SEM CLASSIFICACAO"
        insumos[codigo] = {
            "classificacao_original": classificacao_original,
            "classificacao": categoria_sintetica_sinapi(classificacao_original),
            "descricao": descricao or "",
            "unidade": unidade or "",
            "preco_ce": preco_ce,
            "preco_sp": preco_sp,
            "preco": preco,
            "preco_oficial": preco_oficial,
            "personalizado": preco_personalizado is not None,
            "usa_sp": usa_sp and preco_personalizado is None,
        }

    cache_itens = {}
    cache_composicoes = {}
    cache_calculo = {}

    def obter_composicao(codigo):
        if codigo not in cache_composicoes:
            cur.execute(
                "SELECT codigo, descricao, unidade FROM composicoes WHERE codigo=?",
                (codigo,),
            )
            cache_composicoes[codigo] = cur.fetchone()
        return cache_composicoes[codigo]

    def obter_itens(codigo):
        if codigo not in cache_itens:
            cur.execute("""
                SELECT tipo_item, codigo_item, descricao, unidade, coeficiente
                FROM itens_composicao
                WHERE composicao_codigo=?
                ORDER BY id
            """, (codigo,))
            cache_itens[codigo] = cur.fetchall()
        return cache_itens[codigo]

    def calcular(codigo, pilha=()):
        if codigo in cache_calculo:
            return cache_calculo[codigo]
        if codigo in pilha:
            return {
                "codigo": codigo, "descricao": "Ciclo detectado", "unidade": "",
                "total_unit": DECIMAL_ZERO, "valor_sp_unit": DECIMAL_ZERO, "pct_as": DECIMAL_ZERO,
                "categorias": {}, "filhos": []
            }

        comp = obter_composicao(codigo)
        if not comp:
            return None

        filhos = []
        total_unit = DECIMAL_ZERO
        valor_sp_unit = DECIMAL_ZERO
        categorias = {}
        nova_pilha = pilha + (codigo,)

        for tipo, codigo_item, desc_item, und_item, coeficiente in obter_itens(codigo):
            # O coeficiente já foi gravado no banco com a precisão desejada.
            # Não fazemos round() novamente: preservamos exatamente o valor armazenado.
            coef = decimal_exato(coeficiente)

            if tipo == "INSUMO":
                info = insumos.get(codigo_item, {
                    "classificacao_original": "SEM CLASSIFICACAO",
                    "classificacao": categoria_sintetica_sinapi("SEM CLASSIFICACAO"),
                    "descricao": desc_item or "",
                    "unidade": und_item or "",
                    "preco_ce": DECIMAL_ZERO, "preco_sp": DECIMAL_ZERO, "preco": DECIMAL_ZERO, "usa_sp": False,
                })
                preco = info["preco"]
                # O preço e o coeficiente permanecem exatos, mas o valor monetário
                # calculado do item entra na composição com no máximo 2 casas,
                # sempre por truncamento (nunca por arredondamento).
                total = truncar_2_casas(coef * preco)
                valor_sp = total if info["usa_sp"] else DECIMAL_ZERO
                pct_as = DECIMAL_CEM if info["usa_sp"] and total != DECIMAL_ZERO else DECIMAL_ZERO
                categoria = info["classificacao"] or "SEM CLASSIFICACAO"
                categorias[categoria] = categorias.get(categoria, DECIMAL_ZERO) + total
                total_unit += total
                valor_sp_unit += valor_sp
                filhos.append({
                    "tipo": "INSUMO",
                    "categoria": categoria,
                    "classificacao_original": info.get("classificacao_original", categoria),
                    "codigo": codigo_item,
                    "descricao": info["descricao"],
                    "unidade": info["unidade"],
                    "coeficiente": coef,
                    "preco_unitario": preco,
                    "total": total,
                    "valor_sp": valor_sp,
                    "pct_as": pct_as,
                    "preco_ce": info["preco_ce"],
                    "preco_sp": info["preco_sp"],
                    "usa_sp": info["usa_sp"],
                    "filhos": [],
                })
                continue

            if tipo == "COMPOSICAO":
                sub = calcular(codigo_item, nova_pilha)
                if not sub:
                    filhos.append({
                        "tipo": "COMPOSICAO", "categoria": "COMPOSIÇÃO",
                        "codigo": codigo_item, "descricao": desc_item or "",
                        "unidade": und_item or "", "coeficiente": coef,
                        "preco_unitario": DECIMAL_ZERO, "total": DECIMAL_ZERO, "valor_sp": DECIMAL_ZERO,
                        "pct_as": DECIMAL_ZERO, "filhos": []
                    })
                    continue

                total = truncar_2_casas(coef * sub["total_unit"])
                valor_sp = truncar_2_casas(coef * sub["valor_sp_unit"])
                pct_as = (valor_sp / total * DECIMAL_CEM) if total else DECIMAL_ZERO
                total_unit += total
                valor_sp_unit += valor_sp
                for cat, valor in sub["categorias"].items():
                    valor_categoria = truncar_2_casas(coef * valor)
                    categorias[cat] = categorias.get(cat, DECIMAL_ZERO) + valor_categoria

                filhos.append({
                    "tipo": "COMPOSICAO",
                    "categoria": "COMPOSIÇÃO",
                    "codigo": codigo_item,
                    "descricao": sub["descricao"],
                    "unidade": sub["unidade"],
                    "coeficiente": coef,
                    "preco_unitario": sub["total_unit"],
                    "total": total,
                    "valor_sp": valor_sp,
                    "pct_as": pct_as,
                    "filhos": sub["filhos"],
                })

        total_unit = truncar_2_casas(total_unit)
        valor_sp_unit = truncar_2_casas(valor_sp_unit)
        categorias = {cat: truncar_2_casas(valor) for cat, valor in categorias.items()}
        pct_as = (valor_sp_unit / total_unit * DECIMAL_CEM) if total_unit else DECIMAL_ZERO
        resultado = {
            "codigo": comp[0],
            "descricao": comp[1] or "",
            "unidade": comp[2] or "",
            "total_unit": total_unit,
            "valor_sp_unit": valor_sp_unit,
            "pct_as": pct_as,
            "categorias": categorias,
            "filhos": filhos,
        }
        cache_calculo[codigo] = resultado
        return resultado

    return calcular


def inserir_arvore_sinapi(parent, filhos, banco_origem="SINAPI"):
    """Insere recursivamente os itens da composição na Treeview."""
    for item in filhos:
        iid = tabela_composicao.insert(
            parent,
            tk.END,
            text=item["categoria"],
            values=(
                item["codigo"],
                item["descricao"],
                item["unidade"],
                f'{item["coeficiente"]:.8f}',
                formatar_moeda(item["preco_unitario"]),
                formatar_moeda(item["total"]),
                formatar_percentual(item["pct_as"]),
            ),
            open=False,
        )
        metadados_linhas_composicao[iid] = {
            "tipo": item.get("tipo", ""),
            "banco": banco_origem,
            "codigo": str(item.get("codigo", "")).upper(),
        }
        if item["tipo"] == "COMPOSICAO" and item.get("filhos"):
            inserir_arvore_sinapi(iid, item["filhos"], banco_origem)

# ============================================================
# CONSULTAR COMPOSIÇÃO
# ============================================================

def consultar_composicao(codigo=None, silencioso=False):
    global codigo_composicao_atual
    banco = banco_selecionado("combo_banco_consulta")
    codigo = (entrada_codigo_composicao.get() if codigo is None else codigo).strip().upper()
    if not codigo:
        if not silencioso:
            messagebox.showwarning("Atenção", "Informe o código da composição.")
        return

    con = None
    try:
        metadados_linhas_composicao.clear()
        for item in tabela_composicao.get_children():
            tabela_composicao.delete(item)

        if banco == "SINAPI":
            con = conectar_sinapi()
            cur = con.cursor()
            calcular = criar_calculadora_sinapi(cur)
            resultado = calcular(codigo)
            if not resultado:
                if not silencioso:
                    messagebox.showerror("Erro", f"Composição SINAPI {codigo} não encontrada.")
                return

            codigo_composicao_atual = ("SINAPI", codigo)
            label_composicao.config(
                text=(f'SINAPI {codigo} - {resultado["descricao"]} | '
                      f'Base CE + fallback SP | Com desoneração')
            )
            inserir_arvore_sinapi("", resultado["filhos"])

            adicionais = calcular_itens_adicionais("SINAPI", codigo)
            if adicionais["itens"]:
                inserir_arvore_sinapi("", adicionais["itens"])

            categorias_exibicao = dict(resultado["categorias"])
            for cat, valor in adicionais["categorias"].items():
                categorias_exibicao[cat] = truncar_2_casas(
                    categorias_exibicao.get(cat, DECIMAL_ZERO) + valor
                )
            total_exibicao = truncar_2_casas(
                resultado["total_unit"] + adicionais["total_unit"]
            )

            mao_exib = categorias_exibicao.get("MAO DE OBRA", DECIMAL_ZERO)
            eq_exib = sum(
                (v for k, v in categorias_exibicao.items() if k.startswith("EQUIPAMENTO")),
                DECIMAL_ZERO,
            )
            mat_exib = categorias_exibicao.get("MATERIAL", DECIMAL_ZERO)

            linhas = [
                f"MÃO DE OBRA: {formatar_moeda(mao_exib)}",
                f"EQUIPAMENTOS: {formatar_moeda(eq_exib)}",
                f"MATERIAIS: {formatar_moeda(mat_exib)}",
                "",
                f"TOTAL GERAL: {formatar_moeda(total_exibicao)}",
            ]
            linhas.append(
                f'% ATRIBUÍDO SP: {formatar_percentual(resultado["pct_as"])} '
                f'({formatar_moeda(resultado["valor_sp_unit"])})'
            )
            label_totais.config(text="\n".join(linhas))

        elif banco in ("PRÓPRIO", "PROPRIO"):
            resultado = obter_resumo_composicao_propria(codigo)
            if not resultado:
                if not silencioso:
                    messagebox.showerror("Erro", f"Composição PRÓPRIO {codigo} não encontrada.")
                return
            codigo_composicao_atual = ("PRÓPRIO", codigo)
            label_composicao.config(text=f'PRÓPRIO {codigo} - {resultado["descricao"]}')
            inserir_arvore_sinapi("", resultado["itens"], "PRÓPRIO")
            label_totais.config(text=(
                f'MAO DE OBRA: {formatar_moeda(resultado["mao_obra_unit"])}\n'
                f'EQUIPAMENTOS: {formatar_moeda(resultado["equipamentos_unit"])}\n'
                f'MATERIAIS: {formatar_moeda(resultado["materiais_unit"])}\n\n'
                f'TOTAL GERAL: {formatar_moeda(resultado["total_unit"])}'
            ))

        else:
            con = conectar()
            cur = con.cursor()
            cur.execute("SELECT codigo,descricao FROM composicoes WHERE codigo=?", (codigo,))
            comp = cur.fetchone()
            if not comp:
                if not silencioso:
                    messagebox.showerror("Erro", f"Composição SEINFRA {codigo} não encontrada.")
                return

            codigo_composicao_atual = ("SEINFRA", codigo)
            label_composicao.config(text=f"SEINFRA {codigo} - {comp[1]}")
            cur.execute("""
                SELECT categoria,codigo_item,descricao,unidade,coeficiente,preco_unitario
                FROM itens_composicao_detalhado
                WHERE composicao_codigo=? ORDER BY id
            """, (codigo,))
            totais = {
                "MAO DE OBRA": DECIMAL_ZERO,
                "EQUIPAMENTOS": DECIMAL_ZERO,
                "MATERIAIS": DECIMAL_ZERO,
                "OUTROS": DECIMAL_ZERO,
            }
            geral = DECIMAL_ZERO
            overrides = obter_precos_personalizados_banco("SEINFRA")

            for cat, cod, desc, und, coef, preco_oficial in cur.fetchall():
                cat_original = (cat or "SEM CATEGORIA").upper()
                coef_dec = decimal_exato(coef or 0)
                preco = overrides.get(str(cod).upper(), decimal_exato(preco_oficial or 0))
                preco = truncar_2_casas(preco)
                total = truncar_2_casas(coef_dec * preco)

                if cat_original == "MAO DE OBRA":
                    cat_final = "MAO DE OBRA"
                elif cat_original.startswith("EQUIPAMENTOS"):
                    cat_final = "EQUIPAMENTOS"
                elif cat_original == "MATERIAIS":
                    cat_final = "MATERIAIS"
                else:
                    cat_final = "OUTROS"

                totais[cat_final] = truncar_2_casas(totais[cat_final] + total)
                geral = truncar_2_casas(geral + total)

                iid = tabela_composicao.insert(
                    "", tk.END,
                    text=cat_final,
                    values=(
                        cod, desc, und, f"{coef_dec:.8f}",
                        formatar_moeda(preco), formatar_moeda(total), ""
                    )
                )
                metadados_linhas_composicao[iid] = {
                    "tipo": "INSUMO",
                    "banco": "SEINFRA",
                    "codigo": str(cod).upper(),
                }
            adicionais = calcular_itens_adicionais("SEINFRA", codigo)
            for item in adicionais["itens"]:
                # Em SEINFRA mantemos a tabela plana, mas identificamos a origem.
                tabela_composicao.insert(
                    "", tk.END,
                    text=item["categoria"],
                    values=(
                        item["codigo"], item["descricao"], item["unidade"],
                        f'{item["coeficiente"]:.8f}',
                        formatar_moeda(item["preco_unitario"]),
                        formatar_moeda(item["total"]), ""
                    )
                )
            for cat, valor in adicionais["categorias"].items():
                totais[cat] = decimal_exato(totais.get(cat, 0)) + valor
            geral = decimal_exato(geral) + adicionais["total_unit"]

            label_totais.config(
                text=(
                    f'MÃO DE OBRA: {formatar_moeda(totais.get("MAO DE OBRA", 0))}\n'
                    f'EQUIPAMENTOS: {formatar_moeda(totais.get("EQUIPAMENTOS", 0))}\n'
                    f'MATERIAIS: {formatar_moeda(totais.get("MATERIAIS", 0))}\n'
                    + (
                        f'OUTROS: {formatar_moeda(totais.get("OUTROS", 0))}\n'
                        if totais.get("OUTROS", DECIMAL_ZERO) != DECIMAL_ZERO else ""
                    )
                    + f'\nTOTAL GERAL: {formatar_moeda(geral)}'
                )
            )
    except Exception as erro:
        if not silencioso:
            messagebox.showerror("Erro", f"Erro ao consultar composição:\n\n{erro}")
    finally:
        if con:
            con.close()

# ============================================================
# CONSULTAR INSUMO
# ============================================================

def consultar_insumo():
    global insumo_consulta_atual

    banco = banco_selecionado("combo_banco_consulta")
    codigo = entrada_codigo_consulta.get().strip().upper()

    if not codigo:
        messagebox.showwarning("Atenção", "Informe o código do insumo.")
        return

    try:
        resultado = obter_insumo_por_banco(banco, codigo)
        if not resultado:
            insumo_consulta_atual = None
            label_resultado_insumo.config(text="Insumo não encontrado.")
            entrada_preco_consulta_insumo.delete(0, tk.END)
            return

        insumo_consulta_atual = resultado

        origem = "PERSONALIZADO" if resultado.get("personalizado") else "OFICIAL"
        if banco == "PRÓPRIO":
            origem = "PRÓPRIO"

        texto_categoria = (
            f'Categoria: {resultado.get("categoria")}\n'
            if resultado.get("categoria") else ""
        )

        label_resultado_insumo.config(
            text=(
                f'Banco: {resultado["banco"]}\n'
                f'Código: {resultado["codigo"]}\n'
                f'Descrição: {resultado["descricao"]}\n'
                f'Unidade: {resultado["unidade"]}\n'
                f'{texto_categoria}'
                f'Preço oficial: {formatar_moeda(resultado["preco_oficial"])}\n'
                f'Preço utilizado: {formatar_moeda(resultado["preco_utilizado"])}\n'
                f'Origem do preço: {origem}'
            )
        )

        entrada_preco_consulta_insumo.delete(0, tk.END)
        entrada_preco_consulta_insumo.insert(
            0, str(resultado["preco_utilizado"]).replace(".", ",")
        )

        estado = "normal" if banco in ("SEINFRA", "SINAPI") and resultado.get("personalizado") else "disabled"
        botao_restaurar_preco_insumo.config(state=estado)

    except Exception as erro:
        messagebox.showerror("Erro", f"Erro ao consultar insumo:\n\n{erro}")


def salvar_preco_insumo_consultado():
    if not insumo_consulta_atual:
        messagebox.showwarning("Atenção", "Consulte um insumo primeiro.")
        return

    try:
        novo_preco = decimal_texto_usuario(entrada_preco_consulta_insumo.get(), 2)
        if novo_preco < 0:
            raise ValueError("O preço não pode ser negativo.")
    except Exception as erro:
        messagebox.showerror("Erro", f"Informe um preço válido.\n\n{erro}")
        return

    banco = insumo_consulta_atual["banco"]
    codigo = insumo_consulta_atual["codigo"]

    try:
        aplicar_preco_insumo(banco, codigo, novo_preco)
        atualizar_dependencias_apos_preco()
        consultar_insumo()
        messagebox.showinfo(
            "Preço atualizado",
            f"{banco} {codigo}\nNovo preço utilizado: {formatar_moeda(novo_preco)}"
        )
    except Exception as erro:
        messagebox.showerror("Erro", f"Não foi possível alterar o preço:\n\n{erro}")


def restaurar_preco_insumo_consultado():
    if not insumo_consulta_atual:
        return

    banco = insumo_consulta_atual["banco"]
    codigo = insumo_consulta_atual["codigo"]

    if banco not in ("SEINFRA", "SINAPI"):
        return

    restaurar_preco_oficial(banco, codigo)
    atualizar_dependencias_apos_preco()
    consultar_insumo()


def editar_preco_tabela_composicao(evento):
    """Duplo clique no preço unitário de um insumo para editar seu valor."""
    iid = tabela_composicao.identify_row(evento.y)
    coluna = tabela_composicao.identify_column(evento.x)

    # #0 = categoria; #1 código; #2 descrição; #3 unidade;
    # #4 coeficiente; #5 preço unitário.
    if not iid or coluna != "#5":
        return

    meta = metadados_linhas_composicao.get(iid)
    if not meta or meta.get("tipo") != "INSUMO":
        return

    banco = meta.get("banco", "")
    codigo = meta.get("codigo", "")
    if banco not in ("SEINFRA", "SINAPI", "PRÓPRIO"):
        return

    bbox = tabela_composicao.bbox(iid, coluna)
    if not bbox:
        return

    x, y, largura, altura = bbox
    valores = tabela_composicao.item(iid, "values")
    atual = valores[4] if len(valores) > 4 else ""

    editor = ttk.Entry(tabela_composicao, justify="right")
    editor.place(x=x, y=y, width=largura, height=altura)
    editor.insert(0, str(atual).replace("R$", "").strip())
    editor.select_range(0, tk.END)
    editor.focus_set()

    finalizado = {"ok": False}

    def fechar():
        if editor.winfo_exists():
            editor.destroy()

    def cancelar(e=None):
        finalizado["ok"] = True
        fechar()
        return "break"

    def confirmar(e=None):
        if finalizado["ok"]:
            return "break"

        try:
            novo_preco = decimal_texto_usuario(editor.get(), 2)
            if novo_preco < 0:
                raise ValueError("O preço não pode ser negativo.")
        except Exception as erro:
            messagebox.showerror("Preço inválido", str(erro))
            editor.focus_set()
            editor.select_range(0, tk.END)
            return "break"

        finalizado["ok"] = True
        fechar()

        try:
            aplicar_preco_insumo(banco, codigo, novo_preco)
            atualizar_dependencias_apos_preco()
        except Exception as erro:
            messagebox.showerror("Erro", f"Não foi possível alterar o preço:\\n\\n{erro}")
        return "break"

    editor.bind("<Return>", confirmar)
    editor.bind("<Escape>", cancelar)
    editor.bind("<FocusOut>", confirmar)





# ============================================================
# BANCO PRÓPRIO - INSUMOS E COMPOSIÇÕES DO USUÁRIO
# ============================================================

def inicializar_tabelas_banco_proprio():
    """Cria as tabelas persistentes do banco PRÓPRIO em usuario.db."""
    con = conectar_usuario()
    try:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS insumos_proprios (
                codigo TEXT PRIMARY KEY,
                descricao TEXT NOT NULL,
                unidade TEXT,
                categoria TEXT NOT NULL CHECK(categoria IN ('MAO DE OBRA','EQUIPAMENTOS','MATERIAIS')),
                preco TEXT NOT NULL DEFAULT '0.00',
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS composicoes_proprias (
                codigo TEXT PRIMARY KEY,
                descricao TEXT NOT NULL,
                unidade TEXT,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS itens_composicao_propria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                composicao_codigo TEXT NOT NULL,
                insumo_codigo TEXT NOT NULL,
                coeficiente TEXT NOT NULL DEFAULT '0',
                ordem INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(composicao_codigo) REFERENCES composicoes_proprias(codigo) ON DELETE CASCADE,
                FOREIGN KEY(insumo_codigo) REFERENCES insumos_proprios(codigo) ON DELETE RESTRICT
            );

            CREATE INDEX IF NOT EXISTS idx_itens_proprios_comp
            ON itens_composicao_propria(composicao_codigo, ordem, id);

            CREATE TABLE IF NOT EXISTS itens_adicionais_composicao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                banco_destino TEXT NOT NULL,
                composicao_codigo TEXT NOT NULL,
                tipo_item TEXT NOT NULL CHECK(tipo_item IN ('INSUMO_PROPRIO','COMPOSICAO_PROPRIA')),
                item_codigo TEXT NOT NULL,
                coeficiente TEXT NOT NULL DEFAULT '1',
                ordem INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_itens_adicionais_destino
            ON itens_adicionais_composicao(banco_destino, composicao_codigo, ordem, id);

            CREATE TABLE IF NOT EXISTS insumos_adicionados_composicao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                banco_destino TEXT NOT NULL,
                composicao_codigo TEXT NOT NULL,
                banco_origem TEXT NOT NULL,
                insumo_codigo TEXT NOT NULL,
                coeficiente TEXT NOT NULL DEFAULT '1',
                ordem INTEGER NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_insumos_adicionados_destino
            ON insumos_adicionados_composicao(
                banco_destino, composicao_codigo, ordem, id
            );

            CREATE TABLE IF NOT EXISTS precos_personalizados (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                banco TEXT NOT NULL,
                codigo_insumo TEXT NOT NULL,
                preco TEXT NOT NULL,
                referencia_origem TEXT,
                ativo INTEGER NOT NULL DEFAULT 1,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(banco, codigo_insumo)
            );

            CREATE INDEX IF NOT EXISTS idx_precos_personalizados
            ON precos_personalizados(banco, codigo_insumo, ativo);
        """)
        col_ins = {r[1] for r in con.execute("PRAGMA table_info(insumos_proprios)")}
        if "coeficiente_padrao" not in col_ins:
            con.execute(
                "ALTER TABLE insumos_proprios "
                "ADD COLUMN coeficiente_padrao TEXT NOT NULL DEFAULT '1'"
            )

        col_comp = {r[1] for r in con.execute("PRAGMA table_info(composicoes_proprias)")}
        if "coeficiente_padrao" not in col_comp:
            con.execute(
                "ALTER TABLE composicoes_proprias "
                "ADD COLUMN coeficiente_padrao TEXT NOT NULL DEFAULT '1'"
            )

        con.commit()
    finally:
        con.close()


def migrar_dados_usuario_legados():
    """Copia dados do usuário de versões antigas para usuario.db.

    A migração só copia uma tabela quando a tabela equivalente em usuario.db
    ainda está vazia. O banco oficial antigo não é apagado nem modificado.
    """
    if not DB_PATH.exists():
        return

    origem = conectar()
    destino = conectar_usuario()

    tabelas = [
        "insumos_proprios",
        "composicoes_proprias",
        "itens_composicao_propria",
        "itens_adicionais_composicao",
        "insumos_adicionados_composicao",
        "precos_personalizados",
        "orcamentos",
        "estrutura_orcamento",
    ]

    try:
        destino.execute("PRAGMA foreign_keys = OFF")

        for tabela in tabelas:
            existe_origem = origem.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tabela,)
            ).fetchone()
            existe_destino = destino.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tabela,)
            ).fetchone()

            if not existe_origem or not existe_destino:
                continue

            qtd_destino = destino.execute(
                f'SELECT COUNT(*) FROM "{tabela}"'
            ).fetchone()[0]

            if qtd_destino:
                continue

            col_origem = [
                r[1] for r in origem.execute(f'PRAGMA table_info("{tabela}")')
            ]
            col_destino = [
                r[1] for r in destino.execute(f'PRAGMA table_info("{tabela}")')
            ]

            colunas = [c for c in col_origem if c in col_destino]
            if not colunas:
                continue

            nomes = ", ".join(f'"{c}"' for c in colunas)
            placeholders = ", ".join("?" for _ in colunas)

            registros = origem.execute(
                f'SELECT {nomes} FROM "{tabela}"'
            ).fetchall()

            if registros:
                destino.executemany(
                    f'INSERT OR IGNORE INTO "{tabela}" ({nomes}) '
                    f'VALUES ({placeholders})',
                    registros
                )

        destino.commit()
    except Exception:
        destino.rollback()
        raise
    finally:
        try:
            destino.execute("PRAGMA foreign_keys = ON")
        except Exception:
            pass
        origem.close()
        destino.close()


def decimal_texto_usuario(texto, casas=None):
    """Converte número digitado em pt-BR para Decimal."""
    txt = str(texto or '').strip().replace('R$', '').replace('%', '').replace(' ', '')
    if not txt:
        return DECIMAL_ZERO
    if ',' in txt:
        txt = txt.replace('.', '').replace(',', '.')
    valor = Decimal(txt)
    if casas == 2:
        return truncar_2_casas(valor)
    return valor


def coeficiente_usuario(texto):
    """Valida coeficiente positivo e preserva até 8 casas, sem arredondar."""
    coef = decimal_texto_usuario(texto)
    if coef <= 0:
        raise ValueError("O coeficiente deve ser maior que zero.")
    return coef.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)


def normalizar_categoria_orcamento(categoria):
    categoria = (categoria or "").strip().upper()
    if categoria in ("MAO DE OBRA", "MÃO DE OBRA"):
        return "MAO DE OBRA"
    if categoria.startswith("EQUIPAMENTO"):
        return "EQUIPAMENTOS"
    if categoria in ("MATERIAL", "MATERIAIS", "SERVIÇOS", "ESPECIAIS"):
        return "MATERIAIS"
    if categoria == "ENCARGOS COMPLEMENTARES":
        return "MAO DE OBRA"
    return "MATERIAIS"


def obter_categoria_insumo_seinfra(codigo):
    con = conectar()
    try:
        r = con.execute("""
            SELECT categoria, COUNT(*) AS qtd
            FROM itens_composicao
            WHERE insumo_codigo=?
            GROUP BY categoria
            ORDER BY qtd DESC
            LIMIT 1
        """, (codigo,)).fetchone()
        return normalizar_categoria_orcamento(r[0] if r else "MATERIAIS")
    finally:
        con.close()


def obter_insumo_para_adicao(banco, codigo):
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()
    info = obter_insumo_por_banco(banco, codigo)
    if not info:
        return None
    categoria = info.get("categoria") or ""
    if banco == "SEINFRA":
        categoria = obter_categoria_insumo_seinfra(codigo)
    return {
        "banco": banco,
        "codigo": info["codigo"],
        "descricao": info["descricao"],
        "unidade": info["unidade"],
        "categoria": normalizar_categoria_orcamento(categoria),
        "preco": truncar_2_casas(info["preco_utilizado"]),
    }


def obter_insumos_adicionados_composicao(banco_destino, composicao_codigo):
    con = conectar_usuario()
    try:
        return con.execute("""
            SELECT id, banco_origem, insumo_codigo, coeficiente
            FROM insumos_adicionados_composicao
            WHERE banco_destino=? AND composicao_codigo=?
            ORDER BY ordem, id
        """, ((banco_destino or "").strip().upper(), (composicao_codigo or "").strip().upper())).fetchall()
    finally:
        con.close()


def obter_itens_adicionais_composicao(banco_destino, composicao_codigo):
    """Retorna itens PRÓPRIOS anexados a uma composição de qualquer banco."""
    banco_destino = (banco_destino or "").strip().upper()
    composicao_codigo = (composicao_codigo or "").strip().upper()
    con = conectar_usuario()
    try:
        return con.execute("""
            SELECT id, tipo_item, item_codigo, coeficiente
            FROM itens_adicionais_composicao
            WHERE banco_destino=? AND composicao_codigo=?
            ORDER BY ordem, id
        """, (banco_destino, composicao_codigo)).fetchall()
    finally:
        con.close()


def obter_insumo_proprio(codigo):
    codigo = (codigo or "").strip().upper()
    con = conectar_usuario()
    try:
        return con.execute("""
            SELECT codigo, descricao, unidade, categoria, preco
            FROM insumos_proprios
            WHERE codigo=?
        """, (codigo,)).fetchone()
    finally:
        con.close()


def _somar_categoria(totais, categoria, valor):
    totais[categoria] = truncar_2_casas(
        totais.get(categoria, DECIMAL_ZERO) + truncar_2_casas(valor)
    )


def calcular_itens_adicionais(banco_destino, composicao_codigo, pilha=()):
    """Calcula itens próprios anexados a uma composição existente.

    Um item adicional pode ser:
      - um insumo próprio;
      - uma composição própria inteira.

    O coeficiente do item adicional é aplicado ao valor unitário do item e o
    resultado monetário é truncado para 2 casas, mantendo a regra do sistema.
    """
    chave = ((banco_destino or "").upper(), (composicao_codigo or "").upper())
    if chave in pilha:
        return {
            "itens": [],
            "categorias": {
                "MAO DE OBRA": DECIMAL_ZERO,
                "EQUIPAMENTOS": DECIMAL_ZERO,
                "MATERIAIS": DECIMAL_ZERO,
            },
            "total_unit": DECIMAL_ZERO,
        }

    nova_pilha = pilha + (chave,)
    itens_saida = []
    totais = {
        "MAO DE OBRA": DECIMAL_ZERO,
        "EQUIPAMENTOS": DECIMAL_ZERO,
        "MATERIAIS": DECIMAL_ZERO,
    }

    for item_id, tipo_item, item_codigo, coef_txt in obter_itens_adicionais_composicao(*chave):
        coef = decimal_exato(coef_txt)

        if tipo_item == "INSUMO_PROPRIO":
            ins = obter_insumo_proprio(item_codigo)
            if not ins:
                continue
            cod, desc, und, cat, preco_txt = ins
            preco = truncar_2_casas(preco_txt)
            total = truncar_2_casas(coef * preco)
            _somar_categoria(totais, cat, total)
            itens_saida.append({
                "id_adicional": item_id,
                "tipo": "INSUMO",
                "categoria": cat,
                "codigo": cod,
                "descricao": desc or "",
                "unidade": und or "",
                "coeficiente": coef,
                "preco_unitario": preco,
                "total": total,
                "pct_as": DECIMAL_ZERO,
                "filhos": [],
                "origem": "PRÓPRIO",
            })
            continue

        if tipo_item == "COMPOSICAO_PROPRIA":
            sub = obter_resumo_composicao_propria(item_codigo, pilha=nova_pilha)
            if not sub:
                continue

            total = truncar_2_casas(coef * sub["total_unit"])
            mao = truncar_2_casas(coef * sub["mao_obra_unit"])
            eq = truncar_2_casas(coef * sub["equipamentos_unit"])
            mat = truncar_2_casas(coef * sub["materiais_unit"])

            _somar_categoria(totais, "MAO DE OBRA", mao)
            _somar_categoria(totais, "EQUIPAMENTOS", eq)
            _somar_categoria(totais, "MATERIAIS", mat)

            itens_saida.append({
                "id_adicional": item_id,
                "tipo": "COMPOSICAO",
                "categoria": "COMPOSIÇÃO PRÓPRIA",
                "codigo": sub["codigo"],
                "descricao": sub["descricao"],
                "unidade": sub["unidade"],
                "coeficiente": coef,
                "preco_unitario": sub["total_unit"],
                "total": total,
                "pct_as": DECIMAL_ZERO,
                "filhos": sub.get("itens", []),
                "origem": "PRÓPRIO",
            })

    for item_id, banco_origem, item_codigo, coef_txt in obter_insumos_adicionados_composicao(chave[0], chave[1]):
        info = obter_insumo_para_adicao(banco_origem, item_codigo)
        if not info:
            continue
        coef = decimal_exato(coef_txt)
        preco = truncar_2_casas(info["preco"])
        total = truncar_2_casas(coef * preco)
        categoria = normalizar_categoria_orcamento(info["categoria"])
        _somar_categoria(totais, categoria, total)
        itens_saida.append({
            "id_externo": item_id,
            "tipo": "INSUMO",
            "categoria": categoria,
            "codigo": info["codigo"],
            "descricao": info["descricao"],
            "unidade": info["unidade"],
            "coeficiente": coef,
            "preco_unitario": preco,
            "total": total,
            "pct_as": DECIMAL_ZERO,
            "filhos": [],
            "origem": banco_origem,
        })

    for cat in list(totais):
        totais[cat] = truncar_2_casas(totais[cat])

    return {
        "itens": itens_saida,
        "categorias": totais,
        "total_unit": truncar_2_casas(sum(totais.values(), DECIMAL_ZERO)),
    }


def obter_resumo_composicao_propria(codigo, pilha=()):
    codigo = (codigo or '').strip().upper()
    chave = ("PRÓPRIO", codigo)
    if chave in pilha:
        return None

    con = conectar_usuario()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT codigo, descricao, unidade FROM composicoes_proprias WHERE codigo=?",
            (codigo,)
        )
        comp = cur.fetchone()
        if not comp:
            return None

        cur.execute("""
            SELECT ip.id, ip.insumo_codigo, i.descricao, i.unidade, i.categoria,
                   ip.coeficiente, i.preco
            FROM itens_composicao_propria ip
            JOIN insumos_proprios i ON i.codigo=ip.insumo_codigo
            WHERE ip.composicao_codigo=?
            ORDER BY ip.ordem, ip.id
        """, (codigo,))

        itens = []
        totais = {
            'MAO DE OBRA': DECIMAL_ZERO,
            'EQUIPAMENTOS': DECIMAL_ZERO,
            'MATERIAIS': DECIMAL_ZERO,
        }

        for item_id, cod_ins, desc, und, cat, coef_txt, preco_txt in cur.fetchall():
            coef = decimal_exato(coef_txt)
            preco = truncar_2_casas(preco_txt)
            total = truncar_2_casas(coef * preco)
            _somar_categoria(totais, cat, total)
            itens.append({
                'id': item_id,
                'tipo': 'INSUMO',
                'codigo': cod_ins,
                'descricao': desc or '',
                'unidade': und or '',
                'categoria': cat,
                'coeficiente': coef,
                'preco_unitario': preco,
                'total': total,
                'pct_as': DECIMAL_ZERO,
                'filhos': [],
                'origem': 'PRÓPRIO',
            })
    finally:
        con.close()

    # Acrescenta itens PRÓPRIOS que tenham sido anexados a esta composição.
    adicionais = calcular_itens_adicionais("PRÓPRIO", codigo, pilha)
    for cat, valor in adicionais["categorias"].items():
        _somar_categoria(totais, cat, valor)
    itens.extend(adicionais["itens"])

    for k in list(totais):
        totais[k] = truncar_2_casas(totais[k])

    total_geral = truncar_2_casas(sum(totais.values(), DECIMAL_ZERO))
    return {
        'codigo': comp[0],
        'descricao': comp[1] or '',
        'unidade': comp[2] or '',
        'banco': 'PRÓPRIO',
        'mao_obra_unit': totais['MAO DE OBRA'],
        'equipamentos_unit': totais['EQUIPAMENTOS'],
        'materiais_unit': totais['MATERIAIS'],
        'total_unit': total_geral,
        'itens': itens,
    }


def atualizar_listas_banco_proprio():
    """Atualiza as listas de insumos e composições criados pelo usuário."""
    if "tabela_insumos_proprios" not in globals():
        return

    con = conectar_usuario()
    try:
        cur = con.cursor()

        for iid in tabela_insumos_proprios.get_children():
            tabela_insumos_proprios.delete(iid)

        cur.execute("""
            SELECT codigo, descricao, unidade, categoria, preco, coeficiente_padrao
            FROM insumos_proprios
            ORDER BY codigo
        """)

        for cod, desc, und, cat, preco, coef_padrao in cur.fetchall():
            tabela_insumos_proprios.insert(
                "",
                tk.END,
                values=(
                    cod,
                    desc,
                    und,
                    cat,
                    str(coef_padrao).replace(".", ","),
                    formatar_moeda(decimal_exato(preco)),
                )
            )

        for iid in tabela_composicoes_proprias.get_children():
            tabela_composicoes_proprias.delete(iid)

        cur.execute("""
            SELECT codigo, descricao, unidade, coeficiente_padrao
            FROM composicoes_proprias
            ORDER BY codigo
        """)

        for cod, desc, und, coef_padrao in cur.fetchall():
            resumo = obter_resumo_composicao_propria(cod)
            tabela_composicoes_proprias.insert(
                "",
                tk.END,
                values=(
                    cod,
                    desc,
                    und,
                    str(coef_padrao).replace(".", ","),
                    formatar_moeda(resumo["total_unit"] if resumo else 0),
                )
            )
    finally:
        con.close()



def limpar_form_insumo_proprio():
    for ent in (
        entrada_codigo_insumo_proprio, entrada_desc_insumo_proprio,
        entrada_unidade_insumo_proprio, entrada_preco_insumo_proprio,
        entrada_coef_padrao_insumo_proprio
    ):
        ent.delete(0,tk.END)
    combo_categoria_insumo_proprio.set('MATERIAIS')
    entrada_coef_padrao_insumo_proprio.insert(0,'1')


def salvar_insumo_proprio():
    codigo=entrada_codigo_insumo_proprio.get().strip().upper()
    desc=entrada_desc_insumo_proprio.get().strip()
    und=entrada_unidade_insumo_proprio.get().strip().upper()
    cat=combo_categoria_insumo_proprio.get().strip().upper()
    if not codigo or not desc:
        messagebox.showwarning('Atenção','Informe código e descrição do insumo.')
        return
    if cat not in ('MAO DE OBRA','EQUIPAMENTOS','MATERIAIS'):
        messagebox.showwarning('Atenção','Selecione uma categoria válida.')
        return
    try:
        preco=decimal_texto_usuario(entrada_preco_insumo_proprio.get(),2)
    except Exception:
        messagebox.showerror('Erro','Informe um preço válido.')
        return
    if preco < 0:
        messagebox.showerror('Erro','O preço não pode ser negativo.')
        return
    try:
        coef_padrao=coeficiente_usuario(entrada_coef_padrao_insumo_proprio.get())
    except Exception as erro:
        messagebox.showerror('Erro',f'Coeficiente inválido.\n\n{erro}')
        return
    con=conectar_usuario()
    try:
        cur=con.cursor()
        cur.execute("""
            INSERT INTO insumos_proprios(codigo,descricao,unidade,categoria,preco,coeficiente_padrao)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(codigo) DO UPDATE SET
                descricao=excluded.descricao, unidade=excluded.unidade,
                categoria=excluded.categoria, preco=excluded.preco,
                coeficiente_padrao=excluded.coeficiente_padrao,
                atualizado_em=CURRENT_TIMESTAMP
        """,(codigo,desc,und,cat,str(preco),str(coef_padrao)))
        con.commit()
    finally:
        con.close()
    atualizar_listas_banco_proprio()
    if itens_orcamento:
        recalcular_orcamento()
    messagebox.showinfo('Banco PRÓPRIO',f'Insumo {codigo} salvo com sucesso.')


def selecionar_insumo_proprio(evento=None):
    sel=tabela_insumos_proprios.selection()
    if not sel: return
    vals=tabela_insumos_proprios.item(sel[0],'values')
    if not vals: return
    codigo=vals[0]
    con=conectar_usuario()
    try:
        r=con.execute("""
            SELECT codigo,descricao,unidade,categoria,preco,coeficiente_padrao
            FROM insumos_proprios WHERE codigo=?
        """,(codigo,)).fetchone()
    finally: con.close()
    if not r:return
    limpar_form_insumo_proprio()
    entrada_codigo_insumo_proprio.insert(0,r[0])
    entrada_desc_insumo_proprio.insert(0,r[1])
    entrada_unidade_insumo_proprio.insert(0,r[2] or '')
    combo_categoria_insumo_proprio.set(r[3])
    entrada_preco_insumo_proprio.insert(0,str(r[4]).replace('.',','))
    entrada_coef_padrao_insumo_proprio.insert(0,str(r[5] or '1').replace('.',','))


def excluir_insumo_proprio():
    codigo=entrada_codigo_insumo_proprio.get().strip().upper()
    if not codigo:
        messagebox.showwarning('Atenção','Selecione ou informe o código do insumo.')
        return
    con=conectar_usuario()
    try:
        uso=con.execute("SELECT COUNT(*) FROM itens_composicao_propria WHERE insumo_codigo=?",(codigo,)).fetchone()[0]
        uso_adicional=con.execute("""
            SELECT COUNT(*) FROM itens_adicionais_composicao
            WHERE tipo_item='INSUMO_PROPRIO' AND item_codigo=?
        """,(codigo,)).fetchone()[0]
        uso_novo=con.execute("""
            SELECT COUNT(*) FROM insumos_adicionados_composicao
            WHERE banco_origem='PRÓPRIO' AND insumo_codigo=?
        """,(codigo,)).fetchone()[0]
        uso_total = uso + uso_adicional + uso_novo
        if uso_total:
            messagebox.showwarning(
                'Insumo em uso',
                f'O insumo {codigo} é utilizado em {uso_total} item(ns) de composição e não pode ser excluído.'
            )
            return
        if not messagebox.askyesno('Excluir insumo',f'Deseja excluir o insumo {codigo}?'):
            return
        con.execute("DELETE FROM insumos_proprios WHERE codigo=?",(codigo,)); con.commit()
    finally: con.close()
    limpar_form_insumo_proprio(); atualizar_listas_banco_proprio()


def limpar_form_composicao_propria():
    for ent in (
        entrada_codigo_comp_propria,
        entrada_desc_comp_propria,
        entrada_unidade_comp_propria,
        entrada_coef_padrao_comp_propria,
    ):
        ent.delete(0, tk.END)

    entrada_coef_padrao_comp_propria.insert(0, "1")



def salvar_cabecalho_composicao_propria(mostrar=True):
    codigo=entrada_codigo_comp_propria.get().strip().upper()
    desc=entrada_desc_comp_propria.get().strip()
    und=entrada_unidade_comp_propria.get().strip().upper()
    if not codigo or not desc:
        if mostrar: messagebox.showwarning('Atenção','Informe código e descrição da composição.')
        return False
    try:
        coef_padrao=coeficiente_usuario(entrada_coef_padrao_comp_propria.get())
    except Exception as erro:
        if mostrar: messagebox.showerror('Erro',f'Coeficiente inválido.\n\n{erro}')
        return False
    con=conectar_usuario()
    try:
        con.execute("""
            INSERT INTO composicoes_proprias(codigo,descricao,unidade,coeficiente_padrao)
            VALUES(?,?,?,?)
            ON CONFLICT(codigo) DO UPDATE SET
                descricao=excluded.descricao, unidade=excluded.unidade,
                coeficiente_padrao=excluded.coeficiente_padrao,
                atualizado_em=CURRENT_TIMESTAMP
        """,(codigo,desc,und,str(coef_padrao)))
        con.commit()
    finally: con.close()
    atualizar_listas_banco_proprio()
    if mostrar: messagebox.showinfo('Banco PRÓPRIO',f'Composição {codigo} salva com sucesso.')
    return True


def carregar_composicao_propria(codigo=None):
    if codigo is None:
        sel = tabela_composicoes_proprias.selection()
        if not sel:
            return
        codigo = tabela_composicoes_proprias.item(sel[0], "values")[0]

    con = conectar_usuario()
    try:
        r = con.execute("""
            SELECT codigo, descricao, unidade, coeficiente_padrao
            FROM composicoes_proprias
            WHERE codigo=?
        """, (codigo,)).fetchone()
    finally:
        con.close()

    if not r:
        return

    limpar_form_composicao_propria()
    entrada_codigo_comp_propria.insert(0, r[0])
    entrada_desc_comp_propria.insert(0, r[1] or "")
    entrada_unidade_comp_propria.insert(0, r[2] or "")
    entrada_coef_padrao_comp_propria.insert(
        0, str(r[3] or "1").replace(".", ",")
    )



def excluir_composicao_propria():
    codigo=entrada_codigo_comp_propria.get().strip().upper()
    if not codigo:return
    con=conectar_usuario()
    try:
        uso = con.execute("""
            SELECT COUNT(*) FROM itens_adicionais_composicao
            WHERE tipo_item='COMPOSICAO_PROPRIA' AND item_codigo=?
        """, (codigo,)).fetchone()[0]
        if uso:
            messagebox.showwarning(
                'Composição em uso',
                f'A composição própria {codigo} é utilizada em {uso} outra(s) composição(ões) e não pode ser excluída.'
            )
            return
        if not messagebox.askyesno('Excluir composição',f'Deseja excluir a composição própria {codigo} e seus itens?'):
            return
        con.execute("DELETE FROM composicoes_proprias WHERE codigo=?",(codigo,))
        con.commit()
    finally: con.close()
    limpar_form_composicao_propria(); atualizar_listas_banco_proprio()



def composicao_destino_existe(banco, codigo):
    banco = (banco or "").strip().upper()
    codigo = (codigo or "").strip().upper()

    if banco == "SEINFRA":
        con = conectar()
        try:
            return con.execute(
                "SELECT 1 FROM composicoes WHERE codigo=?", (codigo,)
            ).fetchone() is not None
        finally:
            con.close()

    if banco == "SINAPI":
        con = conectar_sinapi()
        try:
            return con.execute(
                "SELECT 1 FROM composicoes WHERE codigo=?", (codigo,)
            ).fetchone() is not None
        finally:
            con.close()

    if banco in ("PRÓPRIO", "PROPRIO"):
        con = conectar_usuario()
        try:
            return con.execute(
                "SELECT 1 FROM composicoes_proprias WHERE codigo=?", (codigo,)
            ).fetchone() is not None
        finally:
            con.close()

    return False







# ============================================================
# ADICIONAR INSUMOS DE QUALQUER BANCO EM COMPOSIÇÕES
# ============================================================

def filtrar_composicoes_destino(evento=None):
    if "tabela_filtro_composicoes" not in globals(): return
    banco = combo_banco_destino_filtro.get().strip().upper()
    filtro = entrada_filtro_composicao.get().strip().upper()
    for iid in tabela_filtro_composicoes.get_children(): tabela_filtro_composicoes.delete(iid)
    limite = 150
    if banco == "SEINFRA":
        con = conectar(); tabela="composicoes"
    elif banco == "SINAPI":
        con = conectar_sinapi(); tabela="composicoes"
    else:
        con = conectar_usuario(); tabela="composicoes_proprias"
    try:
        rows = con.execute(f"""SELECT codigo, descricao, unidade FROM {tabela}
            WHERE UPPER(codigo) LIKE ? OR UPPER(descricao) LIKE ? ORDER BY codigo LIMIT ?""",
            (f"%{filtro}%", f"%{filtro}%", limite)).fetchall()
    finally: con.close()
    for cod, desc, und in rows:
        tabela_filtro_composicoes.insert("", tk.END, values=(cod, desc or "", und or ""))


def selecionar_composicao_destino(evento=None):
    sel=tabela_filtro_composicoes.selection()
    if not sel: return
    vals=tabela_filtro_composicoes.item(sel[0],"values")
    if not vals: return
    entrada_comp_destino_nova.delete(0,tk.END); entrada_comp_destino_nova.insert(0,vals[0])


def filtrar_insumos_origem(evento=None):
    if "tabela_filtro_insumos" not in globals(): return
    banco=combo_banco_origem_insumo.get().strip().upper(); filtro=entrada_filtro_insumo.get().strip().upper()
    for iid in tabela_filtro_insumos.get_children(): tabela_filtro_insumos.delete(iid)
    limite=200
    if banco=="SEINFRA": con=conectar(); tabela="insumos"
    elif banco=="SINAPI": con=conectar_sinapi(); tabela="insumos"
    else: con=conectar_usuario(); tabela="insumos_proprios"
    try:
        rows=con.execute(f"""SELECT codigo,descricao,unidade FROM {tabela}
            WHERE UPPER(codigo) LIKE ? OR UPPER(descricao) LIKE ? ORDER BY codigo LIMIT ?""",
            (f"%{filtro}%", f"%{filtro}%", limite)).fetchall()
    finally: con.close()
    for cod,desc,und in rows:
        tabela_filtro_insumos.insert("",tk.END,values=(cod,desc or "",und or ""))


def selecionar_insumo_origem(evento=None):
    sel=tabela_filtro_insumos.selection()
    if not sel: return
    vals=tabela_filtro_insumos.item(sel[0],"values")
    if not vals: return
    entrada_insumo_origem_nova.delete(0,tk.END); entrada_insumo_origem_nova.insert(0,vals[0])
    if combo_banco_origem_insumo.get().strip().upper()=="PRÓPRIO":
        con=conectar_usuario()
        try: r=con.execute("SELECT coeficiente_padrao FROM insumos_proprios WHERE codigo=?",(vals[0],)).fetchone()
        finally: con.close()
        if r:
            entrada_coef_insumo_nova.delete(0,tk.END); entrada_coef_insumo_nova.insert(0,str(r[0] or "1").replace(".",","))


def salvar_insumo_em_composicao():
    bd=combo_banco_destino_filtro.get().strip().upper(); comp=entrada_comp_destino_nova.get().strip().upper()
    bo=combo_banco_origem_insumo.get().strip().upper(); ins=entrada_insumo_origem_nova.get().strip().upper()
    if bd not in ("SEINFRA","SINAPI","PRÓPRIO"):
        messagebox.showwarning("Atenção","Selecione o banco de destino."); return
    if not comp or not composicao_destino_existe(bd,comp):
        messagebox.showerror("Composição não encontrada",f"A composição {comp or '(vazia)'} não foi encontrada em {bd}."); return
    info=obter_insumo_para_adicao(bo,ins)
    if not info:
        messagebox.showerror("Insumo não encontrado",f"O insumo {ins or '(vazio)'} não foi encontrado em {bo}."); return
    try: coef=coeficiente_usuario(entrada_coef_insumo_nova.get())
    except Exception as erro:
        messagebox.showerror("Erro",f"Coeficiente inválido.\n\n{erro}"); return
    con=conectar_usuario()
    try:
        ordem=con.execute("SELECT COALESCE(MAX(ordem),0)+1 FROM insumos_adicionados_composicao WHERE banco_destino=? AND composicao_codigo=?",(bd,comp)).fetchone()[0]
        con.execute("""INSERT INTO insumos_adicionados_composicao
            (banco_destino,composicao_codigo,banco_origem,insumo_codigo,coeficiente,ordem)
            VALUES(?,?,?,?,?,?)""",(bd,comp,bo,ins,str(coef),ordem)); con.commit()
    finally: con.close()
    atualizar_lista_novos_insumos_adicionados()
    if codigo_composicao_atual==(bd,comp):
        combo_banco_consulta.set(bd); consultar_composicao(comp,silencioso=True)
    if itens_orcamento: recalcular_orcamento()


def atualizar_lista_novos_insumos_adicionados():
    if "tabela_novos_insumos_adicionados" not in globals(): return
    for iid in tabela_novos_insumos_adicionados.get_children(): tabela_novos_insumos_adicionados.delete(iid)
    con=conectar_usuario()
    try: rows=con.execute("""SELECT id,banco_destino,composicao_codigo,banco_origem,insumo_codigo,coeficiente
        FROM insumos_adicionados_composicao ORDER BY banco_destino,composicao_codigo,ordem,id""").fetchall()
    finally: con.close()
    for item_id,bd,comp,bo,ins,coef in rows:
        info=obter_insumo_para_adicao(bo,ins); desc=info["descricao"] if info else ""
        tabela_novos_insumos_adicionados.insert("",tk.END,iid=f"insadd_{item_id}",values=(bd,comp,bo,ins,desc,str(coef).replace(".",",")))


def excluir_novo_insumo_adicionado():
    sel=tabela_novos_insumos_adicionados.selection()
    if not sel or not sel[0].startswith("insadd_"):
        messagebox.showwarning("Atenção","Selecione um insumo adicionado."); return
    item_id=int(sel[0].split("_",1)[1])
    if not messagebox.askyesno("Remover insumo","Deseja remover este insumo da composição?"): return
    con=conectar_usuario()
    try: con.execute("DELETE FROM insumos_adicionados_composicao WHERE id=?",(item_id,)); con.commit()
    finally: con.close()
    atualizar_lista_novos_insumos_adicionados()
    if codigo_composicao_atual:
        banco,codigo=codigo_composicao_atual; combo_banco_consulta.set(banco); consultar_composicao(codigo,silencioso=True)
    if itens_orcamento: recalcular_orcamento()


# ============================================================
# CÁLCULO DO ORÇAMENTO
# ============================================================

def obter_resumo_composicao(codigo, banco="SEINFRA", uf=None, regime=None):
    banco = (banco or "SEINFRA").upper()
    if banco in ("PRÓPRIO", "PROPRIO"):
        return obter_resumo_composicao_propria(codigo)
    if banco == "SINAPI":
        # O orçamento usa a mesma regra da consulta: CE como base e SP apenas
        # para insumos sem preço no CE. O total é sempre recalculado a partir
        # dos componentes, sem usar diretamente o custo oficial da planilha.
        con = conectar_sinapi()
        try:
            cur = con.cursor()
            calcular = criar_calculadora_sinapi(cur)
            r = calcular(codigo)
            if not r:
                return None
            cats = r["categorias"]

            # A calculadora já agrupa:
            # ENCARGOS COMPLEMENTARES -> MAO DE OBRA
            # SERVIÇOS e ESPECIAIS    -> MATERIAL
            # EQUIPAMENTOS            -> EQUIPAMENTO (...)
            mao = cats.get("MAO DE OBRA", DECIMAL_ZERO)
            equipamentos = sum(
                (v for k, v in cats.items() if k.startswith("EQUIPAMENTO")),
                DECIMAL_ZERO,
            )
            materiais = cats.get("MATERIAL", DECIMAL_ZERO)

            adicionais = calcular_itens_adicionais("SINAPI", codigo)
            mao = truncar_2_casas(mao + adicionais["categorias"].get("MAO DE OBRA", DECIMAL_ZERO))
            equipamentos = truncar_2_casas(equipamentos + adicionais["categorias"].get("EQUIPAMENTOS", DECIMAL_ZERO))
            materiais = truncar_2_casas(materiais + adicionais["categorias"].get("MATERIAIS", DECIMAL_ZERO))
            total_com_adicionais = truncar_2_casas(r["total_unit"] + adicionais["total_unit"])

            # Mantém Decimal até o orçamento e até a exportação.
            # Nenhum valor SINAPI é convertido para float nesta etapa.
            return {
                "codigo": r["codigo"], "descricao": r["descricao"],
                "unidade": r["unidade"], "banco": "SINAPI",
                "uf": "CE", "regime": "COM_DESONERACAO",
                "mao_obra_unit": mao,
                "equipamentos_unit": equipamentos,
                "materiais_unit": materiais,
                "total_unit": total_com_adicionais,
                "percentual_atribuido_sp": r["pct_as"],
                "valor_atribuido_sp": r["valor_sp_unit"],
            }
        finally:
            con.close()

    con = conectar()
    try:
        cur = con.cursor()
        cur.execute("SELECT codigo, descricao, unidade, banco FROM composicoes WHERE codigo=?", (codigo,))
        comp = cur.fetchone()
        if not comp:
            return None
        cur.execute("""
            SELECT categoria, codigo_item, coeficiente, preco_unitario
            FROM itens_composicao_detalhado
            WHERE composicao_codigo=?
            ORDER BY id
        """, (codigo,))

        overrides = obter_precos_personalizados_banco("SEINFRA")
        mao_base = DECIMAL_ZERO
        equip_base = DECIMAL_ZERO
        mat_base = DECIMAL_ZERO
        outros_base = DECIMAL_ZERO

        for categoria, codigo_item, coeficiente, preco_oficial in cur.fetchall():
            cat = (categoria or "").upper()
            coef = decimal_exato(coeficiente or 0)
            preco = overrides.get(str(codigo_item).upper(), decimal_exato(preco_oficial or 0))
            total_item = truncar_2_casas(coef * truncar_2_casas(preco))

            if cat == "MAO DE OBRA":
                mao_base = truncar_2_casas(mao_base + total_item)
            elif cat.startswith("EQUIPAMENTOS"):
                equip_base = truncar_2_casas(equip_base + total_item)
            elif cat == "MATERIAIS":
                mat_base = truncar_2_casas(mat_base + total_item)
            else:
                outros_base = truncar_2_casas(outros_base + total_item)

        adicionais = calcular_itens_adicionais("SEINFRA", codigo)
        mao = truncar_2_casas(mao_base + adicionais["categorias"].get("MAO DE OBRA", DECIMAL_ZERO))
        equip = truncar_2_casas(equip_base + adicionais["categorias"].get("EQUIPAMENTOS", DECIMAL_ZERO))
        mat = truncar_2_casas(mat_base + adicionais["categorias"].get("MATERIAIS", DECIMAL_ZERO))
        total = truncar_2_casas(
            mao + equip + mat + outros_base
        )
        return {"codigo": comp[0], "descricao": comp[1], "unidade": comp[2] or "",
                "banco": comp[3] or "SEINFRA", "uf": "", "regime": "",
                "mao_obra_unit": mao, "equipamentos_unit": equip,
                "materiais_unit": mat, "total_unit": total}
    finally:
        con.close()


def obter_bdi_percentual():
    texto = entrada_bdi_orcamento.get().strip()

    if not texto:
        return 0.0

    try:
        bdi = converter_numero(texto)
    except ValueError:
        raise ValueError("Informe um BDI válido. Exemplo: 25,00")

    if bdi < 0:
        raise ValueError("O BDI não pode ser negativo.")

    return bdi


def formatar_unidade_excel(unidade):
    """Normaliza a unidade somente para exibição na planilha Excel exportada."""
    if unidade is None:
        return ""

    original = str(unidade).strip()
    chave = original.upper()

    unidades = {
        "M2": "m²",
        "M²": "m²",
        "M3": "m³",
        "M": "m",
        "CM2": "cm²",
        "CM3": "cm³",
        "M2XMÊS": "m²×mês",
        "M2XARF": "m²×ARF",
        "M3XKM": "m³×km",
        "KG": "kg",
        "KM": "km",
        "H": "h",
        "HA": "ha",
        "L": "l",
        "T": "t",
        "UN": "un",
        "UNXMÊS": "un×mês",
        "CJ": "cj",
        "PAR": "par",
        "PT": "pt",
        "PTXDIA": "pt×dia",
        "CICLO": "ciclo",
        "MÊS": "mês",
        "QUADRA": "quadra",
        "IMÓVEL": "imóvel",
    }

    return unidades.get(chave, original)


def obter_unidade_composicao(descricao):
    """Fallback legado. As unidades do orçamento agora vêm da tabela composicoes."""
    if not descricao:
        return ""
    partes = str(descricao).rsplit(" - ", 1)
    if len(partes) == 2:
        return partes[1].strip().upper()
    return ""


# ============================================================
# CATEGORIAS E SUBCATEGORIAS
# ============================================================

def atualizar_combobox_categorias():
    nomes = list(categorias_orcamento.keys())
    combo_categoria["values"] = nomes

    atual = combo_categoria.get()
    if atual not in nomes:
        combo_categoria.set(nomes[0] if nomes else "")

    atualizar_combobox_subcategorias()


def atualizar_combobox_subcategorias(evento=None):
    categoria = combo_categoria.get()

    if categoria not in categorias_orcamento:
        combo_subcategoria["values"] = []
        combo_subcategoria.set("")
        return

    nomes = list(categorias_orcamento[categoria]["subcategorias"].keys())
    combo_subcategoria["values"] = nomes

    atual = combo_subcategoria.get()
    if atual not in nomes:
        combo_subcategoria.set(nomes[0] if nomes else "")


def criar_categoria():
    nome = simpledialog.askstring(
        "Nova categoria",
        "Nome da categoria:\n",
        parent=janela
    )

    if not nome:
        return

    nome = nome.strip().upper()

    if not nome:
        return

    if nome in categorias_orcamento:
        messagebox.showwarning("Atenção", "Essa categoria já existe.")
        combo_categoria.set(nome)
        atualizar_combobox_subcategorias()
        return

    iid = tabela_orcamento.insert(
        "",
        tk.END,
        open=True,
        values=("", "", "", "", nome, "", "", "", "", "", "")
    )

    categorias_orcamento[nome] = {
        "iid": iid,
        "subcategorias": {}
    }

    combo_categoria.set(nome)
    atualizar_combobox_categorias()
    combo_categoria.set(nome)
    atualizar_combobox_subcategorias()
    renumerar_orcamento()
    atualizar_totais_orcamento()


def criar_subcategoria():
    categoria = combo_categoria.get()

    if categoria not in categorias_orcamento:
        messagebox.showwarning(
            "Atenção",
            "Crie ou selecione uma categoria primeiro."
        )
        return

    nome = simpledialog.askstring(
        "Nova subcategoria",
        f"Categoria: {categoria}\n\nNome da subcategoria:\n",
        parent=janela
    )

    if not nome:
        return

    nome = nome.strip().upper()

    if not nome:
        return

    subs = categorias_orcamento[categoria]["subcategorias"]

    if nome in subs:
        messagebox.showwarning("Atenção", "Essa subcategoria já existe nessa categoria.")
        combo_subcategoria.set(nome)
        return

    iid_categoria = categorias_orcamento[categoria]["iid"]

    iid = tabela_orcamento.insert(
        iid_categoria,
        tk.END,
        open=True,
        values=("", "", "", "", nome, "", "", "", "", "", "")
    )

    subs[nome] = {"iid": iid}

    combo_subcategoria.set(nome)
    atualizar_combobox_subcategorias()
    combo_subcategoria.set(nome)
    renumerar_orcamento()
    atualizar_totais_orcamento()


# ============================================================
# ITENS DO ORÇAMENTO
# ============================================================

def adicionar_composicao_orcamento():
    categoria = combo_categoria.get()
    subcategoria = combo_subcategoria.get()
    codigo = entrada_codigo_orcamento.get().strip().upper()
    texto_quantidade = entrada_quantidade_orcamento.get().strip()

    if categoria not in categorias_orcamento:
        messagebox.showwarning(
            "Atenção",
            "Selecione ou crie uma categoria para o item."
        )
        return

    # A subcategoria é opcional. Se nenhuma estiver selecionada,
    # a composição será adicionada diretamente à categoria.
    if subcategoria and subcategoria not in categorias_orcamento[categoria]["subcategorias"]:
        messagebox.showwarning(
            "Atenção",
            "A subcategoria selecionada não existe."
        )
        return

    if not codigo:
        messagebox.showwarning("Atenção", "Informe o código da composição.")
        return

    try:
        quantidade = converter_numero(texto_quantidade)
    except ValueError:
        messagebox.showerror("Erro", "Informe uma quantidade válida. Exemplo: 15,50")
        return

    if quantidade <= 0:
        messagebox.showerror("Erro", "A quantidade deve ser maior que zero.")
        return

    try:
        obter_bdi_percentual()
    except ValueError as erro:
        messagebox.showerror("Erro", str(erro))
        return

    banco = banco_selecionado("combo_banco_orcamento")
    uf, regime = contexto_sinapi() if banco == "SINAPI" else ("", "")
    resumo = obter_resumo_composicao(codigo, banco, uf, regime)

    if not resumo:
        messagebox.showerror("Erro", f"Composição {codigo} não encontrada.")
        return

    if subcategoria:
        pai = categorias_orcamento[categoria]["subcategorias"][subcategoria]["iid"]
    else:
        pai = categorias_orcamento[categoria]["iid"]

    # Não bloqueia códigos repetidos. Cada ocorrência é um item
    # independente e pode estar em pilares, vigas, lajes etc.
    iid = tabela_orcamento.insert(pai, tk.END)

    itens_orcamento[iid] = {
        "categoria": categoria,
        "subcategoria": subcategoria,
        "codigo": codigo,
        "banco": resumo.get("banco", "SEINFRA"),
        "uf": resumo.get("uf", ""),
        "regime": resumo.get("regime", ""),
        "unidade": resumo.get("unidade", ""),
        "descricao": resumo["descricao"],
        "quantidade": quantidade
    }

    recalcular_linha_orcamento(iid)
    renumerar_orcamento()
    atualizar_totais_orcamento()

    entrada_codigo_orcamento.delete(0, tk.END)
    entrada_quantidade_orcamento.delete(0, tk.END)
    entrada_codigo_orcamento.focus_set()



# ============================================================
# EDIÇÃO DIRETA DA QUANTIDADE NO ORÇAMENTO
# ============================================================

def editar_quantidade_orcamento(evento):
    """Permite editar a quantidade de uma composição com duplo clique."""
    iid = tabela_orcamento.identify_row(evento.y)
    coluna = tabela_orcamento.identify_column(evento.x)

    # Quantidade é a 6ª coluna (#6). Categorias e subcategorias não são editáveis.
    if not iid or coluna != "#6" or iid not in itens_orcamento:
        return

    bbox = tabela_orcamento.bbox(iid, coluna)
    if not bbox:
        return

    x, y, largura, altura = bbox
    dados = itens_orcamento[iid]

    editor = ttk.Entry(tabela_orcamento, justify="right")
    editor.place(x=x, y=y, width=largura, height=altura)
    editor.insert(0, formatar_quantidade(dados["quantidade"]))
    editor.select_range(0, tk.END)
    editor.focus_set()

    estado = {"finalizado": False}

    def fechar_editor():
        if editor.winfo_exists():
            editor.destroy()

    def cancelar(evento=None):
        estado["finalizado"] = True
        fechar_editor()
        return "break"

    def confirmar(evento=None):
        if estado["finalizado"]:
            return "break"

        texto = editor.get().strip()

        try:
            nova_quantidade = converter_numero(texto)
        except (ValueError, TypeError):
            messagebox.showerror(
                "Quantidade inválida",
                "Informe uma quantidade válida. Exemplo: 15,50"
            )
            editor.focus_set()
            editor.select_range(0, tk.END)
            return "break"

        if nova_quantidade <= 0:
            messagebox.showerror(
                "Quantidade inválida",
                "A quantidade deve ser maior que zero."
            )
            editor.focus_set()
            editor.select_range(0, tk.END)
            return "break"

        estado["finalizado"] = True
        dados["quantidade"] = nova_quantidade
        fechar_editor()

        # Recalcula a composição alterada e todos os subtotais/totais.
        recalcular_linha_orcamento(iid)
        atualizar_totais_orcamento()
        return "break"

    editor.bind("<Return>", confirmar)
    editor.bind("<Escape>", cancelar)
    editor.bind("<FocusOut>", confirmar)

def recalcular_linha_orcamento(iid):
    dados = itens_orcamento.get(iid)

    if not dados:
        return

    resumo = obter_resumo_composicao(
        dados["codigo"], dados.get("banco", "SEINFRA"),
        dados.get("uf") or None, dados.get("regime") or None
    )

    if not resumo:
        return

    quantidade = dados["quantidade"]
    quantidade_dec = decimal_exato(quantidade)

    try:
        bdi = obter_bdi_percentual()
    except ValueError:
        bdi = 0.0

    # Todo o fluxo monetário do orçamento usa Decimal.
    # Isso vale especialmente para SINAPI e evita voltar para float depois
    # que a composição foi calculada com alta precisão.
    bdi_dec = decimal_exato(bdi)
    fator_bdi = Decimal("1") + (bdi_dec / DECIMAL_CEM)

    # Mantemos Decimal, mas toda grandeza monetária usada nos cálculos
    # considera no máximo 2 casas decimais, SEM arredondar.
    mao_obra_sem_bdi = truncar_2_casas(resumo["mao_obra_unit"])
    equipamentos_sem_bdi = truncar_2_casas(resumo["equipamentos_unit"])
    materiais_sem_bdi = truncar_2_casas(resumo["materiais_unit"])

    # O BDI é aplicado sobre a base de 2 casas e o resultado também é truncado.
    mao_obra = truncar_2_casas(mao_obra_sem_bdi * fator_bdi)
    equipamentos = truncar_2_casas(equipamentos_sem_bdi * fator_bdi)
    materiais = truncar_2_casas(materiais_sem_bdi * fator_bdi)

    # Valor unitário completo SEM BDI já segue a mesma regra.
    valor_unitario = truncar_2_casas(resumo["total_unit"])

    # Totais monetários também usam somente 2 casas, sempre sem arredondamento.
    valor_unitario_com_bdi = truncar_2_casas(valor_unitario * fator_bdi)
    custo_base = truncar_2_casas(valor_unitario * quantidade_dec)
    total = truncar_2_casas(valor_unitario_com_bdi * quantidade_dec)

    dados.update({
        "descricao": resumo["descricao"],
        "banco": resumo.get("banco", dados.get("banco", "SEINFRA")),
        "unidade": resumo.get("unidade", dados.get("unidade", "")),
        "mao_obra": mao_obra,
        "equipamentos": equipamentos,
        "materiais": materiais,
        "mao_obra_sem_bdi": mao_obra_sem_bdi,
        "equipamentos_sem_bdi": equipamentos_sem_bdi,
        "materiais_sem_bdi": materiais_sem_bdi,
        "valor_unitario": valor_unitario,
        "valor_unitario_com_bdi": valor_unitario_com_bdi,
        "custo_base": custo_base,
        "bdi_percentual": bdi_dec,
        "total": total
    })

    valores = tabela_orcamento.item(iid, "values")
    numero = valores[0] if valores else ""

    tabela_orcamento.item(
        iid,
        values=(
            numero,
            dados["codigo"],
            dados.get("banco", "SEINFRA"),
            dados.get("unidade", ""),
            dados["descricao"],
            formatar_quantidade(quantidade),
            formatar_moeda(valor_unitario),
            formatar_moeda(mao_obra),
            formatar_moeda(equipamentos),
            formatar_moeda(materiais),
            formatar_moeda(total)
        )
    )


def recalcular_orcamento():
    try:
        obter_bdi_percentual()
    except ValueError as erro:
        messagebox.showerror("Erro", str(erro))
        return

    for iid in list(itens_orcamento):
        recalcular_linha_orcamento(iid)

    atualizar_totais_orcamento()


def subtotal_por_parent(parent_iid):
    mao = equip = mat = total = DECIMAL_ZERO

    def acumular(no):
        nonlocal mao, equip, mat, total

        if no in itens_orcamento:
            dados = itens_orcamento[no]
            quantidade = decimal_exato(dados.get("quantidade", 0))
            mao += truncar_2_casas(decimal_exato(dados.get("mao_obra", 0)) * quantidade)
            equip += truncar_2_casas(decimal_exato(dados.get("equipamentos", 0)) * quantidade)
            mat += truncar_2_casas(decimal_exato(dados.get("materiais", 0)) * quantidade)
            total += truncar_2_casas(dados.get("total", 0))

        for filho in tabela_orcamento.get_children(no):
            acumular(filho)

    for filho in tabela_orcamento.get_children(parent_iid):
        acumular(filho)

    return mao, equip, mat, total


def atualizar_totais_orcamento():
    # Atualiza subtotais das subcategorias.
    for categoria, dados_cat in categorias_orcamento.items():
        for subcategoria, dados_sub in dados_cat["subcategorias"].items():
            iid_sub = dados_sub["iid"]
            mao, equip, mat, total = subtotal_por_parent(iid_sub)

            valores = list(tabela_orcamento.item(iid_sub, "values"))
            numero = valores[0] if valores else ""

            tabela_orcamento.item(
                iid_sub,
                values=(
                    numero, "", "", "", subcategoria, "", "",
                    formatar_moeda(mao), formatar_moeda(equip),
                    formatar_moeda(mat), formatar_moeda(total)
                )
            )

        # Atualiza subtotal da categoria inteira.
        iid_cat = dados_cat["iid"]
        mao, equip, mat, total = subtotal_por_parent(iid_cat)

        valores = list(tabela_orcamento.item(iid_cat, "values"))
        numero = valores[0] if valores else ""

        tabela_orcamento.item(
            iid_cat,
            values=(
                numero, "", "", "", categoria, "", "",
                formatar_moeda(mao), formatar_moeda(equip),
                formatar_moeda(mat), formatar_moeda(total)
            )
        )

    # Total geral em Decimal, sem arredondamentos intermediários.
    mao = sum(
        (truncar_2_casas(decimal_exato(d.get("mao_obra", 0)) * decimal_exato(d.get("quantidade", 0)))
         for d in itens_orcamento.values()),
        DECIMAL_ZERO,
    )
    equip = sum(
        (truncar_2_casas(decimal_exato(d.get("equipamentos", 0)) * decimal_exato(d.get("quantidade", 0)))
         for d in itens_orcamento.values()),
        DECIMAL_ZERO,
    )
    mat = sum(
        (truncar_2_casas(decimal_exato(d.get("materiais", 0)) * decimal_exato(d.get("quantidade", 0)))
         for d in itens_orcamento.values()),
        DECIMAL_ZERO,
    )
    total = sum(
        (truncar_2_casas(d.get("total", 0)) for d in itens_orcamento.values()),
        DECIMAL_ZERO,
    )

    label_orc_mao_obra.config(text=formatar_moeda(mao))
    label_orc_equipamentos.config(text=formatar_moeda(equip))
    label_orc_materiais.config(text=formatar_moeda(mat))
    label_orc_total.config(text=formatar_moeda(total))


def renumerar_orcamento():
    numero_categoria = 0

    for iid_cat in tabela_orcamento.get_children(""):
        numero_categoria += 1

        valores_cat = list(tabela_orcamento.item(iid_cat, "values"))
        if valores_cat:
            valores_cat[0] = str(numero_categoria)
            tabela_orcamento.item(iid_cat, values=valores_cat)

        numero_segundo_nivel = 0

        for iid_filho in tabela_orcamento.get_children(iid_cat):
            numero_segundo_nivel += 1

            # Se o filho está em itens_orcamento, é uma composição
            # adicionada diretamente à categoria.
            if iid_filho in itens_orcamento:
                valores_item = list(tabela_orcamento.item(iid_filho, "values"))
                if valores_item:
                    valores_item[0] = (
                        f"{numero_categoria}.{numero_segundo_nivel}"
                    )
                    tabela_orcamento.item(iid_filho, values=valores_item)
                continue

            # Caso contrário, o filho é uma subcategoria.
            valores_sub = list(tabela_orcamento.item(iid_filho, "values"))
            if valores_sub:
                valores_sub[0] = (
                    f"{numero_categoria}.{numero_segundo_nivel}"
                )
                tabela_orcamento.item(iid_filho, values=valores_sub)

            numero_item = 0

            for iid_item in tabela_orcamento.get_children(iid_filho):
                numero_item += 1

                valores_item = list(tabela_orcamento.item(iid_item, "values"))
                if valores_item:
                    valores_item[0] = (
                        f"{numero_categoria}."
                        f"{numero_segundo_nivel}."
                        f"{numero_item}"
                    )
                    tabela_orcamento.item(iid_item, values=valores_item)


def remover_selecionado_orcamento():
    selecionados = tabela_orcamento.selection()

    if not selecionados:
        messagebox.showwarning("Atenção", "Selecione um item, subcategoria ou categoria.")
        return

    if not messagebox.askyesno(
        "Remover",
        "Deseja remover o(s) elemento(s) selecionado(s)?\n\n"
        "Ao remover uma categoria ou subcategoria, todas as composições "
        "dentro dela também serão removidas."
    ):
        return

    def remover_recursivo(iid):
        for filho in list(tabela_orcamento.get_children(iid)):
            remover_recursivo(filho)

        itens_orcamento.pop(iid, None)

        # Remove referências de subcategorias/categorias.
        for nome_cat, dados_cat in list(categorias_orcamento.items()):
            for nome_sub, dados_sub in list(dados_cat["subcategorias"].items()):
                if dados_sub["iid"] == iid:
                    del dados_cat["subcategorias"][nome_sub]

            if dados_cat["iid"] == iid:
                del categorias_orcamento[nome_cat]

        if tabela_orcamento.exists(iid):
            tabela_orcamento.delete(iid)

    # Evita tentar remover filho já removido por um pai selecionado.
    selecionados_set = set(selecionados)

    for iid in list(selecionados):
        pai = tabela_orcamento.parent(iid)
        ignorar = False

        while pai:
            if pai in selecionados_set:
                ignorar = True
                break
            pai = tabela_orcamento.parent(pai)

        if not ignorar and tabela_orcamento.exists(iid):
            remover_recursivo(iid)

    atualizar_combobox_categorias()
    renumerar_orcamento()
    atualizar_totais_orcamento()


def limpar_orcamento():
    if not tabela_orcamento.get_children(""):
        return

    if not messagebox.askyesno(
        "Limpar orçamento",
        "Deseja remover todo o orçamento?"
    ):
        return

    for iid in tabela_orcamento.get_children(""):
        tabela_orcamento.delete(iid)

    categorias_orcamento.clear()
    itens_orcamento.clear()

    atualizar_combobox_categorias()
    atualizar_totais_orcamento()



# ============================================================
# SALVAR / ABRIR ORÇAMENTOS
# ============================================================

def inicializar_tabelas_orcamentos_salvos():
    """Cria as tabelas de projetos de orçamento em usuario.db."""
    conexao = conectar_usuario()
    try:
        conexao.executescript("""
            CREATE TABLE IF NOT EXISTS orcamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                obra TEXT,
                cliente TEXT,
                bdi REAL NOT NULL DEFAULT 0,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS estrutura_orcamento (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                orcamento_id INTEGER NOT NULL,
                tipo TEXT NOT NULL CHECK(tipo IN ('categoria', 'subcategoria', 'composicao')),
                categoria TEXT,
                subcategoria TEXT,
                codigo_composicao TEXT,
                quantidade REAL,
                ordem_categoria INTEGER NOT NULL DEFAULT 0,
                ordem_subcategoria INTEGER NOT NULL DEFAULT 0,
                ordem_item INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (orcamento_id) REFERENCES orcamentos(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_estrutura_orcamento
            ON estrutura_orcamento(orcamento_id, ordem_categoria, ordem_subcategoria, ordem_item);
        """)
        colunas = {r[1] for r in conexao.execute("PRAGMA table_info(estrutura_orcamento)")}
        for nome in ("banco", "uf", "regime"):
            if nome not in colunas:
                conexao.execute(f"ALTER TABLE estrutura_orcamento ADD COLUMN {nome} TEXT")
        conexao.commit()
    finally:
        conexao.close()


def _coletar_estrutura_orcamento():
    registros = []
    ordem_cat = 0

    for iid_cat in tabela_orcamento.get_children(""):
        ordem_cat += 1
        nome_cat = next(
            (nome for nome, dados in categorias_orcamento.items() if dados["iid"] == iid_cat),
            ""
        )
        if not nome_cat:
            continue

        registros.append(("categoria", nome_cat, "", None, None, ordem_cat, 0, 0, "", "", ""))
        ordem_segundo = 0

        for iid_filho in tabela_orcamento.get_children(iid_cat):
            ordem_segundo += 1

            if iid_filho in itens_orcamento:
                d = itens_orcamento[iid_filho]
                registros.append((
                    "composicao", nome_cat, "", d["codigo"], d["quantidade"],
                    ordem_cat, ordem_segundo, 0, d.get("banco","SEINFRA"), d.get("uf",""), d.get("regime","")
                ))
                continue

            nome_sub = next(
                (nome for nome, dados in categorias_orcamento[nome_cat]["subcategorias"].items()
                 if dados["iid"] == iid_filho),
                ""
            )
            if not nome_sub:
                continue

            registros.append((
                "subcategoria", nome_cat, nome_sub, None, None,
                ordem_cat, ordem_segundo, 0, "", "", ""
            ))

            ordem_item = 0
            for iid_item in tabela_orcamento.get_children(iid_filho):
                if iid_item not in itens_orcamento:
                    continue
                ordem_item += 1
                d = itens_orcamento[iid_item]
                registros.append((
                    "composicao", nome_cat, nome_sub, d["codigo"], d["quantidade"],
                    ordem_cat, ordem_segundo, ordem_item, d.get("banco","SEINFRA"), d.get("uf",""), d.get("regime","")
                ))

    return registros


def salvar_orcamento():
    global orcamento_salvo_atual_id, orcamento_salvo_atual_nome

    if not tabela_orcamento.get_children(""):
        messagebox.showwarning("Atenção", "O orçamento está vazio.")
        return

    try:
        bdi = obter_bdi_percentual()
    except ValueError as erro:
        messagebox.showerror("Erro", str(erro))
        return

    nome_obra = entrada_obra.get().strip()
    cliente = entrada_cliente.get().strip()

    if orcamento_salvo_atual_id is None:
        sugestao = nome_obra or "Novo orçamento"
        nome = simpledialog.askstring(
            "Salvar orçamento",
            "Nome para identificar este orçamento:",
            initialvalue=sugestao,
            parent=janela
        )
        if not nome or not nome.strip():
            return
        nome = nome.strip()
    else:
        nome = orcamento_salvo_atual_nome or nome_obra or "Orçamento"

    registros = _coletar_estrutura_orcamento()
    conexao = conectar_usuario()

    try:
        cursor = conexao.cursor()

        if orcamento_salvo_atual_id is None:
            cursor.execute("""
                INSERT INTO orcamentos (nome, obra, cliente, bdi)
                VALUES (?, ?, ?, ?)
            """, (nome, nome_obra, cliente, bdi))
            orcamento_salvo_atual_id = cursor.lastrowid
        else:
            cursor.execute("""
                UPDATE orcamentos
                SET nome = ?, obra = ?, cliente = ?, bdi = ?, atualizado_em = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (nome, nome_obra, cliente, bdi, orcamento_salvo_atual_id))
            cursor.execute(
                "DELETE FROM estrutura_orcamento WHERE orcamento_id = ?",
                (orcamento_salvo_atual_id,)
            )

        cursor.executemany("""
            INSERT INTO estrutura_orcamento (
                orcamento_id, tipo, categoria, subcategoria,
                codigo_composicao, quantidade,
                ordem_categoria, ordem_subcategoria, ordem_item, banco, uf, regime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (orcamento_salvo_atual_id,) + registro
            for registro in registros
        ])

        conexao.commit()
        orcamento_salvo_atual_nome = nome
        messagebox.showinfo(
            "Orçamento salvo",
            f"O orçamento '{nome}' foi salvo no banco de dados."
        )
    except Exception as erro:
        conexao.rollback()
        messagebox.showerror("Erro", f"Não foi possível salvar o orçamento:\n\n{erro}")
    finally:
        conexao.close()


def salvar_orcamento_como():
    global orcamento_salvo_atual_id, orcamento_salvo_atual_nome
    id_anterior = orcamento_salvo_atual_id
    nome_anterior = orcamento_salvo_atual_nome
    orcamento_salvo_atual_id = None
    orcamento_salvo_atual_nome = None
    salvar_orcamento()
    if orcamento_salvo_atual_id is None:
        orcamento_salvo_atual_id = id_anterior
        orcamento_salvo_atual_nome = nome_anterior


def _limpar_orcamento_sem_confirmacao():
    for iid in tabela_orcamento.get_children(""):
        tabela_orcamento.delete(iid)
    categorias_orcamento.clear()
    itens_orcamento.clear()
    atualizar_combobox_categorias()
    combo_categoria.set("")
    combo_subcategoria.set("")


def carregar_orcamento_salvo(orcamento_id):
    global orcamento_salvo_atual_id, orcamento_salvo_atual_nome

    conexao = conectar_usuario()
    try:
        cursor = conexao.cursor()
        cursor.execute("""
            SELECT id, nome, obra, cliente, bdi
            FROM orcamentos WHERE id = ?
        """, (orcamento_id,))
        cabecalho = cursor.fetchone()
        if not cabecalho:
            messagebox.showerror("Erro", "Orçamento salvo não encontrado.")
            return

        cursor.execute("""
            SELECT tipo, categoria, subcategoria, codigo_composicao, quantidade,
                   ordem_categoria, ordem_subcategoria, ordem_item,
                   COALESCE(banco,'SEINFRA'), COALESCE(uf,''), COALESCE(regime,'')
            FROM estrutura_orcamento
            WHERE orcamento_id = ?
            ORDER BY ordem_categoria, ordem_subcategoria, ordem_item, id
        """, (orcamento_id,))
        registros = cursor.fetchall()
    finally:
        conexao.close()

    _limpar_orcamento_sem_confirmacao()

    _, nome, obra, cliente, bdi = cabecalho
    entrada_obra.delete(0, tk.END)
    entrada_obra.insert(0, obra or "")
    entrada_cliente.delete(0, tk.END)
    entrada_cliente.insert(0, cliente or "")
    entrada_bdi_orcamento.delete(0, tk.END)
    entrada_bdi_orcamento.insert(0, str(bdi).replace(".", ","))

    # Primeiro recria categorias e subcategorias na ordem salva.
    for tipo, categoria, subcategoria, codigo, quantidade, _, _, _, banco, uf, regime in registros:
        if tipo == "categoria" and categoria not in categorias_orcamento:
            iid = tabela_orcamento.insert(
                "", tk.END, open=True,
                values=("", "", "", "", categoria, "", "", "", "", "", "")
            )
            categorias_orcamento[categoria] = {"iid": iid, "subcategorias": {}}
        elif tipo == "subcategoria":
            if categoria not in categorias_orcamento:
                continue
            subs = categorias_orcamento[categoria]["subcategorias"]
            if subcategoria not in subs:
                iid = tabela_orcamento.insert(
                    categorias_orcamento[categoria]["iid"], tk.END, open=True,
                    values=("", "", "", "", subcategoria, "", "", "", "", "", "")
                )
                subs[subcategoria] = {"iid": iid}

    # Depois recria as composições e recalcula com os preços atuais do banco.
    for tipo, categoria, subcategoria, codigo, quantidade, _, _, _, banco, uf, regime in registros:
        if tipo != "composicao" or categoria not in categorias_orcamento:
            continue

        resumo = obter_resumo_composicao(codigo, banco or "SEINFRA", uf or None, regime or None)
        if not resumo:
            continue

        if subcategoria:
            subs = categorias_orcamento[categoria]["subcategorias"]
            if subcategoria not in subs:
                continue
            pai = subs[subcategoria]["iid"]
        else:
            pai = categorias_orcamento[categoria]["iid"]

        iid = tabela_orcamento.insert(pai, tk.END)
        itens_orcamento[iid] = {
            "categoria": categoria,
            "subcategoria": subcategoria or "",
            "codigo": codigo,
            "banco": resumo.get("banco", banco or "SEINFRA"),
            "uf": resumo.get("uf", uf or ""),
            "regime": resumo.get("regime", regime or ""),
            "unidade": resumo.get("unidade", ""),
            "descricao": resumo["descricao"],
            "quantidade": quantidade or 0
        }
        recalcular_linha_orcamento(iid)

    orcamento_salvo_atual_id = orcamento_id
    orcamento_salvo_atual_nome = nome
    atualizar_combobox_categorias()
    renumerar_orcamento()
    atualizar_totais_orcamento()

    messagebox.showinfo("Orçamento aberto", f"Orçamento '{nome}' carregado com sucesso.")


def abrir_orcamento():
    conexao = conectar_usuario()
    try:
        registros = conexao.execute("""
            SELECT id, nome, obra, atualizado_em
            FROM orcamentos
            ORDER BY atualizado_em DESC, id DESC
        """).fetchall()
    finally:
        conexao.close()

    if not registros:
        messagebox.showinfo("Abrir orçamento", "Ainda não há orçamentos salvos.")
        return

    janela_lista = tk.Toplevel(janela)
    janela_lista.title("Abrir orçamento salvo")
    janela_lista.geometry("650x380")
    janela_lista.transient(janela)
    janela_lista.grab_set()

    ttk.Label(
        janela_lista,
        text="Selecione um orçamento salvo:",
        font=("Arial", 11, "bold")
    ).pack(anchor="w", padx=12, pady=(12, 6))

    lista = tk.Listbox(janela_lista, font=("Arial", 10))
    lista.pack(fill="both", expand=True, padx=12, pady=6)

    ids = []
    for oid, nome, obra, atualizado in registros:
        ids.append(oid)
        complemento = f" — {obra}" if obra else ""
        lista.insert(tk.END, f"{nome}{complemento}   [{atualizado}]")

    if ids:
        lista.selection_set(0)

    frame = ttk.Frame(janela_lista)
    frame.pack(fill="x", padx=12, pady=(6, 12))

    def confirmar(evento=None):
        selecao = lista.curselection()
        if not selecao:
            return
        oid = ids[selecao[0]]
        janela_lista.destroy()
        carregar_orcamento_salvo(oid)

    ttk.Button(frame, text="Abrir", command=confirmar).pack(side="right", padx=5)
    ttk.Button(frame, text="Cancelar", command=janela_lista.destroy).pack(side="right", padx=5)
    lista.bind("<Double-1>", confirmar)
    lista.bind("<Return>", confirmar)


# ============================================================
# EXPORTAR ORÇAMENTO PARA EXCEL
# ============================================================

def exportar_orcamento_excel():
    if not itens_orcamento:
        messagebox.showwarning(
            "Atenção",
            "Adicione pelo menos uma composição ao orçamento antes de exportar."
        )
        return

    try:
        bdi_atual = obter_bdi_percentual()
    except ValueError as erro:
        messagebox.showerror("Erro", str(erro))
        return

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    except ImportError:
        messagebox.showerror(
            "Dependência não instalada",
            "Para exportar para Excel, instale o openpyxl:\n\n"
            "pip install openpyxl"
        )
        return

    nome_obra = entrada_obra.get().strip()
    nome_cliente = entrada_cliente.get().strip()

    nome_padrao = "Orcamento_Sintetico.xlsx"
    if nome_obra:
        nome_seguro = "".join(
            c if c.isalnum() or c in (" ", "-", "_") else "_"
            for c in nome_obra
        ).strip().replace(" ", "_")
        if nome_seguro:
            nome_padrao = f"Orcamento_{nome_seguro}.xlsx"

    caminho = filedialog.asksaveasfilename(
        title="Salvar orçamento sintético",
        defaultextension=".xlsx",
        filetypes=[("Arquivo Excel", "*.xlsx")],
        initialfile=nome_padrao
    )

    if not caminho:
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Sintético c MO MAT EQ"

    # Layout da planilha:
    # A Item | B Código | C Banco | D Und | E Descrição | F Quant.
    # G Valor Unit | H:J Valor Unit com BDI | K:N Total (N = Total geral da composição)
    # O Peso | P:S auxiliares ocultas sem BDI.

    # Cabeçalho superior alinhado com a coluna Descrição.
    ws["E1"] = "Obra"
    ws["E2"] = nome_obra

    # Banco ocupa 3 colunas. Lista automaticamente os bancos usados no orçamento.
    ws.merge_cells("F1:H1")
    ws["F1"] = "Banco"
    ws.merge_cells("F2:H2")
    bancos_usados = sorted({
        dados.get("banco", "SEINFRA")
        for dados in itens_orcamento.values()
        if dados.get("banco", "SEINFRA")
    })
    ws["F2"] = ", ".join(bancos_usados)

    # BDI ocupa 3 colunas e permanece editável.
    ws.merge_cells("I1:K1")
    ws["I1"] = "B.D.I."
    ws.merge_cells("I2:K2")
    ws["I2"] = bdi_atual
    ws["I2"].number_format = '0.00"%"'

    # Encargos Sociais ocupa até a coluna Peso.
    ws.merge_cells("L1:O1")
    ws["L1"] = "Encargos Sociais"
    ws.merge_cells("L2:O2")
    ws["L2"] = ""

    ws.merge_cells("A3:O3")
    ws["A3"] = "Planilha Orçamentária Sintética Com Valor do Material, Mão de Obra e Equipamento"

    for faixa in ("A4:A5", "B4:B5", "C4:C5", "D4:D5", "E4:E5", "F4:F5", "G4:G5", "O4:O5"):
        ws.merge_cells(faixa)

    ws.merge_cells("H4:J4")
    ws.merge_cells("K4:N4")

    ws["A4"] = "Item"
    ws["B4"] = "Código"
    ws["C4"] = "Banco"
    ws["D4"] = "Und"
    ws["E4"] = "Descrição"
    ws["F4"] = "Quant."
    ws["G4"] = "Valor Unit"
    ws["H4"] = "Valor Unit com BDI"
    ws["K4"] = "Total"
    ws["O4"] = "Peso"

    ws["H5"] = "M. O."
    ws["I5"] = "EQ."
    ws["J5"] = "MAT."
    ws["K5"] = "M. O."
    ws["L5"] = "EQ."
    ws["M5"] = "MAT."
    ws["N5"] = "Total"

    # Auxiliares ocultas: valores unitários sem BDI.
    ws["P4"] = "MO s/ BDI"
    ws["Q4"] = "EQ s/ BDI"
    ws["R4"] = "MAT s/ BDI"
    ws["S4"] = "Valor Unit Completo s/ BDI"

    linha_atual = 6
    linhas_composicoes = []
    linhas_categoria = []
    linhas_subcategoria = []
    linha_por_iid = {}

    def escrever_composicao(iid, numero):
        nonlocal linha_atual

        dados = itens_orcamento[iid]
        resumo = obter_resumo_composicao(
            dados["codigo"],
            dados.get("banco", "SEINFRA"),
            dados.get("uf") or None,
            dados.get("regime") or None,
        )
        if not resumo:
            return

        linha = linha_atual
        linha_por_iid[iid] = linha
        linhas_composicoes.append(linha)

        mo_base = dados.get("mao_obra_sem_bdi", resumo["mao_obra_unit"])
        eq_base = dados.get("equipamentos_sem_bdi", resumo["equipamentos_unit"])
        mat_base = dados.get("materiais_sem_bdi", resumo["materiais_unit"])
        valor_unit_base = dados.get("valor_unitario", resumo["total_unit"])

        ws.cell(linha, 1, numero)
        ws.cell(linha, 2, dados["codigo"])
        ws.cell(linha, 3, dados.get("banco", "SEINFRA"))
        ws.cell(linha, 4, formatar_unidade_excel(dados.get("unidade", "")))
        ws.cell(linha, 5, dados["descricao"])
        ws.cell(linha, 6, dados["quantidade"])
        ws.cell(linha, 7, f"=S{linha}")

        # BDI em I2 é digitado como percentual "25", por isso /100.
        ws.cell(linha, 8, f"=TRUNC(P{linha}*(1+$I$2/100),2)")
        ws.cell(linha, 9, f"=TRUNC(Q{linha}*(1+$I$2/100),2)")
        ws.cell(linha, 10, f"=TRUNC(R{linha}*(1+$I$2/100),2)")

        ws.cell(linha, 11, f"=TRUNC(H{linha}*F{linha},2)")
        ws.cell(linha, 12, f"=TRUNC(I{linha}*F{linha},2)")
        ws.cell(linha, 13, f"=TRUNC(J{linha}*F{linha},2)")
        ws.cell(linha, 14, f"=TRUNC(TRUNC(S{linha}*(1+$I$2/100),2)*F{linha},2)")

        # As células auxiliares armazenam a base monetária usada no cálculo:
        # no máximo 2 casas decimais, obtidas por truncamento e nunca por round().
        ws.cell(linha, 16, float(truncar_2_casas(mo_base)))
        ws.cell(linha, 17, float(truncar_2_casas(eq_base)))
        ws.cell(linha, 18, float(truncar_2_casas(mat_base)))
        ws.cell(linha, 19, float(truncar_2_casas(valor_unit_base)))

        linha_atual += 1

    def escrever_subcategoria(iid_sub, numero):
        nonlocal linha_atual

        linha = linha_atual
        linha_por_iid[iid_sub] = linha
        linhas_subcategoria.append(linha)

        valores = tabela_orcamento.item(iid_sub, "values")
        nome = valores[4] if len(valores) > 4 else "SUBCATEGORIA"

        ws.cell(linha, 1, numero)
        ws.cell(linha, 5, nome)
        linha_atual += 1

        linhas_filhos = []
        for indice, iid_item in enumerate(tabela_orcamento.get_children(iid_sub), start=1):
            escrever_composicao(iid_item, f"{numero}.{indice}")
            if iid_item in linha_por_iid:
                linhas_filhos.append(linha_por_iid[iid_item])

        if linhas_filhos:
            for coluna in range(11, 15):
                letra = chr(64 + coluna)
                refs = ",".join(f"{letra}{x}" for x in linhas_filhos)
                ws.cell(linha, coluna, f"=SUM({refs})")

    def escrever_categoria(iid_cat, numero):
        nonlocal linha_atual

        linha = linha_atual
        linha_por_iid[iid_cat] = linha
        linhas_categoria.append(linha)

        valores = tabela_orcamento.item(iid_cat, "values")
        nome = valores[4] if len(valores) > 4 else "CATEGORIA"

        ws.cell(linha, 1, numero)
        ws.cell(linha, 5, nome)
        linha_atual += 1

        linhas_filhos = []
        numero_segundo_nivel = 0

        for iid_filho in tabela_orcamento.get_children(iid_cat):
            numero_segundo_nivel += 1
            numero_filho = f"{numero}.{numero_segundo_nivel}"

            if iid_filho in itens_orcamento:
                escrever_composicao(iid_filho, numero_filho)
            else:
                escrever_subcategoria(iid_filho, numero_filho)

            if iid_filho in linha_por_iid:
                linhas_filhos.append(linha_por_iid[iid_filho])

        if linhas_filhos:
            for coluna in range(11, 15):
                letra = chr(64 + coluna)
                refs = ",".join(f"{letra}{x}" for x in linhas_filhos)
                ws.cell(linha, coluna, f"=SUM({refs})")

    for indice_cat, iid_cat in enumerate(tabela_orcamento.get_children(""), start=1):
        escrever_categoria(iid_cat, str(indice_cat))

    linha_total = linha_atual + 1

    # Total principal: começa em H, imediatamente antes do grupo "Total" (I:K).
    ws.cell(linha_total, 10, "TOTAL GERAL")

    if linhas_categoria:
        for coluna in range(11, 15):
            letra = chr(64 + coluna)
            refs = ",".join(f"{letra}{x}" for x in linhas_categoria)
            ws.cell(linha_total, coluna, f"=SUM({refs})")

    ws.cell(linha_total, 15, 1)

    for linha in linhas_composicoes + linhas_categoria + linhas_subcategoria:
        ws.cell(linha, 15, f'=IFERROR(N{linha}/$N${linha_total},0)')

    # Resumo financeiro abaixo do total principal.
    # Rótulos ocupam I:J e os valores K:M. As fórmulas ficam no próprio Excel.
    linha_total_sem_bdi = linha_total + 2
    linha_total_bdi = linha_total + 3
    linha_total_final = linha_total + 4

    for linha_resumo, rotulo in (
        (linha_total_sem_bdi, "Total sem BDI"),
        (linha_total_bdi, "Total do BDI"),
        (linha_total_final, "Total Geral"),
    ):
        ws.merge_cells(start_row=linha_resumo, start_column=11, end_row=linha_resumo, end_column=12)
        ws.cell(linha_resumo, 11, rotulo)
        ws.merge_cells(start_row=linha_resumo, start_column=13, end_row=linha_resumo, end_column=15)

    # Total sem BDI:
    # cada composição é calculada individualmente com truncamento em 2 casas,
    # seguindo a mesma lógica usada no Total com BDI.
    #
    # Exemplo por composição:
    #   TRUNC(valor_unitario_sem_bdi * quantidade, 2)
    #
    # Depois somamos os resultados já truncados de todas as composições.
    # Com BDI = 0%, isso mantém o Total sem BDI consistente com o Total com BDI.
    if linhas_composicoes:
        parcelas_sem_bdi = "+".join(
            f"TRUNC(S{x}*F{x},2)"
            for x in linhas_composicoes
        )
        ws.cell(linha_total_sem_bdi, 13, f"={parcelas_sem_bdi}")
    else:
        ws.cell(linha_total_sem_bdi, 13, "=0")

    # Total do BDI = total com BDI - total sem BDI.
    ws.cell(linha_total_bdi, 13, f"=N{linha_total}-M{linha_total_sem_bdi}")

    # Total Geral = total completo do orçamento com BDI.
    ws.cell(linha_total_final, 13, f"=N{linha_total}")

    # --------------------- FORMATAÇÃO ------------------------
    branco = "FFFFFF"
    preto = "000000"
    azul_categoria = "A6C9EB"
    verde_item = "DFF0D8"
    cinza_total = "D9E1F2"
    #cinza = "B7B7B7"

    borda = Side(style="thin", color="B7B7B7")
    borda_celula = Border(left=borda, right=borda, top=borda, bottom=borda)

    fonte_padrao = Font(name="Arial", size=10, color=preto)
    fonte_negrito = Font(name="Arial", size=10, bold=True, color=preto)

    for row in ws.iter_rows(min_row=1, max_row=linha_total_final, min_col=1, max_col=15):
        for cel in row:
            cel.font = fonte_padrao
            cel.alignment = Alignment(vertical="top", wrap_text=True)

    # Primeira linha: fonte 11 e negrito. Segunda linha: negrito.
    for coluna in range(1, 16):
        ws.cell(1, coluna).font = Font(name="Arial", size=11, bold=True, color=preto)
        ws.cell(2, coluna).font = Font(name="Arial", size=10, bold=True, color=preto)

    ws["A3"].font = Font(name="Arial", size=11, bold=True)
    ws["A3"].alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Cabeçalhos brancos com bordas, como na planilha enviada.
    for linha in (4, 5):
        for coluna in range(1, 16):
            cel = ws.cell(linha, coluna)
            cel.font = Font(name="Arial", size=11, bold=True)
            cel.fill = PatternFill("solid", fgColor=branco)
            cel.border = borda_celula
            cel.alignment = Alignment(
                horizontal="center",
                vertical="top",
                wrap_text=True
            )
    ws["B4"].alignment = Alignment(horizontal="right", vertical="top", wrap_text=True)
    ws["C4"].alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)

    # Cabeçalhos internos de Valor Unit com BDI e Total alinhados à direita.
    for coluna in range(8, 15):
        ws.cell(5, coluna).alignment = Alignment(horizontal="right", vertical="top", wrap_text=True)

    # Linhas de composição em verde-claro.
    for linha in linhas_composicoes:
        for coluna in range(1, 16):
            cel = ws.cell(linha, coluna)
            cel.fill = PatternFill("solid", fgColor=verde_item)
            cel.border = borda_celula
            cel.alignment = Alignment(vertical="top", wrap_text=True)

        # Código à direita; Banco (coluna seguinte) à esquerda.
        ws.cell(linha, 2).alignment = Alignment(horizontal="right", vertical="top", wrap_text=True)
        ws.cell(linha, 3).alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        # Valores unitários com BDI, totais, valor total e peso à direita.
        for coluna in range(8, 16):
            ws.cell(linha, coluna).alignment = Alignment(horizontal="right", vertical="top", wrap_text=True)

    # Categorias e subcategorias no azul-claro da referência.
    for linha in linhas_categoria + linhas_subcategoria:
        for coluna in range(1, 16):
            cel = ws.cell(linha, coluna)
            cel.fill = PatternFill("solid", fgColor=azul_categoria)
            cel.font = fonte_negrito
            cel.border = borda_celula
            cel.alignment = Alignment(vertical="top", wrap_text=True)

    for coluna in range(10, 16):
        cel = ws.cell(linha_total, coluna)
        cel.fill = PatternFill("solid", fgColor=cinza_total)
        cel.font = fonte_negrito
        cel.border = borda_celula

    # Três linhas do resumo financeiro abaixo do total principal.
    for linha in (linha_total_sem_bdi, linha_total_bdi, linha_total_final):
        for coluna in range(11, 16):
            cel = ws.cell(linha, coluna)
            cel.font = fonte_negrito
            cel.border = borda_celula
            cel.alignment = Alignment(vertical="center", wrap_text=True)
        ws.cell(linha, 13).number_format = '#,##0.00'

    formato_moeda = '#,##0.00'
    formato_qtd = '#,##0.0000'

    for linha in linhas_composicoes:
        ws.cell(linha, 6).number_format = formato_qtd
        for coluna in range(7, 15):
            ws.cell(linha, coluna).number_format = formato_moeda
        ws.cell(linha, 15).number_format = "0.00%"

    for linha in linhas_categoria + linhas_subcategoria + [linha_total]:
        for coluna in range(11, 15):
            ws.cell(linha, coluna).number_format = formato_moeda
        ws.cell(linha, 15).number_format = "0.00%"

    # Dimensões próximas às do arquivo de referência.
    larguras = {
        "A": 10, "B": 14, "C": 12, "D": 8, "E": 60,
        "F": 10, "G": 13, "H": 13, "I": 13, "J": 13,
        "K": 13, "L": 13, "M": 13, "N": 15, "O": 10
    }
    for coluna, largura in larguras.items():
        ws.column_dimensions[coluna].width = largura

    ws.row_dimensions[1].height = 18
    ws.row_dimensions[2].height = 80
    ws.row_dimensions[3].height = 20
    ws.row_dimensions[4].height = 22
    ws.row_dimensions[5].height = 22

    for linha in linhas_categoria + linhas_subcategoria:
        ws.row_dimensions[linha].height = 24

    for linha in linhas_composicoes:
        ws.row_dimensions[linha].height = 32

    # Colunas auxiliares não aparecem para o cliente.
    for coluna in ("P", "Q", "R", "S"):
        ws.column_dimensions[coluna].hidden = True

    #ws.freeze_panes = "A6"
    ws.sheet_view.showGridLines = False
    #ws.auto_filter.ref = f"A4:M{linha_total}"

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:5"
    ws.print_area = f"A1:O{linha_total_final}"

    try:
        wb.save(caminho)
    except PermissionError:
        messagebox.showerror(
            "Erro ao salvar",
            "Não foi possível salvar o arquivo.\n\n"
            "Verifique se ele já está aberto no Excel e tente novamente."
        )
        return
    except Exception as erro:
        messagebox.showerror(
            "Erro ao exportar",
            f"Não foi possível gerar o Excel:\n\n{erro}"
        )
        return

    messagebox.showinfo(
        "Exportação concluída",
        f"Orçamento exportado com sucesso para:\n\n{caminho}\n"
    )



# ============================================================
# BUSCA POR DESCRIÇÃO
# ============================================================

def _consultar_registros_por_descricao(banco, tipo, termo, limite=300):
    banco = (banco or "").strip().upper()
    tipo = (tipo or "").strip().upper()
    termo = (termo or "").strip()

    if not termo:
        return []

    palavras = [p for p in termo.split() if p]
    if not palavras:
        return []

    if banco == "SEINFRA":
        con = conectar()
        tabela = "composicoes" if tipo == "COMPOSICAO" else "insumos"
    elif banco == "SINAPI":
        con = conectar_sinapi()
        tabela = "composicoes" if tipo == "COMPOSICAO" else "insumos"
    elif banco in ("PRÓPRIO", "PROPRIO"):
        con = conectar_usuario()
        tabela = "composicoes_proprias" if tipo == "COMPOSICAO" else "insumos_proprios"
    else:
        return []

    try:
        condicoes = []
        params = []
        for palavra in palavras:
            condicoes.append("UPPER(descricao) LIKE ?")
            params.append(f"%{palavra.upper()}%")

        sql = f"""
            SELECT codigo, descricao, unidade
            FROM {tabela}
            WHERE {' AND '.join(condicoes)}
            ORDER BY descricao, codigo
            LIMIT ?
        """
        params.append(limite)
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def abrir_busca_descricao(tipo, destino):
    tipo = (tipo or "").strip().upper()
    destino = (destino or "").strip().upper()

    if destino in ("CONSULTA_COMPOSICAO", "CONSULTA_INSUMO"):
        banco_inicial = banco_selecionado("combo_banco_consulta")
    elif destino == "ORCAMENTO":
        banco_inicial = banco_selecionado("combo_banco_orcamento")
    else:
        banco_inicial = "SEINFRA"

    janela_busca = tk.Toplevel(janela)
    janela_busca.title(
        "Buscar composição por descrição"
        if tipo == "COMPOSICAO"
        else "Buscar insumo por descrição"
    )
    janela_busca.geometry("980x560")
    janela_busca.minsize(780, 420)
    janela_busca.transient(janela)

    frame_filtros = ttk.LabelFrame(janela_busca, text="Busca")
    frame_filtros.pack(fill="x", padx=10, pady=10)

    ttk.Label(frame_filtros, text="Banco:").grid(
        row=0, column=0, padx=(10, 5), pady=8, sticky="e"
    )

    combo_banco_busca = ttk.Combobox(
        frame_filtros,
        state="readonly",
        width=12,
        values=("SEINFRA", "SINAPI", "PRÓPRIO"),
    )
    combo_banco_busca.grid(row=0, column=1, padx=5, pady=8)
    combo_banco_busca.set(banco_inicial if banco_inicial else "SEINFRA")

    ttk.Label(frame_filtros, text="Descrição:").grid(
        row=0, column=2, padx=(15, 5), pady=8, sticky="e"
    )

    entrada_descricao_busca = ttk.Entry(frame_filtros, width=55)
    entrada_descricao_busca.grid(row=0, column=3, padx=5, pady=8, sticky="ew")
    frame_filtros.columnconfigure(3, weight=1)

    frame_resultados = ttk.Frame(janela_busca)
    frame_resultados.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    tabela_busca = ttk.Treeview(
        frame_resultados,
        columns=("codigo", "descricao", "unidade"),
        show="headings",
        selectmode="browse",
    )
    tabela_busca.heading("codigo", text="Código")
    tabela_busca.heading("descricao", text="Descrição")
    tabela_busca.heading("unidade", text="Und")
    tabela_busca.column("codigo", width=130, anchor="center")
    tabela_busca.column("descricao", width=680, anchor="w")
    tabela_busca.column("unidade", width=90, anchor="center")

    scroll_y_busca = ttk.Scrollbar(
        frame_resultados,
        orient="vertical",
        command=tabela_busca.yview,
    )
    tabela_busca.configure(yscrollcommand=scroll_y_busca.set)
    tabela_busca.pack(side="left", fill="both", expand=True)
    scroll_y_busca.pack(side="right", fill="y")

    label_status_busca = ttk.Label(
        janela_busca,
        text="Digite uma descrição e clique em Buscar.",
        foreground="#555555",
    )
    label_status_busca.pack(anchor="w", padx=15, pady=(0, 5))

    def executar_busca(evento=None):
        termo = entrada_descricao_busca.get().strip()
        banco = combo_banco_busca.get().strip().upper()

        for iid in tabela_busca.get_children():
            tabela_busca.delete(iid)

        if not termo:
            label_status_busca.config(
                text="Informe uma palavra ou parte da descrição."
            )
            return

        try:
            resultados = _consultar_registros_por_descricao(
                banco, tipo, termo, limite=300
            )
        except Exception as erro:
            messagebox.showerror(
                "Erro",
                f"Não foi possível realizar a busca:\n\n{erro}",
                parent=janela_busca,
            )
            return

        for codigo, descricao, unidade in resultados:
            tabela_busca.insert(
                "", tk.END,
                values=(codigo, descricao or "", unidade or "")
            )

        if resultados:
            label_status_busca.config(
                text=(
                    f"{len(resultados)} resultado(s) encontrado(s). "
                    "Dê duplo clique no item desejado."
                )
            )
        else:
            label_status_busca.config(
                text="Nenhum resultado encontrado para essa descrição."
            )

    def selecionar_resultado(evento=None):
        selecao = tabela_busca.selection()
        if not selecao:
            return

        valores = tabela_busca.item(selecao[0], "values")
        if not valores:
            return

        codigo = str(valores[0]).strip()
        banco = combo_banco_busca.get().strip().upper()

        if destino == "CONSULTA_COMPOSICAO":
            combo_banco_consulta.set(banco)
            entrada_codigo_composicao.delete(0, tk.END)
            entrada_codigo_composicao.insert(0, codigo)
            janela_busca.destroy()
            consultar_composicao(codigo)

        elif destino == "CONSULTA_INSUMO":
            combo_banco_consulta.set(banco)
            entrada_codigo_consulta.delete(0, tk.END)
            entrada_codigo_consulta.insert(0, codigo)
            janela_busca.destroy()
            consultar_insumo()

        elif destino == "ORCAMENTO":
            combo_banco_orcamento.set(banco)
            entrada_codigo_orcamento.delete(0, tk.END)
            entrada_codigo_orcamento.insert(0, codigo)
            janela_busca.destroy()
            entrada_quantidade_orcamento.focus_set()

    ttk.Button(
        frame_filtros,
        text="Buscar",
        command=executar_busca,
    ).grid(row=0, column=4, padx=8, pady=8)

    ttk.Button(
        frame_filtros,
        text="Usar selecionado",
        command=selecionar_resultado,
    ).grid(row=0, column=5, padx=(0, 10), pady=8)

    entrada_descricao_busca.bind("<Return>", executar_busca)
    tabela_busca.bind("<Double-1>", selecionar_resultado)
    entrada_descricao_busca.focus_set()


# ============================================================
# INTERFACE
# ============================================================

janela = tk.Tk()
janela.title("Sistema de Composições e Orçamentos")
janela.geometry("1450x820")

abas = ttk.Notebook(janela)
abas.pack(fill="both", expand=True, padx=10, pady=10)


# ============================================================
# ABA 1 - CONSULTA UNIFICADA
# ============================================================

aba_consulta = ttk.Frame(abas)
abas.add(aba_consulta, text="Consulta")

frame_banco_consulta = ttk.LabelFrame(
    aba_consulta,
    text="Banco de consulta"
)
frame_banco_consulta.pack(fill="x", padx=10, pady=(10, 5))

ttk.Label(frame_banco_consulta, text="Banco:").pack(side="left", padx=(10, 5), pady=8)
combo_banco_consulta = ttk.Combobox(
    frame_banco_consulta,
    state="readonly",
    width=12,
    values=("SEINFRA", "SINAPI", "PRÓPRIO")
)
combo_banco_consulta.set("SEINFRA")
combo_banco_consulta.pack(side="left", padx=(0, 10), pady=8)

subabas_consulta = ttk.Notebook(aba_consulta)
subabas_consulta.pack(fill="both", expand=True, padx=10, pady=(5, 10))


# ---------------- COMPOSIÇÕES ----------------
aba_consulta_composicoes = ttk.Frame(subabas_consulta)
subabas_consulta.add(aba_consulta_composicoes, text="Composições")

frame_busca = ttk.Frame(aba_consulta_composicoes)
frame_busca.pack(fill="x", padx=10, pady=10)

ttk.Label(frame_busca, text="Código da composição:").pack(side="left")

entrada_codigo_composicao = ttk.Entry(frame_busca, width=20)
entrada_codigo_composicao.pack(side="left", padx=10)

ttk.Button(
    frame_busca,
    text="Consultar",
    command=consultar_composicao
).pack(side="left")

ttk.Button(
    frame_busca,
    text="Buscar por descrição",
    command=lambda: abrir_busca_descricao(
        "COMPOSICAO", "CONSULTA_COMPOSICAO"
    )
).pack(side="left", padx=(8, 0))

ttk.Label(
    frame_busca,
    text="Dê duplo clique no Preço unitário de um insumo para alterá-lo.",
    foreground="#555555"
).pack(side="left", padx=20)

label_composicao = ttk.Label(
    aba_consulta_composicoes,
    text="Nenhuma composição selecionada.",
    font=("Arial", 12, "bold")
)
label_composicao.pack(anchor="w", padx=10, pady=10)

frame_tabela = ttk.Frame(aba_consulta_composicoes)
frame_tabela.pack(fill="both", expand=True, padx=10, pady=5)

colunas_comp = (
    "codigo", "descricao", "unidade", "coeficiente", "preco", "total", "pct_as"
)

tabela_composicao = ttk.Treeview(
    frame_tabela,
    columns=colunas_comp,
    show="tree headings"
)

tabela_composicao.heading("#0", text="Categoria")
tabela_composicao.column("#0", width=170, anchor="w")

titulos_comp = (
    "Código", "Descrição", "Un.", "Coeficiente",
    "Preço unitário", "Total", "%AS"
)
larguras_comp = (90, 500, 65, 120, 120, 120, 85)

for coluna, titulo, largura in zip(colunas_comp, titulos_comp, larguras_comp):
    tabela_composicao.heading(coluna, text=titulo)
    tabela_composicao.column(
        coluna,
        width=largura,
        anchor="w" if coluna == "descricao" else "e"
    )

scroll_comp = ttk.Scrollbar(
    frame_tabela,
    orient="vertical",
    command=tabela_composicao.yview
)
tabela_composicao.configure(yscrollcommand=scroll_comp.set)
tabela_composicao.pack(side="left", fill="both", expand=True)
scroll_comp.pack(side="right", fill="y")
tabela_composicao.bind("<Double-1>", editar_preco_tabela_composicao)

label_totais = ttk.Label(
    aba_consulta_composicoes,
    text="",
    font=("Arial", 11, "bold"),
    justify="left"
)
label_totais.pack(anchor="e", padx=20, pady=15)


# ---------------- INSUMOS ----------------
aba_consulta_insumos = ttk.Frame(subabas_consulta)
subabas_consulta.add(aba_consulta_insumos, text="Insumos")

frame_insumo = ttk.Frame(aba_consulta_insumos)
frame_insumo.pack(fill="x", padx=20, pady=20)

ttk.Label(frame_insumo, text="Código do insumo:").grid(
    row=0, column=0, pady=8, sticky="e"
)

entrada_codigo_consulta = ttk.Entry(frame_insumo, width=25)
entrada_codigo_consulta.grid(row=0, column=1, padx=10, pady=8)

ttk.Button(
    frame_insumo,
    text="Consultar",
    command=consultar_insumo
).grid(row=0, column=2, padx=10, pady=8)

ttk.Button(
    frame_insumo,
    text="Buscar por descrição",
    command=lambda: abrir_busca_descricao(
        "INSUMO", "CONSULTA_INSUMO"
    )
).grid(row=0, column=3, padx=10, pady=8)

label_resultado_insumo = ttk.Label(
    aba_consulta_insumos,
    text="Nenhum insumo selecionado.",
    font=("Arial", 11),
    justify="left"
)
label_resultado_insumo.pack(anchor="w", padx=30, pady=(5, 15))

frame_edicao_insumo = ttk.LabelFrame(
    aba_consulta_insumos,
    text="Alterar preço utilizado"
)
frame_edicao_insumo.pack(fill="x", padx=30, pady=10)

ttk.Label(frame_edicao_insumo, text="Novo preço:").pack(
    side="left", padx=(10, 5), pady=10
)

entrada_preco_consulta_insumo = ttk.Entry(frame_edicao_insumo, width=20)
entrada_preco_consulta_insumo.pack(side="left", padx=5, pady=10)

ttk.Button(
    frame_edicao_insumo,
    text="Salvar preço",
    command=salvar_preco_insumo_consultado
).pack(side="left", padx=5, pady=10)

botao_restaurar_preco_insumo = ttk.Button(
    frame_edicao_insumo,
    text="Restaurar preço oficial",
    command=restaurar_preco_insumo_consultado,
    state="disabled"
)
botao_restaurar_preco_insumo.pack(side="left", padx=5, pady=10)

ttk.Label(
    aba_consulta_insumos,
    text=(
        "SEINFRA/SINAPI: o preço alterado é salvo em usuario.db e não modifica o banco oficial. "
        "PRÓPRIO: o preço do próprio cadastro é atualizado."
    ),
    foreground="#555555",
    wraplength=1100,
    justify="left"
).pack(anchor="w", padx=30, pady=10)


# Compatibilidade interna com funções legadas que ainda usam estes nomes.
combo_banco_composicao = combo_banco_consulta


# ============================================================
# ABA 2 - BANCO PRÓPRIO
# ============================================================

aba_proprio = ttk.Frame(abas)
abas.add(aba_proprio, text="Criar Insumos/Composições")

subabas_proprio = ttk.Notebook(aba_proprio)
subabas_proprio.pack(fill="both", expand=True, padx=10, pady=10)

# ---- Insumos próprios ----
aba_prop_ins = ttk.Frame(subabas_proprio)
subabas_proprio.add(aba_prop_ins, text="Criar Insumos")

form_prop_ins = ttk.LabelFrame(aba_prop_ins, text="Cadastro / edição de insumo")
form_prop_ins.pack(fill="x", padx=10, pady=10)

ttk.Label(form_prop_ins,text="Código:").grid(row=0,column=0,padx=5,pady=6,sticky="e")
entrada_codigo_insumo_proprio=ttk.Entry(form_prop_ins,width=16); entrada_codigo_insumo_proprio.grid(row=0,column=1,padx=5,pady=6)
ttk.Label(form_prop_ins,text="Descrição:").grid(row=0,column=2,padx=5,pady=6,sticky="e")
entrada_desc_insumo_proprio=ttk.Entry(form_prop_ins,width=55); entrada_desc_insumo_proprio.grid(row=0,column=3,padx=5,pady=6,sticky="ew")
ttk.Label(form_prop_ins,text="Und:").grid(row=0,column=4,padx=5,pady=6,sticky="e")
entrada_unidade_insumo_proprio=ttk.Entry(form_prop_ins,width=10); entrada_unidade_insumo_proprio.grid(row=0,column=5,padx=5,pady=6)

ttk.Label(form_prop_ins,text="Categoria:").grid(row=1,column=0,padx=5,pady=6,sticky="e")
combo_categoria_insumo_proprio=ttk.Combobox(form_prop_ins,state="readonly",width=18,values=("MAO DE OBRA","EQUIPAMENTOS","MATERIAIS")); combo_categoria_insumo_proprio.grid(row=1,column=1,padx=5,pady=6); combo_categoria_insumo_proprio.set("MATERIAIS")
ttk.Label(form_prop_ins,text="Preço unitário:").grid(row=1,column=2,padx=5,pady=6,sticky="e")
entrada_preco_insumo_proprio=ttk.Entry(form_prop_ins,width=18)
entrada_preco_insumo_proprio.grid(row=1,column=3,padx=5,pady=6,sticky="w")

ttk.Label(form_prop_ins,text="Coeficiente padrão:").grid(row=1,column=4,padx=5,pady=6,sticky="e")
entrada_coef_padrao_insumo_proprio=ttk.Entry(form_prop_ins,width=16)
entrada_coef_padrao_insumo_proprio.grid(row=1,column=5,padx=5,pady=6,sticky="w")
entrada_coef_padrao_insumo_proprio.insert(0,"1")

botoes_prop_ins=ttk.Frame(form_prop_ins)
botoes_prop_ins.grid(row=2,column=0,columnspan=6,padx=5,pady=6)
ttk.Button(botoes_prop_ins,text="Salvar",command=salvar_insumo_proprio).pack(side="left",padx=3)
ttk.Button(botoes_prop_ins,text="Novo",command=limpar_form_insumo_proprio).pack(side="left",padx=3)
ttk.Button(botoes_prop_ins,text="Excluir",command=excluir_insumo_proprio).pack(side="left",padx=3)
form_prop_ins.columnconfigure(3,weight=1)

frame_lista_prop_ins=ttk.Frame(aba_prop_ins); frame_lista_prop_ins.pack(fill="both",expand=True,padx=10,pady=(0,10))
tabela_insumos_proprios=ttk.Treeview(
    frame_lista_prop_ins,
    columns=("codigo","descricao","unidade","categoria","coef_padrao","preco"),
    show="headings"
)
for c,t,w in (
    ("codigo","Código",100),("descricao","Descrição",500),("unidade","Und",70),
    ("categoria","Categoria",150),("coef_padrao","Coef. padrão",110),("preco","Preço unitário",120)
):
    tabela_insumos_proprios.heading(c,text=t); tabela_insumos_proprios.column(c,width=w,anchor="w" if c=="descricao" else "center")
tabela_insumos_proprios.pack(side="left",fill="both",expand=True)
scroll_pi=ttk.Scrollbar(frame_lista_prop_ins,orient="vertical",command=tabela_insumos_proprios.yview); scroll_pi.pack(side="right",fill="y"); tabela_insumos_proprios.configure(yscrollcommand=scroll_pi.set)
tabela_insumos_proprios.bind("<<TreeviewSelect>>",selecionar_insumo_proprio)

# ---- Composições próprias ----
aba_prop_comp = ttk.Frame(subabas_proprio)
subabas_proprio.add(aba_prop_comp, text="Criar Composições")

form_prop_comp=ttk.LabelFrame(aba_prop_comp,text="Cadastro / edição da composição")
form_prop_comp.pack(fill="x",padx=10,pady=10)
ttk.Label(form_prop_comp,text="Código:").grid(row=0,column=0,padx=5,pady=6,sticky="e")
entrada_codigo_comp_propria=ttk.Entry(form_prop_comp,width=16); entrada_codigo_comp_propria.grid(row=0,column=1,padx=5,pady=6)
ttk.Label(form_prop_comp,text="Descrição:").grid(row=0,column=2,padx=5,pady=6,sticky="e")
entrada_desc_comp_propria=ttk.Entry(form_prop_comp,width=65); entrada_desc_comp_propria.grid(row=0,column=3,padx=5,pady=6,sticky="ew")
ttk.Label(form_prop_comp,text="Und:").grid(row=0,column=4,padx=5,pady=6,sticky="e")
entrada_unidade_comp_propria=ttk.Entry(form_prop_comp,width=10)
entrada_unidade_comp_propria.grid(row=0,column=5,padx=5,pady=6)

ttk.Label(form_prop_comp,text="Coeficiente padrão:").grid(row=0,column=6,padx=5,pady=6,sticky="e")
entrada_coef_padrao_comp_propria=ttk.Entry(form_prop_comp,width=14)
entrada_coef_padrao_comp_propria.grid(row=0,column=7,padx=5,pady=6)
entrada_coef_padrao_comp_propria.insert(0,"1")

ttk.Button(form_prop_comp,text="Salvar composição",command=salvar_cabecalho_composicao_propria).grid(row=0,column=8,padx=5,pady=6)
ttk.Button(form_prop_comp,text="Nova",command=limpar_form_composicao_propria).grid(row=0,column=9,padx=5,pady=6)
ttk.Button(form_prop_comp,text="Excluir",command=excluir_composicao_propria).grid(row=0,column=10,padx=5,pady=6)
form_prop_comp.columnconfigure(3,weight=1)

frame_prop_comp_lista = ttk.LabelFrame(
    aba_prop_comp,
    text="Composições salvas"
)
frame_prop_comp_lista.pack(
    fill="both",
    expand=True,
    padx=10,
    pady=(0, 10)
)

tabela_composicoes_proprias = ttk.Treeview(
    frame_prop_comp_lista,
    columns=("codigo", "descricao", "unidade", "coef_padrao", "total"),
    show="headings",
    height=18
)

for c, t, w in (
    ("codigo", "Código", 100),
    ("descricao", "Descrição", 650),
    ("unidade", "Und", 80),
    ("coef_padrao", "Coef. padrão", 110),
    ("total", "Total atual", 120),
):
    tabela_composicoes_proprias.heading(c, text=t)
    tabela_composicoes_proprias.column(
        c,
        width=w,
        anchor="w" if c == "descricao" else "center"
    )

tabela_composicoes_proprias.pack(
    fill="both",
    expand=True,
    padx=5,
    pady=5
)

tabela_composicoes_proprias.bind(
    "<Double-1>",
    lambda e: carregar_composicao_propria()
)

ttk.Label(
    aba_prop_comp,
    text=(
        "Para adicionar insumos à composição, use a aba "
        "'Adicionar em Composições'."
    ),
    foreground="#555555"
).pack(anchor="w", padx=15, pady=(0, 10))


# ---- Adicionar em composições ----
aba_prop_adicionais = ttk.Frame(subabas_proprio)
subabas_proprio.add(aba_prop_adicionais, text="Adicionar em Composições")

frame_destino_novo=ttk.LabelFrame(aba_prop_adicionais,text="1. Escolha a composição de destino")
frame_destino_novo.pack(fill="x",padx=10,pady=(10,5))
ttk.Label(frame_destino_novo,text="Banco:").grid(row=0,column=0,padx=5,pady=6,sticky="e")
combo_banco_destino_filtro=ttk.Combobox(frame_destino_novo,state="readonly",width=12,values=("SEINFRA","SINAPI","PRÓPRIO")); combo_banco_destino_filtro.grid(row=0,column=1,padx=5,pady=6); combo_banco_destino_filtro.set("SEINFRA")
combo_banco_destino_filtro.bind("<<ComboboxSelected>>",lambda e: filtrar_composicoes_destino())
ttk.Label(frame_destino_novo,text="Filtrar código/descrição:").grid(row=0,column=2,padx=5,pady=6,sticky="e")
entrada_filtro_composicao=ttk.Entry(frame_destino_novo,width=28); entrada_filtro_composicao.grid(row=0,column=3,padx=5,pady=6)
ttk.Button(frame_destino_novo,text="Filtrar",command=filtrar_composicoes_destino).grid(row=0,column=4,padx=5,pady=6)
ttk.Label(frame_destino_novo,text="Composição escolhida:").grid(row=0,column=5,padx=5,pady=6,sticky="e")
entrada_comp_destino_nova=ttk.Entry(frame_destino_novo,width=18); entrada_comp_destino_nova.grid(row=0,column=6,padx=5,pady=6)
frame_lista_comp_destino=ttk.Frame(frame_destino_novo); frame_lista_comp_destino.grid(row=1,column=0,columnspan=7,sticky="nsew",padx=5,pady=(0,8))
tabela_filtro_composicoes=ttk.Treeview(frame_lista_comp_destino,columns=("codigo","descricao","unidade"),show="headings",height=6)
for c,t,w in (("codigo","Código",120),("descricao","Descrição",760),("unidade","Und",80)):
    tabela_filtro_composicoes.heading(c,text=t); tabela_filtro_composicoes.column(c,width=w,anchor="w" if c=="descricao" else "center")
tabela_filtro_composicoes.pack(side="left",fill="both",expand=True)
scroll_dest=ttk.Scrollbar(frame_lista_comp_destino,orient="vertical",command=tabela_filtro_composicoes.yview); scroll_dest.pack(side="right",fill="y"); tabela_filtro_composicoes.configure(yscrollcommand=scroll_dest.set)
tabela_filtro_composicoes.bind("<<TreeviewSelect>>",selecionar_composicao_destino)
tabela_filtro_composicoes.bind("<Double-1>",selecionar_composicao_destino)

frame_origem_novo=ttk.LabelFrame(aba_prop_adicionais,text="2. Escolha o insumo que será adicionado")
frame_origem_novo.pack(fill="both",expand=True,padx=10,pady=5)
ttk.Label(frame_origem_novo,text="Banco do insumo:").grid(row=0,column=0,padx=5,pady=6,sticky="e")
combo_banco_origem_insumo=ttk.Combobox(frame_origem_novo,state="readonly",width=12,values=("SEINFRA","SINAPI","PRÓPRIO")); combo_banco_origem_insumo.grid(row=0,column=1,padx=5,pady=6); combo_banco_origem_insumo.set("PRÓPRIO")
combo_banco_origem_insumo.bind("<<ComboboxSelected>>",lambda e: filtrar_insumos_origem())
ttk.Label(frame_origem_novo,text="Filtrar código/descrição:").grid(row=0,column=2,padx=5,pady=6,sticky="e")
entrada_filtro_insumo=ttk.Entry(frame_origem_novo,width=28); entrada_filtro_insumo.grid(row=0,column=3,padx=5,pady=6)
ttk.Button(frame_origem_novo,text="Filtrar",command=filtrar_insumos_origem).grid(row=0,column=4,padx=5,pady=6)
ttk.Label(frame_origem_novo,text="Insumo escolhido:").grid(row=0,column=5,padx=5,pady=6,sticky="e")
entrada_insumo_origem_nova=ttk.Entry(frame_origem_novo,width=18); entrada_insumo_origem_nova.grid(row=0,column=6,padx=5,pady=6)
ttk.Label(frame_origem_novo,text="Coeficiente:").grid(row=0,column=7,padx=5,pady=6,sticky="e")
entrada_coef_insumo_nova=ttk.Entry(frame_origem_novo,width=14); entrada_coef_insumo_nova.grid(row=0,column=8,padx=5,pady=6); entrada_coef_insumo_nova.insert(0,"1")
ttk.Button(frame_origem_novo,text="Adicionar à composição",command=salvar_insumo_em_composicao).grid(row=0,column=9,padx=8,pady=6)
frame_lista_ins_origem=ttk.Frame(frame_origem_novo); frame_lista_ins_origem.grid(row=1,column=0,columnspan=10,sticky="nsew",padx=5,pady=(0,8))
tabela_filtro_insumos=ttk.Treeview(frame_lista_ins_origem,columns=("codigo","descricao","unidade"),show="headings",height=8)
for c,t,w in (("codigo","Código",120),("descricao","Descrição",780),("unidade","Und",80)):
    tabela_filtro_insumos.heading(c,text=t); tabela_filtro_insumos.column(c,width=w,anchor="w" if c=="descricao" else "center")
tabela_filtro_insumos.pack(side="left",fill="both",expand=True)
scroll_origem=ttk.Scrollbar(frame_lista_ins_origem,orient="vertical",command=tabela_filtro_insumos.yview); scroll_origem.pack(side="right",fill="y"); tabela_filtro_insumos.configure(yscrollcommand=scroll_origem.set)
tabela_filtro_insumos.bind("<<TreeviewSelect>>",selecionar_insumo_origem)
tabela_filtro_insumos.bind("<Double-1>",selecionar_insumo_origem)
frame_origem_novo.rowconfigure(1,weight=1); frame_origem_novo.columnconfigure(3,weight=1)

frame_adicionados_novo=ttk.LabelFrame(aba_prop_adicionais,text="Insumos adicionados pelo usuário")
frame_adicionados_novo.pack(fill="both",expand=True,padx=10,pady=(5,10))
tabela_novos_insumos_adicionados=ttk.Treeview(frame_adicionados_novo,columns=("bdest","comp","borigem","codigo","descricao","coef"),show="headings",height=7)
for c,t,w in (("bdest","Banco destino",110),("comp","Composição",110),("borigem","Banco origem",110),("codigo","Código insumo",110),("descricao","Descrição",600),("coef","Coeficiente",110)):
    tabela_novos_insumos_adicionados.heading(c,text=t); tabela_novos_insumos_adicionados.column(c,width=w,anchor="w" if c=="descricao" else "center")
tabela_novos_insumos_adicionados.pack(side="left",fill="both",expand=True,padx=(5,0),pady=5)
scroll_novos_add=ttk.Scrollbar(frame_adicionados_novo,orient="vertical",command=tabela_novos_insumos_adicionados.yview); scroll_novos_add.pack(side="left",fill="y",pady=5); tabela_novos_insumos_adicionados.configure(yscrollcommand=scroll_novos_add.set)
ttk.Button(frame_adicionados_novo,text="Remover selecionado",command=excluir_novo_insumo_adicionado).pack(side="right",padx=10,pady=10)


# ============================================================
# ABA 3 - ORÇAMENTO SINTÉTICO HIERÁRQUICO
# ============================================================

aba_orcamento = ttk.Frame(abas)
abas.add(aba_orcamento, text="Orçamento sintético")

# Dados gerais
frame_dados = ttk.LabelFrame(aba_orcamento, text="Dados do orçamento")
frame_dados.pack(fill="x", padx=10, pady=(10, 5))

# Obra e Cliente foram retirados da interface.
# As entradas continuam existindo internamente, sem grid/pack, para manter
# compatibilidade com salvamento/abertura de orçamentos e com a exportação Excel.
entrada_obra = ttk.Entry(frame_dados)
entrada_cliente = ttk.Entry(frame_dados)

ttk.Label(frame_dados, text="BDI (%):").grid(
    row=0, column=0, padx=(10, 5), pady=8
)
entrada_bdi_orcamento = ttk.Entry(frame_dados, width=10)
entrada_bdi_orcamento.grid(row=0, column=1, padx=(5, 10), pady=8, sticky="w")
entrada_bdi_orcamento.insert(0, "0,00")

ttk.Label(frame_dados, text="SINAPI:").grid(
    row=1, column=0, padx=(10, 5), pady=8
)
ttk.Label(
    frame_dados,
    text="CE como base • preço de SP somente quando o insumo estiver sem preço no CE"
).grid(row=1, column=1, columnspan=3, sticky="w", padx=5, pady=8)

frame_dados.columnconfigure(1, weight=1)

# Organização
frame_organizacao = ttk.LabelFrame(
    aba_orcamento,
    text="Organização do orçamento"
)
frame_organizacao.pack(fill="x", padx=10, pady=5)

ttk.Label(frame_organizacao, text="Categoria:").grid(
    row=0, column=0, padx=(10, 5), pady=8
)

combo_categoria = ttk.Combobox(
    frame_organizacao,
    state="readonly",
    width=28
)
combo_categoria.grid(row=0, column=1, padx=5, pady=8)
combo_categoria.bind("<<ComboboxSelected>>", atualizar_combobox_subcategorias)

ttk.Button(
    frame_organizacao,
    text="+ Nova categoria",
    command=criar_categoria
).grid(row=0, column=2, padx=(5, 20), pady=8)

ttk.Label(frame_organizacao, text="Subcategoria (opcional):").grid(
    row=0, column=3, padx=(10, 5), pady=8
)

combo_subcategoria = ttk.Combobox(
    frame_organizacao,
    state="readonly",
    width=28
)
combo_subcategoria.grid(row=0, column=4, padx=5, pady=8)

ttk.Button(
    frame_organizacao,
    text="+ Nova subcategoria",
    command=criar_subcategoria
).grid(row=0, column=5, padx=5, pady=8)

# Adição de composição
frame_adicionar = ttk.LabelFrame(
    aba_orcamento,
    text="Adicionar composição"
)
frame_adicionar.pack(fill="x", padx=10, pady=5)

ttk.Label(frame_adicionar, text="Banco:").grid(row=0, column=0, padx=(10,5), pady=8)
combo_banco_orcamento = ttk.Combobox(frame_adicionar, state="readonly", width=10, values=("SEINFRA", "SINAPI", "PRÓPRIO"))
combo_banco_orcamento.set("SEINFRA")
combo_banco_orcamento.grid(row=0, column=1, padx=5, pady=8)

ttk.Label(frame_adicionar, text="Código:").grid(row=0, column=2, padx=(15,5), pady=8)
entrada_codigo_orcamento = ttk.Entry(frame_adicionar, width=18)
entrada_codigo_orcamento.grid(row=0, column=3, padx=5, pady=8)

ttk.Button(
    frame_adicionar,
    text="Buscar por descrição",
    command=lambda: abrir_busca_descricao(
        "COMPOSICAO", "ORCAMENTO"
    )
).grid(row=0, column=4, padx=(5, 10), pady=8)

ttk.Label(frame_adicionar, text="Quantidade:").grid(
    row=0, column=5, padx=(10, 5), pady=8
)

entrada_quantidade_orcamento = ttk.Entry(frame_adicionar, width=15)
entrada_quantidade_orcamento.grid(row=0, column=6, padx=5, pady=8)

ttk.Button(
    frame_adicionar,
    text="Adicionar ao orçamento",
    command=adicionar_composicao_orcamento
).grid(row=0, column=7, padx=10, pady=8)

ttk.Button(
    frame_adicionar,
    text="Recalcular preços / BDI",
    command=recalcular_orcamento
).grid(row=0, column=8, padx=5, pady=8)

# Árvore
frame_arvore = ttk.Frame(aba_orcamento)
frame_arvore.pack(fill="both", expand=True, padx=10, pady=5)

colunas_orc = (
    "item", "codigo", "banco", "unidade", "descricao", "quantidade", "valor_unitario",
    "mao_obra", "equipamentos", "materiais", "total"
)

tabela_orcamento = ttk.Treeview(
    frame_arvore,
    columns=colunas_orc,
    show="headings",
    selectmode="extended"
)

titulos_orc = (
    "Item", "Código", "Banco", "Und", "Descrição", "Quantidade", "Valor unitário",
    "Mão de obra unit. c/ BDI", "Equip. unit. c/ BDI",
    "Materiais unit. c/ BDI", "Valor total"
)

larguras_orc = (70, 85, 90, 60, 330, 90, 130, 145, 140, 145, 130)

for coluna, titulo, largura in zip(colunas_orc, titulos_orc, larguras_orc):
    tabela_orcamento.heading(coluna, text=titulo)
    tabela_orcamento.column(coluna, width=largura)

tabela_orcamento.column("item", anchor="center")
tabela_orcamento.column("codigo", anchor="e")
tabela_orcamento.column("banco", anchor="w")
tabela_orcamento.column("unidade", anchor="center")
tabela_orcamento.column("descricao", anchor="w")
tabela_orcamento.column("quantidade", anchor="e")
tabela_orcamento.column("valor_unitario", anchor="e")
tabela_orcamento.column("mao_obra", anchor="e")
tabela_orcamento.column("equipamentos", anchor="e")
tabela_orcamento.column("materiais", anchor="e")
tabela_orcamento.column("total", anchor="e")

# Duplo clique na coluna Quantidade permite editar o valor diretamente.
tabela_orcamento.bind("<Double-1>", editar_quantidade_orcamento)

scroll_y = ttk.Scrollbar(
    frame_arvore,
    orient="vertical",
    command=tabela_orcamento.yview
)
scroll_x = ttk.Scrollbar(
    frame_arvore,
    orient="horizontal",
    command=tabela_orcamento.xview
)

tabela_orcamento.configure(
    yscrollcommand=scroll_y.set,
    xscrollcommand=scroll_x.set
)

tabela_orcamento.grid(row=0, column=0, sticky="nsew")
scroll_y.grid(row=0, column=1, sticky="ns")
scroll_x.grid(row=1, column=0, sticky="ew")

frame_arvore.rowconfigure(0, weight=1)
frame_arvore.columnconfigure(0, weight=1)

# Ações
frame_acoes = ttk.Frame(aba_orcamento)
frame_acoes.pack(fill="x", padx=10, pady=5)

ttk.Button(
    frame_acoes,
    text="Remover selecionado",
    command=remover_selecionado_orcamento
).pack(side="left", padx=(0, 5))

ttk.Button(
    frame_acoes,
    text="Limpar orçamento",
    command=limpar_orcamento
).pack(side="left", padx=5)

ttk.Button(
    frame_acoes,
    text="Salvar orçamento",
    command=salvar_orcamento
).pack(side="left", padx=5)

ttk.Button(
    frame_acoes,
    text="Salvar como...",
    command=salvar_orcamento_como
).pack(side="left", padx=5)

ttk.Button(
    frame_acoes,
    text="Abrir orçamento",
    command=abrir_orcamento
).pack(side="left", padx=5)

ttk.Button(
    frame_acoes,
    text="Exportar para Excel",
    command=exportar_orcamento_excel
).pack(side="right", padx=5)

# Resumo
frame_resumo = ttk.LabelFrame(aba_orcamento, text="Resumo geral")
frame_resumo.pack(fill="x", padx=10, pady=(5, 10))

ttk.Label(frame_resumo, text="MÃO DE OBRA:").grid(
    row=0, column=0, padx=(15, 5), pady=8
)
label_orc_mao_obra = ttk.Label(
    frame_resumo, text="R$ 0,00", font=("Arial", 10, "bold")
)
label_orc_mao_obra.grid(row=0, column=1, padx=(5, 20), pady=8)

ttk.Label(frame_resumo, text="EQUIPAMENTOS:").grid(
    row=0, column=2, padx=(15, 5), pady=8
)
label_orc_equipamentos = ttk.Label(
    frame_resumo, text="R$ 0,00", font=("Arial", 10, "bold")
)
label_orc_equipamentos.grid(row=0, column=3, padx=(5, 20), pady=8)

ttk.Label(frame_resumo, text="MATERIAIS:").grid(
    row=0, column=4, padx=(15, 5), pady=8
)
label_orc_materiais = ttk.Label(
    frame_resumo, text="R$ 0,00", font=("Arial", 10, "bold")
)
label_orc_materiais.grid(row=0, column=5, padx=(5, 20), pady=8)

ttk.Label(frame_resumo, text="TOTAL GERAL:").grid(
    row=0, column=6, padx=(20, 5), pady=8
)
label_orc_total = ttk.Label(
    frame_resumo, text="R$ 0,00", font=("Arial", 12, "bold")
)
label_orc_total.grid(row=0, column=7, padx=(5, 15), pady=8)


# ============================================================
# ATALHOS
# ============================================================

entrada_codigo_composicao.bind(
    "<Return>",
    lambda evento: consultar_composicao()
)

entrada_codigo_consulta.bind(
    "<Return>",
    lambda evento: consultar_insumo()
)

entrada_preco_consulta_insumo.bind(
    "<Return>",
    lambda evento: salvar_preco_insumo_consultado()
)


entrada_filtro_composicao.bind("<Return>", lambda evento: filtrar_composicoes_destino())
entrada_filtro_insumo.bind("<Return>", lambda evento: filtrar_insumos_origem())

entrada_quantidade_orcamento.bind(
    "<Return>",
    lambda evento: adicionar_composicao_orcamento()
)

entrada_bdi_orcamento.bind(
    "<Return>",
    lambda evento: recalcular_orcamento()
)


# ============================================================
# VERIFICAÇÃO DO BANCO
# ============================================================

bancos_faltando = [str(p) for p in (DB_PATH, SINAPI_DB_PATH) if not p.exists()]
if bancos_faltando:
    messagebox.showerror(
        "Banco não encontrado",
        "Banco(s) necessário(s) não encontrado(s):\n\n" +
        "\n".join(bancos_faltando) +
        "\n\nColoque os dois arquivos .db na mesma pasta do programa."
    )
    janela.destroy()
    sys.exit()

# Cria/abre o banco persistente do usuário.
# usuario.db não deve ser substituído ao atualizar o aplicativo.
inicializar_tabelas_orcamentos_salvos()
inicializar_tabelas_banco_proprio()

# Migra automaticamente cadastros e orçamentos gravados por versões anteriores
# no banco SEINFRA, sem apagar o conteúdo original.
migrar_dados_usuario_legados()

atualizar_listas_banco_proprio()
filtrar_composicoes_destino()
filtrar_insumos_origem()
atualizar_lista_novos_insumos_adicionados()


# ============================================================
# EXECUÇÃO
# ============================================================

janela.mainloop()
