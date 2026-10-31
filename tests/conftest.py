import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Never read a real server config while testing.
os.environ["NXB_ENV_FILE"] = os.devnull
for _key in [k for k in os.environ if k.startswith("NXB_") and k != "NXB_ENV_FILE"]:
    del os.environ[_key]

from nxblogger import create_app  # noqa: E402
from nxblogger.auth import set_admin_password  # noqa: E402

PASSWORD = "test-password-1"


@pytest.fixture
def app(tmp_path):
    app = create_app({"DATA_DIR": tmp_path, "SECRET_KEY": "test", "TESTING": True})
    set_admin_password(app.config["DATABASE"], app.config["ADMIN_USER"], PASSWORD)
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
    resp = client.post("/admin/login", data={"username": "admin", "password": PASSWORD, "_csrf": token})
    assert resp.status_code == 302
    client.csrf = token
    return client
