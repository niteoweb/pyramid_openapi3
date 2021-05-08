"""Configure pyramid_openapi3 addon."""

from .exceptions import extract_errors
from .exceptions import MissingEndpointsError
from .exceptions import RequestValidationError
from .exceptions import ResponseValidationError
from .wrappers import PyramidOpenAPIRequestFactory
from openapi_core import create_spec
from openapi_core.schema.specs.models import Spec
from openapi_core.validation.exceptions import InvalidSecurity
from openapi_core.validation.request.validators import RequestValidator
from openapi_core.validation.response.validators import ResponseValidator
from openapi_spec_validator import validate_spec
from openapi_spec_validator.schemas import read_yaml_file
from pathlib import Path
from pyramid.config import Configurator
from pyramid.config import PHASE0_CONFIG
from pyramid.config import PHASE1_CONFIG
from pyramid.config.views import ViewDeriverInfo
from pyramid.events import ApplicationCreated
from pyramid.exceptions import ConfigurationError
from pyramid.httpexceptions import exception_response
from pyramid.path import AssetResolver
from pyramid.request import Request
from pyramid.response import FileResponse
from pyramid.response import Response
from pyramid.security import NO_PERMISSION_REQUIRED
from pyramid.settings import asbool
from pyramid.tweens import EXCVIEW
from string import Template
from urllib.parse import urlparse

import hupper
import logging
import typing as t

logger = logging.getLogger(__name__)

APIS = []


def includeme(config: Configurator) -> None:
    """Pyramid knob."""
    config.add_request_method(openapi_validated, name="openapi_validated", reify=True)
    config.add_view_deriver(openapi_view)
    config.add_directive("pyramid_openapi3_add_formatter", add_formatter)
    config.add_directive("pyramid_openapi3_add_explorer", add_explorer_view)
    config.add_directive("pyramid_openapi3_spec", add_spec_view)
    config.add_directive("pyramid_openapi3_spec_directory", add_spec_view_directory)
    config.add_directive("pyramid_openapi3_register_routes", register_routes)
    config.add_tween("pyramid_openapi3.tween.response_tween_factory", over=EXCVIEW)
    config.add_subscriber(check_all_routes, ApplicationCreated)

    if not config.registry.settings.get(  # pragma: no branch
        "pyramid_openapi3_extract_errors"
    ):
        config.registry.settings["pyramid_openapi3_extract_errors"] = extract_errors

    config.add_exception_view(
        view=openapi_validation_error, context=RequestValidationError, renderer="json"
    )

    config.add_exception_view(
        view=openapi_validation_error, context=ResponseValidationError, renderer="json"
    )


def openapi_validated(request: Request) -> dict:
    """Get validated parameters."""
    # Validate request and attach all findings for view to introspect
    validate_request = asbool(
        request.registry.settings.get(
            "pyramid_openapi3.enable_request_validation", True
        )
    )
    validate_response = asbool(
        request.registry.settings.get(
            "pyramid_openapi3.enable_response_validation", True
        )
    )
    request.environ["pyramid_openapi3.validate_response"] = validate_response
    gsettings = settings = request.registry.settings["pyramid_openapi3"]
    route_settings = gsettings.get("routes")
    if route_settings and request.matched_route.name in route_settings:
        settings = request.registry.settings[route_settings[request.matched_route.name]]

    if validate_request:
        request.environ["pyramid_openapi3.validate_request"] = True
        openapi_request = PyramidOpenAPIRequestFactory.create(request)
        validated = settings["request_validator"].validate(openapi_request)
        return validated


Context = t.TypeVar("Context")
View = t.Callable[[Context, Request], Response]


def openapi_view(view: View, info: ViewDeriverInfo) -> View:
    """View deriver that takes care of request/response validation.

    If `openapi=True` is passed to `@view_config`, this decorator will:

    - validate request and submit results into request.openapi_validated
    - Only request is validated here. The response is validated inside a tween,
    so that other tweens can intercept the response, and only the final
    response is validated against the openapi spec.
    """
    if info.options.get("openapi"):

        def wrapper_view(context: Context, request: Request) -> Response:
            validate_request = asbool(
                request.registry.settings.get(
                    "pyramid_openapi3.enable_request_validation", True
                )
            )
            if validate_request and request.openapi_validated.errors:
                raise RequestValidationError(errors=request.openapi_validated.errors)

            # Do the view
            return view(context, request)

        return wrapper_view
    return view


openapi_view.options = ("openapi",)  # type: ignore


