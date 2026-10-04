# 安装 azir-dispatch

让自己的 agent 读取 [skills/setup/SKILL.md](skills/setup/SKILL.md)，按六步引导完成检测、herdr 安装、本仓库配置、角色选择、调度图回放和第一次真实派发。所有操作都是普通 shell 命令，适用于 Codex、Grok、AGy 和其他终端 agent。

```sh
git clone https://github.com/JosssphZhou/azir-dispatch.git ~/azir-dispatch
cd ~/azir-dispatch
python3 bin/azir-dispatch doctor
```

Windows 在 WSL2 中运行，Python 需要 3.11+。herdr 必装，调度图和执行窗格都在其中运行。没有 API 密钥也可以配置角色、回放演示和使用规则判断；真实派发仍需登录至少一个 agent CLI。

完整执行顺序以安装技能为准。非交互答案字段、独立授权及已有配置处理见 [配置说明](docs/setup.md)，角色首选和备选见 [角色示例](examples/roles.example.toml)。
