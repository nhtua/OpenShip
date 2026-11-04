FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN npm install --global pnpm@11.22.0 \
    && pnpm install --frozen-lockfile \
    && chown -R 1000:1000 /app

FROM dependencies AS development
COPY . .
RUN chown -R 1000:1000 /app
USER 1000:1000
CMD ["pnpm", "run", "dev", "--host", "0.0.0.0"]

FROM dependencies AS build
COPY . .
RUN pnpm build

FROM nginx:alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
RUN addgroup -S -g 1000 openship \
    && adduser -S -D -H -u 1000 -G openship openship \
    && rm -f /etc/nginx/conf.d/default.conf \
    && mkdir -p /tmp/nginx/client_temp /tmp/nginx/proxy_temp \
        /tmp/nginx/fastcgi_temp /tmp/nginx/uwsgi_temp /tmp/nginx/scgi_temp \
    && mkdir -p /var/cache/nginx \
    && chown -R 1000:1000 /tmp/nginx /var/cache/nginx /usr/share/nginx/html
USER 1000:1000
