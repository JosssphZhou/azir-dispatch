# 终端看板

需要 Python 3.11 或更新版本、支持 24 位真彩色的终端，最小尺寸为 80 列 × 36 行。按 `q` 退出。看板只读取事件和会话文件。窗格改变大小后，看板会在下一帧重画。所有尺寸使用同一套布局：工程经理居中在上，下面左边顾问、右边 JEV，再下面一排三个执行者卡位、「回到工程经理 · 审查 · 验证」框和会话记录，各部分之间用虚线连接。终端变高时，JEV 框和会话记录多显示几条；宽于 100 列或高于 52 行时，画面居中。事件动画使用沿虚线移动的渐暗拖尾、方框到达闪光和 JEV 边框扫光。

```sh
python3 -m azir_dispatch.dashboard --replay tests/fixtures/events-sample.jsonl
python3 -m azir_dispatch.dashboard --replay tests/fixtures/events-sample.jsonl --speed 2
python3 -m azir_dispatch.dashboard --replay tests/fixtures/events-sample.jsonl --check
python3 -m azir_dispatch.dashboard --replay tests/fixtures/events-sample.jsonl --check --size 80x36
python3 -m azir_dispatch.dashboard --once --size 85x47 --seconds 0
```

## 内置演示

`azir_dispatch/demo/video-run.jsonl` 是演示录屏里那一轮：工程经理问一次顾问，JEV 把两件事分别派给 GPT-6 Luna 和 GPT-6.1 Sol，两个执行者运行，最后两次收尾判断（一次交回，一次建议）。任务名已换成中性的示例。不需要任何密钥：

```sh
python3 -m azir_dispatch.dashboard --replay azir_dispatch/demo/video-run.jsonl
```

回放从第一条事件开始计时，比录屏晚 13 秒：回放第 3 秒对应录屏第 16 秒。顾问在第 0 秒亮起，第 7.5 秒派出两个执行者，第 84.5 秒和第 90 秒出现收尾判断。回放不读安装状态，画面和录屏一致；标题、主框名称和顾问今日次数来自录屏机器的配置和会话记录，回放里分别是默认值和 1 次。

`--once` 走到 `--seconds` 指定的时刻，按 `--size` 输出一帧带颜色的文字后退出，用于截图和检查。

## 安装进度

实时模式还读 `setup-state.json`，把安装进度画在同一张图上。路径依次取 `--setup-state`、配置 `setup_state`、`$AZIR_DISPATCH_STATE_DIR/setup-state.json`，默认 `~/.local/state/azir-dispatch/setup-state.json`。回放模式只在给了 `--setup-state` 时读。`--no-setup` 或配置 `setup_state = false` 关掉这部分。看板每帧比较文件的 inode、大小和修改时间，文件一变，下一帧就重画，不用重启。

| 文件内容 | 画面 |
| --- | --- |
| 不存在 | 副标题位置画六步进度，全部是空心圆；页脚写「还没开始安装」。执行者卡位保持原样。 |
| 不是 JSON 或顶层不是对象 | 和不存在一样，页脚写「安装状态文件读不出」。 |
| `steps` | 六步依次为环境、herdr、仓库、模型、调度图、首次派发。`ok` 画绿勾，`missing` 和 `failed` 画橙色叉，第一个没完成的步骤画闪动的实心圆。某一步刚变成 `ok` 时亮起约 1 秒。六步都 `ok` 后副标题恢复原样。 |
| `hints` | 页脚写第一个没完成的步骤和它的提示；过长时截短，「q 退出」始终保留。 |
| `agents` | 工程经理框右边按文件里键的顺序列出每个命令行，有的打绿勾，没有的打橙色叉；放不下时最后一项写「+N」。 |
| `roles.main` | 工程经理框第二行写型号和执行工具，不是首选时加「备选」。配置了 `main_subtitle` 时以配置为准。 |
| `roles.advisor` | 顾问框写型号；`optional_unconfigured` 写「未配置，可选」，`missing` 写「未配置」。 |
| `roles.dev`、`review`、`research` | 文件存在时，执行者卡位换成开发、审查、调研三张，写型号和执行工具，不是首选时标「备选」，还没选写「待选模型」。派发出的执行者按型号落到对应卡位。型号刚填上时卡片边框亮起约 1 秒。 |
| `jev_mode = "rules"` | JEV 框标题后标「规则模式」。 |

字段类型不对时按「没完成」处理，不报错。

实时模式直接运行 `python3 -m azir_dispatch.dashboard`。事件文件依次取 `--log`、`AZIR_DISPATCH_LOG`、`~/.local/state/azir-dispatch/events.jsonl`。不存在或为空时显示空闲画面：三个空的执行者卡位，以及今天的历史判断和会话记录。历史记录默认取事件文件所在目录下 `演示/旧记录/*.jsonl` 中今天（本地日期）的记录，只用于 JEV 框和会话记录，不生成执行者卡片，也不计入顾问次数；用配置 `history`（一个或多个路径通配）或命令行 `--history` 改为其他文件。启动时读取已有事件，重建执行者和最近几条判断的状态，不播放历史动画；若启动时文件不存在或为空，之后写入的第一批事件也会播放小球。文件缩小或被替换时重新读取并重建状态。末尾半行会等补全；已换行的坏行会跳过，底部显示累计行数。回放模式按事件时间播放，事件过密时排队，不丢事件。

