import csv
import json
import math
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import BOTH, END, LEFT, RIGHT, TOP, X, Y, Button, Entry, Frame, Label, PanedWindow, StringVar, Tk, messagebox, simpledialog, ttk

try:
    import winsound
except ImportError:  # pragma: no cover - Windows target, fallback keeps source runnable elsewhere.
    winsound = None


DEFAULT_CSV_PATH = Path(r"D:\research_1\Absorption Experiment\timer1\single_specimen_weighing_template.csv")
STATE_FILE = "timer_state.json"
RESULTS_DIR = "results"
APP_TITLE = "吸水实验多试件倒计时"


@dataclass(frozen=True)
class ScheduleRow:
    index: int
    interval_label: str
    cumulative_label: str
    interval_seconds: int


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_schedule_csv() -> Path:
    candidates = [
        app_dir() / "single_specimen_weighing_template.csv",
        Path(__file__).resolve().parent / "single_specimen_weighing_template.csv",
        DEFAULT_CSV_PATH,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("找不到 single_specimen_weighing_template.csv")


def parse_duration(value: str) -> int:
    text = value.strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|second|seconds|min|minute|minutes|h|hr|hour|hours)", text)
    if not match:
        raise ValueError(f"无法解析时间间隔: {value}")
    number = float(match.group(1))
    unit = match.group(2)
    if unit.startswith("s"):
        multiplier = 1
    elif unit.startswith("min"):
        multiplier = 60
    else:
        multiplier = 3600
    return int(round(number * multiplier))


