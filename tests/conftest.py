import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nxblogger import create_app  # noqa: E402
from nxblogger.auth import set_admin_password  # noqa: E402

PASSWORD = "test-password-1"


@pytest.fixture
def app(tmp_path):
    app = create_app({"DATA_DIR": tmp_path, "SECRET_KEY": "test", "TESTING": True})
    set_admin_password(app.config["DATABASE"], "oh2gax", PASSWORD)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def get_csrf(client, path="/admin/login"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


@pytest.fixture
def admin(client):
    token = get_csrf(client)
    resp = client.post("/admin/login", data={"username": "oh2gax", "password": PASSWORD, "_csrf": token})
    assert resp.status_code == 302
    client.csrf = token
    return client
