import sqlite3
from datetime import date

import pytest

from app.db.import_sqlite import import_sqlite
from app.modules.accounts.repository import list_accounts


def legacy_file(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE accounts (id TEXT PRIMARY KEY, borrower_name TEXT NOT NULL, "
            "loan_reference TEXT NOT NULL UNIQUE, outstanding_amount INTEGER NOT NULL "
            "CHECK (outstanding_amount >= 0), due_date TEXT NOT NULL, assigned_to TEXT NOT NULL)"
        )
        connection.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?)", rows)
    return path


def row(account_id="legacy", reference="LN-legacy", due="2026-11-01", amount=5_000_000_000):
    return account_id, "Legacy Demo", reference, amount, due, "Demo Agent"


def test_import_preserves_all_fields_and_source_and_is_idempotent(database_url, tmp_path):
    source = legacy_file(tmp_path / "old.db", [row()])
    before = source.read_bytes()
    assert import_sqlite(source, database_url) == 1
    assert import_sqlite(source, database_url) == 0
    assert source.read_bytes() == before
    assert list_accounts(database_url) == [{
        "id": "legacy", "borrower_name": "Legacy Demo", "loan_reference": "LN-legacy",
        "outstanding_amount": 5_000_000_000, "due_date": date(2026, 11, 1),
        "assigned_to": "Demo Agent",
    }]


@pytest.mark.parametrize("conflicting", [row(amount=1), row("other")])
def test_conflict_rolls_back_entire_import(database_url, tmp_path, conflicting):
    original = legacy_file(tmp_path / "original.db", [row()])
    import_sqlite(original, database_url)
    source = legacy_file(tmp_path / "conflict.db", [row("new", "LN-new"), conflicting])
    with pytest.raises(ValueError, match="conflict"):
        import_sqlite(source, database_url)
    assert len(list_accounts(database_url)) == 1


def test_invalid_date_rolls_back_and_missing_file_is_not_created(database_url, tmp_path):
    source = legacy_file(tmp_path / "invalid.db", [row("new", "LN-new"), row(due="invalid")])
    with pytest.raises(ValueError):
        import_sqlite(source, database_url)
    assert list_accounts(database_url) == []
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        import_sqlite(missing, database_url)
    assert not missing.exists()


def test_fractional_idr_is_rejected(database_url, tmp_path):
    source = legacy_file(tmp_path / "fractional.db", [row(amount=1.5)])
    with pytest.raises(ValueError):
        import_sqlite(source, database_url)
    assert list_accounts(database_url) == []


def test_cli_reports_failure_without_row_payloads(database_url, tmp_path, monkeypatch, capsys):
    from app.db.import_sqlite import main

    source = legacy_file(tmp_path / "invalid-cli.db", [row(due="private-invalid-value")])
    monkeypatch.setattr("sys.argv", ["import_sqlite", str(source)])
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 1
    output = capsys.readouterr().err
    assert "Import failed" in output
    assert "private-invalid-value" not in output
    assert list_accounts(database_url) == []
