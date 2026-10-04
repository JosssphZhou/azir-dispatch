#!/bin/sh
# herdr 插件入口：在插件目录里打开 azir 调度的终端视图。
# 用法：herdr/open.sh dashboard|jev_view|office
view="${1:-dashboard}"
cd "${HERDR_PLUGIN_ROOT:-$(dirname "$0")/..}" || exit 1
config="${AZIR_DISPATCH_CONFIG:-$HOME/.config/azir-dispatch/config.toml}"
if [ "$view" = dashboard ] && [ -f "$config" ]; then
  exec python3 -m "azir_dispatch.$view" --config "$config"
fi
exec python3 -m "azir_dispatch.$view"
