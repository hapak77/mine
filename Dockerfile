FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
# aiogram==3.15.0 требует aiohttp<3.11 и pydantic<2.10, а в requirements.txt
# зафиксированы aiohttp==3.11.0 и pydantic==2.10.0. Ставим именно эти версии,
# пакет aiogram — без повторного разрешения зависимостей. certifi импортирует
# сам aiogram, поэтому его ставим явно: --no-deps иначе его не принесёт.
RUN grep -v -E '^(aiogram|aiohttp|pydantic)==' requirements.txt > /tmp/reqs-rest.txt \
    && pip install --no-cache-dir -r /tmp/reqs-rest.txt \
    && pip install --no-cache-dir "pydantic==2.10.0" "aiohttp==3.11.0" \
        "aiofiles>=23.2.1,<24.2" "magic-filter>=1.0.12,<1.1" \
        "certifi==2026.10.23" \
    && pip install --no-cache-dir --no-deps "aiogram==3.15.0" \
    && rm /tmp/reqs-rest.txt

COPY . .

CMD ["python", "main.py"]
