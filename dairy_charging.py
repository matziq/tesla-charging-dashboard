#!/usr/bin/env python3
"""Local-only dairy-trip charging ledger."""

import datetime as dt
import os
import sqlite3
import sys
import tkinter as tk
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import messagebox, ttk


FIELDS = ("vehicle", "dairy", "direction", "date", "time", "location",
          "odometer", "trip_miles", "energy", "rate", "paid")
DIRECTIONS = ("To dairy", "From dairy")
NUMBER_FIELDS = ("odometer", "trip_miles", "energy", "rate", "paid")
LABELS = {
    "vehicle": "Vehicle", "dairy": "Dairy", "direction": "Direction",
    "date": "Date (MM/DD/YYYY)", "time": "Time (HH:MM, 24-hour)",
    "location": "Charging location", "odometer": "Odometer (miles, optional)",
    "trip_miles": "Trip distance (miles, optional)", "energy": "Energy (kWh)",
    "rate": "Rate (USD/kWh, optional)", "paid": "Actual paid (USD, optional)",
}


def default_path():
    if os.name == "nt":
        return Path(r"D:\AI_Output\TeslaChargingDashboard\ledger.sqlite3")
    return Path.home() / ".local" / "share" / "tesla-charging-dashboard" / "ledger.sqlite3"


def parse_date(value):
    try:
        date = dt.datetime.strptime(value, "%m/%d/%Y").date()
    except ValueError:
        raise ValueError("Date must be a valid MM/DD/YYYY date.") from None
    if date.strftime("%m/%d/%Y") != value:
        raise ValueError("Date must be a valid MM/DD/YYYY date.")
    return date.isoformat()


def validate(raw):
    values = {key: raw.get(key, "").strip() for key in FIELDS}
    for key in ("vehicle", "dairy", "location"):
        if not values[key] or len(values[key]) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in values[key]):
            raise ValueError(f"{LABELS[key]} is required and must be plain text (up to 200 characters).")
    if values["direction"] not in DIRECTIONS:
        raise ValueError("Choose a trip direction.")
    values["date"] = parse_date(values["date"])
    try:
        time = dt.datetime.strptime(values["time"], "%H:%M")
    except ValueError:
        raise ValueError("Time must be HH:MM in 24-hour time.") from None
    if time.strftime("%H:%M") != values["time"]:
        raise ValueError("Time must be HH:MM in 24-hour time.")
    for key in NUMBER_FIELDS:
        text = values[key]
        if not text and key != "energy":
            values[key] = None
            continue
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"{LABELS[key]} must be a number.") from None
        if not number.is_finite() or number < 0 or number > Decimal("1000000000") or number.as_tuple().exponent < -6:
            raise ValueError(f"{LABELS[key]} must be nonnegative with at most six decimal places.")
        values[key] = format(number, "f")
    if Decimal(values["energy"]) <= 0:
        raise ValueError("Energy must be greater than zero.")
    return values


