#!/usr/bin/env python3
"""Local-only dairy-trip charging ledger."""

import datetime as dt
import json
import os
import sqlite3
import sys
import tkinter as tk
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


FIELDS = ("vehicle", "dairy", "direction", "date", "time", "location",
          "odometer", "trip_miles", "energy", "rate", "paid")
DIRECTIONS = ("To dairy", "From dairy")
NUMBER_FIELDS = ("odometer", "trip_miles", "energy", "rate", "paid")
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_REVIEW_PAGES = 20
MAX_REVIEW_CANDIDATES = 5000
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


def read_candidates(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON keys")
            result[key] = value
        return result

    try:
        with Path(path).open("rb") as source:
            data = source.read(MAX_RESPONSE_BYTES + 1)
    except OSError:
        raise ValueError("The selected response could not be opened.") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("The selected response exceeds 10 MiB.")
    try:
        payload = json.loads(data, object_pairs_hook=unique_keys)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("The selected response is not valid unambiguous JSON.") from None
    if not isinstance(payload, dict):
        raise ValueError("The selected response has an unsupported structure.")
    rows, total = payload.get("data"), payload.get("totalResults")
    if (not isinstance(rows, list) or type(total) is not int or total < len(rows)
            or len(rows) > 5000):
        raise ValueError("The selected response has invalid page metadata.")
    candidates, identifiers = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("The selected response has an invalid candidate.")
        identifier = row.get("sessionId")
        if type(identifier) is not int or not 0 <= identifier <= 9223372036854775807:
            raise ValueError("The selected response has an invalid session identifier.")
        if identifier in identifiers:
            raise ValueError("The selected response has duplicate session identifiers.")
        identifiers.add(identifier)
        candidate = {"sessionId": identifier}
        for key in ("vin", "siteLocationName", "chargeStartDateTime", "chargeStopDateTime"):
            value = row.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 500
                                      or any(ord(char) < 32 or 127 == ord(char)
                                             or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
                raise ValueError("The selected response has invalid candidate details.")
            candidate[key] = value or "Not provided"
        candidates.append(candidate)
    return candidates


def read_candidate_pages(paths):
    if not paths or len(paths) > MAX_REVIEW_PAGES:
        raise ValueError("Select between 1 and 20 saved responses.")
    candidates, identifiers = [], set()
    for path in paths:
        page = read_candidates(path)
        if len(candidates) + len(page) > MAX_REVIEW_CANDIDATES:
            raise ValueError("The selected responses contain too many candidates.")
        for candidate in page:
            identifier = candidate["sessionId"]
            if identifier in identifiers:
                raise ValueError("The selected responses have overlapping session identifiers. "
                                 "Select non-overlapping pages or review them separately.")
            identifiers.add(identifier)
            candidates.append(candidate)
    return candidates


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
        self.db.execute("""CREATE TABLE IF NOT EXISTS reviewed_tesla_sessions (
            session_id INTEGER PRIMARY KEY, charge_id INTEGER NOT NULL UNIQUE,
            FOREIGN KEY (charge_id) REFERENCES charges(id))""")
        self.db.commit()

    def _insert(self, values):
        placeholders = ", ".join("?" for _ in FIELDS)
        return self.db.execute(f"INSERT INTO charges ({', '.join(FIELDS)}) VALUES ({placeholders})",
                               tuple(values[key] for key in FIELDS))

    def save(self, raw, record_id=None):
        values = validate(raw)
        with self.db:
            if record_id is None:
                self._insert(values)
            else:
                assignments = ", ".join(f"{key} = ?" for key in FIELDS)
                result = self.db.execute(f"UPDATE charges SET {assignments} WHERE id = ?",
                                         (*[values[key] for key in FIELDS], record_id))
                if result.rowcount != 1:
                    raise ValueError("The selected record no longer exists.")

    def imported(self, session_id):
        return self.db.execute("SELECT 1 FROM reviewed_tesla_sessions WHERE session_id = ?",
                               (session_id,)).fetchone() is not None

    def save_reviewed(self, raw, session_id, confirmed):
        if confirmed is not True:
            raise ValueError("Confirm the review before saving.")
        if type(session_id) is not int or not 0 <= session_id <= 9223372036854775807:
            raise ValueError("The candidate session identifier is invalid.")
        values = validate(raw)
        try:
            with self.db:
                if self.imported(session_id):
                    raise ValueError("This Tesla candidate was already imported.")
                charge_id = self._insert(values).lastrowid
                self.db.execute("INSERT INTO reviewed_tesla_sessions (session_id, charge_id) VALUES (?, ?)",
                                (session_id, charge_id))
        except sqlite3.IntegrityError:
            raise ValueError("The candidate could not be saved; it may already be imported.") from None

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
        ttk.Button(shell, text="Review saved Tesla responses", command=self.open_review).pack(anchor="w", pady=(0, 8))
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

    def open_review(self):
        if self.selected is not None:
            messagebox.showinfo("Finish editing first",
                                "Save or clear the selected record before opening the review.",
                                parent=self.root)
            return
        paths = filedialog.askopenfilenames(
            parent=self.root, title="Select privately saved Tesla responses",
            filetypes=(("Response files", "*.bin *.json"), ("All files", "*.*")))
        if not paths:
            return
        try:
            candidates = read_candidate_pages(paths)
        except ValueError as error:
            messagebox.showerror("Could not open response", str(error), parent=self.root)
            return
        if not candidates:
            messagebox.showinfo("No candidates", "The selected responses contain no candidates.", parent=self.root)
            return
        ReviewWindow(self, candidates)

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


class ReviewWindow:
    def __init__(self, dashboard, candidates):
        self.dashboard, self.candidates = dashboard, candidates
        self.window = tk.Toplevel(dashboard.root)
        self.window.title("Review saved Tesla responses")
        self.window.geometry("1120x700")
        self.window.transient(dashboard.root)
        self.window.grab_set()
        self.variables = {key: tk.StringVar(self.window) for key in FIELDS}
        self.confirmed = tk.BooleanVar(self.window, value=False)
        self.detail = tk.StringVar(self.window)
        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, wraplength=1060, text=(
            "These selected pages may be incomplete and are not a complete history. "
            "Select a candidate and independently enter every required value. "
            "Tesla details below are unverified; no amount, mileage, units or date is inferred."
        )).pack(anchor="w")
        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True, pady=8)
        self.listbox = tk.Listbox(body, exportselection=False, width=24)
        self.listbox.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.listbox.configure(yscrollcommand=scrollbar.set)
        right = ttk.Frame(body)
        right.grid(row=0, column=2, sticky="nsew")
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)
        ttk.Label(right, textvariable=self.detail, justify="left", wraplength=790).pack(anchor="w", pady=(0, 8))
        form = ttk.LabelFrame(right, text="Manually verified dairy-trip details", padding=8)
        form.pack(fill="x")
        for index, key in enumerate(FIELDS):
            column, row = (index % 2) * 2, index // 2
            ttk.Label(form, text=LABELS[key]).grid(row=row, column=column, sticky="w", padx=(0, 5), pady=3)
            if key == "direction":
                widget = ttk.Combobox(form, textvariable=self.variables[key], values=DIRECTIONS, state="readonly")
            else:
                widget = ttk.Entry(form, textvariable=self.variables[key])
            widget.grid(row=row, column=column + 1, sticky="ew", padx=(0, 8), pady=3)
            form.columnconfigure(column + 1, weight=1)
        ttk.Label(right, wraplength=790, text=(
            "Energy is required. Leave optional mileage, rate or payment blank if unknown. "
            "Previously manual entries cannot be automatically matched; check the ledger for duplicates."
        )).pack(anchor="w", pady=8)
        ttk.Checkbutton(right, variable=self.confirmed, text=(
            "I verified the vehicle, dairy trip, entered values and any blanks, and checked for duplicates."
        )).pack(anchor="w")
        ttk.Button(right, text="Save reviewed charge", command=self.save).pack(anchor="w", pady=8)
        for index in range(len(candidates)):
            self.listbox.insert("end", f"Candidate {index + 1}")
        self.listbox.bind("<<ListboxSelect>>", self.select)
        self.listbox.selection_set(0)
        self.select()

    def select(self, _event=None):
        selection = self.listbox.curselection()
        if not selection:
            return
        candidate = self.candidates[selection[0]]
        for value in self.variables.values():
            value.set("")
        self.confirmed.set(False)
        self.detail.set(
            "Tesla-reported candidate details (verify independently):\n"
            f"Vehicle identifier: {candidate['vin']}\n"
            f"Location: {candidate['siteLocationName']}\n"
            f"Start: {candidate['chargeStartDateTime']}\n"
            f"End: {candidate['chargeStopDateTime']}"
        )

    def save(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        try:
            self.dashboard.ledger.save_reviewed(
                {key: value.get() for key, value in self.variables.items()},
                self.candidates[selection[0]]["sessionId"], self.confirmed.get())
        except (ValueError, sqlite3.Error) as error:
            messagebox.showerror("Could not save candidate",
                                 str(error) if isinstance(error, ValueError) else "Local ledger could not be saved.",
                                 parent=self.window)
            return
        self.dashboard.refresh()
        messagebox.showinfo("Candidate saved", "The reviewed charge was saved to the local ledger.",
                            parent=self.window)
        self.confirmed.set(False)


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
