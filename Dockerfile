# Works on Fly.io, Railway, Google Cloud Run, or any Docker host.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY risklens ./risklens
COPY data ./data
ENV PORT=8080
EXPOSE 8080
USER nobody
CMD ["python", "-m", "risklens.server", "--public", "--results-dir", "/tmp/risklens-results"]
