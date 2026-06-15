ARG TELOP_OUTPUT_PORT=8030
FROM python:3.12-slim
ARG TELOP_OUTPUT_PORT=8030

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-ipaexfont \
        fonts-ipafont \
        fonts-mplus \
        fonts-takao \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/config /app/assets/fonts

EXPOSE ${TELOP_OUTPUT_PORT}

CMD ["python", "app.py"]
