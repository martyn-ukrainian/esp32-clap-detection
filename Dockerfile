# Сервер світла для Pi: лише server/ і tools/lamp.py, без прошивки. Ключі ламп — з .env через compose.
FROM python:3.13-slim
RUN pip install --no-cache-dir "fastapi[standard]" cryptography
WORKDIR /app
COPY tools/lamp.py tools/
COPY server/ server/
ENV PYTHONUNBUFFERED=1
CMD ["fastapi", "run", "server/main.py", "--host", "0.0.0.0", "--port", "7076"]
