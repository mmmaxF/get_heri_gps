ARG APP_PORT=8010
FROM python:3.12-slim
ARG APP_PORT=8010

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/output

EXPOSE ${APP_PORT}

CMD ["python", "app.py"]
