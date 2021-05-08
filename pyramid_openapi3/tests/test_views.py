"""Tests views."""

from dataclasses import dataclass
from openapi_core.shortcuts import RequestValidator
from openapi_core.shortcuts import ResponseValidator
from pyramid.exceptions import ConfigurationError
from pyramid.interfaces import Interface
from pyramid.interfaces import IRouteRequest
from pyramid.interfaces import IRoutesMapper
from pyramid.interfaces import IView
from pyramid.interfaces import IViewClassifier
from pyramid.request import apply_request_extensions
from pyramid.router import Router
from pyramid.testing import DummyRequest
from pyramid.testing import testConfig
from pyramid_openapi3.exceptions import RequestValidationError

import os
import pytest
import tempfile


class DummyStartResponse(object):
    def __call__(self, status, headerlist) -> None:
        """WSGI start_response protocol."""
        self.status = status
        self.headerlist = headerlist


MINIMAL_DOCUMENT = b"""
    openapi: "3.0.0"
    info:
      version: "1.0.0"
      title: Foo API
    paths:
      /foo:
        get:
          responses:
            200:
              description: A foo
"""

ALTERNATE_DOCUMENT = b"""
    openapi: "3.0.0"
    info:
      version: "1.0.0"
      title: Bar API
    paths:
      /bar:
        get:
          responses:
            200:
              description: A bar
"""

SPLIT_DOCUMENT = b"""
    openapi: "3.0.0"
    info:
      version: "1.0.0"
      title: Foo API
    paths:
      /foo:
        $ref: "paths.yaml#/foo"
"""

SPLIT_DOCUMENT_PATHS = b"""
    foo:
      get:
        responses:
          200:
            description: A foo
"""


def test_add_spec_view() -> None:
    """Test registration of a view that serves the openapi document."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.NamedTemporaryFile() as document:
            document.write(MINIMAL_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo.yaml", route_name="foo_api_spec"
            )

            # assert settings
            openapi_settings = config.registry.settings["pyramid_openapi3"]
            assert openapi_settings["filepath"] == document.name
            assert openapi_settings["spec_route_name"] == "foo_api_spec"
            assert openapi_settings["spec"].info.title == "Foo API"
            assert isinstance(openapi_settings["request_validator"], RequestValidator)
            assert isinstance(openapi_settings["response_validator"], ResponseValidator)

            # assert route
            mapper = config.registry.getUtility(IRoutesMapper)
            routes = mapper.get_routes()
            assert routes[0].name == "foo_api_spec"
            assert routes[0].path == "/foo.yaml"

            # assert view
            request = config.registry.queryUtility(IRouteRequest, name="foo_api_spec")
            view = config.registry.adapters.registered(
                (IViewClassifier, request, Interface), IView, name=""
            )
            assert view(request=None, context=None).body == MINIMAL_DOCUMENT


def test_add_spec_view_already_defined() -> None:
    """Test that creating a spec more than once raises an Exception."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.TemporaryDirectory() as directory:
            spec_name = os.path.join(directory, "openapi.yaml")
            spec_paths_name = os.path.join(directory, "paths.yaml")
            with open(spec_name, "wb") as f:
                f.write(SPLIT_DOCUMENT)
            with open(spec_paths_name, "wb") as f:
                f.write(SPLIT_DOCUMENT_PATHS)

            config.pyramid_openapi3_spec_directory(
                spec_name, route="/foo", route_name="foo_api_spec"
            )

            with tempfile.NamedTemporaryFile() as document:
                document.write(MINIMAL_DOCUMENT)
                document.seek(0)

                with pytest.raises(
                    ConfigurationError,
                    match=(
                        "Spec has already been configured. You may only call "
                        "pyramid_openapi3_spec or pyramid_openapi3_spec_directory once"
                    ),
                ):
                    config.pyramid_openapi3_spec(
                        document.name, route="/foo.yaml", route_name="foo_api_spec"
                    )


