from app.config import Settings


def test_postgresql_url_is_runtime_configuration():
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql://")
    assert not hasattr(settings, "database_path")


def test_legacy_sqlite_url_is_rejected():
    import pytest
    from psycopg import ProgrammingError

    with pytest.raises(ProgrammingError):
        Settings(_env_file=None, database_url="sqlite:///data/collectflow.db")
