FROM python:3.12-slim

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt pytest httpx
# Tests seed data through the real operator commands, so they load the backend code (mounted at /app).
ENV PYTHONPATH=/app DJANGO_SETTINGS_MODULE=config.settings PYTHONDONTWRITEBYTECODE=1
WORKDIR /tests/stack
CMD ["pytest", "-v", "-p", "no:cacheprovider"]
