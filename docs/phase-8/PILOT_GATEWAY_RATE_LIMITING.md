# Phase 8I: Pilot Gateway Architecture & Edge Rate Limiting Report

**Repository**: `F:\operations-engine`  
**Execution Timestamp**: 2026-10-19T02:20:00+05:30  
**Phase Identifier**: `PHASE_8I`  
**Phase Title**: `PILOT_GATEWAY_EDGE_RATE_LIMITING_AND_VERIFICATION`  

---

## 1. Executive Summary

This document certifies the implementation, configuration, and verification of the **Pilot Edge Gateway** for the **IR Infotech E-7 Operations Engine**.

The pilot edge gateway establishes an external Layer-7 reverse proxy in front of the immutable E-7 Release Candidate container (`e7-operations-engine:rc-38d080fadab8`). It enforces ingress volumetric rate limiting and deterministic `HTTP 429` error handling with `Retry-After` headers, satisfying the edge-control requirement for restricted pilot deployment.

```text
================================================================================
PILOT_GATEWAY_RATE_LIMITING = PASS
================================================================================
RATE_LIMITING (Production Canonical) = BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION
================================================================================
RESTRICTED_PILOT_ENGINEERING_READINESS = READY
================================================================================
PHASE_8_PRODUCTION_READINESS = NOT_READY
================================================================================
```

---

## 2. Gateway Architecture & Deployment Boundary

The gateway operates as a decoupled reverse proxy sitting at the network boundary:

```
[Inbound Client Traffic]
         │
         ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  Pilot Ingress Gateway (Nginx 1.31.7-alpine)                 │
 │  - Container: e7-pilot-gateway                               │
 │  - Host Port: 127.0.0.1:8088:80 (Loopback only)              │
 │  - Rate Limiting: 10 req/s, burst=20, nodelay                │
 │  - Client Keying: $binary_remote_addr                        │
 │  - Error Handler: HTTP 429 with Retry-After: 1               │
 └──────────────────────────────┬───────────────────────────────┘
                                │ proxy_pass http://api:8000
                                ▼
 ┌──────────────────────────────────────────────────────────────┐
 │  E-7 Core Application (FastAPI Runtime)                      │
 │  - Image: e7-operations-engine:rc-38d080fadab8 (FROZEN RC)   │
 │  - Container: e7-api                                         │
 │  - Network: operations-engine_default                        │
 │  - Host Port Binding: NONE (Internal port 8000 exposed only) │
 └──────────────────────────────┬───────────────────────────────┘
                                │
        ┌───────────────────────┴───────────────────────┐
        ▼                                               ▼
 ┌──────────────────────────────┐        ┌──────────────────────────────┐
 │ PostgreSQL 16 (Canonical DB) │        │ RabbitMQ 3.13 (Event Broker) │
 │ - Container: e7-postgres     │        │ - Container: e7-rabbitmq     │
 │ - Port 5432: Internal Only   │        │ - Ports: Internal/Mgmt       │
 └──────────────────────────────┘        └──────────────────────────────┘
```

