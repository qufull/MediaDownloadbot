FROM python:3.13-slim

WORKDIR /app

# Зависимости: ffmpeg, curl, unzip. Кэш apt — быстрее пересборка
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update -qq \
    && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Установка uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Установка Deno (нужен yt-dlp для расшифровки JS-токенов YouTube)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# Копируем файлы зависимостей
COPY pyproject.toml uv.lock ./

# Устанавливаем зависимости проекта
RUN uv sync --frozen

# Фикс n-challenge/Deno "Cannot read properties of undefined (reading 'origin')"
# yt-dlp 2026.03.17 + yt-dlp-ejs 0.8.0 закрывают баг "Only images are available"
RUN uv pip install --upgrade \
    "yt-dlp[default]>=2026.03.17" \
    "yt-dlp-ejs>=0.8.0"

# Проверка версий при сборке
RUN uv run python -c "import importlib.metadata; print('yt-dlp =', importlib.metadata.version('yt-dlp')); print('yt-dlp-ejs =', importlib.metadata.version('yt-dlp-ejs'))"

# Создаем директории
RUN mkdir -p /app/src/storage /app/src/storage/downloads

# Копируем проект (включая tests)
COPY . .

CMD ["uv", "run", "main.py"]
