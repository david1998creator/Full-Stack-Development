---
name: fastapi_production
title: "FastAPI Production Architecture"
target_roles: ["Developer"]
description: "Best practices for clean FastAPI routers, Pydantic v2 schemas, and dependency injection."
---

### FastAPI Production Architecture Guidelines
- Structure API endpoints using `APIRouter` under `src/api/v1/`.
- Validate all incoming request payloads with strict Pydantic v2 `BaseModel` schemas.
- Implement explicit health check endpoints at `GET /health` returning `{"status": "healthy"}`.
- Use dependency injection (`Depends`) for database sessions and configuration parameters.
- Handle exceptions with custom HTTPException handlers returning clear JSON error structures.
