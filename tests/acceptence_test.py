from urllib import request

import requests


def test_ok():
    response = request.get("http://localhost:9120/")
    assert response.status_code == 200
