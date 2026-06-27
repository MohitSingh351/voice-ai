import pytest
import redis
from django.conf import settings


@pytest.fixture(autouse=True)
def clear_throttle_keys():
    """Ensure each test starts with a clean per-campaign rate budget."""
    client = redis.from_url(settings.REDIS_URL)
    for key in client.scan_iter("campaign:*:rate:*"):
        client.delete(key)
    yield
    for key in client.scan_iter("campaign:*:rate:*"):
        client.delete(key)
