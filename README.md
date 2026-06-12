# NewFace – Monitor de Reconhecimento Facial

Aplicação Flask que coleta eventos da API Facial, persiste no PostgreSQL (schema `itumbiara`) e exibe um dashboard de monitoramento.

---

## Stack

- Python 3.11+ / Flask 3
- PostgreSQL (schema `itumbiara`, banco `Lojas`)
- APScheduler (coleta automática a cada 30s)
- Gunicorn + Nginx (produção no VPS)

---

## Estrutura

```
newface/
├── app/
│   ├── __init__.py      # App factory + scheduler
│   ├── config.py        # Configurações (lê .env)
│   ├── models.py        # Models SQLAlchemy
│   ├── routes.py        # Rotas Flask + API JSON
│   ├── collector.py     # Lógica de coleta da API Facial
│   └── templates/
│       ├── base.html
│       └── index.html   # Dashboard
├── migrations/
│   └── 001_create_tables.sql  # DDL – rodar no DBeaver
├── .env.example
├── .gitignore
├── gunicorn.conf.py
├── requirements.txt
└── run.py
```

---

## 1. Setup local (Windows / PyCharm)

```bash
# Clone o repositório
git clone https://github.com/SEU_USUARIO/newface.git
cd newface

# Crie o virtualenv
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# Instale dependências
pip install -r requirements.txt

# Copie e ajuste o .env
copy .env.example .env

# Rode
python run.py
```

Acesse: http://localhost:5006

---

## 2. Banco de dados

Execute o DDL no DBeaver:

```
migrations/001_create_tables.sql
```

Conexão:
- Host: `72.60.58.241`
- Porta: `5432`
- Banco: `Lojas`
- Schema: `itumbiara`
- Usuário: `fefa_dev`

---

## 3. Deploy no VPS (72.60.50.241)

### 3.1 Primeiro deploy

```bash
ssh root@72.60.50.241

cd /var/www
git clone https://github.com/SEU_USUARIO/newface.git
cd newface

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # ajustar SECRET_KEY e DEBUG=false

mkdir -p /var/log/newface
```

### 3.2 Serviço systemd

```bash
nano /etc/systemd/system/newface.service
```

```ini
[Unit]
Description=NewFace – Monitor Facial
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/newface
Environment="PATH=/var/www/newface/venv/bin"
ExecStart=/var/www/newface/venv/bin/gunicorn -c gunicorn.conf.py run:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable newface
systemctl start newface
systemctl status newface
```

### 3.3 Nginx (proxy reverso)

```nginx
server {
    listen 80;
    server_name 72.60.50.241;

    location /newface/ {
        proxy_pass http://127.0.0.1:5006/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Ou acesso direto pela porta 5006:
```bash
# Liberar no firewall
ufw allow 5006
```

### 3.4 Atualizar após push no GitHub

```bash
cd /var/www/newface
git pull
source venv/bin/activate
pip install -r requirements.txt
systemctl restart newface
```

---

## 4. Criar repositório no GitHub (via terminal)

```bash
# Na sua máquina, dentro do diretório do projeto
git init
git add .
git commit -m "feat: initial commit newface"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/newface.git
git push -u origin main
```

---

## 5. Endpoints da API interna

| Método | URL | Descrição |
|--------|-----|-----------|
| GET | `/` | Dashboard |
| GET | `/api/status` | Status do sync + estatísticas |
| GET | `/api/eventos?limit=50` | Eventos recentes |
| GET | `/api/pessoas?limit=50` | Pessoas identificadas |
| POST | `/api/pessoas/:id/nome` | Atualizar nome de uma pessoa |
| POST | `/api/coletar` | Disparar coleta manual |
| GET | `/api/cameras` | Câmeras cadastradas |
| GET | `/api/estabelecimentos` | Estabelecimentos |

---

## 6. Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `SECRET_KEY` | — | Chave Flask |
| `DB_HOST` | 72.60.58.241 | Host PostgreSQL |
| `FACIAL_API_BASE` | http://201.71.234.84:8000 | URL da API Facial |
| `FACIAL_POLL_SECS` | 30 | Intervalo de coleta em segundos |
| `FACIAL_LIMIT` | 50 | Eventos por requisição |
| `PORT` | 5006 | Porta do app |
