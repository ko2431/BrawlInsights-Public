"""DBを使わず、一覧の切替コントロールと初期状態を検証する。"""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader


class AdminControlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.edit_fields = []
        self.edit_inputs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "data-admin-edit-field" in attrs:
            self.edit_fields.append(attrs)
            if tag == "input":
                self.edit_inputs.append(attrs)
            assert tag == "button"


@pytest.mark.parametrize("page", ["posts", "messages"])
@pytest.mark.parametrize("can_edit", [True, False])
def test_admin_edit_controls_avoid_autofill_and_preserve_permissions(page, can_edit):
    # 実テンプレートとマクロを読み、共通レイアウトだけを省略して描画する。
    templates = Path(__file__).resolve().parents[1] / "app" / "templates"
    env = Environment(loader=ChoiceLoader([
        DictLoader({"base.html": "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"}),
        FileSystemLoader(templates),
    ]))
    env.globals.update(
        url_for=lambda name, **kwargs: f"/{name}",
        admin_filter_active=lambda *args: False,
        admin_filter_url=lambda *args: "/filter",
    )
    context = {
        "lang": "ja",
        "platform": "web",
        "app_version": "test",
        "applied_filters": {},
        "pagination": {"total_pages": 1, "total_items": 2, "total_logged_in_users": 2, "total_guest_users": 0},
        "can_view_ip": False,
        "can_view_penalties": False,
        "can_save": can_edit,
        "can_edit_deleted": can_edit,
        "current_sort": "",
    }
    if page == "posts":
        context[page] = [
            {"id": 1, "is_deleted": True, "is_closed": False, "can_delete": can_edit, "can_close": False},
            {"id": 2, "is_deleted": False, "is_closed": True, "can_delete": can_edit, "can_close": can_edit},
        ]
        expected = [(True, not can_edit), (False, True), (False, not can_edit), (True, not can_edit)]
    else:
        context[page] = [{"id": 1, "is_deleted": True}, {"id": 2, "is_deleted": False}]
        expected = [(True, not can_edit), (False, not can_edit)]

    parser = AdminControlParser()
    parser.feed(env.get_template(f"admin/{page}.html").render(**context))
    assert not parser.edit_inputs
    assert all(field.get("type") == "button" and field.get("role") == "checkbox" for field in parser.edit_fields)
    assert all(field.get("aria-label") for field in parser.edit_fields)
    assert all(field["aria-checked"] == field["data-admin-edit-initial"] for field in parser.edit_fields)
    assert [(field["aria-checked"] == "true", "disabled" in field) for field in parser.edit_fields] == expected
