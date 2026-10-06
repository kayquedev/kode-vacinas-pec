#!/usr/bin/env python3
"""
Kode Vacinas PEC - Interface Web
=================================
Aplicacao Flask para upload e analise de CSVs do e-SUS PEC,
cruzando com o Calendario PNI 2026. Gera relatorio consolidado
por paciente no estilo Kode APS, com unificacao de imunos,
filtros interativos e exportacao PDF com separacao por
logradouro/bairro.
Deploy: vacinas.kodesaude.com.br
"""
import base64
import io
import json
import os
import re
import tempfile
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from flask import Flask, render_template_string, request, send_file, flash, redirect, url_for, jsonify
from analisador_vacinas import analisar, exportar_csv, _parse_idade_meses

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kode-vacinas-pec-secret-key-2026")
UPLOAD_FOLDER = tempfile.mkdtemp(prefix="kode_vacinas_")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max

# Armazena resultado da ultima analise em memoria (sessao unica)
_ultimo_resultado = None
_ultimo_csv_path = None

# Carrega logo em base64 para uso no template HTML/PDF
_LOGO_B64 = ""
_logo_path = os.path.join(os.path.dirname(__file__), "static", "logo.png")
if os.path.exists(_logo_path):
    with open(_logo_path, "rb") as f:
        _LOGO_B64 = base64.b64encode(f.read()).decode("utf-8")


def _unificar_imunos(imunos_pendentes):
    """Unifica imunos iguais agrupando doses entre parenteses."""
    if not imunos_pendentes:
        return []
    grupos = OrderedDict()
    for imuno_str in imunos_pendentes:
        match = re.match(r'^([^([]+?)(?:\s*\(|\s*\[|$)', imuno_str.strip())
        nome_base = match.group(1).strip() if match else imuno_str.strip()
        dose_match = re.search(r'\(([^)]+)\)', imuno_str)
        dose = dose_match.group(1).strip() if dose_match else ""
        if nome_base not in grupos:
            grupos[nome_base] = []
        if dose and dose not in grupos[nome_base]:
            grupos[nome_base].append(dose)
    resultado = []
    for nome, doses in grupos.items():
        if doses:
            resultado.append(f"{nome} ({', '.join(doses)})")
        else:
            resultado.append(nome)
    return resultado


def _extrair_logradouro_bairro(endereco):
    """Extrai logradouro e bairro de um endereco completo."""
    if not endereco or endereco == "-":
        return ("Sem endereco", "Sem bairro")
    partes = [p.strip() for p in endereco.split(",") if p.strip()]
    logradouro = "Sem logradouro"
    bairro = "Sem bairro"
    if len(partes) >= 1:
        log_raw = partes[0]
        log_raw = re.sub(
            r'^(rua|avenida|av\.?|estrada|praca|travessa|alameda|rodovia)\s+',
            '', log_raw, flags=re.IGNORECASE
        )
        logradouro = log_raw.strip().title() if log_raw.strip() else "Sem logradouro"
    if len(partes) >= 3:
        bairro = partes[-1].strip().title()
    elif len(partes) >= 2:
        ultima = partes[-1].strip()
        if not re.match(r'^\d+', ultima) and ultima.upper() not in ('S/N', 'SN', 'CASA', 'APARTAMENTO'):
            bairro = ultima.title()
    return (logradouro, bairro)


def _aplicar_filtros(pacientes, filtros):
    """Aplica filtros na lista de pacientes."""
    if not filtros:
        return pacientes
    resultado = []
    idade_min = filtros.get("idade_min")
    idade_max = filtros.get("idade_max")
    endereco_busca = filtros.get("endereco", "").lower().strip()
    imuno_busca = filtros.get("imuno", "").lower().strip()
    imunos_remover = filtros.get("imunos_remover", [])
    for p in pacientes:
        if idade_min is not None or idade_max is not None:
            idade_m = p.get("idade_meses")
            if idade_m is not None:
                if idade_min is not None and idade_m < idade_min:
                    continue
                if idade_max is not None and idade_m > idade_max:
                    continue
        if endereco_busca:
            if endereco_busca not in (p.get("endereco") or "").lower():
                continue
        imunos_filtrados = list(p.get("imunos_pendentes", []))
        if imunos_remover:
            imunos_filtrados = [
                i for i in imunos_filtrados
                if not any(rem.lower() in i.lower() for rem in imunos_remover)
            ]
        if imuno_busca:
            imunos_filtrados = [
                i for i in imunos_filtrados
                if imuno_busca in i.lower()
            ]
        if imunos_filtrados:
            p_copia = dict(p)
            p_copia["imunos_pendentes"] = imunos_filtrados
            p_copia["total_imunos"] = len(imunos_filtrados)
            resultado.append(p_copia)
    return resultado


