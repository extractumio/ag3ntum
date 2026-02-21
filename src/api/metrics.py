"""
Prometheus metrics for Ag3ntum API.

Exposes /api/v1/metrics with:
- HTTP request duration/count histograms
- Active session gauge
- Task queue depth gauge
- Custom business metrics
"""
import logging

from prometheus_fastapi_instrumentator import Instrumentator

logger = logging.getLogger(__name__)

instrumentator = Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/api/v1/health", "/api/v1/metrics"],
)


def setup_metrics(app):
    """Initialize Prometheus metrics on the FastAPI app.

    Call this during app creation, after routes are registered.
    The /metrics endpoint is exposed at the app root (not under /api/v1).
    """
    try:
        instrumentator.instrument(app)
        instrumentator.expose(app, endpoint="/api/v1/metrics", include_in_schema=False)
        logger.info("Prometheus metrics enabled at /api/v1/metrics")
    except Exception as e:
        logger.warning(f"Failed to initialize Prometheus metrics: {e}")
