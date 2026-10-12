FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PADDLE_HOME=/home/app/.paddle \
    PADDLE_PDX_CACHE_HOME=/home/app/.paddlex \
    EASYOCR_MODULE_PATH=/home/app/.EasyOCR \
    TORCH_HOME=/home/app/.cache/torch

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

# Keep NumPy on the 1.x line for compatibility across the CPU OCR packages.
RUN printf 'numpy<2\n' > /tmp/constraints.txt \
    && python -m pip install --upgrade pip \
    && python -m pip install --constraint /tmp/constraints.txt \
        --index-url https://download.pytorch.org/whl/cpu \
        torch torchvision \
    && python -m pip install --constraint /tmp/constraints.txt '.[ocr,dataset]' \
    && rm /tmp/constraints.txt

COPY config/ ./config/
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
    && mkdir -p /app/data/secure /home/app/.paddle /home/app/.paddlex /home/app/.paddleocr /home/app/.EasyOCR /home/app/.cache/torch \
    && chown -R app:app /app/data /home/app

ENV HOME=/home/app
USER app

EXPOSE 8000
VOLUME ["/app/data", "/home/app/.paddle", "/home/app/.paddlex", "/home/app/.paddleocr", "/home/app/.EasyOCR", "/home/app/.cache/torch"]

CMD ["online-dekanat", "serve", "--host", "0.0.0.0", "--port", "8000", "--backend", "fastapi"]
