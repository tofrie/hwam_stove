"""Test-only quiescence checks, independent of HA's platform object cache."""


def assert_no_client_consumers(platforms, coordinator):
    """Retained empty objects are allowed; live consumers are never allowed."""
    assert platforms, "Capture platforms before unload; do not accept an empty sample"
    for platform in platforms:
        assert not platform.entities
        assert platform._async_polling_timer is None
        assert platform._async_cancel_retry_setup is None
        assert all(task.done() for task in platform._tasks)
    assert not coordinator._listeners
    assert coordinator._unsub_refresh is None
