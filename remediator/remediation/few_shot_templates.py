"""闭环修复阶段注入的 few-shot（仅作格式与思路参考，勿机械照抄路径）。"""

FEW_SHOT_REMEDIATION_BLOCK = """
【Few-shot 修复范例（格式参考）】

例1 — 权限不足：
- Current Error: Permission denied 写 /var/log/app.log
- 合理 fixed_command: sudo sh -c 'test -w /var/log || touch /var/log/app.log' 或先 id 确认组权限；risk_warning 说明 sudo 风险。

例2 — 命令未找到（包缺失）：
- Requires Package: maven
- fixed_command: sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y maven && mvn -v

例3 — 链式命令首段失败：
- 原: cd /no/such && ./run.sh
- fixed_command 应修正整条链（从第一个失败子命令起），不可只返回 ./run.sh。
"""
