from libconf import loads
from sanic import Sanic
from sanic import Request
from sanic import HTTPResponse
from sanic import response
from sanic import HTTPMethod
from os.path import split
from aiohttp import ClientSession
from aiohttp_socks import ProxyConnector

from aiohttp import ClientProxyConnectionError
from aiohttp import ClientHttpProxyError
from aiohttp_socks import ProxyConnectionError
from aiohttp_socks import ProxyTimeoutError
from aiohttp_socks import ProxyError
from typing import Any


KNOWN_PROXY_ERRORS = (
    ClientProxyConnectionError,
    ClientHttpProxyError,
    ProxyConnectionError,
    ProxyTimeoutError,
    ProxyError,
)


def locate(filename: str) -> str:
    return split(__file__)[0].replace("\\", "/") + filename


def fast_read(filename: str) -> str:
    with open(
        file=locate(filename=filename), mode="r", encoding="utf-8", errors="ignore"
    ) as file:
        return file.read()


class NoPathException(Exception):
    pass


class ValidationException(Exception):
    pass


def follow_path(path: list[str], data: dict[str, Any]) -> Any:
    current = data

    for key in path[:-1]:
        try:
            selected = current[key]

            if not isinstance(selected, dict):
                raise TypeError(
                    f"cannot follow path '{'.'.join(path)}' because it is not dictionary"
                )

            current = selected

        except KeyError:
            raise NoPathException(
                f"cannot follow path '{'.'.join(path)}' because it is not exists"
            )

    if isinstance(current, dict):
        return current[path[-1]]

    raise TypeError(
        f"cannot follow path '{'.'.join(path)}' because it is not dictionary"
    )


config = loads(string=fast_read(filename="/config.conf"))
server = Sanic(name="http_mirror", ctx=config)
server.config.proxy_index = 0


# ===================================== BEGIN CONFIG =====================================

try:
    launch_host = follow_path(path=["launch", "host"], data=config)
    launch_port = follow_path(path=["launch", "port"], data=config)

    headers_protocol_name = follow_path(
        path=["headers", "protocol", "name"], data=config
    )
    headers_protocol_default = follow_path(
        path=["headers", "protocol", "default"], data=config
    )

    mirror_route_path = follow_path(path=["mirror", "route", "path"], data=config)
    mirror_route_header = follow_path(path=["mirror", "route", "header"], data=config)

    ping_route_path = follow_path(path=["ping", "route", "path"], data=config)
    ping_route_status = follow_path(path=["ping", "route", "status"], data=config)

    privacy_xff_enabled = follow_path(path=["privacy", "xff", "enabled"], data=config)
    privacy_xff_value = follow_path(path=["privacy", "xff", "value"], data=config)

    privacy_proxies_enabled = follow_path(
        path=["privacy", "proxies", "enabled"], data=config
    )
    privacy_proxies_retry = follow_path(
        path=["privacy", "proxies", "retry"], data=config
    )
    privacy_proxies_urls = follow_path(path=["privacy", "proxies", "urls"], data=config)

    auth_enabled = follow_path(path=["auth", "enabled"], data=config)
    auth_password = follow_path(path=["auth", "password"], data=config)
    auth_header = follow_path(path=["auth", "header"], data=config)

except NoPathException as exc:
    print("[Fatal Error] %s" % exc)
    exit()

# ====================================== END CONFIG ======================================


@server.route(
    uri=f"/{mirror_route_path.strip('/')}/<path:path>",
    strict_slashes=False,
    name="mirror.nonstream",
    methods = list(map(str, HTTPMethod))
)
async def nonstream_mirror(request: Request, path: str) -> HTTPResponse:
    try:
        if mirror_route_header not in request.headers.keys():
            return response.json(
                body={
                    "success": False,
                    "error": {
                        "side": "mirror",
                        "message": f"You must provide requested hostname in the '{mirror_route_header}' header",
                    },
                },
                status=400,
            )

        if auth_enabled is True:
            if request.headers.get(auth_header, None) != auth_password:
                return response.json(
                    body={
                        "success": False,
                        "error": {
                            "side": "mirror",
                            "message": (
                                "This mirror requires authorization. "
                                f"Please provide a password in the '{auth_header}' header"
                            ),
                        },
                    },
                    status=401,
                )

        session_kw = {}
        request_kw = {}

        requested_url = "{protocol}://{hostname}/{path}".format(
            protocol=request.headers.get(
                headers_protocol_name, headers_protocol_default
            ),
            hostname=request.headers[mirror_route_header].rstrip("/"),
            path=path.lstrip("/"),
        )
        requested_params = dict(request.get_args().items())
        requested_headers = {
            k: v
            for k, v in request.headers.items()
            if k
            not in (
                headers_protocol_name.lower(),
                mirror_route_header.lower(),
                auth_header.lower(),
            )
        }
        requested_method = request.method.upper()
        requested_body = request.body
        requested_headers["host"] = request.headers.get(mirror_route_header)

        if privacy_xff_enabled is True:
            requested_headers["x-forwarded-for"] = privacy_xff_value

        if privacy_proxies_enabled is True:
            server.config.proxy_index += 1

            if server.config.proxy_index >= len(privacy_proxies_urls):
                server.config.proxy_index = 0

            proxy_url = privacy_proxies_urls[server.config.proxy_index]

            if proxy_url.split("://")[0] in ("http", "https"):
                request_kw.update({"proxy": proxy_url})

            elif proxy_url.split("://")[0] in ("socks4", "socks5"):
                session_kw.update({"connector": ProxyConnector.from_url(url=proxy_url)})

        while True:
            try:
                async with ClientSession(**session_kw) as session:
                    async with session.request(
                        method=requested_method,
                        url=requested_url,
                        headers=requested_headers,
                        params=requested_params,
                        data=requested_body,
                        **request_kw,
                    ) as wresponse:
                        return HTTPResponse(
                            body=await wresponse.read(),
                            status=wresponse.status,
                            headers=dict(wresponse.headers.items()),
                            content_type=wresponse.content_type,
                        )

            except KNOWN_PROXY_ERRORS as exc:
                if privacy_proxies_retry is False:
                    return response.json(
                        body={
                            "success": False,
                            "error": {
                                "side": "mirror",
                                "message": "Failed to establish a connection to the proxy server",
                            },
                        },
                        status=500,
                    )

    except Exception as exc:
        return response.json(
            body={
                "success": False,
                "error": {
                    "side": "mirror",
                    "message": "The mirror server has an error while processing your request",
                },
            },
            status=500,
        )


@server.route(
    uri=f"/{ping_route_path.strip('/')}/",
    strict_slashes=False,
    name="ping",
)
async def ping(unused: Request) -> HTTPResponse:
    return response.json(body={"alive": True}, status=ping_route_status)


if __name__ == "__main__":
    server.run(host=launch_host, port=launch_port)