def test_add_spec_view_directory() -> None:
    """Test registration of a view that serves the openapi document."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.TemporaryDirectory() as directory:
            spec_name = os.path.join(directory, "openapi.yaml")
            spec_paths_name = os.path.join(directory, "paths.yaml")
            with open(spec_name, "wb") as f:
                f.write(SPLIT_DOCUMENT)
            with open(spec_paths_name, "wb") as f:
                f.write(SPLIT_DOCUMENT_PATHS)

            config.pyramid_openapi3_spec_directory(
                spec_name, route="/foo", route_name="foo_api_spec"
            )

            # assert settings
            openapi_settings = config.registry.settings["pyramid_openapi3"]
            assert openapi_settings["filepath"] == spec_name
            assert openapi_settings["spec_route_name"] == "foo_api_spec"
            assert openapi_settings["spec"].info.title == "Foo API"
            assert "get" in openapi_settings["spec"].paths["/foo"].operations
            assert isinstance(openapi_settings["request_validator"], RequestValidator)
            assert isinstance(openapi_settings["response_validator"], ResponseValidator)

            # assert route
            # routes[0] is the static view, routes[1] is the route
            mapper = config.registry.getUtility(IRoutesMapper)
            routes = mapper.get_routes()
            assert routes[0].name == "__/foo/"
            assert routes[0].path == "/foo/*subpath"
            assert routes[1].name == "foo_api_spec"
            assert routes[1].path == "/foo/openapi.yaml"

            # assert view
            route_request = config.registry.queryUtility(
                IRouteRequest, name="foo_api_spec"
            )
            static_request = config.registry.queryUtility(IRouteRequest, name="__/foo/")
            view = config.registry.adapters.registered(
                (IViewClassifier, static_request, Interface), IView, name=""
            )
            assert route_request is not None
            assert static_request is not None
            assert view is not None

            # assert router
            router = Router(config.registry)
            response = router({"PATH_INFO": "/foo/openapi.yaml"}, DummyStartResponse())
            assert next(response) == SPLIT_DOCUMENT
            response = router({"PATH_INFO": "/foo/paths.yaml"}, DummyStartResponse())
            assert next(response) == SPLIT_DOCUMENT_PATHS


def test_add_spec_view_directory_already_defined() -> None:
    """Test that creating a spec more than once raises an Exception."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.NamedTemporaryFile() as document:
            document.write(MINIMAL_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo", route_name="foo_api_spec"
            )

            with tempfile.TemporaryDirectory() as directory:
                spec_name = os.path.join(directory, "openapi.yaml")
                spec_paths_name = os.path.join(directory, "paths.yaml")
                with open(spec_name, "wb") as f:
                    f.write(SPLIT_DOCUMENT)
                with open(spec_paths_name, "wb") as f:
                    f.write(SPLIT_DOCUMENT_PATHS)

                with pytest.raises(
                    ConfigurationError,
                    match=(
                        "Spec has already been configured. You may only call "
                        "pyramid_openapi3_spec or pyramid_openapi3_spec_directory once"
                    ),
                ):
                    config.pyramid_openapi3_spec_directory(
                        spec_name, route="/foo.yaml", route_name="foo_api_spec"
                    )


def test_add_spec_view_directory_invalid_route() -> None:
    """Test that creating a spec directory with a filename route raises an Exception."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.TemporaryDirectory() as directory:
            spec_name = os.path.join(directory, "openapi.yaml")
            spec_paths_name = os.path.join(directory, "paths.yaml")
            with open(spec_name, "wb") as f:
                f.write(SPLIT_DOCUMENT)
            with open(spec_paths_name, "wb") as f:
                f.write(SPLIT_DOCUMENT_PATHS)

            with pytest.raises(
                ConfigurationError,
                match=(
                    "Having route be a filename is not allowed when using a "
                    "spec directory"
                ),
            ):
                config.pyramid_openapi3_spec_directory(
                    spec_name, route="/foo.yaml", route_name="foo_api_spec"
                )


def test_add_explorer_view() -> None:
    """Test registration of a view serving the Swagger UI."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.NamedTemporaryFile() as document:
            document.write(MINIMAL_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo.yaml", route_name="foo_api_spec"
            )

        config.pyramid_openapi3_add_explorer()
        request = config.registry.queryUtility(
            IRouteRequest, name="pyramid_openapi3.explorer"
        )
        view = config.registry.adapters.registered(
            (IViewClassifier, request, Interface), IView, name=""
        )
        response = view(request=DummyRequest(config=config), context=None)
        assert b"<title>Swagger UI</title>" in response.body