def _agrupar_por_logradouro_bairro(pacientes):
    """Agrupa pacientes por logradouro e bairro para o PDF."""
    grupos = defaultdict(list)
    for p in pacientes:
        endereco = p.get("endereco", "")
        logradouro, bairro = _extrair_logradouro_bairro(endereco)
        chave = f"{bairro} - {logradouro}"
        grupos[chave].append(p)
    return OrderedDict(sorted(grupos.items()))


def _gerar_pdf_html(pacientes_agrupados, stats, data_analise):
    """Gera HTML formatado para conversao em PDF via xhtml2pdf.
    Layout padrao Kode APS: logo, cabecalho compacto, tabela com endereco,
    texto legivel, separacao por logradouro/bairro.
    CSS compativel com xhtml2pdf (sem flexbox/grid/border-radius).
    """
    total_no_relatorio = sum(len(v) for v in pacientes_agrupados.values())
    agora = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
    logo_img = f'<img src="data:image/png;base64,{_LOGO_B64}" style="height: 40px; margin-right: 10px;" />' if _LOGO_B64 else ""

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Vacinacao - Imunos - Kode Vacinas PEC</title>
<style>
@page {{
    size: A4 landscape;
    margin: 1cm;
}}
body {{
    font-family: Helvetica, Arial, sans-serif;
    font-size: 9px;
    color: #1e293b;
    line-height: 1.3;
}}
.header {{
    border-bottom: 2px solid #0e7490;
    padding-bottom: 6px;
    margin-bottom: 6px;
    -pdf-keep-with-next: true;
}}
.header-top {{
    margin-bottom: 4px;
}}
.logo {{
    height: 40px;
}}
.titulo {{
    font-size: 14px;
    font-weight: bold;
    color: #0e7490;
}}
.subtitulo {{
    font-size: 9px;
    color: #64748b;
    margin-top: 2px;
}}
.info-bar {{
    font-size: 8px;
    color: #475569;
    margin-bottom: 6px;
    padding: 3px 6px;
    background-color: #f1f5f9;
    border: 1px solid #e2e8f0;
    -pdf-keep-with-next: true;
}}
.filtros-bar {{
    font-size: 7px;
    color: #64748b;
    margin-bottom: 6px;
    padding: 2px 6px;
    border: 1px solid #e2e8f0;
    -pdf-keep-with-next: true;
}}
.resumo-bar {{
    font-size: 8px;
    color: #0e7490;
    font-weight: bold;
    margin-bottom: 8px;
    padding: 3px 6px;
    background-color: #f0f9ff;
    border: 1px solid #bae6fd;
    -pdf-keep-with-next: true;
}}
.grupo-header {{
    background-color: #0e7490;
    color: white;
    padding: 3px 8px;
    margin-bottom: 2px;
    margin-top: 6px;
    font-size: 9px;
    font-weight: bold;
    -pdf-keep-with-next: true;
}}
table.dados {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 4px;
    font-size: 9px;
}}
table.dados th {{
    background-color: #f1f5f9;
    padding: 4px 6px;
    text-align: left;
    font-weight: bold;
    border-bottom: 2px solid #0e7490;
    color: #0e7490;
    font-size: 8px;
}}
table.dados td {{
    padding: 4px 6px;
    border-bottom: 1px solid #e2e8f0;
    vertical-align: top;
    font-size: 9px;
}}
.imuno-tag {{
    display: inline;
    background-color: #eff6ff;
    color: #1e40af;
    padding: 1px 4px;
    font-size: 8px;
    margin-right: 2px;
}}
.footer {{
    margin-top: 8px;
    padding-top: 4px;
    border-top: 1px solid #cbd5e1;
    font-size: 7px;
    color: #64748b;
    text-align: center;
}}
</style>
</head>
<body>
<div class="header">
    <div class="header-top">
        {logo_img}
        <span class="titulo">VACINACAO - IMUNOS</span>
    </div>
    <div class="subtitulo">Kode Vacinas PEC - Sistema de Apoio a Gestao Municipal</div>
</div>
<div class="info-bar">
    EMITIDO POR: Kode Vacinas PEC, EM: {agora} &bull; TOTAL DE REGISTROS: {total_no_relatorio}
