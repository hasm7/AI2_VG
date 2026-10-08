# Microsoft GraphRAG service image: Microsoft's graphrag library in its own Python, apart from the backend's image
# (graphrag 3.2.0 needs Python 3.11-3.13; the backend runs 3.14). The same packages as .venv-graphrag locally.
# Built from the repository root (see docker-compose.yml). GraphRAG's project folder (settings, prompts, input, the
# index) is not in the image: it is mounted from the server, so the index survives a rebuild.
FROM python:3.13-slim

WORKDIR /app
COPY requirements-graphrag.txt .
RUN pip install --no-cache-dir -r requirements-graphrag.txt

COPY graphrag_service/ graphrag_service/

EXPOSE 8100

# GRAPHRAG_SERVICE_HOST=0.0.0.0 (docker-compose.yml) lets the backend container reach it on the internal network; it
# has no published port.
CMD ["python", "graphrag_service/server.py"]
