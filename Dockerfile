FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY streemcam ./streemcam
RUN pip install --no-cache-dir .
ENV STREEMCAM_CONFIG=/app/config.yaml
CMD ["python", "-m", "streemcam", "run"]
