# syntax=docker/dockerfile:1.7

ARG POSTGRES_VERSION=18.6-bookworm
ARG GOSU_VERSION=1.19
ARG GOSU_GO_VERSION=1.27.1

FROM golang:${GOSU_GO_VERSION}-bookworm AS gosu-builder

ARG GOSU_VERSION

RUN git clone --branch "${GOSU_VERSION}" --depth 1 https://github.com/tianon/gosu.git /src/gosu \
    && cd /src/gosu \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gosu .


FROM postgres:${POSTGRES_VERSION} AS pgvector-builder

ARG PGVECTOR_COMMIT=8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        postgresql-server-dev-18 \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && git checkout --detach "${PGVECTOR_COMMIT}" \
    && make OPTFLAGS="" \
    && make install

FROM postgres:${POSTGRES_VERSION}

LABEL org.opencontainers.image.title="Customers Manager HUB PostgreSQL"
LABEL org.opencontainers.image.description="PostgreSQL 18.6 with pgvector 0.8.6"
LABEL org.opencontainers.image.source="https://github.com/pooyahayati/Customers-Manager-HUB"

COPY --from=gosu-builder /out/gosu /usr/local/bin/gosu
COPY --from=pgvector-builder /usr/lib/postgresql/18/lib/vector.so /usr/lib/postgresql/18/lib/vector.so
COPY --from=pgvector-builder /usr/share/postgresql/18/extension/vector.control /usr/share/postgresql/18/extension/vector.control
COPY --from=pgvector-builder /usr/share/postgresql/18/extension/vector--*.sql /usr/share/postgresql/18/extension/

RUN gosu --version \
    && gosu nobody true
