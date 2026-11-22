FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 SKIP_INTEGRATION=0 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 SQLGLOT_SOURCE=/opt/sqlglot BR_PROBE=1
WORKDIR /app
COPY runtime-requirements.txt fetch_source.py /app/
RUN pip install --no-cache-dir -r runtime-requirements.txt && python /app/fetch_source.py && python -m compileall -q /opt/sqlglot/sqlglot /opt/sqlglot/tests
COPY selector.py refine.py probe_runner.py tpc.py test_execution.py dictionary_impact.py comparison_profiles.py value_profiles.py expression_profiles.py analysis.json.gz usage.json.gz attributes.json.gz dictionary_reads.json.gz comparisons.json.gz key_flow.json.gz none_traits.json values.json.gz expressions.json.gz LICENSE.sqlglot /app/
# Pinned-commit facts for the TPC checks: per-query hashes and call profiles of the unchanged source
RUN cd /opt/sqlglot && python /app/tpc.py /opt/sqlglot && python -m compileall -q /app && chmod -R a+rX /app /opt/sqlglot
USER 65534:65534
CMD ["python", "/app/selector.py"]
