# 多组吸水实验 absorption-experiment-timer

Windows 桌面计时器，适用于毛细吸水实验中的多组试件同步计时。支持自定义试件名称、单独倒计时状态、声音提醒，以及每个试件的称重记录与导出。

## 功能

- 从 `single_specimen_weighing_template.csv` 读取称重计划。
- 支持多个试件并行计时，互不干扰。
- 可为每个试件设置下一次称重序号。
- 当试件到达称重时间时发出声音提醒。
- 将称重质量和备注保存到各试件对应的 CSV 文件中。
- 在右侧表格中显示完整计划和已记录结果。
- 支持修改和清空已记录的称重数据。
- 将本地倒计时状态保存在 `timer_state.json`。

## 运行要求

- Windows
- Python 3.10 或更高版本
- 正常源代码运行不需要额外第三方 Python 包
- 可选：PyInstaller，仅在构建 `.exe` 时需要

## 从源代码运行

```powershell
python .\absorption_timer.py
```

运行内置自检：

```powershell
python .\absorption_timer.py --self-test
```

## 时间表 CSV

程序会从脚本或可执行文件同目录下的 `single_specimen_weighing_template.csv` 读取数据，保留列名不变：

```csv
序号,时间间隔,累计时间
0,0 s,0 s
1,2 s,2 s
2,5 min,5 min
3,1 h,1 h
```

支持的时间间隔单位：

- `s`
- `min`
- `h`

修改计划 CSV 后请重启应用。

## 数据文件

这些文件会在本地生成，并且有意不纳入 Git 版本管理：

- `timer_state.json`：当前试件和倒计时状态。
- `results/`：每个试件的称重结果 CSV 文件。
- `dist/`、`build/`、便携版压缩包，以及生成的可执行文件。

## 构建 EXE

如需安装 PyInstaller：

```powershell
python -m pip install pyinstaller
```

构建单文件窗口程序：

```powershell
python -m PyInstaller --onefile --windowed --name AbsorptionExperimentTimer .\absorption_timer.py --noconfirm
```

便携式使用时，请将以下内容放在同一文件夹：

```text
AbsorptionExperimentTimer.exe
single_specimen_weighing_template.csv
timer_state.json
results/
```

## 许可证

MIT License.
