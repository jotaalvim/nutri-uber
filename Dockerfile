# syntax=docker/dockerfile:1
ARG RUBY_VERSION=3.3.5
FROM docker.io/library/ruby:$RUBY_VERSION-slim AS base

WORKDIR /app

RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y curl libjemalloc2 libvips sqlite3 && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

ENV RAILS_ENV=production \
    BUNDLE_DEPLOYMENT=1 \
    BUNDLE_PATH=/usr/local/bundle \
    BUNDLE_WITHOUT=development

FROM base AS build

RUN apt-get update -qq && \
    apt-get install --no-install-recommends -y build-essential git libyaml-dev pkg-config && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

COPY nutri_dashboard_rails/Gemfile nutri_dashboard_rails/Gemfile.lock ./
RUN bundle install && \
    rm -rf ~/.bundle "${BUNDLE_PATH}"/ruby/*/cache "${BUNDLE_PATH}"/ruby/*/bundler/gems/*/.git

COPY nutri_dashboard_rails/ ./
RUN bundle exec bootsnap precompile app/ lib/

RUN SECRET_KEY_BASE_DUMMY=1 DATABASE_PATH=/tmp/db.sqlite3 ./bin/rails assets:precompile

RUN mkdir -p /app/data

FROM base

COPY --from=build /usr/local/bundle /usr/local/bundle
COPY --from=build /app /app
COPY data /app/seed_data

RUN mkdir -p /app/data

ENV DATABASE_PATH=/app/data/nutri_dashboard.db
ENV DOCKER=1

EXPOSE 4000

ENTRYPOINT ["/app/bin/docker-entrypoint"]
CMD ["./bin/rails", "server", "-b", "0.0.0.0", "-p", "4000"]
