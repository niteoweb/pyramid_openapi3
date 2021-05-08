"""Test path-level parameters."""

from pyramid.config import Configurator
from pyramid.request import Request
from tempfile import NamedTemporaryFile
from webtest.app import TestApp


class _FooResource:
    def __init__(self, request: Request) -> None:
        self.request = request
        self.foo_id = request.openapi_validated.parameters["path"]["foo_id"]


def _foo_view(context: _FooResource, request: Request) -> int:
    return context.foo_id


def test_path_parameter_validation() -> None:
    """Test validated parameters in context factory."""

    with NamedTemporaryFile() as tempdoc:
        tempdoc.write(
            b"""\
openapi: "3.0.0"
info:
  version: "1.0.0"
  title: Foo API
paths:
  /foo/{foo_id}:
    parameters:
      - name: foo_id
        in: path
        required: true
        schema:
          type: integer
    get:
      responses:
        200:
          description: A foo
"""
        )
        tempdoc.seek(0)

        with Configurator() as config:
            config.include("pyramid_openapi3")
            config.pyramid_openapi3_spec(tempdoc.name)
            config.pyramid_openapi3_register_routes()
            config.add_route("foo_route", "/foo/{foo_id}", factory=_FooResource)
            config.add_view(_foo_view, route_name="foo_route", renderer="json")
            app = config.make_wsgi_app()
            test_app = TestApp(app)
            resp = test_app.get("/foo/1")
            assert resp.json == 1
