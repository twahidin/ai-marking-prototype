# Image used by Railway when this Dockerfile is present in the repo. We
# moved off Nixpacks because WeasyPrint's ctypes lookup of libgobject /
# libpango was failing on Nix's hashed store paths — apt installs those
# libraries to the standard /usr/lib/x86_64-linux-gnu where dlopen finds
# them without any LD_LIBRARY_PATH gymnastics.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libraries:
#  - libpango / libcairo / libharfbuzz / libfontconfig / libgdk-pixbuf →
#    WeasyPrint's HTML→PDF rendering pipeline
#  - shared-mime-info → mimetype detection used by Pango
#  - fonts-noto + noto-cjk + noto-extra → Tamil + Chinese + Japanese +
#    Korean glyphs available natively, no per-app font shipping
#  - poppler-utils → pdf2image (used on the OpenAI / Qwen marking path)
#  - libheif1 → pillow-heif decoder for HEIC student uploads
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libfontconfig1 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        shared-mime-info \
        fonts-noto \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fonts-noto-extra \
        poppler-utils \
        libheif1 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

# Install Python deps in a separate layer so a code-only change doesn't
# bust the dep cache and re-download wheels every push.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects $PORT at runtime; shell form expands it. One worker +
# many threads matches the Procfile we used pre-Docker.
CMD gunicorn -w 1 --threads 100 --timeout 300 --bind "0.0.0.0:$PORT" app:app
