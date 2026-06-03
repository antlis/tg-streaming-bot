FROM nikolaik/python-nodejs:python3.9-nodejs17
# Debian Buster is EOL; repoint apt to the archive mirror. Node is already in the
# base image, so drop the stale nodesource/yarn repo lists and only pull ffmpeg.
RUN printf 'deb http://archive.debian.org/debian buster main\n' > /etc/apt/sources.list \
    && rm -f /etc/apt/sources.list.d/*.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
COPY . /app
WORKDIR /app
RUN pip3 install --no-cache-dir --upgrade --requirement requirements.txt

CMD ["python3", "main.py"]
