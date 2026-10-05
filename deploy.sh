#!/bin/bash
# ============================================================
# DEPLOY - Kode Vacinas PEC → vacinas.kodesaude.com.br
# ============================================================
set -e

NOME_APP="kode-vacinas"
DIRETORIO_APP="/home/kayquedev/kode-vacinas"
SUBDOMINIO="vacinas.kodesaude.com.br"
PORTA=8501
USUARIO_APP="kayquedev"
COMANDO_PYTHON="gunicorn -b 127.0.0.1:${PORTA} app:app"

if [ "$EUID" -ne 0 ]; then
  echo "Rode com sudo: sudo bash deploy.sh"
  exit 1
fi

echo ">>> Criando/Atualizando diretorio da aplicacao..."
mkdir -p "$DIRETORIO_APP"

echo ">>> Detectando tecnologia..."
cd "$DIRETORIO_APP"

if [ -f "requirements.txt" ]; then
  echo ">>> Projeto Python detectado."
  if ! command -v python3 &> /dev/null; then
    apt update && apt install -y python3 python3-venv python3-pip
  fi
  echo ">>> Configurando ambiente virtual..."
  sudo -u "$USUARIO_APP" python3 -m venv "$DIRETORIO_APP/venv" || true
  sudo -u "$USUARIO_APP" "$DIRETORIO_APP/venv/bin/pip" install --upgrade pip
  sudo -u "$USUARIO_APP" "$DIRETORIO_APP/venv/bin/pip" install -r requirements.txt
  EXEC_START="$DIRETORIO_APP/venv/bin/$COMANDO_PYTHON"
else
  echo "ERRO: requirements.txt nao encontrado em $DIRETORIO_APP"
  exit 1
fi

echo ">>> Criando servico systemd..."
cat > "/etc/systemd/system/${NOME_APP}.service" <<EOF
[Unit]
Description=Kode Vacinas PEC - Analisador de Vacinacao
After=network.target

[Service]
Type=simple
User=${USUARIO_APP}
WorkingDirectory=${DIRETORIO_APP}
ExecStart=${EXEC_START}
Restart=on-failure
RestartSec=5
Environment=PORT=${PORTA}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${NOME_APP}"
systemctl restart "${NOME_APP}"

echo ">>> Configurando Nginx para ${SUBDOMINIO}..."
if ! command -v nginx &> /dev/null; then
  apt install -y nginx
fi

cat > "/etc/nginx/sites-available/${SUBDOMINIO}" <<EOF
server {
    listen 80;
    server_name ${SUBDOMINIO};

    location / {
        proxy_pass http://127.0.0.1:${PORTA};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}
EOF

ln -sf "/etc/nginx/sites-available/${SUBDOMINIO}" "/etc/nginx/sites-enabled/${SUBDOMINIO}"
nginx -t && systemctl reload nginx

echo ""
echo "============================================================"
echo "Deploy concluido! Verifique:"
echo "  curl -I http://${SUBDOMINIO}"
echo ""
echo "Para HTTPS (apos DNS propagar):"
echo "  sudo certbot --nginx -d ${SUBDOMINIO}"
echo ""
echo "Status: sudo systemctl status ${NOME_APP}"
echo "Logs:   sudo journalctl -u ${NOME_APP} -f"
echo "============================================================"