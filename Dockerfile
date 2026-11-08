FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel
COPY src ./src
RUN python -m pip install --no-cache-dir --no-build-isolation .
RUN mkdir -p /data
VOLUME ["/data"]
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "music_sync.app:app", "--host", "0.0.0.0", "--port", "8008"]