</div>
<div class="resumo-bar">
    Cidadãos (filtro): {stats.get('total_pacientes', 0)} | Registros no PDF: {total_no_relatorio} | Criancas: {stats.get('total_criancas', 0)} | Adolescentes: {stats.get('total_adolescentes', 0)}
</div>
"""
    for grupo_nome, pacientes in pacientes_agrupados.items():
        html += f"""
<div class="grupo-header">{grupo_nome} ({len(pacientes)} pacientes)</div>
<table class="dados">
<thead>
<tr>
    <th style="width: 20%;">NOME</th>
    <th style="width: 12%;">CPF/CNS</th>
    <th style="width: 10%;">IDADE</th>
    <th style="width: 18%;">ENDERECO</th>
    <th style="width: 40%;">IMUNOS PENDENTES</th>
</tr>
</thead>
<tbody>
"""
        for p in pacientes:
            imunos_html = " ".join(
                f'<span class="imuno-tag">{im}</span>'
                for im in p.get("imunos_pendentes", [])
            )
            nome = p.get('nome', '')[:40]
            ident = p.get('identificador', '-')
            idade = p.get('idade_texto', '-')[:20]
            endereco = p.get('endereco', '-')[:35]
            html += f"""
<tr>
    <td><b>{nome}</b></td>
    <td>{ident}</td>
    <td>{idade}</td>
    <td>{endereco}</td>
    <td>{imunos_html}</td>
</tr>
"""
        html += """
</tbody>
</table>
"""
    html += f"""
<div class="footer">
    Vacinacao - Imunos - KODE VACINAS PEC - Sistema de Apoio a Gestao Municipal<br/>
    Documento gerado automaticamente em {agora}
</div>
</body>
</html>
"""
    return html


# ============================================================
# TEMPLATE DA TELA INICIAL (CARDS DE ACESSO)
# ============================================================
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kode Vacinas PEC - Inicio</title>
<style>
:root {
    --primary: #0e7490;
    --primary-dark: #0c5f75;
    --bg: #f8fafc;
    --card-bg: #ffffff;
    --text: #1e293b;
    --text-muted: #64748b;
    --border: #e2e8f0;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; }
.home-container { text-align: center; max-width: 800px; padding: 40px 20px; }
.logo-img { max-width: 280px; margin-bottom: 20px; }
.home-title { font-size: 2rem; font-weight: 700; color: var(--primary); margin-bottom: 8px; }
.home-subtitle { font-size: 1rem; color: var(--text-muted); margin-bottom: 40px; }
.cards-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 24px; width: 100%; }
.access-card { background: var(--card-bg); border-radius: 16px; padding: 40px 30px; text-align: center; cursor: pointer; transition: all 0.3s ease; border: 2px solid var(--border); text-decoration: none; color: var(--text); display: block; }
.access-card:hover { border-color: var(--primary); transform: translateY(-4px); box-shadow: 0 12px 24px rgba(14, 116, 144, 0.15); }
.card-icon { font-size: 3rem; margin-bottom: 16px; }
.card-title { font-size: 1.3rem; font-weight: 600; margin-bottom: 8px; color: var(--primary); }
.card-desc { font-size: 0.9rem; color: var(--text-muted); line-height: 1.5; }
.card-badge { display: inline-block; margin-top: 16px; padding: 4px 12px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
.badge-active { background: #d1fae5; color: #065f46; }
.badge-soon { background: #fef3c7; color: #92400e; }
.footer-home { margin-top: 40px; font-size: 0.8rem; color: var(--text-muted); }
@media (max-width: 640px) {
    .cards-grid { grid-template-columns: 1fr; }
    .home-title { font-size: 1.5rem; }
}
</style>
</head>
<body>
<div class="home-container">
    {% if logo_b64 %}
    <img src="data:image/png;base64,{{ logo_b64 }}" alt="Kode APS" class="logo-img">
    {% endif %}
    <h1 class="home-title">Kode Vacinas PEC</h1>
    <p class="home-subtitle">Analisador de Vacinacao e-SUS PEC x Calendario PNI 2026</p>
    <div class="cards-grid">
        <a href="{{ url_for('vacinas') }}" class="access-card">
            <div class="card-icon">&#128118;</div>
            <div class="card-title">Crianca / Adolescente</div>
            <div class="card-desc">Analise de vacinas para criancas (0-9 anos) e adolescentes (10-13 anos) conforme calendario PNI 2026.</div>
            <span class="card-badge badge-active">Disponivel</span>
        </a>
        <a href="#" class="access-card" onclick="alert('Modulo de Idosos sera implementado em breve.'); return false;">
            <div class="card-icon">&#128116;</div>
            <div class="card-title">Idosos</div>
            <div class="card-desc">Analise de vacinas para populacao idosa (60+ anos) conforme calendario PNI 2026.</div>
            <span class="card-badge badge-soon">Em breve</span>
        </a>
    </div>
    <div class="footer-home">Kode Vacinas PEC &copy; 2026 &mdash; Sistema de Apoio a Gestao Municipal</div>
</div>
</body>
</html>
"""

