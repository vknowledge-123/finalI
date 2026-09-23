FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PAPERLAB_DB=/data/paperlab.sqlite3
WORKDIR /app
COPY requirements-runtime.txt ./
RUN python -m pip install --no-cache-dir -r requirements-runtime.txt \
    && groupadd --gid 10001 paper \
    && useradd --uid 10001 --gid paper --no-create-home paper \
    && mkdir /data \
    && chown paper:paper /data
COPY --chown=paper:paper paperlab ./paperlab
USER 10001:10001
EXPOSE 8015
CMD ["python", "-m", "uvicorn", "paperlab.api:default_app", "--factory", "--host", "0.0.0.0", "--port", "8015"]
