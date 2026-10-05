#!/usr/bin/env python3
"""
Kode Vacinas PEC - Interface Web
=================================
Aplicacao Flask para upload e analise de CSVs do e-SUS PEC,
cruzando com o Calendario PNI 2026. Gera relatorio consolidado
por paciente no estilo Kode APS.

Deploy: vacinas.kodesaude.com.br
"""
import io
import os
import tempfile
from datetime import date
from flask import Flask, render_template_string, request, send_file, flash, redirect, url_for
from analisador_vacinas import analisar, exportar_csv, consolidar_por_paciente, validar_contra_calendario, ler_csv_pec

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kode-vacinas-pec-secret-key-2026")

UPLOAD_FOLDER = tempfile.mkdtemp(prefix="kode_vacinas_")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB max

HTML_TEMPLATE = """
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
        header { background: var(--primary); color: white; padding: 20px 0; margin-bottom: 30px; }
        header .container { display: flex; align-items: center; gap: 15px; }
        header h1 { font-size: 1.5rem; font-weight: 600; }
        header .subtitle { opacity: 0.9; font-size: 0.9rem; }
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
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-card { background: var(--bg); border-radius: 8px; padding: 16px; text-align: center; }
        .stat-value { font-size: 2rem; font-weight: 700; color: var(--primary); }
        .stat-label { font-size: 0.85rem; color: var(--text-muted); margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { background: var(--bg); padding: 12px 8px; text-align: left; font-weight: 600; border-bottom: 2px solid var(--border); position: sticky; top: 0; }
        td { padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
        tr:hover { background: #f8fafc; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 500; }
        .badge-atrasada { background: #fef3c7; color: #92400e; }
        .badge-prazo { background: #d1fae5; color: #065f46; }
        .imunos-list { display: flex; flex-wrap: wrap; gap: 4px; }
        .imuno-tag { background: #eff6ff; color: #1e40af; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; white-space: nowrap; }
        .section-title { display: flex; align-items: center; gap: 10px; margin: 24px 0 16px; padding-bottom: 8px; border-bottom: 2px solid var(--primary); }
        .section-title h3 { font-size: 1.1rem; color: var(--primary); }
        .section-count { background: var(--primary); color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.8rem; }
        .alert { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; }
        .alert-error { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
        .alert-success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
        .file-list { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
        .file-item { display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: var(--bg); border-radius: 6px; font-size: 0.85rem; }
        .file-icon { color: var(--success); }
        .actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
        @media (max-width: 768px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            table { font-size: 0.75rem; }
            th, td { padding: 6px 4px; }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <div>
                <h1>Kode Vacinas PEC</h1>
                <div class="subtitle">Analisador de Vacinacao e-SUS PEC x Calendario PNI 2026</div>
            </div>
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
                    <p style="color: var(--text-muted); font-size: 0.85rem;">Selecione os 4 CSVs de busca ativa (atrasada/no prazo, 0-9/10-13)</p>
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
            <div class="stat-card">
                <div class="stat-value">{{ resultado.total_pacientes }}</div>
                <div class="stat-label">Pacientes Unicos</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ resultado.total_criancas }}</div>
                <div class="stat-label">Criancas (0-9 anos)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ resultado.total_adolescentes }}</div>
                <div class="stat-label">Adolescentes (10-13)</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ resultado.enquadrados }}</div>
                <div class="stat-label">Registros Enquadrados</div>
            </div>
        </div>

        <div class="actions" style="margin-bottom: 24px;">
            <a href="{{ url_for('download_csv') }}" class="btn btn-success">Baixar CSV Consolidado</a>
            <a href="{{ url_for('index') }}" class="btn btn-outline">Nova Analise</a>
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
                            <div class="imunos-list">
                                {% for imuno in p.imunos_pendentes %}
                                <span class="imuno-tag">{{ imuno }}</span>
                                {% endfor %}
                            </div>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% else %}
        <div class="card"><p>Nenhum paciente neste grupo.</p></div>
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
    </script>
</body>
</html>
"""

# Armazena resultado da ultima analise em memoria (sessao unica)
_ultimo_resultado = None
_ultimo_csv_path = None


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_TEMPLATE, resultado=_ultimo_resultado)


@app.route("/", methods=["POST"])
def analisar_upload():
    global _ultimo_resultado, _ultimo_csv_path

    files = request.files.getlist("csvs")
    if not files or all(f.filename == "" for f in files):
        flash("Selecione pelo menos um arquivo CSV.", "error")
        return redirect(url_for("index"))

    # Cria pasta temporaria para os CSVs desta analise
    session_dir = tempfile.mkdtemp(prefix="kode_vacinas_session_", dir=UPLOAD_FOLDER)

    nomes_esperados = [
        "atrasada 0 a 9.csv",
        "atrasada 10 a 13.csv",
        "no prazo 0 a 9.csv",
        "no prazo 10 a 13.csv",
    ]

    for f in files:
        if f.filename == "":
            continue
        # Tenta mapear para nome esperado ou usa nome original
        nome_destino = f.filename
        f.save(os.path.join(session_dir, nome_destino))

    # Verifica quantos arquivos foram salvos
    arquivos_salvos = [f for f in os.listdir(session_dir) if f.endswith(".csv")]
    if not arquivos_salvos:
        flash("Nenhum arquivo CSV valido foi enviado.", "error")
        return redirect(url_for("index"))

    try:
        # Monkey-patch temporario para usar a pasta de upload
        import analisador_vacinas as av

        csv_dir_original = av.CSV_DIR_PADRAO
        arquivos_originais = av.CSV_FILES[:]

        # Detecta quais arquivos estao disponiveis e mapeia
        av.CSV_DIR_PADRAO = session_dir
        av.CSV_FILES = arquivos_salvos

        resultado = av.analisar(session_dir)

        # Restaura valores originais
        av.CSV_DIR_PADRAO = csv_dir_original
        av.CSV_FILES = arquivos_originais

        _ultimo_resultado = resultado

        # Exporta CSV consolidado para download
        csv_path = os.path.join(session_dir, "resultado_consolidado.csv")
        exportar_csv(resultado, csv_path)
        global _ultimo_csv_path
        _ultimo_csv_path = csv_path

        flash(
            f"Analise concluida! {resultado['total_pacientes']} pacientes unicos encontrados.",
            "success",
        )
    except Exception as e:
        flash(f"Erro ao analisar: {str(e)}", "error")
        return redirect(url_for("index"))

    return redirect(url_for("index"))


@app.route("/download")
def download_csv():
    global _ultimo_csv_path
    if not _ultimo_csv_path or not os.path.exists(_ultimo_csv_path):
        flash("Nenhum resultado disponivel para download.", "error")
        return redirect(url_for("index"))

    return send_file(
        _ultimo_csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"vacinas_consolidado_{date.today().isoformat()}.csv",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8501))
    app.run(host="127.0.0.1", port=port, debug=False)