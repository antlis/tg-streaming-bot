FROM python:3.11-slim-bookworm

RUN sed -i 's/Components: main.*/Components: main contrib non-free non-free-firmware/' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg git \
        intel-media-va-driver-non-free libva-drm2 vainfo \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
WORKDIR /app
RUN pip3 install --no-cache-dir --upgrade --requirement requirements.txt

# Chromium + its system libs for the headless-browser fallback (Playwright).
# Opt-in — it adds ~450 MB — via `--build-arg INSTALL_BROWSER=true` (or the
# INSTALL_BROWSER var in .env when building through docker compose). When off,
# the fallback no-ops and the bot reports yt-dlp's original error.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ARG INSTALL_BROWSER=false
RUN if [ "$INSTALL_BROWSER" = "true" ]; then \
        playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*; \
    fi

CMD ["python3", "main.py"]
