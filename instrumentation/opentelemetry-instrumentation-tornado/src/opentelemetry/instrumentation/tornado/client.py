# Copyright The OpenTelemetry Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import functools

from tornado.httpclient import HTTPError, HTTPRequest

from opentelemetry import trace
from opentelemetry.instrumentation.utils import http_status_to_status_code
from opentelemetry.propagate import inject
from opentelemetry.trace.status import Status
from opentelemetry.util._time import _time_ns


def _normalize_request(args, kwargs):
    req = args[0]
    if not isinstance(req, str):
        return (args, kwargs)

    new_kwargs = {}
    for param in ("callback", "raise_error"):
        if param in kwargs:
            new_kwargs[param] = kwargs.pop(param)

    req = HTTPRequest(req, **kwargs)
    new_args = [req]
    new_args.extend(args[1:])
    return (new_args, new_kwargs)


def fetch_async(tracer, func, _, args, kwargs):
    start_time = _time_ns()

    # Return immediately if no args were provided (error)
    # or original_request is set (meaning we are in a redirect step).
    if len(args) == 0 or hasattr(args[0], "original_request"):
        return func(*args, **kwargs)

    # Force the creation of a HTTPRequest object if needed,
    # so we can inject the context into the headers.
    args, kwargs = _normalize_request(args, kwargs)
    request = args[0]

    span = tracer.start_span(
        request.method, kind=trace.SpanKind.CLIENT, start_time=start_time,
    )

    if span.is_recording():
        attributes = {
            "http.url": request.url,
            "http.method": request.method,
        }
        for key, value in attributes.items():
            span.set_attribute(key, value)

    with trace.use_span(span):
        inject(request.headers)
        future = func(*args, **kwargs)
        future.add_done_callback(
            functools.partial(_finish_tracing_callback, span=span)
        )
        return future


def _finish_tracing_callback(future, span):
    status_code = None
    description = None
    exc = future.exception()
    if span.is_recording() and exc:
        if isinstance(exc, HTTPError):
            status_code = exc.code
        description = "{}: {}".format(type(exc).__name__, exc)
    else:
        status_code = future.result().code

    if status_code is not None:
        span.set_attribute("http.status_code", status_code)
        span.set_status(
            Status(
                status_code=http_status_to_status_code(status_code),
                description=description,
            )
        )
    span.end()
