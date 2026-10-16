# ==============================================================================
# Stage 1: Dependency Builder Stage
# Pinned Base: python:3.11-slim (Debian 13 trixie, Python 3.11.17)
# Digest: sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
# ==============================================================================
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder

WORKDIR /build

# Copy lockfile and package configuration
COPY requirements.lock.txt pyproject.toml README.md ./
COPY app ./app

# Install exact locked dependencies and local application into an isolated prefix
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock.txt \
    && pip install --no-cache-dir --prefix=/install --no-deps .

# ==============================================================================
# Stage 2: Hardened Runtime Stage (Stripped of Unnecessary Build Tooling)
# ==============================================================================
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime

WORKDIR /app

# Create non-root application user
RUN groupadd -r e7user && useradd -r -g e7user -d /app -s /sbin/nologin e7user

# Copy installed production packages and executables from builder
COPY --from=builder /install /usr/local

# Remove build tooling (setuptools, wheel, pip) from runtime image to eliminate build-time CVEs
RUN rm -rf /usr/local/lib/python3.11/site-packages/setuptools* \
           /usr/local/lib/python3.11/site-packages/wheel* \
           /usr/local/lib/python3.11/site-packages/pip* \
           /root/.cache

# Copy application files (alembic.ini, migrations, etc.)
COPY . .
RUN chown -R e7user:e7user /app

USER e7user

EXPOSE 8000
