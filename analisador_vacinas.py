#!/usr/bin/env python3
"""
Analisador de Vacinas - e-SUS PEC x Calendario PNI 2026 / Kode APS
====================================================================
Le os CSVs de busca ativa do e-SUS PEC, relaciona cada imunobiologico
com o Calendario Nacional de Vacinacao 2026 (PNI) e valida pela faixa
etaria (0-13 anos). Gera listagem separada por Crianca (0-9) e
Adolescente (10-13), com opcao de exportar CSV ou JSON.

Uso:
    python analisador_vacinas.py
    python analisador_vacinas.py --csv-dir ./vacinas
    python analisador_vacinas.py --saida resultado.csv
    python analisador_vacinas.py --json
"""
import argparse, csv, json, os, re, sys, unicodedata
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

def _norm(s):
    """Normaliza string: remove acentos, lowercase, strip."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s.strip().strip('"'))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()

# === 1. MAPEAMENTO: nome e-SUS PEC -> sigla PNI ===
IMUNOS_PNI = {
    "HB": (9, "Hepatite B"), "BCG": (15, "BCG"),
    "PENTA": (42, "Penta (DTP+Hib+HB)"), "VIP": (22, "Poliomielite inativada"),
    "ROTA": (45, "Rotavirus humano"), "VPC20": (107, "Pneumococica 20-valente"),
    "MenC": (41, "Meningococica C"), "VPC10": (26, "Pneumococica 10-valente"),
    "COVID-19": (None, "COVID-19"), "VFA": (14, "Febre amarela"),
    "INF3": (33, "Influenza (gripe)"), "MenACWY": (74, "Meningococica ACWY"),
    "SCR": (24, "Triplice viral"), "DTP": (46, "DTP"),
    "VZ": (34, "Varicela"), "HAinf": (55, "Hepatite A infantil"),
    "HPV4": (67, "HPV quadrivalente"), "DNG": (104, "Dengue tetravalente"),
    "DT": (5, "Dupla adulto"), "dT": (25, "Dupla adulto (gestante)"),
    "dTpa": (57, "Triplice bacteriana acelular"),
    "VPP23": (21, "Pneumococica 23-valente"),
    "VVSR-Rec": (108, "Virus sincicial respiratorio"),
}

# Chaves NORMALIZADAS (sem acento) para evitar problemas de encoding
NOME_PEC_PARA_SIGLA = {
    _norm("vacina bcg"): "BCG",
    _norm("vacina difteria e tetano adulto"): "DT",
    _norm("vacina dtp"): "DTP",
    _norm("vacina febre amarela"): "VFA",
    _norm("vacina hepatite a infantil"): "HAinf",
    _norm("vacina hepatite b"): "HB",
    _norm("vacina hpv quadrivalente"): "HPV4",
    _norm("vacina inativada poliomielite"): "VIP",
    _norm("vacina influenza trivalente"): "INF3",
    _norm("vacina meningo acwy"): "MenACWY",
    _norm("vacina meningo c"): "MenC",
    _norm("vacina penta (dtp/hb/hib)"): "PENTA",
    _norm("vacina pneumo 10"): "VPC10",
    _norm("vacina pneumo 20"): "VPC20",
    _norm("vacina rotavirus"): "ROTA",
    _norm("vacina tetraviral"): "SCR",
    _norm("vacina triplice viral"): "SCR",
    _norm("vacina varicela"): "VZ",
}

# === 2. CALENDARIO PNI 2026 (0-13 anos) ===
# Faixas ampliadas para BUSCA ATIVA (resgate de atrasados):
# - O e-SUS PEC lista vacinas "atrasadas" que podem estar muito alem
#   do marco ideal. Para fins de validacao, consideramos que qualquer
#   dose pendente dentro da faixa pediatrica (0-13 anos = 0-156m) e
#   valida se o imunobiologico esta previsto no PNI para aquela fase.
# - Janelas seguem o PNI oficial como limite INFERIOR (idade minima
#   para tomar), mas o limite SUPERIOR e estendido ate 156 meses
#   (13 anos) para vacinas infantis, pois a busca ativa visa resgatar
#   quem perdeu o prazo.
# - Excecoes: Rotavirus tem janela estreita oficial (ate ~8m); aqui
#   mantemos ate 11m conforme nota tecnica PNI. Influenza e anual.
CALENDARIO_PNI_0_13 = [
    # Ao nascer
    {"sigla":"HB","min_m":0,"max_m":156,"dose":"1 dose","grupo":"CA"},
    {"sigla":"BCG","min_m":0,"max_m":156,"dose":"Dose unica","grupo":"C"},
    # 2 meses (janela ate 11m para serie primaria)
    {"sigla":"PENTA","min_m":2,"max_m":156,"dose":"1a dose","grupo":"C"},
    {"sigla":"VIP","min_m":2,"max_m":156,"dose":"1a dose","grupo":"C"},
    {"sigla":"ROTA","min_m":2,"max_m":11,"dose":"1a dose","grupo":"C"},
    {"sigla":"VPC20","min_m":2,"max_m":156,"dose":"1a dose","grupo":"C"},
    # 3 meses
    {"sigla":"MenC","min_m":3,"max_m":156,"dose":"1a dose","grupo":"C"},
    # 4 meses
    {"sigla":"PENTA","min_m":4,"max_m":156,"dose":"2a dose","grupo":"C"},
    {"sigla":"VIP","min_m":4,"max_m":156,"dose":"2a dose","grupo":"C"},
    {"sigla":"ROTA","min_m":4,"max_m":11,"dose":"2a dose","grupo":"C"},
    {"sigla":"VPC10","min_m":4,"max_m":156,"dose":"2a dose","grupo":"C"},
    # 5 meses
    {"sigla":"MenC","min_m":5,"max_m":156,"dose":"2a dose","grupo":"C"},
    # 6 meses
    {"sigla":"PENTA","min_m":6,"max_m":156,"dose":"3a dose","grupo":"C"},
    {"sigla":"VIP","min_m":6,"max_m":156,"dose":"3a dose","grupo":"C"},
    {"sigla":"COVID-19","min_m":6,"max_m":156,"dose":"1a dose","grupo":"C"},
    # Influenza anual (6m em diante, sem limite superior na faixa ped)
    {"sigla":"INF3","min_m":6,"max_m":156,"dose":"Anual","grupo":"CA"},
    # 7 meses COVID
    {"sigla":"COVID-19","min_m":7,"max_m":156,"dose":"2a dose","grupo":"C"},
    # 9 meses
    {"sigla":"COVID-19","min_m":9,"max_m":156,"dose":"3a dose","grupo":"C"},
    {"sigla":"VFA","min_m":9,"max_m":156,"dose":"1 dose","grupo":"CA"},
    # 12 meses
    {"sigla":"VPC20","min_m":12,"max_m":156,"dose":"Reforco","grupo":"C"},
    {"sigla":"MenACWY","min_m":12,"max_m":156,"dose":"1 dose","grupo":"CA"},
    {"sigla":"SCR","min_m":12,"max_m":156,"dose":"1a dose","grupo":"C"},
    # 15 meses
    {"sigla":"DTP","min_m":15,"max_m":156,"dose":"1o reforco","grupo":"C"},
    {"sigla":"VIP","min_m":15,"max_m":156,"dose":"1o reforco","grupo":"C"},
    {"sigla":"SCR","min_m":15,"max_m":156,"dose":"2a dose","grupo":"C"},
    {"sigla":"VZ","min_m":15,"max_m":156,"dose":"1a dose","grupo":"C"},
    {"sigla":"HAinf","min_m":15,"max_m":156,"dose":"1 dose","grupo":"C"},
    # 4 anos / 48 meses
    {"sigla":"DTP","min_m":48,"max_m":156,"dose":"2o reforco","grupo":"C"},
    {"sigla":"VIP","min_m":48,"max_m":156,"dose":"2o reforco","grupo":"C"},
    {"sigla":"VZ","min_m":48,"max_m":156,"dose":"2a dose","grupo":"C"},
    {"sigla":"VFA","min_m":48,"max_m":156,"dose":"1 reforco","grupo":"CA"},
    # Adolescente (10-13 anos = 120-156 meses)
    {"sigla":"HPV4","min_m":108,"max_m":228,"dose":"1 dose","grupo":"A"},
    {"sigla":"DNG","min_m":72,"max_m":192,"dose":"2 doses","grupo":"A"},
    {"sigla":"MenACWY","min_m":132,"max_m":168,"dose":"1 dose","grupo":"A"},
    {"sigla":"DT","min_m":120,"max_m":168,"dose":"Reforco","grupo":"A"},
]

# === 3. PARSING DOS CSVs ===
def _parse_idade_meses(s):
    if not s or s.strip() == "-":
        return None
    a = re.search(r"(\d+)\s*anos?", s)
    m = re.search(r"(\d+)\s*mes(?:es)?", s)
    return (int(a.group(1)) if a else 0)*12 + (int(m.group(1)) if m else 0)

def _parse_data_nasc(s):
    s = s.strip().strip('"')
    if not s or s == "-":
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None

def _detectar_grupo(idade_meses):
    if idade_meses is None:
        return "Desconhecido"
    if idade_meses < 120:
        return "Crianca"
    if idade_meses <= 156:
        return "Adolescente"
    return "Fora da faixa (>13)"

def _mapear_imuno(nome_pec):
    chave = _norm(nome_pec)
    if chave in NOME_PEC_PARA_SIGLA:
        return NOME_PEC_PARA_SIGLA[chave]
    sem = re.sub(r"^vacina\s+", "", chave)
    return NOME_PEC_PARA_SIGLA.get(sem)

def ler_csv_pec(caminho):
    registros = []
    with open(caminho, "r", encoding="latin-1") as f:
        linhas = f.readlines()
    header_idx = -1
    for i, linha in enumerate(linhas):
        if linha.startswith("Nome do cidad"):
            header_idx = i
            break
    if header_idx < 0:
        print(f" ! Cabecalho nao encontrado em {caminho}", file=sys.stderr)
        return registros
    headers = [h.strip() for h in linhas[header_idx].split(";")]
    col_map = {h: idx for idx, h in enumerate(headers)}
    for linha in linhas[header_idx+1:]:
        linha = linha.strip()
        if not linha:
            continue
        campos = linha.split(";")
        if len(campos) < len(headers):
            continue
        def c(nome, d=0):
            idx = col_map.get(nome, d)
            return campos[idx].strip().strip('"') if idx < len(campos) else ""
        nome = c("Nome do cidad", 0)
        dt_nasc_str = c("Data de nascimento", 1)
        idade_str = c("Idade", 2)
        sexo = c("Sexo", 3)
        cpf = c("CPF", 5)
        cns = c("CNS", 6)
        microarea = c("Micro", 10)
        status_vacina = c("Status da vacina", 23)
        imuno_nome = c("Imunobiol", 24)
        dose = c("Dose", 25)
        idade_meses = _parse_idade_meses(idade_str)
        dt_nasc = _parse_data_nasc(dt_nasc_str)
        grupo = _detectar_grupo(idade_meses)
        sigla_pni = _mapear_imuno(imuno_nome)
        registros.append({
            "nome": nome, "data_nascimento": dt_nasc.isoformat() if dt_nasc else None,
            "idade_meses": idade_meses, "idade_texto": idade_str, "sexo": sexo,
            "cpf": cpf, "cns": cns, "microarea": microarea,
            "status_vacina": status_vacina, "imuno_nome_pec": imuno_nome,
            "sigla_pni": sigla_pni, "dose": dose, "grupo_etario": grupo,
            "arquivo_origem": os.path.basename(caminho),
        })
    return registros

# === 4. VALIDACAO CONTRA O CALENDARIO PNI 2026 ===
_EQUIV_DOSE = {
    "DU": ["DOSE UNICA","1 DOSE","DOSE"],
    "D1": ["1A DOSE","1 DOSE"], "D2": ["2A DOSE","2 DOSE"],
    "D3": ["3A DOSE","3 DOSE"],
    "R1": ["1 REFORCO","1O REFORCO","REFORCO"],
    "R2": ["2 REFORCO","2O REFORCO"],
    "REF": ["REFORCO","1 REFORCO","1O REFORCO","2 REFORCO","2O REFORCO"],
    "ANUAL": ["ANUAL","1 DOSE ANUAL"],
}

def _dose_compativel(dose_pec, dose_pni):
    pec = dose_pec.upper().replace("\u00ba","").replace("\u00aa","").strip()
    pni = dose_pni.upper().replace("\u00ba","").replace("\u00aa","").strip()
    if pec == pni:
        return True
    for chave, vals in _EQUIV_DOSE.items():
        if pec == chave and any(v == pni for v in vals):
            return True
        if pni == chave and any(v == pec for v in vals):
            return True
    return False

def validar_contra_calendario(registro):
    sigla = registro["sigla_pni"]
    idade_m = registro["idade_meses"]
    dose_txt = registro["dose"].upper()
    resultado = {"enquadrado": False, "regra_pni": None, "observacao": ""}
    if sigla is None:
        resultado["observacao"] = f"Imunobiologico '{registro['imuno_nome_pec']}' nao mapeado no PNI 2026"
        return resultado
    if idade_m is None:
        resultado["observacao"] = "Idade nao pode ser determinada"
        return resultado
    compativeis = [r for r in CALENDARIO_PNI_0_13
                   if r["sigla"] == sigla and r["min_m"] <= idade_m <= r["max_m"]]
    if not compativeis:
        # Classifica como CONTRAINDICADO quando ha regra clinica explicita
        if sigla == "ROTA" and idade_m > 11:
            resultado["enquadrado"] = True  # conta como valido (exclusao correta)
            resultado["regra_pni"] = "ROTA | Contraindicado >11m"
            resultado["observacao"] = (
                f"Rotavirus contraindicado para {idade_m} meses (>11m). "
                f"Registro correto: vacina nao deve ser administrada."
            )
            return resultado
        if sigla == "INF3" and idade_m < 6:
            resultado["enquadrado"] = True
            resultado["regra_pni"] = "INF3 | Contraindicado <6m"
            resultado["observacao"] = (
                f"Influenza contraindicada para {idade_m} meses (<6m). "
                f"Registro correto: vacina nao deve ser administrada."
            )
            return resultado
        resultado["observacao"] = (
            f"{sigla} nao prevista no PNI 2026 para {idade_m} meses "
            f"(grupo: {registro['grupo_etario']})"
        )
        return resultado
    for r in compativeis:
        if _dose_compativel(dose_txt, r["dose"]):
            resultado["enquadrado"] = True
            resultado["regra_pni"] = f"{sigla} | {r['dose']} | {r['min_m']}-{r['max_m']}m"
            return resultado
    resultado["enquadrado"] = True
    resultado["regra_pni"] = "; ".join(
        f"{r['sigla']} | {r['dose']} | {r['min_m']}-{r['max_m']}m" for r in compativeis
    )
    resultado["observacao"] = (
        f"Dose PEC '{registro['dose']}' nao corresponde exatamente as doses PNI"
    )
    return resultado

# === 5. ORQUESTRADOR PRINCIPAL ===
CSV_DIR_PADRAO = r"D:\Documentos\KODE\kode-vacinas-pec\vacinas_pec_sgp"
CSV_FILES = [
    "atrasada 0 a 9.csv",
    "atrasada 10 a 13.csv",
    "no prazo 0 a 9.csv",
    "no prazo 10 a 13.csv",
]

def analisar(csv_dir):
    todos = []
    resumo = {}
    for fname in CSV_FILES:
        caminho = os.path.join(csv_dir, fname)
        if not os.path.exists(caminho):
            print(f" ! Arquivo nao encontrado: {caminho}", file=sys.stderr)
            continue
        regs = ler_csv_pec(caminho)
        resumo[fname] = len(regs)
        todos.extend(regs)
        print(f" + {fname}: {len(regs)} registros")
    for reg in todos:
        v = validar_contra_calendario(reg)
        reg.update(v)
    criancas = [r for r in todos if r["grupo_etario"] == "Crianca"]
    adolescentes = [r for r in todos if r["grupo_etario"] == "Adolescente"]
    fora = [r for r in todos if r["grupo_etario"] == "Fora da faixa (>13)"]
    desc = [r for r in todos if r["grupo_etario"] == "Desconhecido"]
    enq = sum(1 for r in todos if r["enquadrado"])
    nao_enq = sum(1 for r in todos if not r["enquadrado"])
    nao_map = sum(1 for r in todos if r["sigla_pni"] is None)
    imunos_nm = sorted(set(r["imuno_nome_pec"] for r in todos if r["sigla_pni"] is None))
    return {
        "data_analise": date.today().isoformat(), "resumo_arquivos": resumo,
        "total_registros": len(todos), "total_criancas": len(criancas),
        "total_adolescentes": len(adolescentes), "total_fora_faixa": len(fora),
        "total_desconhecidos": len(desc), "enquadrados": enq,
        "nao_enquadrados": nao_enq, "nao_mapeados": nao_map,
        "imunos_nao_mapeados": imunos_nm,
        "criancas": criancas, "adolescentes": adolescentes,
        "fora_faixa": fora, "desconhecidos": desc,
    }

# === 6. EXPORTACAO E RELATORIO ===
CAMPOS_CSV = [
    "grupo_etario","nome","data_nascimento","idade_texto","sexo",
    "cpf","cns","microarea","status_vacina","imuno_nome_pec",
    "sigla_pni","dose","enquadrado","regra_pni","observacao","arquivo_origem",
]

def exportar_csv(resultado, caminho):
    linhas = []
    for k in ("criancas","adolescentes","fora_faixa","desconhecidos"):
        linhas.extend(resultado[k])
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_CSV, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)
    print(f"\n+ CSV exportado: {caminho}")

def imprimir_resumo(resultado):
    print("\n" + "="*60)
    print(" RESUMO DA ANALISE - Vacinas e-SUS PEC x PNI 2026")
    print("="*60)
    print(f" Data da analise: {resultado['data_analise']}")
    print(f" Total de registros: {resultado['total_registros']}")
    print(f" Criancas (0-9 anos): {resultado['total_criancas']}")
    print(f" Adolescentes (10-13): {resultado['total_adolescentes']}")
    print(f" Fora da faixa (>13): {resultado['total_fora_faixa']}")
    print(f" Idade desconhecida: {resultado['total_desconhecidos']}")
    print(f" Enquadrados no PNI: {resultado['enquadrados']}")
    print(f" Nao enquadrados: {resultado['nao_enquadrados']}")
    print(f" Imunos nao mapeados: {resultado['nao_mapeados']}")
    if resultado["imunos_nao_mapeados"]:
        print(f" Lista de imunos nao mapeados:")
        for im in resultado["imunos_nao_mapeados"]:
            print(f"   - {im}")
    print("\n--- LISTAGEM POR GRUPO ETARIO ---")
    for grupo_key, titulo in [("criancas","CRIANCAS (0-9 anos)"),("adolescentes","ADOLESCENTES (10-13 anos)")]:
        lista = resultado[grupo_key]
        print(f"\n### {titulo} ({len(lista)} registros) ###")
        if not lista:
            print("  (nenhum registro)")
            continue
        for r in lista:
            status = "OK" if r["enquadrado"] else "NAO ENQUADRADO"
            regra = r["regra_pni"] or "-"
            obs = r["observacao"] or ""
            linha = (
                f"  {r['nome']} | {r['idade_texto']} | {r['imuno_nome_pec']} | "
                f"{r['dose']} | {r['status_vacina']} | [{status}] {regra}"
            )
            if obs:
                linha += f" | OBS: {obs}"
            print(linha)

def main():
    parser = argparse.ArgumentParser(description="Analisador de Vacinas e-SUS PEC x PNI 2026")
    parser.add_argument("--csv-dir", default=CSV_DIR_PADRAO, help="Pasta dos CSVs do e-SUS PEC")
    parser.add_argument("--saida", help="Caminho do CSV de saida")
    parser.add_argument("--json", action="store_true", help="Saida JSON no stdout")
    args = parser.parse_args()
    resultado = analisar(args.csv_dir)
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        imprimir_resumo(resultado)
    if args.saida:
        exportar_csv(resultado, args.saida)

if __name__ == "__main__":
    main()