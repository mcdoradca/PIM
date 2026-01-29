# --- Stage 1: Builder ---
FROM python:3.11-slim as builder

WORKDIR /app

# Ustawienie zmiennych środowiskowych, aby Python nie buforował wyjścia (ważne dla logów Render)
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Instalacja zależności systemowych (np. dla Pillow i psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Kopiowanie i instalacja zależności Python
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.11-slim

WORKDIR /app

# Zmienne środowiskowe
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH=/app

# Kopiowanie tylko niezbędnych bibliotek z Buildera
COPY --from=builder /install /usr/local
COPY --from=builder /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu

# Kopiowanie kodu aplikacji
COPY . .

# Utworzenie użytkownika bez uprawnień roota (Security Best Practice)
RUN useradd -m pimuser
USER pimuser

# Port, na którym nasłuchuje aplikacja (Render domyślnie używa 10000, ale przekazuje PORT w ENV)
EXPOSE 8000

# Komenda startowa
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
