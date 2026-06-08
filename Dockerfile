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

CMD ["python3", "main.py"]
