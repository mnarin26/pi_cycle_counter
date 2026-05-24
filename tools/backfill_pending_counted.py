import sqlite3


def main() -> None:
    db_path = "/home/pi/injection-monitor/backend/data/injection.db"
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute(
        "select count(*) from cycles where exclude_reason=? and is_counted=0",
        ("post_stop_pending",),
    )
    before = int(cur.fetchone()[0])
    cur.execute(
        "update cycles set is_counted=1 where exclude_reason=? and is_counted=0",
        ("post_stop_pending",),
    )
    updated = int(cur.rowcount)
    con.commit()
    cur.execute(
        "select count(*) from cycles where exclude_reason=? and is_counted=0",
        ("post_stop_pending",),
    )
    after = int(cur.fetchone()[0])
    con.close()
    print(f"before={before} updated={updated} after={after}")


if __name__ == "__main__":
    main()
