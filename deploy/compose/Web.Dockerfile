FROM docker.io/library/node:22.21.1-slim AS build
RUN npm install -g pnpm@11.25.0
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web apps/web
COPY packages/web-api-client packages/web-api-client
RUN pnpm install --frozen-lockfile && pnpm build
FROM docker.io/library/caddy:2.10.2
COPY --from=build /app/apps/web/dist /srv
COPY deploy/compose/Caddyfile /etc/caddy/Caddyfile
