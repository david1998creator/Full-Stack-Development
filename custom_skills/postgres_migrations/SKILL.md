---
name: postgres_migrations
title: "Zero-Downtime Database Migrations"
target_roles: ["Database Engineer"]
description: "Authoring idempotent DDL, explicit ON DELETE actions, and high-performance indexes."
---

### Database Migration Guidelines
- All table creations must be idempotent using `CREATE TABLE IF NOT EXISTS`.
- Every foreign key constraint MUST declare an explicit `ON DELETE CASCADE` or `ON DELETE SET NULL` clause.
- Create B-tree indexes on all foreign key columns and high-cardinality query filters.
- Write sample seed fixtures and migration rollback scripts alongside the schema DDL.
