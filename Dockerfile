ARG PYTHON_BASE_IMAGE=mirror.gcr.io/library/python:3.11-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}

ARG DEBIAN_MIRROR=http://mirror.yandex.ru/debian
ARG DEBIAN_SECURITY_MIRROR=http://mirror.yandex.ru/debian-security
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m pip install --upgrade pip --index-url "${PIP_INDEX_URL}" \
    && python -m pip install --index-url "${PIP_INDEX_URL}" ".[tesseract]"

COPY config/ ./config/
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/data/secure \
    && chown -R app:app /app/data

ENV HOME=/home/app \
    ONLINE_DEKANAT_RESULTS_PATH=/app/data/results.json
USER app

EXPOSE 8000
VOLUME ["/app/data"]

CMD ["online-dekanat", "serve", "--host", "0.0.0.0", "--port", "8000", "--backend", "fastapi"]
