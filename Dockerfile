FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Optional: OCR for text embedded in screenshotted images (e.g. coordinates
# typed over a map screenshot rather than sent as a location/caption).
# bot/filters.py:ocr_image() gracefully no-ops without pytesseract installed.
# Uncomment this AND pytesseract in requirements.txt together to enable --
# adds ~15 MB (tesseract-ocr + Ukrainian/Russian language data).
# RUN apt-get update \
#     && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ukr tesseract-ocr-rus \
#     && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
