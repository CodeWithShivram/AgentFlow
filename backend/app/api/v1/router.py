from fastapi import APIRouter

from app.api.v1 import (
    agents,
    analytics,
    artifacts,
    auth,
    health,
    interventions,
    pricing,
    projects,
    workflows,
    workspaces,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(agents.router)
api_router.include_router(agents.catalog_router)
api_router.include_router(workflows.router)
api_router.include_router(interventions.router)
api_router.include_router(interventions.approvals_router)
api_router.include_router(pricing.router)
api_router.include_router(analytics.router)
api_router.include_router(artifacts.router)
api_router.include_router(projects.router)
