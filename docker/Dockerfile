FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    cifs-utils \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker/entrypoint.sh

EXPOSE 5556

ENTRYPOINT ["docker/entrypoint.sh"]