# ============================================================
# TEMPLATE DA FERRAMENTA DE VACINAS (CRIANCA/ADOLESCENTE)
# ============================================================
VACINAS_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kode Vacinas PEC - Analisador de Vacinacao</title>
<style>
:root {
    --primary: #0e7490;
    --primary-dark: #0c5f75;
    --bg: #f8fafc;
    --card-bg: #ffffff;
    --text: #1e293b;
    --text-muted: #64748b;
    --border: #e2e8f0;
    --success: #059669;
    --warning: #d97706;
    --danger: #dc2626;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
header { background: var(--primary); color: white; padding: 12px 0; margin-bottom: 24px; }
header .container { display: flex; align-items: center; gap: 15px; }
header .logo-sm { height: 36px; background: white; border-radius: 6px; padding: 2px; }
header h1 { font-size: 1.3rem; font-weight: 600; }
header .subtitle { opacity: 0.9; font-size: 0.85rem; }
header .back-link { margin-left: auto; color: white; text-decoration: none; font-size: 0.85rem; opacity: 0.8; }
header .back-link:hover { opacity: 1; text-decoration: underline; }
.card { background: var(--card-bg); border-radius: 12px; padding: 24px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.card h2 { font-size: 1.2rem; margin-bottom: 16px; color: var(--primary); }
.upload-area { border: 2px dashed var(--border); border-radius: 8px; padding: 40px; text-align: center; transition: all 0.2s; cursor: pointer; }
.upload-area:hover, .upload-area.dragover { border-color: var(--primary); background: #f0f9ff; }
.upload-area input[type="file"] { display: none; }
.btn { display: inline-flex; align-items: center; gap: 8px; padding: 10px 20px; border-radius: 8px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s; font-size: 0.9rem; }
.btn-primary { background: var(--primary); color: white; }
.btn-primary:hover { background: var(--primary-dark); }
.btn-success { background: var(--success); color: white; }
.btn-outline { background: transparent; border: 1px solid var(--border); color: var(--text); }
.btn-outline:hover { background: var(--bg); }
.btn-pdf { background: #7c3aed; color: white; }
.btn-pdf:hover { background: #6d28d9; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }
.stat-card { background: var(--bg); border-radius: 8px; padding: 12px; text-align: center; }
.stat-value { font-size: 1.8rem; font-weight: 700; color: var(--primary); }
.stat-label { font-size: 0.8rem; color: var(--text-muted); margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th { background: var(--bg); padding: 10px 8px; text-align: left; font-weight: 600; border-bottom: 2px solid var(--border); position: sticky; top: 0; z-index: 10; }
td { padding: 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
tr:hover { background: #f8fafc; }
.imuno-tag { display: inline-block; background: #eff6ff; color: #1e40af; padding: 3px 8px; border-radius: 4px; font-size: 0.75rem; margin: 2px; white-space: nowrap; }
.section-title { display: flex; align-items: center; gap: 10px; margin: 20px 0 12px; padding-bottom: 6px; border-bottom: 2px solid var(--primary); }
.section-title h3 { font-size: 1.1rem; color: var(--primary); }
.section-count { background: var(--primary); color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.8rem; }
.alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; }
.alert-error { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
.alert-success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
.file-list { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
.file-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: var(--bg); border-radius: 6px; font-size: 0.85rem; }
.file-icon { color: var(--success); }
.actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
.filters-panel { background: var(--card-bg); border-radius: 12px; padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.filters-panel h3 { font-size: 1rem; color: var(--primary); margin-bottom: 16px; display: flex; align-items: center; gap: 8px; }
.filters-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
.filter-group label { display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: var(--text); }
.filter-group input, .filter-group select { width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; font-size: 0.9rem; }
.filter-group input:focus, .filter-group select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(14, 116, 144, 0.1); }
.filter-actions { display: flex; gap: 10px; margin-top: 16px; align-items: center; }
.imunos-remove-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.imuno-remove-chip { display: inline-flex; align-items: center; gap: 4px; background: #fee2e2; color: #991b1b; padding: 4px 10px; border-radius: 16px; font-size: 0.8rem; cursor: pointer; }
.imuno-remove-chip:hover { background: #fecaca; }
.imuno-remove-chip .x { font-weight: bold; }
.checkbox-group { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.checkbox-group input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--primary); }
.checkbox-group label { margin-bottom: 0; cursor: pointer; }
@media (max-width: 768px) {
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .filters-grid { grid-template-columns: 1fr; }
    table { font-size: 0.75rem; }
    th, td { padding: 6px 4px; }
}
</style>
</head>
<body>
<header>
<div class="container">
    {% if logo_b64 %}<img src="data:image/png;base64,{{ logo_b64 }}" class="logo-sm" alt="Logo">{% endif %}
    <div>
        <h1>Kode Vacinas PEC</h1>
        <div class="subtitle">Analisador de Vacinacao e-SUS PEC x Calendario PNI 2026</div>
    </div>
    <a href="{{ url_for('home') }}" class="back-link">&larr; Voltar ao Inicio</a>
</div>
</header>
<div class="container">
{% with messages = get_flashed_messages(with_categories=true) %}
{% if messages %}
{% for category, message in messages %}
<div class="alert alert-{{ category }}">{{ message }}</div>
{% endfor %}
{% endif %}
{% endwith %}

{% if not resultado %}
<div class="card">
<h2>Upload dos CSVs do e-SUS PEC</h2>
<form method="POST" enctype="multipart/form-data" id="uploadForm">
    <div class="upload-area" id="dropZone" onclick="document.getElementById('csvFiles').click()">
        <p style="font-size: 1.1rem; margin-bottom: 8px;">Clique ou arraste os arquivos CSV aqui</p>
        <p style="color: var(--text-muted); font-size: 0.85rem;">Selecione os CSVs de busca ativa (atrasada/no prazo)</p>
        <input type="file" id="csvFiles" name="csvs" multiple accept=".csv" required>
    </div>
    <div class="file-list" id="fileList"></div>
    <div class="actions">
        <button type="submit" class="btn btn-primary" id="analyzeBtn">Analisar Vacinas</button>
    </div>
</form>
</div>
{% else %}
<div class="stats-grid">
    <div class="stat-card"><div class="stat-value">{{ stats.total_pacientes }}</div><div class="stat-label">Pacientes Unicos</div></div>
    <div class="stat-card"><div class="stat-value">{{ stats.total_criancas }}</div><div class="stat-label">Criancas (0-9)</div></div>
    <div class="stat-card"><div class="stat-value">{{ stats.total_adolescentes }}</div><div class="stat-label">Adolescentes (10-13)</div></div>
    <div class="stat-card"><div class="stat-value">{{ stats.enquadrados }}</div><div class="stat-label">Registros Enquadrados</div></div>
</div>

<!-- Painel de Filtros -->
<div class="filters-panel">
<h3>Filtros e Exportacao</h3>
<form method="GET" action="{{ url_for('vacinas') }}" id="filterForm">
    <div class="filters-grid">
        <div class="filter-group">
            <label for="idade_min">Idade Minima (meses)</label>
            <input type="number" id="idade_min" name="idade_min" value="{{ filtros.idade_min or '' }}" min="0" max="156" placeholder="Ex: 0">
        </div>
        <div class="filter-group">
            <label for="idade_max">Idade Maxima (meses)</label>
            <input type="number" id="idade_max" name="idade_max" value="{{ filtros.idade_max or '' }}" min="0" max="156" placeholder="Ex: 156">
        </div>
        <div class="filter-group">
            <label for="endereco">Endereco contem</label>
            <input type="text" id="endereco" name="endereco" value="{{ filtros.endereco or '' }}" placeholder="Ex: centro, rural...">
        </div>
        <div class="filter-group">
            <label for="imuno">Imuno contem</label>
            <input type="text" id="imuno" name="imuno" value="{{ filtros.imuno or '' }}" placeholder="Ex: polio, hepatite...">
        </div>
    </div>
    <!-- Imunos para remover -->
    <div class="filter-group" style="margin-top: 16px;">
        <label>Remover Imunos Pendentes</label>
        <select id="imuno_select" style="width: 100%; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px;">
            <option value="">Selecione um imuno para remover...</option>
            {% for imuno in todos_imunos %}
            <option value="{{ imuno }}">{{ imuno }}</option>
            {% endfor %}
        </select>
        <button type="button" class="btn btn-sm btn-outline" style="margin-top: 8px;" onclick="addImunoRemover()">+ Adicionar</button>
        <div class="imunos-remove-list" id="imunosRemoveList">
            {% for imuno in filtros.imunos_remover %}
            <span class="imuno-remove-chip" onclick="removeImunoRemover(this, '{{ imuno }}')">
                {{ imuno }} <span class="x">&times;</span>
            </span>
            {% endfor %}
        </div>
        <input type="hidden" name="imunos_remover" id="imunos_remover_input" value="{{ ','.join(filtros.imunos_remover) }}">
    </div>
    <!-- Opcao de separar por logradouro/bairro no PDF -->
    <div class="filter-group" style="margin-top: 16px;">
        <div class="checkbox-group">
            <input type="checkbox" id="separar_endereco" name="separar_endereco" value="1" {{ 'checked' if filtros.separar_endereco else '' }}>
            <label for="separar_endereco">Separar por Logradouro/Bairro no PDF</label>
        </div>
    </div>
    <div class="filter-actions">
        <button type="submit" class="btn btn-primary">Aplicar Filtros</button>
        <a href="{{ url_for('vacinas') }}" class="btn btn-outline">Limpar Filtros</a>
        <a href="{{ url_for('download_csv') }}" class="btn btn-success">Baixar CSV</a>
        <a href="{{ url_for('download_pdf', idade_min=filtros.idade_min or '', idade_max=filtros.idade_max or '', endereco=filtros.endereco, imuno=filtros.imuno, imunos_remover=','.join(filtros.imunos_remover), separar_endereco='1' if filtros.separar_endereco else '') }}" class="btn btn-pdf">Baixar PDF</a>
        <a href="{{ url_for('nova_analise') }}" class="btn btn-outline">Nova Analise</a>
    </div>
</form>
</div>

{% for grupo_key, titulo in [("criancas", "CRIANCAS (0-9 anos)"), ("adolescentes", "ADOLESCENTES (10-13 anos)")] %}
{% set lista = resultado[grupo_key] %}
<div class="section-title">
    <h3>{{ titulo }}</h3>
    <span class="section-count">{{ lista|length }} pacientes</span>
</div>
{% if lista %}
<div class="card" style="overflow-x: auto;">
<table>
<thead>
<tr>
    <th style="min-width: 200px;">Nome Paciente</th>
    <th style="min-width: 120px;">CPF/CNS</th>
    <th style="min-width: 150px;">Idade</th>
    <th style="min-width: 90px;">D/N</th>
    <th style="min-width: 180px;">Endereco</th>
    <th style="min-width: 300px;">Imunos Pendentes</th>
</tr>
</thead>
<tbody>
{% for p in lista %}
<tr>
    <td><strong>{{ p.nome }}</strong></td>
    <td>{{ p.identificador }}</td>
    <td>{{ p.idade_texto }}</td>
    <td>{{ p.data_nascimento or "-" }}</td>
    <td>{{ p.endereco }}</td>
    <td>
        {% for imuno in p.imunos_pendentes %}
        <span class="imuno-tag">{{ imuno }}</span>
        {% endfor %}
    </td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
{% else %}
<div class="card"><p>Nenhum paciente neste grupo com os filtros aplicados.</p></div>
{% endif %}
{% endfor %}
{% endif %}
</div>
<script>
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('csvFiles');
const fileList = document.getElementById('fileList');
if (dropZone) {
    ['dragenter', 'dragover'].forEach(e => {
        dropZone.addEventListener(e, (ev) => { ev.preventDefault(); dropZone.classList.add('dragover'); });
    });
    ['dragleave', 'drop'].forEach(e => {
        dropZone.addEventListener(e, (ev) => { ev.preventDefault(); dropZone.classList.remove('dragover'); });
    });
    dropZone.addEventListener('drop', (ev) => {
        fileInput.files = ev.dataTransfer.files;
        updateFileList();
    });
    fileInput.addEventListener('change', updateFileList);
}
function updateFileList() {
    if (!fileList) return;
    fileList.innerHTML = '';
    Array.from(fileInput.files).forEach(f => {
        const div = document.createElement('div');
        div.className = 'file-item';
        div.innerHTML = '<span class="file-icon">&#10003;</span> ' + f.name + ' (' + (f.size / 1024).toFixed(1) + ' KB)';
        fileList.appendChild(div);
    });
}
let imunosRemover = new Set({{ filtros.imunos_remover | tojson }});
function addImunoRemover() {
    const select = document.getElementById('imuno_select');
    const valor = select.value;
    if (valor && !imunosRemover.has(valor)) {
        imunosRemover.add(valor);
        renderImunosRemover();
    }
    select.value = '';
}
function removeImunoRemover(element, valor) {
    imunosRemover.delete(valor);
    renderImunosRemover();
}
function renderImunosRemover() {
    const container = document.getElementById('imunosRemoveList');
    const input = document.getElementById('imunos_remover_input');
    container.innerHTML = '';
    imunosRemover.forEach(imuno => {
        const chip = document.createElement('span');
        chip.className = 'imuno-remove-chip';
        chip.onclick = function() { removeImunoRemover(this, imuno); };
        chip.innerHTML = imuno + ' <span class="x">&times;</span>';
        container.appendChild(chip);
    });
    input.value = Array.from(imunosRemover).join(',');
}
</script>
</body>
</html>
"""


# ============================================================
# ROTAS
# ============================================================

@app.route("/")
def home():
    """Tela inicial com cards de acesso."""
    return render_template_string(HOME_TEMPLATE, logo_b64=_LOGO_B64)


@app.route("/vacinas", methods=["GET"])
def vacinas():
    """Ferramenta de vacinas crianca/adolescente."""
    global _ultimo_resultado
    filtros = {
        "idade_min": int(request.args.get("idade_min")) if request.args.get("idade_min") else None,
        "idade_max": int(request.args.get("idade_max")) if request.args.get("idade_max") else None,
        "endereco": request.args.get("endereco", ""),
        "imuno": request.args.get("imuno", ""),
        "imunos_remover": [i.strip() for i in request.args.get("imunos_remover", "").split(",") if i.strip()],
        "separar_endereco": request.args.get("separar_endereco") == "1",
    }
    if not _ultimo_resultado:
        return render_template_string(VACINAS_TEMPLATE, resultado=None, filtros=filtros, stats={}, todos_imunos=[], logo_b64=_LOGO_B64)

    pacientes_unificados = []
    todos_imunos_set = set()
    for grupo_key in ("criancas", "adolescentes"):
        for p in _ultimo_resultado.get(grupo_key, []):
            p_copia = dict(p)
            p_copia["imunos_pendentes"] = _unificar_imunos(p.get("imunos_pendentes", []))
            pacientes_unificados.append(p_copia)
            for imuno in p_copia["imunos_pendentes"]:
                match = re.match(r'^([^(]+?)(?:\s*\(|$)', imuno.strip())
                if match:
                    todos_imunos_set.add(match.group(1).strip())

    pacientes_filtrados = _aplicar_filtros(pacientes_unificados, filtros)
    criancas = [p for p in pacientes_filtrados if p.get("grupo_etario") == "Crianca"]
    adolescentes = [p for p in pacientes_filtrados if p.get("grupo_etario") == "Adolescente"]
    resultado_filtrado = {"criancas": criancas, "adolescentes": adolescentes}
    stats = {
        "total_pacientes": len(pacientes_filtrados),
        "total_criancas": len(criancas),
        "total_adolescentes": len(adolescentes),
        "enquadrados": _ultimo_resultado.get("enquadrados", 0),
    }
    todos_imunos = sorted(todos_imunos_set)
    return render_template_string(
        VACINAS_TEMPLATE,
        resultado=resultado_filtrado,
        filtros=filtros,
        stats=stats,
        todos_imunos=todos_imunos,
        logo_b64=_LOGO_B64,
    )


@app.route("/nova-analise")
def nova_analise():
    global _ultimo_resultado, _ultimo_csv_path
    _ultimo_resultado = None
    _ultimo_csv_path = None
    return redirect(url_for("vacinas"))


@app.route("/vacinas", methods=["POST"])
def analisar_upload():
    global _ultimo_resultado, _ultimo_csv_path
    files = request.files.getlist("csvs")
    if not files or all(f.filename == "" for f in files):
        flash("Selecione pelo menos um arquivo CSV.", "error")
        return redirect(url_for("vacinas"))
    session_dir = tempfile.mkdtemp(prefix="kode_vacinas_session_", dir=UPLOAD_FOLDER)
    for f in files:
        if f.filename == "":
            continue
        f.save(os.path.join(session_dir, f.filename))
    arquivos_salvos = [f for f in os.listdir(session_dir) if f.endswith(".csv")]
    if not arquivos_salvos:
        flash("Nenhum arquivo CSV valido foi enviado.", "error")
        return redirect(url_for("vacinas"))
    try:
        import analisador_vacinas as av
        csv_dir_original = av.CSV_DIR_PADRAO
        arquivos_originais = av.CSV_FILES[:]
        av.CSV_DIR_PADRAO = session_dir
        av.CSV_FILES = arquivos_salvos
        resultado = av.analisar(session_dir)
        av.CSV_DIR_PADRAO = csv_dir_original
        av.CSV_FILES = arquivos_originais
        _ultimo_resultado = resultado
        csv_path = os.path.join(session_dir, "resultado_consolidado.csv")
        exportar_csv(resultado, csv_path)
        _ultimo_csv_path = csv_path
        flash(
            f"Analise concluida! {resultado['total_pacientes']} pacientes unicos encontrados.",
            "success",
        )
    except Exception as e:
        flash(f"Erro ao analisar: {str(e)}", "error")
    return redirect(url_for("vacinas"))


@app.route("/download")
def download_csv():
    global _ultimo_csv_path
    if not _ultimo_csv_path or not os.path.exists(_ultimo_csv_path):
        flash("Nenhum resultado disponivel para download.", "error")
        return redirect(url_for("vacinas"))
    return send_file(
        _ultimo_csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"vacinas_consolidado_{date.today().isoformat()}.csv",
    )


@app.route("/download-pdf")
def download_pdf():
    global _ultimo_resultado
    if not _ultimo_resultado:
        flash("Nenhum resultado disponivel para exportacao PDF.", "error")
        return redirect(url_for("vacinas"))
    filtros = {
        "idade_min": int(request.args.get("idade_min")) if request.args.get("idade_min") else None,
        "idade_max": int(request.args.get("idade_max")) if request.args.get("idade_max") else None,
        "endereco": request.args.get("endereco", ""),
        "imuno": request.args.get("imuno", ""),
        "imunos_remover": [i.strip() for i in request.args.get("imunos_remover", "").split(",") if i.strip()],
        "separar_endereco": request.args.get("separar_endereco") == "1",
    }
    pacientes_unificados = []
    for grupo_key in ("criancas", "adolescentes"):
        for p in _ultimo_resultado.get(grupo_key, []):
            p_copia = dict(p)
            p_copia["imunos_pendentes"] = _unificar_imunos(p.get("imunos_pendentes", []))
            pacientes_unificados.append(p_copia)
    pacientes_filtrados = _aplicar_filtros(pacientes_unificados, filtros)
    stats = {
        "total_pacientes": len(pacientes_filtrados),
        "total_criancas": sum(1 for p in pacientes_filtrados if p.get("grupo_etario") == "Crianca"),
        "total_adolescentes": sum(1 for p in pacientes_filtrados if p.get("grupo_etario") == "Adolescente"),
        "enquadrados": _ultimo_resultado.get("enquadrados", 0),
    }
    if filtros.get("separar_endereco"):
        pacientes_agrupados = _agrupar_por_logradouro_bairro(pacientes_filtrados)
    else:
        pacientes_agrupados = OrderedDict()
        criancas = [p for p in pacientes_filtrados if p.get("grupo_etario") == "Crianca"]
        adolescentes = [p for p in pacientes_filtrados if p.get("grupo_etario") == "Adolescente"]
        if criancas:
            pacientes_agrupados["Criancas (0-9 anos)"] = criancas
        if adolescentes:
            pacientes_agrupados["Adolescentes (10-13 anos)"] = adolescentes

    pdf_html = _gerar_pdf_html(pacientes_agrupados, stats, date.today().isoformat())
    try:
        from xhtml2pdf import pisa
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(pdf_html, dest=pdf_buffer)
        if pisa_status.err:
            flash("Erro ao gerar PDF. Retornando HTML para impressao.", "warning")
            return pdf_html, 200, {"Content-Type": "text/html; charset=utf-8"}
        pdf_bytes = pdf_buffer.getvalue()
    except ImportError:
        flash("xhtml2pdf nao instalado no servidor. Retornando HTML para impressao.", "warning")
        return pdf_html, 200, {"Content-Type": "text/html; charset=utf-8"}
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"vacinacao_imunos_{date.today().isoformat()}.pdf",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8501))
    app.run(host="127.0.0.1", port=port, debug=False)