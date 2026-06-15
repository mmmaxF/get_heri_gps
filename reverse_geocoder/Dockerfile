ARG REVERSE_GEOCODER_PORT=8020
FROM python:3.12-slim
ARG REVERSE_GEOCODER_PORT=8020

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x /app/entrypoint.sh \
    && mkdir -p /app/data /app/output

EXPOSE ${REVERSE_GEOCODER_PORT}

CMD ["/app/entrypoint.sh"]
