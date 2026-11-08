# Standalone image for the operator console (host it anywhere):
#   docker build -t ims-console console/
#   docker run -d -p 8080:8080 -e IMS_API_URL=https://ims.example.com:8443 ims-console
# Put HTTPS in front of it (sign-in cookies are Secure). On the IMS server set
# IMS_CONSOLE_ORIGIN, IMS_CONSOLE_PROXY and IMS_CONSOLE_ALLOW (see README.md).
FROM node:24.11.1-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.28.0-alpine
ENV NGINX_ENVSUBST_FILTER=^IMS_ \
    IMS_API_URL=https://127.0.0.1:8443 \
    IMS_API_HOST=
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
