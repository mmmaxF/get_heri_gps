FROM python:3.12-slim

ARG ATEM_OUTPUT_PORT=8030
ENV PORT=${ATEM_OUTPUT_PORT}
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential python3-dev fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get purge -y --auto-remove build-essential python3-dev

COPY app.py atem_sender.py .

EXPOSE ${ATEM_OUTPUT_PORT}

CMD ["sh", "-c", "uvicorn app:app --host ${HOST:-0.0.0.0} --port ${PORT:-8030}"]
