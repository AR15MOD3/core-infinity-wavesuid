# 插件自带页面 · 0.3.2

## 随包交付

package.json 声明 configPage=config.html、pageBackend=account_admin.py，以及 accounts / delete_account 两项 pageActions。
市场安装须保留这些文件，不能依赖本机额外补过的 waves-page.ts。
页面提供 Bot 插件状态、人数/账号摘要、本机绑定与登录凭证名单、刷新与受保护删除。
不显示历史兼容平台标识 dsh；不会把“已保存登录凭证”解释为凭证实时有效或 QQ 在线。
启用/停用统一由插件总览操作，页面没有启停按钮。

## pageBackend v1 宿主要求

此合同是通用操作者接口，不是模型工具，也不是放开 iframe 同源或任意网络。
Core 需要正式实现该合同后才能使用新页面。现有 Infinity API=1 / botScope=1 本身不证明具备该能力。

- configPage 仍为最多 256KB 的内联 HTML，sandbox=allow-scripts；不授予 same-origin、外网或表单导航。
- config:state 除 fields/revision 外返回已校验的 pageActions；active 表示标签是否可见。
- iframe 接收事件检查 source===parent，发送 telyincore:page:request，包含 id、action、payload、revision。
- 父页只接受当前 iframe 的请求；操作固定绑定当前插件。写操作必须经宿主原生确认，不信任 iframe 自报确认。
- 请求进入 POST /infinity/plugin-page/<当前插件名>，服务端继续使用同源校验；不接受任意 URL、文件路径或 Bot 选择器。
- 后台在正式生命周期锁中调用 handle(context, action, payload)。context 仅提供本 Bot 的稳定ID、配置路径、runtime根、插件生命周期快照与 open_private_new 回调。
- 宿主在导入页面模块之前检查不可逆运行态 guard；受限宿主拒绝，不能由页面恢复或伪造权限。
- revision 同时绑定配置、页面、操作声明和后端内容；更新后旧页面的写请求失效。
- 返回 telyincore:page:result，携带同一 id、ok 和 result 或安全失败说明。失败不自动重试写操作。
- telyincore:page:overview 只允许跳转插件总览，不开放任意导航。

本机任务提供的共享 Core 接线需单独集成；插件安装器不会改写 Core 主程序。
未具备接口的宿主应显示明确“接口尚未接入”，不能伪造空名单或显示操作成功。

## 数据保护

名单只读 SQLite 快照，不返回 cookie、登录正文或账号库路径。
删除需当前 Bot + 精确记录ID + 内容revision + 明确确认，并重新检查 disabled/unloaded/untracked/端口空闲。
删除前由宿主提供的私密文件能力建立所选记录备份，事务失败回滚；不删除游戏账号、不触碰其他Bot或其他用户。
受限生命周期、备份失败、数据漂移或未知进程状态均拒绝删除，不清除保护来换测试通过。

## 验收与回滚

python -B -m unittest discover -s tests -v：标准库合成测试；node tests/test_page.mjs：页面脚本模拟。
通用宿主、实际市场加载器与HTTP同源保护须在 Core 集成时另测；浏览器视觉与目标实例验收分别记录。
页面更新不代表重新完成真实绑定、登录或发图验收，不要求自动登录或发送测试消息。
升级保留 Bot 的 config.json、runtime 和账号备份。代码回滚不得自动回退数据库或覆盖并发配置。
