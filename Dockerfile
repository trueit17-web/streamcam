FROM python:3.12-slim
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY streemcam ./streemcam
RUN pip install --no-cache-dir .
ENV STREEMCAM_CONFIG=/app/config.yaml
CMD ["python", "-m", "streemcam", "run"]
