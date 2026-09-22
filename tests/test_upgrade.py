"""Nang cap database v1 (Booking Deadline Bot) -> v2 khong mat du lieu."""

from __future__ import annotations

import sqlite3

from sqlalchemy import inspect, select

from booking_control.db import init_engine, session_scope
from booking_control.db.models import Shipment


def test_v1_database_is_upgraded_in_place(tmp_path):
    db = tmp_path / "v1.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE shipments (
        id INTEGER PRIMARY KEY, shipment_code VARCHAR(40) NOT NULL, shipment_key VARCHAR(64) NOT NULL,
        source_file_code VARCHAR(40) NOT NULL, source_sheet VARCHAR(160) NOT NULL, source_row INTEGER NOT NULL,
        bot_status VARCHAR(20) NOT NULL, effective_status VARCHAR(20) NOT NULL,
        last_bot_color VARCHAR(12) NOT NULL)""")
    con.execute("INSERT INTO shipments VALUES (1,'SHP-1','k1','F01','S',2,'GREEN','GREEN','FF92D050')")
    con.commit()
    con.close()

    eng = init_engine(f"sqlite:///{db}")
    cols = {c["name"] for c in inspect(eng).get_columns("shipments")}
    assert {"auto_status", "last_auto_color", "assigned_to", "archived", "row_data", "version"} <= cols
    assert "bot_status" not in cols
    with session_scope() as s:
        sh = s.execute(select(Shipment)).scalar_one()
        assert (sh.auto_status, sh.last_auto_color) == ("GREEN", "FF92D050")
        # Cot moi co gia tri mac dinh -> dong cu khong bi loc mat
        assert sh.archived is False and sh.assigned_to == "" and sh.version == 1 and sh.row_data == {}
        assert s.execute(select(Shipment).where(Shipment.archived.is_(False))).scalar_one()
