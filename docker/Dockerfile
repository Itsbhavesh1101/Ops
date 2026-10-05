FROM python:3.11-slim

WORKDIR /app

# Create non-root application user
RUN groupadd -r e7user && useradd -r -g e7user -d /app -s /sbin/nologin e7user

# Copy package definition and application package for deterministic installation
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --default-timeout=120 -e .

# Copy full application context (including alembic.ini and migrations)
COPY . .
RUN chown -R e7user:e7user /app

USER e7user

EXPOSE 8000
