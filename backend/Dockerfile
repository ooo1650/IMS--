# Django API image: web (gunicorn), migrate and worker roles (see docker-entrypoint.sh).
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN groupadd --system --gid 10001 ims \
 && useradd --system --uid 10001 --gid ims --home-dir /app --shell /usr/sbin/nologin ims

COPY requirements.txt .
RUN pip install -r requirements.txt

# Code stays root-owned and read-only to the app user.
COPY . .
RUN chmod 0755 docker-entrypoint.sh \
 && DJANGO_DEBUG=false DJANGO_SECRET_KEY=build-only python manage.py collectstatic --no-input

# Product photos: the `media` volume is mounted here and takes this owner on first use.
RUN mkdir -p /srv/media && chown ims:ims /srv/media

ENV DJANGO_DEBUG=false \
    IMS_MEDIA_ROOT=/srv/media
USER ims
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status != 200)"]
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