def add_explorer_view(
    config: Configurator,
    route: str = "/docs/",
    route_name: str = "pyramid_openapi3.explorer",
    template: str = "static/index.html",
    ui_version: str = "3.17.1",
    permission: str = NO_PERMISSION_REQUIRED,
    apiname: str = "pyramid_openapi3",
) -> None:
    """Serve Swagger UI at `route` url path.

    :param route: URL path where to serve
    :param route_name: Route name that's being added
    :param template: Dotted path to the html template that renders Swagger UI response
    :param ui_version: Swagger UI version string
    :param permission: Permission for the explorer view
    """

    def register() -> None:
        resolved_template = AssetResolver().resolve(template)

        def explorer_view(request: Request) -> Response:
            settings = config.registry.settings
            if settings.get(apiname) is None:
                raise ConfigurationError(
                    "You need to call config.pyramid_openapi3_spec for the explorer "
                    "to work."
                )
            with open(resolved_template.abspath()) as f:
                template = Template(f.read())
                html = template.safe_substitute(
                    ui_version=ui_version,
                    spec_url=request.route_url(settings[apiname]["spec_route_name"]),
                )
            return Response(html)

        config.add_route(route_name, route)
        config.add_view(
            route_name=route_name, permission=permission, view=explorer_view
        )

    config.action((f"{apiname}_add_explorer",), register, order=PHASE0_CONFIG)


def add_formatter(config: Configurator, name: str, func: t.Callable) -> None:
    """Add support for configuring formatters."""
    config.registry.settings.setdefault("pyramid_openapi3_formatters", {})
    reg = config.registry.settings["pyramid_openapi3_formatters"]
    reg[name] = func


def add_spec_view(
    config: Configurator,
    filepath: str,
    route: str = "/openapi.yaml",
    route_name: str = "pyramid_openapi3.spec",
    permission: str = NO_PERMISSION_REQUIRED,
    apiname: str = "pyramid_openapi3",
) -> None:
    """Serve and register OpenApi 3.0 specification file.

    :param filepath: absolute/relative path to the specification file
    :param route: URL path where to serve specification file
    :param route_name: Route name under which specification file will be served
    :param permission: Permission for the spec view
    """

    def register() -> None:
        settings = config.registry.settings.get(apiname)
        if settings and settings.get("spec") is not None:
            raise ConfigurationError(
                "Spec has already been configured. You may only call "
                "pyramid_openapi3_spec or pyramid_openapi3_spec_directory once"
            )

        if hupper.is_active():  # pragma: no cover
            hupper.get_reloader().watch_files([filepath])
        spec_dict = read_yaml_file(filepath)

        validate_spec(spec_dict)
        spec = create_spec(spec_dict)

        def spec_view(request: Request) -> FileResponse:
            return FileResponse(filepath, request=request, content_type="text/yaml")

        config.add_route(route_name, route)
        config.add_view(route_name=route_name, permission=permission, view=spec_view)

        custom_formatters = config.registry.settings.get("pyramid_openapi3_formatters")

        config.registry.settings[apiname] = {
            "filepath": filepath,
            "spec_route_name": route_name,
            "spec": spec,
            "request_validator": RequestValidator(
                spec, custom_formatters=custom_formatters
            ),
            "response_validator": ResponseValidator(
                spec, custom_formatters=custom_formatters
            ),
        }
        APIS.append(apiname)

    config.action((f"{apiname}_spec",), register, order=PHASE0_CONFIG)


def add_spec_view_directory(
    config: Configurator,
    filepath: str,
    route: str = "/spec",
    route_name: str = "pyramid_openapi3.spec",
) -> None:
    """Serve and register OpenApi 3.0 specification directory.

    :param filepath: absolute/relative path to the root specification file
    :param route: URL path where to serve specification file
    :param route_name: Route name under which specification file will be served
    """

    def register() -> None:
        settings = config.registry.settings.get("pyramid_openapi3")
        if settings and settings.get("spec") is not None:
            raise ConfigurationError(
                "Spec has already been configured. You may only call "
                "pyramid_openapi3_spec or pyramid_openapi3_spec_directory once"
            )
        if route.endswith((".yaml", ".yml", ".json")):
            raise ConfigurationError(
                "Having route be a filename is not allowed when using a spec directory"
            )

        path = Path(filepath).resolve()
        if hupper.is_active():  # pragma: no cover
            hupper.get_reloader().watch_files(list(path.parent.iterdir()))

        spec_dict = read_yaml_file(path)
        spec_url = path.as_uri()
        validate_spec(spec_dict, spec_url=spec_url)
        spec = create_spec(spec_dict, spec_url=spec_url)

        config.add_static_view(route, str(path.parent))
        config.add_route(route_name, f"{route}/{path.name}")

        custom_formatters = config.registry.settings.get("pyramid_openapi3_formatters")

        config.registry.settings["pyramid_openapi3"] = {
            "filepath": filepath,
            "spec_route_name": route_name,
            "spec": spec,
            "request_validator": RequestValidator(
                spec, custom_formatters=custom_formatters
            ),
            "response_validator": ResponseValidator(
                spec, custom_formatters=custom_formatters
            ),
        }

    config.action(("pyramid_openapi3_spec",), register, order=PHASE0_CONFIG)


