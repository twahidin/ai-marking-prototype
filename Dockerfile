# Image used by Railway when this Dockerfile is present in the repo. We
# generate PDFs by compiling LaTeX with lualatex (proper math typography
# via amsmath, fontspec for Noto-CJK + Noto-Tamil), so the runtime needs
# TeX Live's lualatex, the latex-extra package set (tcolorbox, tabularx,
# enumitem, ulem, microtype, titlesec) and Noto fonts.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# System libraries split into two apt-get calls so the (much larger) TeX
# Live layer caches independently of the small font + utility layer.
# Total uncompressed image footprint: ~1.5–2 GB. Build time on Railway:
# 4–6 minutes for a fresh build, ~30s when both layers cache.
RUN apt-get update && apt-get install -y --no-install-recommends \
        # fontconfig must be explicit because the font packages list it
        # only as a "Recommends", which --no-install-recommends skips —
        # without it `fc-cache` doesn't exist and this RUN aborts with 127
        fontconfig \
        # Fonts that lualatex picks up by name via fontspec
        fonts-noto \
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-noto-color-emoji \
        fonts-noto-extra \
        fonts-noto-mono \
        # pdf2image (OpenAI / Qwen marking path)
        poppler-utils \
        # pillow-heif decoder for HEIC student uploads
        libheif1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# TeX Live: lualatex binary plus the package collection that backs
# tcolorbox / tabularx / fontspec / amsmath / enumitem / titlesec / ulem.
# texlive-luatex pulls in the engine + scheme. texlive-latex-extra and
# texlive-fontsextra cover the boxes / tables / decorative packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-luatex \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        texlive-fonts-extra \
        texlive-lang-cjk \
        texlive-lang-other \
        texlive-pictures \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps in a separate layer so a code-only change doesn't
# bust the dep cache and re-download wheels every push.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway injects $PORT at runtime; shell form expands it. One worker +
# many threads matches the original Procfile.
CMD gunicorn -w 1 --threads 100 --timeout 300 --bind "0.0.0.0:$PORT" app:app
