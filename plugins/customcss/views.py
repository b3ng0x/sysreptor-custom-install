import logging

from rest_framework import permissions, status, views
from rest_framework.response import Response
from sysreptor.utils.configuration import configuration, reload_server

log = logging.getLogger(__name__)

MAX_CSS_BYTES = 200 * 1024  # 200KB - generous for hand-written/theme CSS, guards against abuse
# Low-effort stored-XSS guard: this value only ever gets placed inside a <style> element's
# textContent via the DOM API (never string-concatenated into raw HTML - see plugin.js), so a
# real injection isn't actually reachable through normal use. Still reject anything that looks
# like it's trying to break out of a style context, in case some other code path ever renders
# this value less carefully.
FORBIDDEN_SUBSTRINGS = ('</style', '<script', 'javascript:', 'expression(')


class IsSuperuser(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


def validate_css(css: str) -> str | None:
    """Returns an error message, or None if valid."""
    if len(css.encode('utf-8')) > MAX_CSS_BYTES:
        return f'CSS exceeds the {MAX_CSS_BYTES} byte limit'
    lowered = css.lower()
    for bad in FORBIDDEN_SUBSTRINGS:
        if bad in lowered:
            return f'CSS contains a disallowed substring: "{bad}"'
    return None


class CustomCssView(views.APIView):
    permission_classes = [IsSuperuser]

    def get(self, request):
        return Response({
            'css': configuration.CUSTOM_CSS,
            'enabled': configuration.CUSTOM_CSS_ENABLED,
        })

    def post(self, request):
        css = request.data.get('css', '')
        enabled = bool(request.data.get('enabled', False))
        if not isinstance(css, str):
            return Response({'detail': 'css must be a string'}, status=status.HTTP_400_BAD_REQUEST)
        error = validate_css(css)
        if error:
            return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)
        configuration.update({'CUSTOM_CSS': css, 'CUSTOM_CSS_ENABLED': enabled})
        reload_server()  # signal running workers to pick up the change without a container restart
        log.info(f'CustomCSS: saved ({len(css)} chars, enabled={enabled}) by {request.user.username}')
        return Response({'css': css, 'enabled': enabled})
