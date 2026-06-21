FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Banco fica neste diretório (volume persistente).
RUN mkdir -p /app/data
ENV DB_PATH=/app/data/tiro_guerra.db

EXPOSE 8000

CMD ["python", "run.py"]
