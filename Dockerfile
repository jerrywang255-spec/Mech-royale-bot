# Survivor bot for Builders Mech Royale.
# Build:  docker build -t survivor .
# The match runs it with no network, a read-only disk (writable /tmp), 1 CPU, 512 MB, uid 65534, map at /arena.
# Versions are pinned so the final rebuild produces the same bot.
FROM python:3.13.18-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# one thread for numpy's math libraries (we only get 1 CPU), no .pyc writes at runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

# system-wide install (not --user), so the unprivileged match user can import it
RUN pip install --no-cache-dir numpy==2.3.5

WORKDIR /bot
COPY mechfight.py nav.py bot.py ./
RUN python -m compileall -q /bot

ENTRYPOINT ["python3", "-u", "/bot/bot.py"]
