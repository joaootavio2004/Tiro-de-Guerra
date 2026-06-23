FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

# Bibliotecas de sistema necessárias para o WeasyPrint (geração de PDF)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libpangocairo-1.0-0 \
    libcairo2 libgdk-pixbuf-2.0-0 libffi8 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Banco fica neste diretório (volume persistente).
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/tiro_guerra.db

EXPOSE 8000

CMD ["python", "run.py"]
