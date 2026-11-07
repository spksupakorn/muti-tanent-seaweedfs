FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Static files for the back office's admin pages (only that service serves them).
RUN DJANGO_SETTINGS_MODULE=config.backoffice_settings DJANGO_SECRET_KEY=build POSTGRES_DB=build \
    POSTGRES_USER=build POSTGRES_PASSWORD=build python manage.py collectstatic --noinput
RUN useradd --system --no-create-home app
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