def register_routes(
    config: Configurator,
    route_name_ext: str = "x-pyramid-route-name",
    root_factory_ext: str = "x-pyramid-root-factory",
    apiname: str = "pyramid_openapi3",
) -> None:
    """Register routes to app from OpenApi 3.0 specification.

    :param route_name_ext: Extension's key for using a ``route_name`` argument
    :param root_factory_ext: Extension's key for using a ``factory`` argument
    """

    def action() -> None:
        spec = config.registry.settings[apiname]["spec"]
        for pattern, path_item in spec.paths.items():
            route_name = path_item.extensions.get(route_name_ext)
            if route_name:
                root_factory = path_item.extensions.get(root_factory_ext)
                config.add_route(
                    route_name.value,
                    pattern=pattern,
                    factory=root_factory.value if root_factory else None,
                )

    config.action(("pyramid_openapi3_register_routes",), action, order=PHASE1_CONFIG)


def openapi_validation_error(
    context: t.Union[RequestValidationError, ResponseValidationError], request: Request
) -> Response:
    """Render any validation errors as JSON."""
    if isinstance(context, RequestValidationError):
        logger.warning(context)
    if isinstance(context, ResponseValidationError):
        logger.error(context)

    extract_errors = request.registry.settings["pyramid_openapi3_extract_errors"]
    errors = list(extract_errors(request, context.errors))

    # If validation failed for request, it is user's fault (-> 400), but if
    # validation failed for response, it is our fault (-> 500)
    if isinstance(context, RequestValidationError):
        status_code = 400
        for error in context.errors:
            if isinstance(error, InvalidSecurity):
                status_code = 401

    if isinstance(context, ResponseValidationError):
        status_code = 500

    return exception_response(status_code, json_body=errors)


def check_all_routes(event: ApplicationCreated):
    """Assert all endpoints in the spec are registered as routes.

    Listen for ApplicationCreated event and assert all endpoints defined in
    the API spec have been registered as Pyramid routes.
    """

    app = event.app
    settings = app.registry.settings
    for name in APIS:  # pragma: no branch
        openapi_settings = settings.get(name)
        if not openapi_settings:
            # pyramid_openapi3 not configured?
            logger.warning(
                "pyramid_openapi3 settings not found. "
                "Did you forget to call config.pyramid_openapi3_spec?"
            )
            return

        if not settings.get("pyramid_openapi3.enable_endpoint_validation", True):
            logger.info("Endpoint validation against specification is disabled")
            return

        prefixes = _get_server_prefixes(openapi_settings["spec"])

        def remove_prefixes(path):
            path = f"/{path}" if not path.startswith("/") else path
            for prefix in prefixes:
                if path.startswith(prefix):
                    prefix_length = len(prefix)
                    return path[prefix_length:]
            return path

        paths = list(openapi_settings["spec"].paths.keys())
        routes = [
            remove_prefixes(route.path) for route in app.routes_mapper.routes.values()
        ]

        missing = [r for r in paths if r not in routes]
        if missing:
            raise MissingEndpointsError(missing)

        settings.setdefault("pyramid_openapi3", {})
        settings["pyramid_openapi3"].setdefault("routes", {})

        # It is possible to have multiple `add_route` for a single path
        # (due to request_method predicates). So loop through each route
        # to create a lookup of route_name -> api_name
        for route_name, route in app.routes_mapper.routes.items():
            if remove_prefixes(route.path) in paths:
                settings["pyramid_openapi3"]["routes"][route_name] = name


def _get_server_prefixes(spec: Spec) -> t.List[str]:
    """Build a set of possible route prefixes from the api spec.

    Api routes may optionally be prefixed using servers (e.g: `/api/v1`).
    See: https://swagger.io/docs/specification/api-host-and-base-path/
    """
    prefixes = []
    for server in spec.servers:
        path = urlparse(server.url).path
        path = f"/{path}" if not path.startswith("/") else path
        if path != "/":
            prefixes.append(path)
    return prefixes
