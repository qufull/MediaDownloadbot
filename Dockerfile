FROM python:3.13-slim

WORKDIR /app

# Зависимости: ffmpeg для yt-dlp (НЕ-YouTube сервисы)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Установка uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Копируем файлы зависимостей
COPY pyproject.toml uv.lock ./

# Устанавливаем зависимости проекта
RUN uv sync --frozen

# Обновляем yt-dlp (для НЕ-YouTube сервисов: TikTok, Reddit, Rutube и т.д.)
RUN uv pip install --upgrade "yt-dlp[default]"

# Создаем директории
RUN mkdir -p /app/src/storage /app/src/storage/downloads

# Копируем проект
COPY . .

CMD ["uv", "run", "main.py"]
