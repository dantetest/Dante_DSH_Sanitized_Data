# -*- coding: utf-8 -*-
import sqlite3

c = sqlite3.connect(r"D:\DSH_DESKTOP_WORKSPACE\Sanitized_Data\data\yinzhan.db")
print("total audit rows:", c.execute("select count(*) from audit").fetchone()[0])
print("mcp_ rows:", c.execute("select count(*) from audit where job_id like 'mcp_%'").fetchone()[0])
print("job_id prefixes:", c.execute(
    "select substr(job_id,1,2) p, count(*) from audit group by p").fetchall())
print("latest 3:", c.execute(
    "select job_id, mode, fmt, ts from audit order by seq desc limit 3").fetchall())
