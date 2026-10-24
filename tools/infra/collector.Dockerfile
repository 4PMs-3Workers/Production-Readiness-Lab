FROM otel/opentelemetry-collector-contrib:0.163.2@sha256:7f22d2a6748110ffb6a59bc0df448bccbf501cdb667d610ede4c61d3863d5849
ARG PRL_SOURCE_SHA
LABEL org.opencontainers.image.revision=$PRL_SOURCE_SHA
COPY --chown=10001:10001 tools/infra/collector-file-storage/ /var/lib/otelcol/file_storage/