def load_schedule(path: Path) -> dict[int, ScheduleRow]:
    encodings = ("gbk", "utf-8-sig", "utf-8")
    last_error = None
    rows = None
    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                rows = list(csv.DictReader(handle))
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if rows is None:
        raise last_error or ValueError("无法读取时间表")

    schedule: dict[int, ScheduleRow] = {}
    for raw in rows:
        index = int(raw["序号"])
        interval = raw["时间间隔"].strip()
        cumulative = raw["累计时间"].strip()
        schedule[index] = ScheduleRow(index, interval, cumulative, parse_duration(interval))
    if 0 not in schedule or max(schedule) < 1:
        raise ValueError("时间表必须包含第 0 行和至少 1 个称重点")
    return schedule


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(math.ceil(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.strip())
    cleaned = cleaned.strip(" .")
    return cleaned or "specimen"


def new_specimen(name: str) -> dict:
    return {
        "name": name,
        "next_index": 1,
        "current_index": None,
        "status": "idle",
        "start_ts": None,
        "end_ts": None,
        "last_mass": "",
        "alert_silenced": False,
    }


class AbsorptionTimerApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.base_dir = app_dir()
        self.state_path = self.base_dir / STATE_FILE
        self.results_dir = self.base_dir / RESULTS_DIR
        self.results_dir.mkdir(exist_ok=True)
        self.schedule_path = find_schedule_csv()
        self.schedule = load_schedule(self.schedule_path)
        self.max_index = max(self.schedule)
        self.specimens: list[dict] = []
        self.selected_name = StringVar(value="")
        self.next_index_var = StringVar(value="1")
        self.mass_var = StringVar(value="")
        self.note_var = StringVar(value="")
        self.result_selected_index_var = StringVar(value="")
        self.result_mass_var = StringVar(value="")
        self.result_note_var = StringVar(value="")
        self.status_var = StringVar(value="")
        self.alert_thread_running = False
        self.stop_event = threading.Event()

        self.load_state()
        self.build_ui()
        self.refresh_table()
        self.tick()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("1380x760")
        self.root.minsize(1180, 620)

        top = Frame(self.root, padx=12, pady=10)
        top.pack(side=TOP, fill=X)
        Label(top, text=APP_TITLE, font=("Microsoft YaHei UI", 18, "bold")).pack(side=LEFT)
        Label(top, text=f"时间表: {self.schedule_path}", font=("Microsoft YaHei UI", 9)).pack(side=RIGHT)

        main_pane = PanedWindow(self.root, orient="horizontal", sashrelief="raised")
        main_pane.pack(fill=BOTH, expand=True, padx=12)

        left_panel = Frame(main_pane)
        right_panel = Frame(main_pane, padx=10)
        main_pane.add(left_panel, minsize=760)
        main_pane.add(right_panel, minsize=360)

        columns = ("name", "next", "interval", "cumulative", "remaining", "status", "last_mass")
        self.tree = ttk.Treeview(left_panel, columns=columns, show="headings", height=18)
        headings = {
            "name": "试件名",
            "next": "当前/下一次",
            "interval": "时间间隔",
            "cumulative": "累计时间",
            "remaining": "剩余时间",
            "status": "状态",
            "last_mass": "最近质量(g)",
        }
        widths = {"name": 180, "next": 95, "interval": 110, "cumulative": 110, "remaining": 130, "status": 160, "last_mass": 115}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="center")
        self.tree.tag_configure("due", background="#ffd6d6")
        self.tree.tag_configure("running", background="#d9ecff")
        self.tree.tag_configure("idle", background="#ffffff")
        self.tree.tag_configure("done", background="#e4f7df")
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        Label(right_panel, text="称重记录", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", pady=(0, 8))
        result_table_frame = Frame(right_panel)
        result_table_frame.pack(side=TOP, fill=BOTH, expand=True)
        result_columns = ("index", "interval", "cumulative", "mass", "recorded_at", "note")
        self.results_tree = ttk.Treeview(result_table_frame, columns=result_columns, show="headings", height=18)
        result_headings = {
            "index": "序号",
            "interval": "间隔",
            "cumulative": "累计",
            "mass": "质量(g)",
            "recorded_at": "记录时间",
            "note": "备注",
        }
        result_widths = {"index": 52, "interval": 70, "cumulative": 70, "mass": 78, "recorded_at": 138, "note": 120}
        for column in result_columns:
            self.results_tree.heading(column, text=result_headings[column])
            self.results_tree.column(column, width=result_widths[column], anchor="center")
        self.results_tree.pack(side=LEFT, fill=BOTH, expand=True)
        result_scrollbar = ttk.Scrollbar(result_table_frame, orient="vertical", command=self.results_tree.yview)
        result_scrollbar.pack(side=RIGHT, fill=Y)
        self.results_tree.configure(yscrollcommand=result_scrollbar.set)
        self.results_tree.bind("<<TreeviewSelect>>", self.on_result_select)

        result_edit = Frame(right_panel, pady=10)
        result_edit.pack(side=TOP, fill=X)
        Label(result_edit, text="选中序号").pack(side=LEFT)
        Entry(result_edit, textvariable=self.result_selected_index_var, width=5, state="readonly").pack(side=LEFT, padx=(4, 8))
        Label(result_edit, text="质量(g)").pack(side=LEFT)
        Entry(result_edit, textvariable=self.result_mass_var, width=10).pack(side=LEFT, padx=(4, 8))
        Label(result_edit, text="备注").pack(side=LEFT)
        Entry(result_edit, textvariable=self.result_note_var, width=16).pack(side=LEFT, padx=(4, 8))
        Button(result_edit, text="修改记录", command=self.update_selected_result, width=10).pack(side=LEFT, padx=(0, 6))
        Button(result_edit, text="清空记录", command=self.clear_selected_result, width=10).pack(side=LEFT)

        toolbar = Frame(self.root, padx=12, pady=8)
        toolbar.pack(fill=X)
        Button(toolbar, text="添加试件", command=self.add_specimen, width=12).pack(side=LEFT, padx=(0, 6))
        Button(toolbar, text="重命名", command=self.rename_specimen, width=10).pack(side=LEFT, padx=6)
        Button(toolbar, text="删除/停用", command=self.delete_specimen, width=10).pack(side=LEFT, padx=6)
        Button(toolbar, text="停止计时", command=self.stop_selected_timer, width=10).pack(side=LEFT, padx=14)
        Button(toolbar, text="停止提醒", command=self.silence_selected, width=10).pack(side=LEFT, padx=6)

        edit = Frame(self.root, padx=12, pady=8)
        edit.pack(fill=X)
        Label(edit, text="选中试件:", font=("Microsoft YaHei UI", 10, "bold")).pack(side=LEFT)
        Label(edit, textvariable=self.selected_name, width=18, anchor="w").pack(side=LEFT, padx=(4, 16))
        Label(edit, text="接下来称重序号").pack(side=LEFT)
        self.next_spin = ttk.Spinbox(edit, from_=1, to=self.max_index, textvariable=self.next_index_var, width=6)
        self.next_spin.pack(side=LEFT, padx=6)
        Button(edit, text="设置序号", command=self.set_next_index, width=10).pack(side=LEFT, padx=(0, 18))
        Label(edit, text="称重质量(g)").pack(side=LEFT)
        Entry(edit, textvariable=self.mass_var, width=12).pack(side=LEFT, padx=6)
        Button(edit, text="下一次", command=self.start_selected, width=12).pack(side=LEFT, padx=(0, 14))
        Label(edit, text="备注").pack(side=LEFT)
        Entry(edit, textvariable=self.note_var, width=28).pack(side=LEFT, padx=6)

        footer = Frame(self.root, padx=12, pady=8)
        footer.pack(fill=X)
        Label(footer, textvariable=self.status_var, anchor="w", font=("Microsoft YaHei UI", 10)).pack(side=LEFT, fill=X, expand=True)

    def load_state(self) -> None:
        if not self.state_path.exists():
            self.specimens = []
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.specimens = data.get("specimens", [])
        except (json.JSONDecodeError, OSError):
            self.specimens = []
        for specimen in self.specimens:
            specimen.setdefault("next_index", 1)
            specimen.setdefault("current_index", None)
            specimen.setdefault("status", "idle")
            specimen.setdefault("start_ts", None)
            specimen.setdefault("end_ts", None)
            specimen.setdefault("last_mass", "")
            specimen.setdefault("alert_silenced", False)
            self.refresh_status_from_time(specimen)

    def save_state(self) -> None:
        payload = {
            "version": 1,
            "saved_at": now_iso(),
            "schedule_csv": str(self.schedule_path),
            "specimens": self.specimens,
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_selected(self) -> dict | None:
        selected = self.tree.selection()
        if not selected:
            return None
        name = selected[0]
        for specimen in self.specimens:
            if specimen["name"] == name:
                return specimen
        return None

    def on_select(self, _event=None) -> None:
        specimen = self.get_selected()
        if specimen is None:
            self.selected_name.set("")
            return
        previous_name = self.selected_name.get()
        self.selected_name.set(specimen["name"])
        if previous_name != specimen["name"]:
            self.next_index_var.set(str(specimen.get("next_index", 1)))
            self.mass_var.set("")
            self.note_var.set("")
        self.status_var.set(f"已选择: {specimen['name']}")
        self.refresh_results_table(specimen)

    def on_result_select(self, _event=None) -> None:
        selected = self.results_tree.selection()
        if not selected:
            return
        values = self.results_tree.item(selected[0], "values")
        if not values:
            return
        self.result_selected_index_var.set(values[0])
        self.result_mass_var.set(values[3] if len(values) > 3 else "")
        self.result_note_var.set(values[5] if len(values) > 5 else "")

    def add_specimen(self) -> None:
        name = simpledialog.askstring("添加试件", "请输入试件名:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if any(item["name"] == name for item in self.specimens):
            messagebox.showerror("名称重复", "已经存在同名试件。")
            return
        self.specimens.append(new_specimen(name))
        self.save_state()
        self.refresh_table()
        self.tree.selection_set(name)
        self.on_select()

    def rename_specimen(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        old_name = specimen["name"]
        new_name = simpledialog.askstring("重命名试件", "请输入新的试件名:", initialvalue=old_name, parent=self.root)
        if not new_name:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if any(item["name"] == new_name for item in self.specimens):
            messagebox.showerror("名称重复", "已经存在同名试件。")
            return
        old_file = self.result_file_for(old_name)
        new_file = self.result_file_for(new_name)
        if old_file.exists() and not new_file.exists():
            old_file.rename(new_file)
        specimen["name"] = new_name
        self.save_state()
        self.refresh_table()
        self.tree.selection_set(new_name)
        self.on_select()

    def delete_specimen(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        if not messagebox.askyesno("删除/停用试件", f"确定从列表中删除 {specimen['name']} 吗？\n已保存的结果 CSV 不会删除。"):
            return
        self.specimens = [item for item in self.specimens if item["name"] != specimen["name"]]
        self.save_state()
        self.refresh_table()
        self.selected_name.set("")

    def set_next_index(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        if specimen.get("status") == "running":
            messagebox.showwarning("正在计时", "该试件正在计时。请先停止计时，再修改接下来称重序号。")
            return
        try:
            next_index = int(self.next_index_var.get())
        except ValueError:
            messagebox.showerror("序号无效", "称重序号必须是数字。")
            return
        if next_index < 1 or next_index > self.max_index:
            messagebox.showerror("序号无效", f"称重序号必须在 1-{self.max_index} 之间。")
            return
        specimen["next_index"] = next_index
        specimen["current_index"] = None
        specimen["status"] = "idle"
        specimen["start_ts"] = None
        specimen["end_ts"] = None
        specimen["alert_silenced"] = False
        self.save_state()
        self.refresh_table()
        self.status_var.set(f"{specimen['name']} 接下来将进行第 {next_index} 次称重。")

    def start_selected(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        self.refresh_status_from_time(specimen)
        if specimen.get("status") == "running":
            messagebox.showinfo("正在计时", "该试件已经在计时。")
            return
        if specimen.get("status") == "due":
            if not self.save_mass_for_specimen(specimen, advance=True):
                return
        next_index = int(specimen.get("next_index", 1))
        if next_index > self.max_index:
            specimen["status"] = "done"
            self.save_state()
            self.refresh_table()
            messagebox.showinfo("已完成", "该试件已经完成全部称重。")
            return
        row = self.schedule[next_index]
        now = time.time()
        specimen["current_index"] = next_index
        specimen["status"] = "running"
        specimen["start_ts"] = now
        specimen["end_ts"] = now + row.interval_seconds
        specimen["alert_silenced"] = False
        self.save_state()
        self.refresh_table()
        self.status_var.set(f"{specimen['name']} 第 {next_index} 次称重倒计时已开始，间隔 {row.interval_label}。")

    def stop_selected_timer(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        if specimen.get("status") not in ("running", "due"):
            return
        if not messagebox.askyesno("停止计时", f"确定停止 {specimen['name']} 当前计时吗？"):
            return
        specimen["status"] = "idle"
        specimen["current_index"] = None
        specimen["start_ts"] = None
        specimen["end_ts"] = None
        specimen["alert_silenced"] = False
        self.save_state()
        self.refresh_table()

    def silence_selected(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        specimen["alert_silenced"] = True
        self.save_state()
        self.refresh_table()
        self.status_var.set(f"已停止 {specimen['name']} 的声音提醒。")

    def save_mass(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        self.save_mass_for_specimen(specimen, advance=True)

    def save_mass_for_specimen(self, specimen: dict, advance: bool) -> bool:
        index = specimen.get("current_index")
        if index is None:
            index = specimen.get("next_index")
        try:
            index = int(index)
        except (TypeError, ValueError):
            messagebox.showerror("无法保存", "当前试件没有有效称重序号。")
            return False
        if index < 1 or index > self.max_index:
            messagebox.showerror("无法保存", "当前称重序号超出时间表范围。")
            return False
        mass = self.mass_var.get().strip()
        if not mass:
            messagebox.showerror("缺少质量", "请先输入称重质量，再点击“下一次”。")
            return False
        try:
            float(mass)
        except ValueError:
            messagebox.showerror("质量无效", "称重质量必须是数字。")
            return False

        result_file = self.result_file_for(specimen["name"])
        existing = self.read_result_rows(result_file)
        if any(row.get("序号") == str(index) for row in existing):
            if not messagebox.askyesno("覆盖确认", f"{specimen['name']} 第 {index} 次称重已有记录，是否覆盖？"):
                return False
            existing = [row for row in existing if row.get("序号") != str(index)]

        row = self.schedule[index]
        existing.append(
            {
                "试件名": specimen["name"],
                "序号": str(index),
                "时间间隔": row.interval_label,
                "累计时间": row.cumulative_label,
                "称重质量_g": mass,
                "记录时间": now_iso(),
                "备注": self.note_var.get().strip(),
            }
        )
        existing.sort(key=lambda item: int(item["序号"]))
        self.write_result_rows(result_file, existing)

        specimen["last_mass"] = mass
        if advance:
            specimen["next_index"] = min(index + 1, self.max_index + 1)
            specimen["current_index"] = None
            specimen["start_ts"] = None
            specimen["end_ts"] = None
            specimen["alert_silenced"] = False
            specimen["status"] = "done" if specimen["next_index"] > self.max_index else "idle"
        self.mass_var.set("")
        self.note_var.set("")
        self.next_index_var.set(str(min(specimen["next_index"], self.max_index)))
        self.save_state()
        self.refresh_table()
        self.refresh_results_table(specimen)
        self.status_var.set(f"已保存 {specimen['name']} 第 {index} 次称重结果。")
        return True

    def result_file_for(self, specimen_name: str) -> Path:
        return self.results_dir / f"{safe_filename(specimen_name)}_results.csv"

    def read_result_rows(self, path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def write_result_rows(self, path: Path, rows: list[dict[str, str]]) -> None:
        fieldnames = ["试件名", "序号", "时间间隔", "累计时间", "称重质量_g", "记录时间", "备注"]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def update_selected_result(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        try:
            index = int(self.result_selected_index_var.get())
        except ValueError:
            messagebox.showinfo("未选择记录", "请先在右侧记录表中选择一行。")
            return
        if index < 1 or index > self.max_index:
            messagebox.showerror("序号无效", f"称重序号必须在 1-{self.max_index} 之间。")
            return
        mass = self.result_mass_var.get().strip()
        if not mass:
            messagebox.showerror("缺少质量", "请输入要修改的称重质量。")
            return
        try:
            float(mass)
        except ValueError:
            messagebox.showerror("质量无效", "称重质量必须是数字。")
            return

        rows = [row for row in self.read_result_rows(self.result_file_for(specimen["name"])) if row.get("序号") != str(index)]
        schedule_row = self.schedule[index]
        rows.append(
            {
                "试件名": specimen["name"],
                "序号": str(index),
                "时间间隔": schedule_row.interval_label,
                "累计时间": schedule_row.cumulative_label,
                "称重质量_g": mass,
                "记录时间": now_iso(),
                "备注": self.result_note_var.get().strip(),
            }
        )
        rows.sort(key=lambda item: int(item["序号"]))
        self.write_result_rows(self.result_file_for(specimen["name"]), rows)
        self.sync_last_mass_from_results(specimen)
        self.save_state()
        self.refresh_table()
        self.refresh_results_table(specimen, selected_index=index)
        self.status_var.set(f"已修改 {specimen['name']} 第 {index} 次称重记录。")

    def clear_selected_result(self) -> None:
        specimen = self.get_selected()
        if specimen is None:
            messagebox.showinfo("未选择", "请先选择一个试件。")
            return
        try:
            index = int(self.result_selected_index_var.get())
        except ValueError:
            messagebox.showinfo("未选择记录", "请先在右侧记录表中选择一行。")
            return
        if not messagebox.askyesno("清空记录", f"确定清空 {specimen['name']} 第 {index} 次称重记录吗？"):
            return
        rows = [row for row in self.read_result_rows(self.result_file_for(specimen["name"])) if row.get("序号") != str(index)]
        self.write_result_rows(self.result_file_for(specimen["name"]), rows)
        self.sync_last_mass_from_results(specimen)
        self.result_mass_var.set("")
        self.result_note_var.set("")
        self.save_state()
        self.refresh_table()
        self.refresh_results_table(specimen, selected_index=index)
        self.status_var.set(f"已清空 {specimen['name']} 第 {index} 次称重记录。")

    def sync_last_mass_from_results(self, specimen: dict) -> None:
        rows = [
            row
            for row in self.read_result_rows(self.result_file_for(specimen["name"]))
            if row.get("序号", "").isdigit() and row.get("称重质量_g", "").strip()
        ]
        if not rows:
            specimen["last_mass"] = ""
            return
        rows.sort(key=lambda item: int(item["序号"]))
        specimen["last_mass"] = rows[-1].get("称重质量_g", "")

    def refresh_results_table(self, specimen: dict | None = None, selected_index: int | None = None) -> None:
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        if selected_index is None:
            self.result_selected_index_var.set("")
            self.result_mass_var.set("")
            self.result_note_var.set("")
        if specimen is None:
            specimen = self.get_selected()
        if specimen is None:
            return
        rows_by_index = {
            int(row["序号"]): row
            for row in self.read_result_rows(self.result_file_for(specimen["name"]))
            if row.get("序号", "").isdigit()
        }
        for index in range(1, self.max_index + 1):
            schedule_row = self.schedule[index]
            result_row = rows_by_index.get(index, {})
            self.results_tree.insert(
                "",
                END,
                iid=f"result-{index}",
                values=(
                    str(index),
                    schedule_row.interval_label,
                    schedule_row.cumulative_label,
                    result_row.get("称重质量_g", ""),
                    result_row.get("记录时间", ""),
                    result_row.get("备注", ""),
                ),
            )
        if selected_index is not None and self.results_tree.exists(f"result-{selected_index}"):
            self.results_tree.selection_set(f"result-{selected_index}")
            self.results_tree.see(f"result-{selected_index}")
            self.on_result_select()

    def refresh_status_from_time(self, specimen: dict) -> None:
        if specimen.get("status") != "running":
            return
        end_ts = specimen.get("end_ts")
        if end_ts is not None and time.time() >= float(end_ts):
            specimen["status"] = "due"
            specimen["alert_silenced"] = False

    def table_values_for(self, specimen: dict) -> tuple:
        self.refresh_status_from_time(specimen)
        status = specimen.get("status", "idle")
        current_index = specimen.get("current_index")
        next_index = int(specimen.get("next_index", 1))
        display_index = current_index if status in ("running", "due") and current_index else next_index
        if display_index and 1 <= int(display_index) <= self.max_index:
            row = self.schedule[int(display_index)]
            interval = row.interval_label
            cumulative = row.cumulative_label
        else:
            interval = "-"
            cumulative = "-"

        remaining = "-"
        status_text = "未开始/待启动"
        if status == "running":
            remaining_seconds = float(specimen["end_ts"]) - time.time()
            remaining = format_seconds(remaining_seconds)
            status_text = "计时中"
        elif status == "due":
            overdue = time.time() - float(specimen["end_ts"])
            remaining = f"超时 {format_seconds(overdue)}"
            status_text = "请称重"
        elif status == "done" or next_index > self.max_index:
            remaining = "-"
            status_text = "已完成"
        return (specimen["name"], f"第 {display_index} 次", interval, cumulative, remaining, status_text, specimen.get("last_mass", ""))

    def refresh_table(self) -> None:
        existing_items = set(self.tree.get_children())
        specimen_names = {specimen["name"] for specimen in self.specimens}
        for item in existing_items - specimen_names:
            self.tree.delete(item)
        for specimen in self.specimens:
            values = self.table_values_for(specimen)
            status = specimen.get("status", "idle")
            tag = "done" if status == "done" else status
            if specimen["name"] in existing_items:
                self.tree.item(specimen["name"], values=values, tags=(tag,))
            else:
                self.tree.insert("", END, iid=specimen["name"], values=values, tags=(tag,))

    def any_active_alerts(self) -> bool:
        return any(item.get("status") == "due" and not item.get("alert_silenced", False) for item in self.specimens)

    def ensure_alert_thread(self) -> None:
        if self.alert_thread_running or not self.any_active_alerts():
            return
        self.alert_thread_running = True
        thread = threading.Thread(target=self.alert_loop, daemon=True)
        thread.start()

    def alert_loop(self) -> None:
        while not self.stop_event.is_set() and self.any_active_alerts():
            if winsound:
                winsound.Beep(1200, 450)
                time.sleep(0.35)
                winsound.Beep(900, 450)
            else:
                self.root.bell()
            time.sleep(1.2)
        self.alert_thread_running = False

    def tick(self) -> None:
        changed = False
        for specimen in self.specimens:
            before = specimen.get("status")
            self.refresh_status_from_time(specimen)
            if specimen.get("status") != before:
                changed = True
        if changed:
            self.save_state()
        self.refresh_table()
        self.ensure_alert_thread()
        self.root.after(1000, self.tick)

    def on_close(self) -> None:
        self.save_state()
        self.stop_event.set()
        self.root.destroy()


def run_self_test() -> None:
    schedule = load_schedule(find_schedule_csv())
    assert 0 in schedule, "missing row 0"
    assert max(schedule) == 44, f"expected 44 weighing rows, got {max(schedule)}"
    assert schedule[1].interval_seconds == 2
    assert schedule[38].interval_seconds == 18 * 3600
    assert safe_filename('A:B/C*D?E"F') == "A_B_C_D_E_F"
    with TemporaryDirectory() as temp_dir:
        result_file = Path(temp_dir) / "test_results.csv"
        fieldnames = ["试件名", "序号", "时间间隔", "累计时间", "称重质量_g", "记录时间", "备注"]
        rows = [
            {
                "试件名": "C2-1",
                "序号": "1",
                "时间间隔": schedule[1].interval_label,
                "累计时间": schedule[1].cumulative_label,
                "称重质量_g": "123.45",
                "记录时间": now_iso(),
                "备注": "",
            }
        ]
        with result_file.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        with result_file.open("r", encoding="utf-8-sig", newline="") as handle:
            loaded = list(csv.DictReader(handle))
        assert loaded[0]["试件名"] == "C2-1"
        assert loaded[0]["称重质量_g"] == "123.45"
    print("self-test ok")
    print(f"schedule rows: 0-{max(schedule)}")
    print(f"csv: {find_schedule_csv()}")


def main() -> None:
    if "--self-test" in sys.argv:
        run_self_test()
        return
    root = Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    AbsorptionTimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