def test_add_multiple_explorer_views() -> None:
    """Test registration of multiple viewa serving different Swagger UI."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.NamedTemporaryFile() as document:
            document.write(MINIMAL_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name,
                route="/foo.yaml",
                route_name="foo_api_spec",
                apiname="foo_api",
            )
            config.pyramid_openapi3_add_explorer(
                route="/foo_api/v1/",
                route_name="foo_api_explorer",
                apiname="foo_api",
            )

        with tempfile.NamedTemporaryFile() as document:
            document.write(ALTERNATE_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name,
                route="/bar.yaml",
                route_name="bar_api_spec",
                apiname="bar_api",
            )
            config.pyramid_openapi3_add_explorer(
                route="/bar_api/v1/",
                route_name="bar_api_explorer",
                apiname="bar_api",
            )

        request = config.registry.queryUtility(IRouteRequest, name="foo_api_explorer")
        view = config.registry.adapters.registered(
            (IViewClassifier, request, Interface), IView, name=""
        )
        response = view(request=DummyRequest(config=config), context=None)
        assert b"<title>Swagger UI</title>" in response.body
        assert b"http://example.com/foo.yaml" in response.body

        request = config.registry.queryUtility(IRouteRequest, name="bar_api_explorer")
        view = config.registry.adapters.registered(
            (IViewClassifier, request, Interface), IView, name=""
        )
        response = view(request=DummyRequest(config=config), context=None)
        assert b"<title>Swagger UI</title>" in response.body
        assert b"http://example.com/bar.yaml" in response.body


def test_explorer_view_missing_spec() -> None:
    """Test graceful failure if explorer view is not registered."""
    with testConfig() as config:
        config.include("pyramid_openapi3")
        config.pyramid_openapi3_add_explorer()

        request = config.registry.queryUtility(
            IRouteRequest, name="pyramid_openapi3.explorer"
        )
        view = config.registry.adapters.registered(
            (IViewClassifier, request, Interface), IView, name=""
        )
        with pytest.raises(
            ConfigurationError,
            match=(
                "You need to call config.pyramid_openapi3_spec for the "
                "explorer to work."
            ),
        ):
            view(request=DummyRequest(config=config), context=None)


@dataclass
class DummyRoute:
    name: str
    pattern: str


def test_openapi_view() -> None:
    """Test registration a an openapi view."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.NamedTemporaryFile() as document:
            document.write(MINIMAL_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo.yaml", route_name="foo_api_spec"
            )

        config.add_route("foo", "/foo")
        view_func = lambda *arg: "bar"  # noqa: E731
        config.add_view(openapi=True, renderer="json", view=view_func, route_name="foo")

        request_interface = config.registry.queryUtility(IRouteRequest, name="foo")
        view = config.registry.adapters.registered(
            (IViewClassifier, request_interface, Interface), IView, name=""
        )
        request = DummyRequest(config=config, content_type="text/html")
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        response = view(context, request)

        assert response.json == "bar"


def test_multiple_openapi_views() -> None:
    """Test registration multiple openapi views."""
    with testConfig() as config:
        config.include("pyramid_openapi3")

        with tempfile.NamedTemporaryFile() as document:
            document.write(MINIMAL_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name,
                route="/foo.yaml",
                route_name="foo_api_spec",
                apiname="foo",
            )

        with tempfile.NamedTemporaryFile() as document:
            document.write(ALTERNATE_DOCUMENT)
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name,
                route="/bar.yaml",
                route_name="bar_api_spec",
                apiname="bar",
            )

        config.add_route("foo", "/foo")
        view_func = lambda *arg: "foo"  # noqa: E731
        config.add_view(openapi=True, renderer="json", view=view_func, route_name="foo")

        config.add_route("bar", "/bar")
        view_func = lambda *arg: "bar"  # noqa: E731
        config.add_view(openapi=True, renderer="json", view=view_func, route_name="bar")

        # Simulate, that `check_all_routes` was called
        settings = config.registry.settings
        settings.setdefault("pyramid_openapi3", {})
        settings["pyramid_openapi3"].setdefault("routes", {})
        settings["pyramid_openapi3"]["routes"]["foo"] = "foo"
        settings["pyramid_openapi3"]["routes"]["bar"] = "bar"

        request_interface = config.registry.queryUtility(IRouteRequest, name="foo")
        view = config.registry.adapters.registered(
            (IViewClassifier, request_interface, Interface), IView, name=""
        )
        request = DummyRequest(config=config, content_type="text/html")
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        response = view(context, request)

        assert response.json == "foo"

        request_interface = config.registry.queryUtility(IRouteRequest, name="bar")
        view = config.registry.adapters.registered(
            (IViewClassifier, request_interface, Interface), IView, name=""
        )
        request = DummyRequest(config=config, content_type="text/html")
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="bar", pattern="/bar")
        context = None
        response = view(context, request)

        assert response.json == "bar"


