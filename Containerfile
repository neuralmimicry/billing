FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY billing_service ./billing_service
RUN pip install --no-cache-dir .

EXPOSE 5020
CMD ["python", "-m", "billing_service"]
