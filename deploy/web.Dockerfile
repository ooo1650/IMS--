# Web image: nginx serving the built SPA + Django admin static files, proxying /api.
# TLS: deploy/nginx/40-ims.sh (Let's Encrypt from the certbot service, else self-signed).
# Build context is the repo root (the Help page bundles docs/user-manual.md).
#   docker build -f deploy/web.Dockerfile .

FROM node:24.11.1-alpine AS spa
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
COPY docs/user-manual.md /src/docs/user-manual.md
# tsc -b also type-checks pricing.test.ts, which imports the shared backend pricing vectors.
COPY backend/sales/tests/pricing_vectors.json /src/backend/sales/tests/pricing_vectors.json
RUN npm run build

# The operator console is its own project (console/); built here when it is
# served by this same web container (port 8443). See console/README.md for
# hosting it on another server instead.
FROM node:24.11.1-alpine AS console
WORKDIR /src/console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

FROM python:3.12.11-slim-bookworm AS static
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/ .
RUN DJANGO_DEBUG=false DJANGO_SECRET_KEY=build-only python manage.py collectstatic --no-input

FROM nginx:1.28.0-alpine
RUN apk add --no-cache openssl && rm -f /etc/nginx/conf.d/default.conf
# The image renders /etc/nginx/templates/*.template into conf.d at start; only
# IMS_* variables are substituted so nginx's own $variables survive.
ENV NGINX_ENVSUBST_FILTER=^IMS_
COPY deploy/nginx/templates/ /etc/nginx/templates/
COPY deploy/nginx/snippets/ /etc/nginx/snippets/
COPY --chmod=0755 deploy/nginx/40-ims.sh /docker-entrypoint.d/40-ims.sh
COPY --from=spa /src/frontend/dist /srv/spa
COPY --from=console /src/console/dist /srv/console
COPY --from=static /app/staticfiles /srv/static
