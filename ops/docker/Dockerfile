# Toolchain image for the weekly refresh. The code is not baked in: compose
# mounts the git checkout at /app, so a `git pull` on the host is a deploy.
# Only a pyproject/uv.lock change (or a tool bump) needs a rebuild.

# innoextract's last release (1.9, 2020) stops at Inno Setup 6.0; master
# reads up to 6.4, which Lenovo's newer installers use.
FROM debian:trixie-slim AS innoextract
ARG INNOEXTRACT_COMMIT=6e9e34ed0876014fdb46e684103ef8c3605e382e
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake ca-certificates curl \
      libboost-iostreams-dev libboost-filesystem-dev libboost-date-time-dev \
      libboost-system-dev libboost-program-options-dev liblzma-dev \
 && curl -fsSL https://github.com/dscharrer/innoextract/archive/${INNOEXTRACT_COMMIT}.tar.gz \
      | tar -xz -C /tmp \
 && cmake -S /tmp/innoextract-${INNOEXTRACT_COMMIT} -B /tmp/build \
      -DCMAKE_BUILD_TYPE=Release \
 && cmake --build /tmp/build -j"$(nproc)" \
 && mkdir -p /out/lib && install -m755 /tmp/build/innoextract /out/ \
 && ldd /out/innoextract | grep -o '/[^ ]*libboost[^ ]*' | xargs -I{} cp {} /out/lib/

FROM python:3.14-slim-trixie

ARG UID=1000
ARG SEVENZIP=2606
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/
COPY --from=rclone/rclone:1.76.0 /usr/local/bin/rclone /usr/local/bin/rclone
COPY --from=innoextract /out/innoextract /usr/local/bin/innoextract
COPY --from=innoextract /out/lib/ /usr/local/lib/
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
 && curl -fsSL https://github.com/ip7z/7zip/releases/download/${SEVENZIP%??}.${SEVENZIP#??}/7z${SEVENZIP}-linux-x64.tar.xz \
      | tar -xJ -C /usr/local/bin 7zz \
 && rm -rf /var/lib/apt/lists/* && ldconfig \
 && useradd -m -u ${UID} winidx \
 && mkdir /opt/venv && chown winidx /opt/venv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    WINIDX_ROOT=/app
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
USER winidx
RUN uv sync --frozen --no-install-project
# Firefox's system libraries (root), then Camoufox's browser build (for ASRock).
USER root
RUN /opt/venv/bin/playwright install-deps firefox && rm -rf /var/lib/apt/lists/*
USER winidx
RUN /opt/venv/bin/python -m camoufox fetch 2>&1 | grep -v '%'

ENTRYPOINT ["/app/ops/docker/entrypoint.sh"]
CMD ["ops/refresh.sh"]
