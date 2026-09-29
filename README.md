# DSH＋蛋白鉴定系统（Web 版）

一个本地运行、零云端依赖的蛋白质质谱数据鉴定流水线，带 Web 界面。

- 四步流水线：蛋白初筛 → 肽段爬取 → 肽段匹配 → 蛋白分析（含蛋白重复矩阵）
- 本地 Web 界面（Flask），参数可在页面上点选，无需改代码
- 可选接入 **DSH（DeepSeek Harness）** 做 AI 代码审查与结果问答
- 所有运行数据写在工程目录内的 `runtime/` 下，不污染系统目录
- 跨平台：Windows（`run.bat`）/ Linux、macOS（`run.sh`）；也可直接用 `python run.py`

> 下载后**无需修改任何文件**即可运行：首次启动会自动创建虚拟环境并安装依赖。

---

## 1. 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.9 或更高（64 位） | 必须项。若未安装，Windows 启动脚本会提示并可自动下载安装 Python 3.11.9 |
| Node.js | 18+（可选） | 仅「AI 分析」按钮需要；不装不影响四步流水线 |
| 网络 | 可访问 UniProt / Expasy | Step2 肽段爬取需要联网 |

---

## 2. 快速开始

### Windows

1. 安装 Python 3.9+（勾选 *Add python.exe to PATH*），可选安装 Node.js LTS
2. 双击 **`run.bat`**
3. 首次运行会自动：创建 `.venv` → 安装 `requirements.txt` 中的依赖 → 启动本地服务并打开浏览器
4. 停止服务：控制台按 `Ctrl+C`，或点击页面右上角「退出服务」

也可以手动执行：

```bat
run.bat --port 8000        :: 指定 Web 端口
run.bat --no-browser       :: 不自动打开浏览器
```

### Linux / macOS

```bash
chmod +x run.sh
./run.sh                   # 首次会自动创建 .venv 并安装依赖
./run.sh --port 8000
```

### 任意平台（纯 Python 方式）

```bash
python run.py              # 自动选端口 + 自动打开浏览器
python run.py --port 8000
python run.py --no-browser
python run.py --check      # 只做环境自检，不启动服务
```

---

## 3. 目录结构

```
DSH_protein_system/
├── run.bat / run.sh          # 薄壳启动脚本（Windows / Linux·macOS）
├── run.py                    # 纯 Python 启动入口（检查版本·装依赖·起服务）
├── env_bootstrap.ps1         # Windows 环境引导（创建 .venv、装依赖、必要时装 Python）
├── requirements.txt          # 依赖清单
├── config.default.yaml       # 默认配置（随仓库分发，请勿直接修改）
├── config.local.yaml         # 本机配置（首次在「设置」页保存时生成，已被 .gitignore 忽略）
├── app_paths.py              # 统一路径层（runtime 子目录解析）
├── config.py                 # 配置中心（读写配置 + 旧版常量兼容）
├── web_app.py                # Flask 服务（Web 界面 + 四步流水线调度 + DSH 拉起）
├── dsh_provider_config.py    # DSH 模型/凭据配置读写
├── *_core.py                 # 四步流水线核心模块（含独立命令行自测入口）
├── expasy_params.py          # Expasy 参数表
├── templates/ static/        # 前端页面与静态资源
└── runtime/                  # 运行时数据（首次运行自动创建，不入库）
    ├── data/                 # 输入数据
    ├── output/               # 流水线结果
    ├── logs/                 # 日志（含 dsh_web.log）
    ├── temp/                 # 临时文件
    ├── sessions/             # 会话数据
    ├── backups/              # DSH 配置备份
    └── DSH_CONTEXT.md        # 点击「AI 分析」时生成的上下文指路牌
```

---

## 4. 配置说明

配置优先级（高 → 低）：

1. `config.local.yaml`（Web 界面「设置」页写入）
2. `config.default.yaml`（仓库默认值）
3. 代码内置兜底值

可在页面右上角切换到 **「设置」** 页修改以下内容，保存后写入 `config.local.yaml`：

| 分节 | 配置项 | 说明 |
| --- | --- | --- |
| `web` | `port` / `host` / `open_browser` | Web 端口（0 = 自动探测空闲端口）、监听地址、是否自动开浏览器 |
| `paths` | `runtime_dir` 及各子目录 | 数据目录，留空即 `<runtime_dir>/<子目录>`，可填绝对路径 |
| `dsh` | `port` / `permission_mode` | DSH Web 端口、DSH 文件访问权限 |
| `pipeline` | `default_tolerance` 等 | 匹配容差默认值、爬取并发数 |

- **端口与数据目录**修改后需**重启服务**生效；**DSH 权限模式**保存后即时生效。
- 也支持环境变量覆盖：`DSH_RUNTIME_DIR`、`DSH_DATA_DIR`、`DSH_OUTPUT_DIR`、`DSH_LOGS_DIR`、`DSH_TEMP_DIR`、`DSH_SESSIONS_DIR`、`DSH_BACKUPS_DIR`。

---

## 5. 使用流水线

1. 打开页面 → 「参数设置」选择蛋白质列表 Excel、实验数据目录、输出目录（默认已填 `runtime/output`）
2. 依次执行 Step1 → Step4，或一键运行
3. 结果 Excel 与 `run_log.json` 写入输出目录，可在页面直接查看表格

---

## 6. AI 分析（DSH，可选）

点击页面「AI 分析」按钮时：

1. 程序会检测 `node` / `npm` / `dsh` 是否存在
2. 若 DSH 未安装，会提示执行：`npm install -g @deepseek-ai/dsh`
3. DSH 未运行时，程序**静默后台启动** `dsh web`，工作目录为工程根目录，并生成 `runtime/DSH_CONTEXT.md` 指路牌（写明代码与结果文件位置），随后自动打开 DSH 页面

相关配置：

- `dsh.port`：DSH Web 端口（默认 3080，被占用时可在「设置」页修改）
- `dsh.permission_mode`：DSH 文件访问权限，默认 `workspace-write`（仅工作区可写）。可选 `read-only` / `workspace-write` / `danger-full-access`

---

## 7. 各核心模块命令行自测

```bash
python protein_matcher_core.py --protein 蛋白列表.xlsx --data 实验数据目录
python peptide_crawler_core.py  --help
python peptide_matcher_core.py --crawler runtime/output/peptide_crawl_database.xlsx --data 实验数据目录
python protein_analyzer_core.py --crawler runtime/output/peptide_crawl_database.xlsx
```

默认输出均落在工程内 `runtime/output/`，不依赖任何本机绝对路径。

---

## 8. 常见问题

**Q：双击 `run.bat` 后窗口一闪而过？**
A：多数是 Python 未安装或未加入 PATH。手动执行 `powershell -NoProfile -ExecutionPolicy Bypass -File env_bootstrap.ps1` 查看详细输出。

**Q：提示端口被占用？**
A：将 Web 端口配置为 `0`（自动探测），或在「设置」页指定一个空闲端口。

**Q：依赖装到了系统 Python 里？**
A：不会。`run.bat` / `run.sh` / `env_bootstrap.ps1` 统一使用工程内 `.venv` 解释器；如需重建，删除 `.venv` 目录后重新运行启动脚本。

**Q：中文输出乱码？**
A：启动入口已把控制台与子进程输出统一为 UTF-8（`PYTHONUTF8=1`，输出 `errors=replace`）。若仍异常，请在「区域设置」中开启 *Beta: 使用 Unicode UTF-8 提供全球语言支持*。

---

## 9. 许可证

MIT License，详见 [LICENSE](LICENSE)。
