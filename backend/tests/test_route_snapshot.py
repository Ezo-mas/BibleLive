"""Pin routes so the M3 route move can be proven identical; change the list only on purpose."""

import re
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

from core.server.auth import verify_operator_token

EXPECTED = [
    ("/", ("GET",), False),
    ("/api/approve", ("POST",), True),
    ("/api/bible-version", ("GET",), True),
    ("/api/bible-version", ("POST",), True),
    ("/api/books", ("GET",), True),
    ("/api/clear", ("POST",), True),
    ("/api/clear-pending", ("POST",), True),
    ("/api/idle-screen", ("DELETE",), True),
    ("/api/idle-screen", ("GET",), True),
    ("/api/idle-screen", ("POST",), True),
    ("/api/idle-screen/fit", ("PUT",), True),
    ("/api/idle-screen/image", ("GET",), False),
    ("/api/language", ("GET",), True),
    ("/api/language", ("POST",), True),
    ("/api/lookup", ("GET",), True),
    ("/api/pipeline/heartbeat", ("POST",), True),
    ("/api/pipeline/status", ("GET",), True),
    ("/api/reference", ("POST",), True),
    ("/api/state", ("GET",), True),
    ("/api/transcript", ("POST",), True),
    ("/api/ui-language", ("GET",), False),
    ("/display", ("GET",), False),
    ("/docs", ("GET",), False),
    ("/docs/oauth2-redirect", ("GET",), False),
    ("/healthz", ("GET",), False),
    ("/openapi.json", ("GET",), False),
    ("/operator", ("GET",), False),
    ("/readyz", ("GET",), False),
    ("/redoc", ("GET",), False),
    ("/ws/{client_type}", "WEBSOCKET", False),
]

# Map actual routes to their documented entries and explain the intentional differences.
EXCEPTIONS = {
    ("/operator", ("GET",), False): (
        {("GET /operator", True)},
        "The public HTML asks for a token used by its API and WebSocket calls.",
    ),
    ("/ws/{client_type}", "WEBSOCKET", False): (
        {("GET /ws/display", False), ("GET /ws/operator", True)},
        "Other client types close with 4000 before accept; with auth enabled, the operator "
        "token is checked in the first message.",
    ),
}


def _protected(dependant):
    return dependant.call is verify_operator_token or any(
        _protected(child) for child in dependant.dependencies
    )


def _flatten(routes):
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts is None:
            yield route, route
        else:
            for ctx in contexts():
                yield ctx.original_route, (ctx.starlette_route or ctx)


def _routes(app):
    # FastAPI keeps frontend() routes outside app.routes, so this walker cannot see them.
    low_priority = getattr(app.router, "_iter_low_priority_routes", None)
    assert low_priority is None or not list(low_priority()), (
        "frontend() routes are not covered by this snapshot"
    )
    routes = []
    docs = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    for original, route in _flatten(app.routes):
        if isinstance(original, APIRoute):
            routes.append((route.path, tuple(sorted(route.methods)), _protected(route.dependant)))
        elif isinstance(original, APIWebSocketRoute):
            routes.append((route.path, "WEBSOCKET", _protected(route.dependant)))
        elif isinstance(original, Route) and route.path in docs:
            # Starlette adds HEAD implicitly to GET routes.
            methods = route.methods - {"HEAD"} if "GET" in route.methods else route.methods
            routes.append((route.path, tuple(sorted(methods)), False))
        elif isinstance(original, Mount) and route.path == "/static":
            # Generated CSS/JS bundle, mounted only when the frontend has been built.
            continue
        else:
            raise AssertionError(f"Unrecognised route: {route!r}")
    return sorted(routes)


def test_included_router_routes():
    app = FastAPI(openapi_url=None)
    included = APIRouter()
    protected = APIRouter(dependencies=[Depends(verify_operator_token)])
    nested = APIRouter()

    @app.get("/direct")
    def direct():
        pass

    @included.get("/included")
    def included_route():
        pass

    @protected.get("/router")
    def router_route():
        pass

    @nested.get("/parameter")
    def parameter_route(token=Depends(verify_operator_token)):
        pass

    @nested.get("/decorator", dependencies=[Depends(verify_operator_token)])
    def decorator_route():
        pass

    @nested.websocket("/socket")
    async def socket_route(websocket: WebSocket):
        pass

    included.include_router(nested, prefix="/nested")
    app.include_router(included, prefix="/included", dependencies=[Depends(verify_operator_token)])
    app.include_router(protected, prefix="/protected")
    # Also include nested separately so parameter and decorator dependencies are independently tested.
    app.include_router(nested, prefix="/alone")
    assert _routes(app) == [
        ("/alone/decorator", ("GET",), True),
        ("/alone/parameter", ("GET",), True),
        ("/alone/socket", "WEBSOCKET", False),
        ("/direct", ("GET",), False),
        ("/included/included", ("GET",), True),
        ("/included/nested/decorator", ("GET",), True),
        ("/included/nested/parameter", ("GET",), True),
        ("/included/nested/socket", "WEBSOCKET", True),
        ("/protected/router", ("GET",), True),
    ]


def test_unrecognised_route_fails():
    app = FastAPI()
    app.router.routes.append(Route("/unexpected", lambda request: None))
    with pytest.raises(AssertionError, match="/unexpected"):
        _routes(app)


def test_route_snapshot(app):
    assert _routes(app) == EXPECTED


def test_security_endpoint_lists_match_routes(app):
    security = (Path(__file__).resolve().parents[2] / "SECURITY.md").read_text(encoding="utf-8")
    documented = set()
    for heading, protected in (("Public", False), ("Protected", True)):
        section = security.split(f"### {heading} endpoints", 1)[1].split("\n##", 1)[0]
        entries = re.findall(r"^- `([A-Z]+ /[^`]*)`", section, re.MULTILINE)
        assert len(re.findall(r"^- ", section, re.MULTILINE)) == len(entries)
        documented.update((entry, protected) for entry in entries)

    actual = set()
    for route in _routes(app):
        if route in EXCEPTIONS:
            actual.update(EXCEPTIONS[route][0])
        else:
            path, methods, protected = route
            assert methods != "WEBSOCKET", f"Undocumented WebSocket route: {path}"
            actual.update((f"{method} {path}", protected) for method in methods)
    assert actual == documented
