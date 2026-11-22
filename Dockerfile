# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image we labeled every commit in: its Python and SQLite
# decide which sqlglot tests run, so keep it if your image runs them.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY selector.py analysis.json.gz LICENSE.sqlglot /app/
RUN chmod 0444 /app/selector.py /app/analysis.json.gz /app/LICENSE.sqlglot
USER 65534:65534
CMD ["python", "/app/selector.py"]