class Ledger:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS charges (
            id INTEGER PRIMARY KEY, vehicle TEXT NOT NULL, dairy TEXT NOT NULL,
            direction TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL,
            location TEXT NOT NULL, odometer TEXT, trip_miles TEXT,
            energy TEXT NOT NULL, rate TEXT, paid TEXT)""")
        self.db.commit()

    def save(self, raw, record_id=None):
        values = validate(raw)
        with self.db:
            if record_id is None:
                placeholders = ", ".join("?" for _ in FIELDS)
                self.db.execute(f"INSERT INTO charges ({', '.join(FIELDS)}) VALUES ({placeholders})",
                                tuple(values[key] for key in FIELDS))
            else:
                assignments = ", ".join(f"{key} = ?" for key in FIELDS)
                result = self.db.execute(f"UPDATE charges SET {assignments} WHERE id = ?",
                                         (*[values[key] for key in FIELDS], record_id))
                if result.rowcount != 1:
                    raise ValueError("The selected record no longer exists.")

    def rows(self, vehicle="", dairy="", direction="", start="", end=""):
        conditions, parameters = [], []
        for field, value in (("vehicle", vehicle), ("dairy", dairy), ("direction", direction)):
            if value:
                conditions.append(f"{field} = ?")
                parameters.append(value)
        for operator, value in ((">=", start), ("<=", end)):
            if value:
                conditions.append(f"date {operator} ?")
                parameters.append(parse_date(value))
        if start and end and parse_date(start) > parse_date(end):
            raise ValueError("Start date must not be after end date.")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        return self.db.execute("SELECT * FROM charges" + where + " ORDER BY date DESC, time DESC, id DESC",
                               parameters).fetchall()

    def options(self, field):
        if field not in ("vehicle", "dairy"):
            raise ValueError("Invalid option field.")
        return [row[0] for row in self.db.execute(f"SELECT DISTINCT {field} FROM charges ORDER BY {field}")]

    def close(self):
        self.db.close()


def summarize(rows):
    energy = sum((Decimal(row["energy"]) for row in rows), Decimal(0))
    paid = sum((Decimal(row["paid"]) for row in rows if row["paid"] is not None), Decimal(0))
    trip = sum((Decimal(row["trip_miles"]) for row in rows if row["trip_miles"] is not None), Decimal(0))
    return {
        "count": len(rows), "energy": energy, "paid": paid,
        "paid_missing": sum(row["paid"] is None for row in rows),
        "trip": trip, "trip_missing": sum(row["trip_miles"] is None for row in rows),
    }


class Dashboard:
    def __init__(self, root, ledger):
        self.root, self.ledger, self.selected = root, ledger, None
        self.variables = {key: tk.StringVar() for key in FIELDS}
        self.filters = {key: tk.StringVar() for key in ("vehicle", "dairy", "direction", "start", "end")}
        self.summary = tk.StringVar()
        root.title("Dairy-trip charging")
        root.geometry("1250x800")
        root.minsize(1000, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        shell = ttk.Frame(root, padding=12)
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="Dairy-trip charging", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(shell, text="Local ledger · Unknown amounts stay blank · No Tesla connection").pack(anchor="w", pady=(0, 8))
        form = ttk.LabelFrame(shell, text="Record or correct a charge", padding=8)
        form.pack(fill="x")
        self.vehicle_box = self.dairy_box = None
        for index, key in enumerate(FIELDS):
            column, row = (index % 4) * 2, index // 4
            ttk.Label(form, text=LABELS[key]).grid(row=row, column=column, sticky="w", padx=(0, 6), pady=4)
            if key in ("vehicle", "dairy", "direction"):
                widget = ttk.Combobox(form, textvariable=self.variables[key],
                                      values=DIRECTIONS if key == "direction" else (),
                                      state="readonly" if key == "direction" else "normal", width=19)
                if key == "vehicle":
                    self.vehicle_box = widget
                elif key == "dairy":
                    self.dairy_box = widget
            else:
                widget = ttk.Entry(form, textvariable=self.variables[key], width=22)
            widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 10), pady=4)
            form.columnconfigure(column + 1, weight=1)
        actions = ttk.Frame(form)
        actions.grid(row=3, column=0, columnspan=8, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="Save record", command=self.save).pack(side="left")
        ttk.Button(actions, text="New / clear", command=self.clear).pack(side="left", padx=8)
        self.selection_label = ttk.Label(actions, text="New record")
        self.selection_label.pack(side="left")

        filters = ttk.LabelFrame(shell, text="Filter records", padding=8)
        filters.pack(fill="x", pady=8)
        for i, (key, label) in enumerate((("vehicle", "Vehicle"), ("dairy", "Dairy"),
                                          ("direction", "Direction"), ("start", "From date"),
                                          ("end", "Through date"))):
            ttk.Label(filters, text=label).grid(row=0, column=i * 2, padx=(0, 4))
            if key == "direction":
                widget = ttk.Combobox(filters, textvariable=self.filters[key], width=14,
                                      values=("", *DIRECTIONS), state="readonly")
            else:
                widget = ttk.Entry(filters, textvariable=self.filters[key], width=15)
            widget.grid(row=0, column=i * 2 + 1, padx=(0, 10))
        ttk.Button(filters, text="Apply", command=self.refresh).grid(row=0, column=10)
        ttk.Button(filters, text="Clear filters", command=self.clear_filters).grid(row=0, column=11, padx=6)
        ttk.Label(filters, text="Exact vehicle/dairy/direction matches; dates MM/DD/YYYY.").grid(row=1, column=0, columnspan=12, sticky="w")
        ttk.Label(shell, textvariable=self.summary, wraplength=1180).pack(anchor="w", pady=(0, 8))

        table_frame = ttk.Frame(shell)
        table_frame.pack(fill="both", expand=True)
        columns = ("date", "time", "vehicle", "dairy", "direction", "location",
                   "odometer", "trip_miles", "energy", "rate", "paid")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "odometer": "Odometer (mi)", "trip_miles": "Trip (mi)", "energy": "Energy (kWh)",
            "rate": "Rate ($/kWh)", "paid": "Actual paid ($)",
        }
        for key in columns:
            self.table.heading(key, text=headings.get(key, LABELS[key].split(" (")[0]))
            self.table.column(key, width=110 if key != "location" else 180, minwidth=75, stretch=True)
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.table.bind("<<TreeviewSelect>>", self.select)
        self.clear()
        self.refresh()

    def clear(self):
        self.selected = None
        for value in self.variables.values():
            value.set("")
        self.variables["date"].set(dt.date.today().strftime("%m/%d/%Y"))
        self.variables["time"].set(dt.datetime.now().strftime("%H:%M"))
        self.selection_label.configure(text="New record")
        self.table.selection_remove(self.table.selection())

    def save(self):
        try:
            self.ledger.save({key: value.get() for key, value in self.variables.items()}, self.selected)
            self.clear()
            self.refresh()
        except (ValueError, sqlite3.Error) as error:
            messagebox.showerror("Could not save record", str(error) if isinstance(error, ValueError) else "Local ledger could not be saved.")

    def refresh(self):
        try:
            rows = self.ledger.rows(**{key: value.get().strip() for key, value in self.filters.items()})
            totals = summarize(rows)
            self.vehicle_box.configure(values=self.ledger.options("vehicle"))
            self.dairy_box.configure(values=self.ledger.options("dairy"))
        except (ValueError, sqlite3.Error) as error:
            messagebox.showerror("Could not load records", str(error) if isinstance(error, ValueError) else "Local ledger could not be read.")
            return
        self.table.delete(*self.table.get_children())
        for row in rows:
            date = dt.date.fromisoformat(row["date"]).strftime("%m/%d/%Y")
            values = [date if key == "date" else (row[key] if row[key] is not None else "Unknown")
                      for key in self.table["columns"]]
            self.table.insert("", "end", iid=str(row["id"]), values=values)
        self.summary.set(
            f"{totals['count']} charges  |  {totals['energy']:.2f} kWh  |  "
            f"${totals['paid']:.2f} known paid ({totals['paid_missing']} unknown)  |  "
            f"{totals['trip']:.2f} known trip miles ({totals['trip_missing']} unknown). "
            "Odometer readings are not summed."
        )

    def select(self, _event):
        selection = self.table.selection()
        if not selection:
            return
        row = self.ledger.db.execute("SELECT * FROM charges WHERE id = ?", (int(selection[0]),)).fetchone()
        if row is None:
            messagebox.showerror("Record unavailable", "The selected record no longer exists.")
            return
        self.selected = row["id"]
        for key in FIELDS:
            value = row[key]
            self.variables[key].set(dt.date.fromisoformat(value).strftime("%m/%d/%Y") if key == "date"
                                    else (value if value is not None else ""))
        self.selection_label.configure(text="Editing selected record")

    def clear_filters(self):
        for value in self.filters.values():
            value.set("")
        self.refresh()

    def close(self):
        self.ledger.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ledger = Ledger(default_path())
    except (OSError, sqlite3.Error):
        messagebox.showerror("Ledger unavailable", "Could not open the local ledger. Check the storage location and permissions.")
        root.destroy()
        return 1
    Dashboard(root, ledger)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
