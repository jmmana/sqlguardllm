FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY docs/pypi.md docs/pypi.md
COPY src src
RUN pip install ".[serve]" && useradd --create-home --uid 10001 sqlguard
USER sqlguard
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8787/v1/health', timeout=2)" || exit 1
ENTRYPOINT ["sqlguardllm", "serve", "--host", "0.0.0.0"]
CMD ["--policy", "/policy/policy.yaml"]
