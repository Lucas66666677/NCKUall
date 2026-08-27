"""
Contract guards for the public, anonymous course and trending endpoints.

The Next.js frontend renders course pages and the home-page trending panel
with no credentials at all: `lib/course-api.ts` server-renders
`/api/courses/{id}`, `AppContext` lists `/api/courses`, and `TrendingPanel`
fetches `/api/analytics/trending` from the browser. Adding an auth gate to any
of them, renaming a response field, or moving a path would break the public
site while every existing test still passed, because those tests all drive the
endpoints as an already-authorised caller or not at all.

These checks are deliberately import-only: they need no database, so they also
run as a standalone release-readiness step ahead of the integration suite.
"""

from __future__ import annotations

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.router import api_router
from app.api.routes import analytics, courses
from app.auth import (
    get_current_user,
    verify_admin_user,
    verify_ncku_user,
    verify_visual_ingestion_user,
)
from app.main import app
from app.schemas import TrendingResourceResponse, TrendingResponse


# Dependencies that reject a request without acceptable credentials. Optional
# ones (`get_optional_user`, `require_developer_scope`) are absent on purpose:
# they return a guest principal instead of raising, so they keep an endpoint
# anonymous-friendly.
AUTH_GATES = {
    get_current_user,
    verify_admin_user,
    verify_ncku_user,
    verify_visual_ingestion_user,
}

PUBLIC_ANONYMOUS_ROUTES = {
    ("GET", "/api/courses"),
    ("GET", "/api/courses/search"),
    ("GET", "/api/courses/filter"),
    ("GET", "/api/courses/{course_id}"),
    ("GET", "/api/analytics/trending"),
}


def _collect_routes() -> dict[tuple[str, str], APIRoute]:
    """Map (method, full path) to the route object for the public routers."""

    assert api_router.prefix == "/api", (
        "The public frontend hard-codes the /api prefix in its fetch URLs."
    )

    routes: dict[tuple[str, str], APIRoute] = {}
    for module in (courses, analytics):
        for route in module.router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                routes[(method, f"{api_router.prefix}{route.path}")] = route
    return routes


def _gates_in(dependant: Dependant) -> set[str]:
    """Names of auth-gating dependencies anywhere in a route's dependency tree."""

    found: set[str] = set()
    for sub_dependant in dependant.dependencies:
        if sub_dependant.call in AUTH_GATES:
            found.add(sub_dependant.call.__name__)
        found |= _gates_in(sub_dependant)
    return found


def test_public_anonymous_routes_are_still_mounted() -> None:
    """A rename or prefix change would 404 the public site, not fail loudly."""

    routes = _collect_routes()
    missing = sorted(PUBLIC_ANONYMOUS_ROUTES - set(routes))
    assert not missing, f"Public endpoints are no longer mounted: {missing}"


def test_public_anonymous_routes_require_no_credentials() -> None:
    """No public endpoint may grow a dependency that rejects a guest."""

    routes = _collect_routes()
    gated = {
        f"{method} {path}": sorted(gates)
        for method, path in sorted(PUBLIC_ANONYMOUS_ROUTES)
        for gates in [_gates_in(routes[(method, path)].dependant)]
        if gates
    }
    assert not gated, f"Public endpoints now demand credentials: {gated}"


def test_no_global_dependency_gates_the_public_routers() -> None:
    """Router-level dependencies bypass the per-route check above."""

    for label, router in (
        ("app", app.router),
        ("api_router", api_router),
        ("courses", courses.router),
        ("analytics", analytics.router),
    ):
        gates = {
            dependency.dependency.__name__
            for dependency in router.dependencies
            if dependency.dependency in AUTH_GATES
        }
        assert not gates, f"{label} applies auth gates to every route: {sorted(gates)}"


def test_course_review_post_is_still_gated() -> None:
    """Guards the guard: prove the gate check can actually detect a gate."""

    route = _collect_routes()[("POST", "/api/courses/{course_id}/reviews")]
    assert "verify_ncku_user" in _gates_in(route.dependant)


def test_trending_payload_shape_is_frozen() -> None:
    """
    Trending is an anonymous aggregate over view logs, so its field set is
    pinned in both directions: a dropped field breaks `TrendingPanel`, and an
    added one risks widening an endpoint that promises to carry no visitor
    identifiers.
    """

    assert set(TrendingResponse.model_fields) == {
        "window_hours",
        "courses",
        "labs",
        "events",
    }
    assert set(TrendingResourceResponse.model_fields) == {
        "resource_type",
        "resource_id",
        "title",
        "subtitle",
        "interaction_count",
        "href",
    }


def test_public_course_payloads_keep_the_fields_the_frontend_reads() -> None:
    """Additive changes stay fine; removing or renaming a consumed field does not."""

    consumed = {
        courses.CourseSearchResponse: {"query", "count", "results"},
        courses.CourseFilterResponse: {"count", "results"},
        courses.CourseSearchItem: {
            "id",
            "course_code",
            "title_zh",
            "title_en",
            "instructor_name",
            "department_id",
            "credits",
            "required_for_major",
            "tags",
            "href",
        },
        # frontend/lib/course-api.ts CourseDetail
        courses.CourseResponse: {
            "id",
            "course_code",
            "title_zh",
            "title_en",
            "instructor_name",
            "credits",
            "required_for_major",
            "description",
            "syllabus_url",
            "department",
        },
    }
    for model, expected in consumed.items():
        missing = expected - set(model.model_fields)
        assert not missing, f"{model.__name__} dropped public fields: {sorted(missing)}"
