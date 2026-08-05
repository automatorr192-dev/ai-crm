FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home app && mkdir -p /data && chown -R app /app /data
USER app

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
