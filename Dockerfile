FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 SKIP_INTEGRATION=0 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 SQLGLOT_SOURCE=/opt/sqlglot BR_PROBE=1
WORKDIR /app
COPY runtime-requirements.txt fetch_source.py /app/
RUN pip install --no-cache-dir -r runtime-requirements.txt && python /app/fetch_source.py && python -m compileall -q /opt/sqlglot/sqlglot /opt/sqlglot/tests
COPY selector.py refine.py probe_runner.py test_execution.py dictionary_impact.py proxy.py analysis.json.gz usage.json.gz attributes.json.gz dictionary_reads.json.gz LICENSE.sqlglot /app/
# Record the pinned commit's TPC fingerprints (a map of the pinned source, computed at build time)
RUN cd /opt/sqlglot && python /app/proxy.py /opt/sqlglot && python -m compileall -q /app && chmod -R a+rX /app /opt/sqlglot
USER 65534:65534
CMD ["python", "/app/selector.py"]
