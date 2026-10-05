-- ==============================================================================
-- IR INFOTECH E-7 OPERATIONS ENGINE - LOCAL POSTGRESQL INITIALIZATION
-- Part of Phase 1 Foundation Task 1J.1.
-- Creates dedicated database for Flowable OSS workflow runtime.
-- Guarantees total database isolation: Flowable Liquibase ACT_* tables are
-- completely segregated from canonical E-7 operational state (Base.metadata).
-- ==============================================================================

CREATE DATABASE flowable_db;
