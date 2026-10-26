"""Expose gateway fixtures to other container modules before test collection."""

from tests.containers.test_gateway import backend_image, gateway

__all__ = ["backend_image", "gateway"]
