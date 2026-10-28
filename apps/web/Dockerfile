FROM node:22-alpine AS dependencies
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN npm install --global pnpm@11.22.0 \
    && pnpm install --frozen-lockfile

FROM dependencies AS development
COPY . .
CMD ["pnpm", "run", "dev", "--host", "0.0.0.0"]

FROM dependencies AS build
COPY . .
RUN pnpm build

FROM nginx:alpine AS production
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
