"""Test fixtures — build the seed warehouse/marts once, expose a TestClient."""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def seeded():
    from backend.marts.materialize import materialize_from_warehouse
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.warehouse.seed import build_seed_dataset

    build_warehouse_from_dataset(build_seed_dataset(), is_seed=True)
    materialize_from_warehouse()
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def _restore_seed():
    from backend.marts.materialize import materialize_from_warehouse
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.warehouse.seed import build_seed_dataset

    build_warehouse_from_dataset(build_seed_dataset(), is_seed=True)
    materialize_from_warehouse()
