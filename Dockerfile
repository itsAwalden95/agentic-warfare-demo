# Agentic Cyber Range -- container image for IBM Cloud (Code Engine / IKS / ROKS)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY cyberrange/ ./cyberrange/
COPY web/ ./web/
COPY run_local.py .

# Non-root user (required by ROKS / good practice on IKS)
RUN useradd -m -u 1001 ranger && chown -R ranger:ranger /app
USER 1001

EXPOSE 8080

# watsonx.ai is optional; the container runs with the heuristic commander if the
# WATSONX_* environment variables are not supplied.
CMD ["python", "-m", "cyberrange.server"]
