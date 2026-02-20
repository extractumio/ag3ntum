"""
Production frontend server.

Serves the pre-built React bundle from /web_dist with SPA client-side routing.
Used by the web container in production mode (AG3NTUM_MODE=prod).
In development mode, the Vite dev server is used instead.

Routing: if the request path matches a static file under the dist directory,
serve it. Otherwise, serve index.html for React Router client-side routing.

Set WEB_DIST_DIR env var to override the dist directory (used in tests).
"""
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Route


def _get_dist_dir() -> Path:
    """Return the web distribution directory path."""
    return Path(os.environ.get("WEB_DIST_DIR", "/web_dist"))


async def serve(request: Request) -> Response:
    """Serve static files with SPA fallback to index.html."""
    dist = _get_dist_dir()
    path = request.path_params.get("path", "")

    if path:
        file_path = (dist / path).resolve()
        # Serve the file if it exists and is within the dist directory
        if file_path.is_file() and str(file_path).startswith(str(dist.resolve())):
            return FileResponse(str(file_path))

    # SPA fallback: serve index.html for all unmatched paths
    index = dist / "index.html"
    if not index.is_file():
        return Response(
            "Frontend not built. Run: ./run.sh build",
            status_code=503,
            media_type="text/plain",
        )
    return FileResponse(str(index), media_type="text/html")


app = Starlette(routes=[
    Route("/", endpoint=serve),
    Route("/{path:path}", endpoint=serve),
])
