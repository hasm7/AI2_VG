# Backend image: the Flask app under Gunicorn, plus the SQL viewer it can start as a child process.
# Built from the repository root (see docker-compose.yml).
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY backend/ backend/
COPY viewer/ viewer/

WORKDIR /app/backend
EXPOSE 8000

# One worker with several threads: chat conversations and the SQL viewer process live in the process memory, so every
# request must reach the same process. The long timeout covers streamed chat answers and test runs.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "600", "--bind", "0.0.0.0:8000", "app:app"]
