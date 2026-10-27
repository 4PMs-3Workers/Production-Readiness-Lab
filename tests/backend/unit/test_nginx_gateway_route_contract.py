from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_gateway_route_and_log_methods_use_the_original_request_line():
    nginx_conf = (ROOT / "tools/deploy/nginx/nginx.conf").read_text(encoding="utf-8")
    routes_conf = (ROOT / "tools/deploy/nginx/routes.conf").read_text(encoding="utf-8")
    route_generator = (ROOT / "tools/infra/generate_gateway_routes.py").read_text(encoding="utf-8")
    route_map = (ROOT / "tools/deploy/nginx/route-map.conf").read_text(encoding="utf-8")

    assert "map $request $prl_method {" in nginx_conf
    assert '"~^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) " $1;' in nginx_conf
    assert '"method":"$prl_method"' in nginx_conf
    assert "error_page 400 414 431 = /__prl/error/bad-request;" in routes_conf
    assert "location = /__prl/error/bad-request {\n    internal;" in routes_conf
    assert 'map_block("$prl_method:$request_uri", "$prl_route", route_rows)' in route_generator
    assert (
        'map_block("$prl_method:$upstream_http_location", "$prl_location_route", location_rows)'
        in route_generator
    )
    assert route_generator.count('"$prl_method:$request_uri"') == 2
    assert '"$status:$prl_method:$prl_route:$prl_location_route:' in route_generator
    assert 'map "$prl_method:$request_uri" $prl_route {' in route_map
    assert 'map "$prl_method:$upstream_http_location" $prl_location_route {' in route_map
    assert 'map "$prl_method:$request_uri" $prl_terminal_slash {' in route_map
    assert "$status:$prl_method:$prl_route:$prl_location_route:" in route_map
    assert "$request_method:" not in route_map