def test_path_parameters() -> None:
    """Test parameters in path are validated correctly."""
    with testConfig() as config:
        config.include("pyramid_openapi3")
        with tempfile.NamedTemporaryFile() as document:
            document.write(
                b'openapi: "3.0.0"\n'
                b"info:\n"
                b'  version: "1.0.0"\n'
                b"  title: Foo API\n"
                b"paths:\n"
                b"  /foo:\n"
                b"    parameters:\n"
                b"      - name: foo\n"
                b"        in: query\n"
                b"        required: true\n"
                b"        schema:\n"
                b"          type: integer\n"
                b"    get:\n"
                b"      responses:\n"
                b"        200:\n"
                b"          description: A foo\n"
            )
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo.yaml", route_name="foo_api_spec"
            )

        config.add_route("foo", "/foo")
        view_func = lambda *arg: "foo"  # noqa: E731  # pragma: no branch
        config.add_view(openapi=True, renderer="json", view=view_func, route_name="foo")

        request_interface = config.registry.queryUtility(IRouteRequest, name="foo")
        view = config.registry.adapters.registered(
            (IViewClassifier, request_interface, Interface), IView, name=""
        )
        # Test validation fails
        request = DummyRequest(config=config, content_type="application/json")
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        with pytest.raises(
            RequestValidationError, match="Missing required parameter: foo"
        ):
            response = view(context, request)

        # Test validation succeeds
        request = DummyRequest(
            config=config, params={"foo": "1"}, content_type="application/json"
        )
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        response = view(context, request)

        assert response.json == "foo"


def test_header_parameters() -> None:
    """Test parameters in header are validated correctly."""
    with testConfig() as config:
        config.include("pyramid_openapi3")
        with tempfile.NamedTemporaryFile() as document:
            document.write(
                b'openapi: "3.0.0"\n'
                b"info:\n"
                b'  version: "1.0.0"\n'
                b"  title: Foo API\n"
                b"paths:\n"
                b"  /foo:\n"
                b"    get:\n"
                b"      parameters:\n"
                b"        - name: foo\n"
                b"          in: header\n"
                b"          required: true\n"
                b"          schema:\n"
                b"            type: integer\n"
                b"      responses:\n"
                b"        200:\n"
                b"          description: A foo\n"
            )
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo.yaml", route_name="foo_api_spec"
            )

        config.add_route("foo", "/foo")
        view_func = lambda *arg: "foo"  # noqa: E731  # pragma: no branch
        config.add_view(openapi=True, renderer="json", view=view_func, route_name="foo")

        request_interface = config.registry.queryUtility(IRouteRequest, name="foo")
        view = config.registry.adapters.registered(
            (IViewClassifier, request_interface, Interface), IView, name=""
        )
        # Test validation fails
        request = DummyRequest(config=config, content_type="text/html")
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None

        with pytest.raises(
            RequestValidationError, match="Missing required parameter: foo"
        ):
            response = view(context, request)

        # Test validation succeeds
        request = DummyRequest(
            config=config, headers={"foo": "1"}, content_type="text/html"
        )
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        response = view(context, request)

        assert response.json == "foo"


def test_cookie_parameters() -> None:
    """Test parameters in cookie are validated correctly."""
    with testConfig() as config:
        config.include("pyramid_openapi3")
        with tempfile.NamedTemporaryFile() as document:
            document.write(
                b'openapi: "3.0.0"\n'
                b"info:\n"
                b'  version: "1.0.0"\n'
                b"  title: Foo API\n"
                b"paths:\n"
                b"  /foo:\n"
                b"    get:\n"
                b"      parameters:\n"
                b"        - name: foo\n"
                b"          in: cookie\n"
                b"          required: true\n"
                b"          schema:\n"
                b"            type: integer\n"
                b"      responses:\n"
                b"        200:\n"
                b"          description: A foo\n"
            )
            document.seek(0)

            config.pyramid_openapi3_spec(
                document.name, route="/foo.yaml", route_name="foo_api_spec"
            )

        config.add_route("foo", "/foo")
        view_func = lambda *arg: "foo"  # noqa: E731  # pragma: no branch
        config.add_view(openapi=True, renderer="json", view=view_func, route_name="foo")

        request_interface = config.registry.queryUtility(IRouteRequest, name="foo")
        view = config.registry.adapters.registered(
            (IViewClassifier, request_interface, Interface), IView, name=""
        )
        # Test validation fails
        request = DummyRequest(config=config, content_type="text/html")
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        with pytest.raises(
            RequestValidationError, match="Missing required parameter: foo"
        ):
            response = view(context, request)

        # Test validation succeeds
        request = DummyRequest(
            config=config, cookies={"foo": "1"}, content_type="text/html"
        )
        apply_request_extensions(request)
        request.matched_route = DummyRoute(name="foo", pattern="/foo")
        context = None
        response = view(context, request)

        assert response.json == "foo"
