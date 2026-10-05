FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg gcc g++ libffi-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN mkdir -p /data/sessions /data/tmp

CMD ["python", "main.py"]