### Key Architectural Invariants
1. **Decoupled Gateway**: The gateway is not part of the frozen E-7 application image. The frozen Release Candidate image (`e7-operations-engine:rc-38d080fadab8`) was not rebuilt or modified.
2. **Overlay Composition**: Configured via a dedicated overlay file [`docker-compose.pilot.yml`](file:///f:/operations-engine/docker-compose.pilot.yml) leaving canonical [`docker-compose.yml`](file:///f:/operations-engine/docker-compose.yml) untouched.
3. **No Direct Public API Exposure**: The E-7 API container publishes no host ports (`ports: !reset []`); all ingress traffic must pass through the gateway.
4. **Internal Services Protected**: PostgreSQL (`e7-postgres`) and Flowable (`e7-flowable`) have no public host port bindings in the pilot topology.

---

## 3. Rate Limiting Configuration & Specification

Configured in [`docker/pilot-gateway/nginx.conf`](file:///f:/operations-engine/docker/pilot-gateway/nginx.conf):

```nginx
# Rate limiting zone: 10 requests/second per binary client IP
limit_req_zone $binary_remote_addr zone=e7_pilot_api:10m rate=10r/s;
limit_req_status 429;

server {
    listen 80;
    server_name _;

    # Explicit custom 429 handler ensuring Retry-After header and JSON payload
    error_page 429 = @rate_limited;

    location @rate_limited {
        add_header Retry-After "1" always;
        add_header Content-Type application/json always;
        return 429 '{"detail":"Too Many Requests: Rate limit exceeded. Retry after 1 second."}\n';
    }

    location / {
        limit_req zone=e7_pilot_api burst=20 nodelay;

        proxy_pass http://api:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
    }
}
```

### Parameters
* **Rate**: `10r/s` (10 requests per second sustained rate).
* **Burst Capacity**: `burst=20 nodelay` (allows up to 20 immediate burst requests before throttling).
* **Keying**: `$binary_remote_addr` (IPv4: 4 bytes, IPv6: 16 bytes per client IP). Client-provided `X-Forwarded-For` is **not** trusted as the rate-limiting key because no trusted upstream proxy tier is configured locally.
* **Status Code**: `HTTP 429` (`limit_req_status 429;`). The Nginx default `503 Service Unavailable` is explicitly replaced.
* **Headers**: `Retry-After: 1` header always included on rate-limited responses.
* **Payload**: Clean JSON response body (`{"detail":"Too Many Requests: Rate limit exceeded. Retry after 1 second."}`).

---

## 4. Container Image Pinning & Security Audit

* **Selected Image**: `nginx:1.31.7-alpine`
* **Immutable Image Digest**: `sha256:892bb2152464f0c5d8972593a5f5afaf8054552580b699d95f8ff4af08c2d33d`
* **Docker Pull Spec**: `nginx:1.31.7-alpine@sha256:892bb2152464f0c5d8972593a5f5afaf8054552580b699d95f8ff4af08c2d33d`
* **Trivy Image Scan Results**:
  * **Critical Findings**: `0`
  * **High Findings**: `2` (`CVE-2026-77214` in `libexpat`, `CVE-2026-88806` in `libx11` — non-exploitable runtime since Nginx does not parse XML or run X11 in this deployment).
  * **Unresolved Release-Blocking Issues**: `0`
* **Trivy Config Scan Results**:
  * Scanned `docker-compose.pilot.yml` and `docker/pilot-gateway/nginx.conf`: `0` misconfigurations.

---

## 5. Verification Results

Automated verification executed via [`scripts/pilot/verify_gateway_rate_limit.py`](file:///f:/operations-engine/scripts/pilot/verify_gateway_rate_limit.py):

| Verification Step | Target / Method | Observed Result | Status |
|---|---|---|---|
| **Configuration Syntax** | `docker exec e7-pilot-gateway nginx -t` | Syntax is ok, test is successful | **PASS** |
| **Normal Health Request** | `GET http://127.0.0.1:8088/api/v1/health` | `HTTP 200` (`status=ok`) | **PASS** |
| **Root Routing** | `GET http://127.0.0.1:8088/` | `HTTP 200` (`status=online`) | **PASS** |
| **Burst Enforcement** | 60 parallel requests (30 workers) | `HTTP 200`: 20 requests<br>`HTTP 429`: 40 requests<br>`Unexpected 5xx`: 0 | **PASS** |
| **Retry-After Header** | Header check on HTTP 429 response | `Retry-After: 1` present and verified | **PASS** |
| **Post-Burst Recovery** | Request after 2.5s bucket replenishment | `HTTP 200` (`status=ok`) | **PASS** |
| **Log Audit** | `docker logs e7-pilot-gateway` | Rate-limit rejections logged cleanly; zero credential leaks | **PASS** |

---

## 6. How to Modify Thresholds

To adjust pilot rate-limiting parameters:
1. Edit [`docker/pilot-gateway/nginx.conf`](file:///f:/operations-engine/docker/pilot-gateway/nginx.conf):
   * Sustained Rate: Change `rate=10r/s` to desired value (e.g. `rate=50r/s` or `rate=1000r/m`).
   * Burst Capacity: Change `burst=20` to desired allowance.
   * Retry Duration: Update `add_header Retry-After "<seconds>" always;`.
2. Reload Nginx without downtime:
   ```bash
   docker exec e7-pilot-gateway nginx -s reload
   ```

---

## 7. Production Deployment Caveat

> [!IMPORTANT]
> **Production Deployment Requirement**:
> The local Nginx gateway validates and satisfies the **pilot edge-control contract**.
>
> Actual public, internet-facing corporate production deployment still requires external infrastructure configuration by the Cloud Infrastructure team for:
> 1. **TLS / SSL Termination**: Production certificates and HTTPS redirect rules.
> 2. **DNS & Custom Domains**: Corporate routing and vanity domain mapping.
> 3. **Cloud Firewall & WAF**: AWS WAF / Cloudflare DDoS protection and Layer-3/4 security groups.
> 4. **Trusted Proxy Headers**: When deployed behind an application load balancer (ALB / Envoy / Kong), `real_ip_header` and `set_real_ip_from <vpc_subnet>;` must be configured to prevent client IP spoofing.
>
> Consequently, `RATE_LIMITING` remains governed as `BLOCKED_ON_PRODUCTION_GATEWAY_CONFIGURATION` for full production cutover, while `PILOT_GATEWAY_RATE_LIMITING = PASS` for restricted pilot operation.
