from app.factory import create_app


def test_notification_routes_publish_the_private_paged_contract(auth_settings):
    schema = create_app(auth_settings).openapi()
    list_route = schema["paths"]["/api/v1/notifications"]["get"]
    query_parameters = {parameter["name"]: parameter for parameter in list_route["parameters"]}

    assert query_parameters["unread_only"]["schema"]["default"] is False
    assert query_parameters["page_size"]["schema"]["maximum"] == 100
    assert list_route["responses"]["200"]["headers"]["Cache-Control"]
    page_schema = schema["components"]["schemas"]["PageResponse_NotificationListItem_"]
    assert "$ref" in page_schema["properties"]["items"]["items"]
    item_schema = schema["components"]["schemas"]["NotificationListItem"]
    assert "description" not in item_schema["properties"]
    assert "recipient_id" not in item_schema["properties"]
    assert "created_at DESC, id DESC" in list_route["description"]
    assert "unknown event" in list_route["description"].lower()
    assert "inaccessible appointment" in list_route["description"].lower()
    assert "INVALID_SESSION" in list_route["description"]
    assert "FORBIDDEN_ROLE" in list_route["description"]
    assert "VALIDATION_ERROR" in list_route["description"]

    read_route = schema["paths"]["/api/v1/notifications/{notification_id}/read"]["post"]
    headers = {parameter["name"]: parameter for parameter in read_route["parameters"]}
    assert headers["X-CSRF-Token"]["required"] is True
    assert read_route["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/NotificationReadRequest"
    )
    request_schema = schema["components"]["schemas"]["NotificationReadRequest"]
    assert request_schema["properties"] == {}
    assert request_schema["additionalProperties"] is False
    assert "413" in read_route["responses"]
    assert "415" in read_route["responses"]
    assert "read_at" in read_route["description"]
    assert "NOT_FOUND" in read_route["description"]
    assert "INVALID_CSRF" in read_route["description"]
    assert "INVALID_ORIGIN" in read_route["description"]
