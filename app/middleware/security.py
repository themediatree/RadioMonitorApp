"""
app/middleware/security.py

Security headers middleware — adds standard HTTP security headers to every response.
These protect against clickjacking, MIME sniffing, and information leakage.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Don't send referrer to external sites
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Force HTTPS for 1 year (only effective over HTTPS)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

        # Restrict browser feature access
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), "
            "payment=(), usb=(), magnetometer=(), gyroscope=()"
        )

        # Basic CSP — tightened in production when assets are finalised
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://*.tile.openstreetmap.org https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.de https://server.arcgisonline.com; "
            "connect-src 'self' https: https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org https://server.arcgisonline.com; "
            #"connect-src 'self' https://*.basemaps.cartocdn.com https://*.tile.openstreetmap.org https://server.arcgisonline.com https://cdn.freevisiontv.co.za https://*.iono.fm https://*.streamtheworld.com https://*.live.streamtheworld.com https://*.radioca.st https://*.voscast.com; "
            "media-src * data: blob:; "
            "frame-ancestors 'none';"
        )

        # Remove server identification
        try:
            del response.headers["server"]
        except KeyError:
            pass
        try:
            del response.headers["x-powered-by"]
        except KeyError:
            pass

        return response
