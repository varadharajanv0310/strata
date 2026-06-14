"""End-to-end test for the warehouse → served-artifact publish path.

ISOLATION: conftest redirects DUCKDB_PATH / DATABASE_URL to a throwaway temp
DuckDB + SQLite *before* any backend import, so this runs the whole
staging→warehouse→marts pipeline WITHOUT touching the persistent warehouse or the
live app.db. Real staging is read read-only (it's pipeline input, never mutated).
"""
from __future__ import annotations

from sqlalchemy import func, select

from backend.core.db import session_scope
from backend.marts import models as M


def test_publish_served_end_to_end():
    from backend.pipelines.publish import publish_served

    summary = publish_served()  # fuse staging → temp warehouse → temp marts

    with session_scope() as db:
        n_roles = db.scalar(select(func.count()).select_from(M.MartRole))
        n_rc = db.scalar(select(func.count()).select_from(M.MartRoleCountry))
        n_alias = db.scalar(select(func.count()).select_from(M.MartRoleAlias))
        n_prov = db.scalar(select(func.count()).select_from(M.MartProvenance))

    # the path must actually populate the served layer (not 0)
    assert n_roles > 0, "no roles materialized"
    assert n_rc > 0, "no role×country spine materialized"
    assert n_alias > 0, "alias graph not materialized (taxonomy → mart_role_alias)"
    assert n_prov > 0, "provenance manifest not materialized"
    assert summary["provenance_rows"] == n_prov


def test_provenance_tuple_surfaces():
    """A served figure carries its full lineage tuple (transform_version at least)."""
    from backend.app import services

    with session_scope() as db:
        rc = db.scalars(select(M.MartRoleCountry)).first()
        assert rc is not None, "publish test must run first"
        prov = services.provenance(db, rc.role_id, rc.country_code)

    assert prov is not None
    assert "transform_version" in prov
    # at least one source in the manifest should have a real snapshot hash
    with session_scope() as db:
        manifest = services.list_provenance(db)
    assert manifest, "empty provenance manifest"
    assert any(m["snapshot_hash"] for m in manifest), "no snapshot hashes computed"
