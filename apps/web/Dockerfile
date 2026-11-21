FROM node:24-bookworm-slim

WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm install --global pnpm@10.33.2
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY app app
COPY postcss.config.mjs .
RUN pnpm build

CMD ["pnpm", "start", "--hostname", "0.0.0.0"]