所有文字按终端列宽裁在画布或框内，只取字段的第一行，并过滤终端控制字符。JEV 框和会话记录里的判断显示判断点短名。没有判断点时，使用问题的第一行。看板不显示 `state_preview` 或问题后续的背景说明。`--check` 逐帧检查框线、绘制行的列宽和控制字符，发现可能画到画布外的文字时退出 1。用 `--size 列x行` 指定检查尺寸。

配置可由 `--config examples/config.example.toml` 加载。`[dashboard]` 中的 `title`、`main_title`、`main_subtitle` 分别控制大标题、主会话标题和副标题；`log`、`advisor_dir` 控制读取路径；`retention_seconds` 控制完成或失败后执行者的保留秒数；`advisor_active_seconds` 控制顾问调用后高亮多久，默认 20 秒。`--no-advisor-scan` 关闭会话目录扫描。命令行的 `--log` 和 `--advisor-dir` 优先于配置文件。

每行事件为一个 JSON 对象。`ts` 使用带毫秒的 UTC 时间（以 `Z` 结尾），画面上的事件时间转成本地时间。认识 `decision`、`dispatch`、`done`、`failed`、`handback`、`advisor`，忽略额外字段和未知类型。公共字段 `task_id`、`caused_by` 可由写入方提供，看板不依赖它们。画面对应关系如下：

| 事件 | 画面 |
| --- | --- |
| `decision` | 小球从工程经理到 JEV。最近几条判断各占两行：第一行显示判断点短名（选执行者、下一步、收尾），其他判断点显示 `point`；第二行显示答案、把握条、把握数字和去向；去向「直接执行」为绿色，「建议」为青色，「交回」为黄色，「规则」为蓝色，「JEV 不可用，走默认」为灰色。收尾的 `close_keep`、`keep`、`close_and_clean` 后面附中文说明。长答案从中间截断，保留开头和末尾的思考等级。`source=jev, disposition=apply` 显示「直接执行」；`disposition=handback` 显示「交回」，此时答案为空，显示 `jev_choice`；`source=default, disposition=apply` 显示「JEV 不可用，走默认」。 |
| `decision`，`source=rules` | 没有 OpenRouter 密钥或配置成规则模式时，核心按配置的默认答案判断，事件写 `source=rules`、`disposition=apply`、`jev_mode=rules`，`confidence` 为空。JEV 框和会话记录的去向写「规则」，蓝色，把握数字显示 0.00。JEV 判断视图结论行写「→ 规则」，标出采用的答案，不播放推理动画。 |
| `dispatch` | 按 `target` 新增执行者框，显示 `actual.executor` 和 `actual.model`；`suggested` 与 `actual` 不同时显示「已改派」。小球从 JEV 沿虚线到执行者卡位。没有安装状态文件时画五个卡位，空位标 6.1 Sol、Luna、Gemini、Grok、Opus 并写「空闲」；有安装状态文件时画开发、审查、调研三个卡位。执行者多于卡位时显示最近几个和其余数量。执行者完成后卡片留在原位。 |
| `done`、`failed` | 对应执行者标完成或失败；小球沿虚线到「回到工程经理」框；到保留时间后移除。 |
| `handback` | 小球从 JEV 返回主会话。 |
| `advisor` | 顾问框亮起约 20 秒，今日调用次数增加，会话记录写「问顾问」；之后变暗，保留最近一次本地时间。 |

## JEV 判断视图

`python3 -m azir_dispatch.jev_view --log <记录>` 实时显示最新判断，终端约 82×17 列时可完整显示。新记录先播放约 0.6 秒推理动画，随后概率条约一秒长出；选中项用紫粉渐变，把握值以三行方块数字递增，底部火花线显示最近 14 次判断。把握线以上的执行者和技能显示「过线 → 直接执行」，下一步和收尾显示「过线 → 建议」；低于线显示「没过线 → 交回工程经理」。规则判断显示「→ 规则」。实时模式等待半行补全，跳过坏行，并在记录被截短或替换时重建。用 `--replay <记录>` 查看已有记录，或加 `--check` 检查画面是否超出终端宽高。终端较宽时视图居中；旧判断缺少概率或状态开头时显示「无」。

默认还会只读扫描 `~/.claude/projects/` 下的 JSON Lines 会话文件，从 `server_tool_use` 且 `name=advisor` 的记录提取调用时间、会话和目录。启动扫描只重建今日次数和最近调用时间，不把历史调用显示成正在发生。看板不显示顾问回答或会话正文，也不改动会话文件。可以用 `--advisor-dir` 指向别的目录。实时模式每秒扫描一次。
