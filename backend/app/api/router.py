from fastapi import APIRouter

from app.api.routes import (
    admin,
    analytics,
    careers,
    chat,
    courses,
    departments,
    events,
    life,
    recommendations,
    search,
    visual_ingestion,
)


api_router = APIRouter(prefix="/api")

api_router.include_router(courses.router)
api_router.include_router(departments.router)
api_router.include_router(careers.router)
api_router.include_router(events.router)
api_router.include_router(life.router)
api_router.include_router(chat.router)
api_router.include_router(analytics.router)
api_router.include_router(recommendations.router)
api_router.include_router(search.router)
api_router.include_router(visual_ingestion.router)
api_router.include_router(admin.router)
