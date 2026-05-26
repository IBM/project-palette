# Palette — single-stage Dockerfile.
#
# Bundles Python, Node, LibreOffice, poppler, and the three OFL font families
# the LoRA's design language reaches for (IBM Plex, Inter, JetBrains Mono).
# Result is one container that runs the full request → plan → deck pipeline
# end-to-end. Image is ~1.3 GB; most of that is LibreOffice.
#
# Build:  docker build -t palette .
# Run:    docker run --rm -p 8080:8080 -e RITS_API_KEY=$RITS_API_KEY palette

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

# System deps:
#   nodejs/npm — runs the pptxgenjs renderer
#   libreoffice — pptx -> pdf for previews
#   poppler-utils — pdfplumber + pdf2image backend
#   fonts-* — the typography the LoRA emits; without these LibreOffice
#             substitutes Liberation Sans and decks look generic
#   fontconfig — for `fc-cache` after font install
RUN apt-get update && apt-get install -y --no-install-recommends \
      nodejs npm \
      libreoffice \
      poppler-utils \
      fonts-ibm-plex \
      fonts-inter \
      fonts-jetbrains-mono \
      fontconfig \
      curl \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so they cache independently of source changes
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Install Node deps (just pptxgenjs); --omit=dev keeps it small
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Copy the rest of the source
COPY . .

# Code Engine (and most PaaS) inject PORT at runtime; uvicorn picks it up
# via the PORT env var in app.py:main(). Default to 8080 when unset.
EXPOSE 8080

# Shell form so $PORT expands at container start, not at build time
CMD python app.py
