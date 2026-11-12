# 桌面验证码服务

本目录只部署到服务器，不加入Windows/Mac安装器、程序更新归档或任何桌面构建资源。协议公用部分位于 `desktop_licensing/protocol.py`；服务端只需要Python标准库及项目已有的 `cryptography`，本轮没有新增运行依赖。验证码管理页是独立的管理员网站，支持登录后生成验证码、查看设备和管理期限；客户桌面程序及业务展示网页均不提供管理入口。原服务器本机CLI继续可用。

本轮仅完成本地代码和隔离检查，尚未部署、设置生产管理账号或签发生产验证码。生产用独立签名配置和空授权库已在源码目录外本地准备，尚未上传服务器；不要重复初始化或替换客户端已经固定的公开信任配置。服务器命令、配置修改、部署、日志查看和文件传输统一通过本机OrcaTerm客户端执行，复用并核对现有服务器连接；不改用网页终端或其他服务器操作入口。以下命令和nginx片段均为后续部署说明，本轮未执行。

## 管理页日常操作（部署后）

1. 在浏览器打开部署时确定的独立管理网址，输入管理员账号和密码，点击**登录**。
2. 点击**生成验证码**，填写**客户 / 实验室备注（选填）**，核对**最多允许几台电脑**和**使用截止时间（北京时间）**。默认10台、2026年11月30日23:59；确认后点击**生成验证码**。
3. 在“验证码已生成”窗口点击**复制验证码**，保存后交给对应客户。原码仅在这次生成后显示，关闭窗口后不能从列表找回；需要换码时可先停用原记录，再生成新码。
4. 在列表按备注或记录编号查找，点击**查看与管理**。详情显示已用/允许台数和期限，设备表显示激活、最近验证时间、**首次记录 IP**和**最近 IP**。IP只是服务器观察到的网络地址，不是电脑身份或地理位置；共享网络下不同电脑可能显示相同IP。
5. 延期时在**延长使用期限**选择新的日期和时间，点击**保存延期**。已激活电脑下次联网取得新期限；新时间不能早于原截止时间。
6. 点击**停用验证码**并确认，客户下次联网验证时停止使用。**重新启用**后，原电脑需要再次输入验证码联网验证，原设备名额保留。
7. 在设备行点击**解绑**并确认，释放一个名额并撤销该设备的当前激活。设备历史保留为“已解绑”，不删除记录。已离线的电脑最多仍可使用至原租约到期。

使用完毕点击**退出登录**。管理页面不保存可再次查看的验证码原文；请妥善保存本次复制的内容和管理员密码。上述完整操作路径已在隔离浏览器中通过，本段说明操作方式，不代表已上线；本轮未做小屏幕实际验收。

## 已确定的规则

- 验证入口位于软件首次启动时：安装器按现有流程完成安装，用户第一次打开软件后输入验证码并联网激活。Setup安装过程中无需输入验证码，也不占用设备席位。
- 每个验证码默认最多10台电脑，默认统一截止 `2026-11-30T23:59:00+08:00`，即北京时间2026年11月30日23:59整。
- 首次激活必须联网。断网宽限自上一次成功联网验证起最多24小时，且不超过验证码的总截止时间；断网或重启软件不会重新开始这24小时。客户端另有最多5分钟的同会话缓存。
- 激活前先取得90秒有效、只能完成一次的挑战，设备用自己的私钥证明持有权。挑战绑定操作、设备公钥、机器摘要和完整请求摘要。
- 以验证码和机器摘要建立唯一席位，同时限制同一设备公钥不能绑定另一机器。同机同密钥重试、正常更新及保留授权身份的程序重装不新增席位。不同机器的激活采用SQLite `BEGIN IMMEDIATE` 原子计数，第11台被拒绝。
- 服务器只保存高随机性验证码的带私有pepper的HMAC-SHA256摘要，不保存验证码原文。验证码仅在管理页生成成功窗口或本机签发命令输出中显示一次，随后无法从列表取回。
- 授权库和配置独立于业务库。服务拒绝使用源码目录、默认业务资料目录 `/var/lib/ljqc` 中的路径，也拒绝向含业务表的现有SQLite文件添加授权表。配置和数据库文件权限为0600。
- 授权库版本1升级到版本2时仅增加设备的 `first_ip` 和 `last_ip` 两列，保留验证码、席位、密钥和代次。历史设备的IP字段保持空白，之后首次成功观察到IP时才填写，不补造过去的激活地址。

## 授权服务准备（仅明确部署时执行）

以下为操作示例，不是本轮已执行步骤。将源码部署到现有服务器后，由专用的低权限服务账号在受保护的私有目录执行。当前项目沿用已经准备好的独立正式签名配置、公开信任资源及空授权库，不重复运行初始化。请勿使用业务数据库或将私钥放进Git仓库、公开下载目录、GitHub日志或客户端资源。

下面的 `init` 仅供完全新环境、建立另一套独立授权体系时参考，不用于替换本项目已固定的签名身份：

```bash
python -m licensing_server.cli init \
  --config /var/lib/ljqc-licensing/private/server.json \
  --database /var/lib/ljqc-licensing/private/licenses.sqlite3 \
  --public-config /var/lib/ljqc-licensing/public/trust.json \
  --origin https://159.75.243.120
```

`init` 不覆盖任何现有文件，显式生成独立的Ed25519授权签名密钥和验证码摘要pepper。与桌面更新签名密钥分开管理。生成的 `trust.json` 只含公开公钥、HTTPS地址和有界的请求/缓存参数，经检查后可复制为构建所需的公开信任资源。该信任资源必须在构建时固定，客户端不信任服务器响应附带的新公钥。

运行服务：

```bash
python -m licensing_server --config /var/lib/ljqc-licensing/private/server.json --port 8518
```

服务只监听 `127.0.0.1:8518`。部署时用现有nginx HTTPS入口增加固定 `/licensing/` 前缀转发，去掉此路径前缀后交给服务；保留现有业务网站和更新/下载配置。配置示例：

```nginx
location /licensing/ {
    client_max_body_size 16k;
    proxy_pass http://127.0.0.1:8518/;
    proxy_connect_timeout 3s;
    proxy_read_timeout 10s;
    proxy_send_timeout 10s;
    proxy_set_header Host $http_host;
    proxy_set_header X-LJQC-Client-IP $remote_addr;
}
```

因此客户端公开地址为 `https://159.75.243.120/licensing/v1/challenge` 等。不要把服务器直接绑定到公网，也不要转发管理命令。健康检查仅返回状态：`GET /health`。POST接口只有 `/v1/challenge`、`/v1/activate`、`/v1/renew`。接口不上传质控数据、患者信息、报告或附件。

激活服务默认不信任任何代理IP头，记录实际socket对端。只有确认nginx如上**强制覆写** `X-LJQC-Client-IP` 后，才在启动激活服务时增加 `--trust-proxy-ip`；此时仍只接受来自本机反代的单一合法IPv4/IPv6地址。不追加或透传外部自带的头，不使用通用 `X-Forwarded-For` 或客户JSON声明的IP作为可信来源。

部署应使用独立systemd服务账号，读取私有配置并持有授权库写权限，设置 `UMask=0077`、`NoNewPrivileges=true`，限制可写目录为授权私有目录。私钥和授权库须定期加密备份到非公开位置。恢复旧授权库可能恢复旧席位或撤销状态，因此恢复后须核对最近签发、解绑及启停事件，不能用业务库恢复功能处理它。

## 独立管理网站准备（仅明确部署时执行）

管理后端独立监听 `127.0.0.1:8519`，通过专用管理主机名的HTTPS入口访问。**必须与业务展示站使用不同的主机名，并固定完整管理Origin。** 仅在同一个IP或主机名上换端口不能完整隔离Cookie；也不要将管理页面挂在业务展示站的同一Origin下。

部署前先确定实际的专用管理主机名及其有效HTTPS证书。下例 `license-admin.example.com` 是占位域名，须替换为实际部署地址；私有文件路径也须核对现有配置。管理员配置不进入源码目录或业务资料目录，已有授权签名配置直接通过 `--license-config` 引用：

```bash
python -m licensing_server.admin_cli init \
  --config /var/lib/ljqc-licensing/private/admin.json \
  --license-config /var/lib/ljqc-licensing/private/server.json \
  --origin https://license-admin.example.com \
  --username admin \
  --trust-proxy-ip
```

初始化会通过交互式密码输入提示连续输入两次管理员密码，至少12个字符。不提供默认密码，不使用 `--password` 参数或把密码写入命令历史。私有配置只保存加盐密码摘要；初始化不覆盖现有文件。`--trust-proxy-ip` 只在下面的nginx专用头覆写已确认时使用，用于记录可信IP及管理登录限速；未启用时只使用真实对端IP。

启动管理后端：

```bash
python -m licensing_server.admin_cli serve \
  --config /var/lib/ljqc-licensing/private/admin.json --port 8519
```

独立nginx站点示例，实际部署时必须补齐该专用主机名的证书配置：

```nginx
server {
    listen 443 ssl;
    server_name license-admin.example.com;
    # 配置此独立主机名对应的有效HTTPS证书。
    location / {
        client_max_body_size 16k;
        proxy_pass http://127.0.0.1:8519;
        proxy_connect_timeout 3s;
        proxy_read_timeout 10s;
        proxy_send_timeout 10s;
        proxy_set_header Host $http_host;
        proxy_set_header X-LJQC-Client-IP $remote_addr;
    }
}
```

nginx转发的Host必须保留浏览器原始Host，与管理员配置中的主机名和端口一致；管理操作还校验精确Origin及CSRF令牌。管理会话使用独立的受保护Cookie，登录失败受限速约束。公网TLS与受信任的反代配置须先验证，再开放管理网址。生产环境不启用仅供隔离验证的本机HTTP模式。

管理服务与激活服务共用既有授权库，服务器配置、TLS和私有备份规则继续沿用，不复制或重新生成授权私钥。管理页资源仅属于服务器，不加入客户端、业务展示站或桌面包。

## 本机管理命令

签发一个默认10台、统一到期的验证码：

```bash
python -m licensing_server.cli issue \
  --config /var/lib/ljqc-licensing/private/server.json --label '某实验室测试'
```

输出中的 `code` 只交给对应测试者，保留 `license_id` 供后续管理。可在签发时显式指定 `--max-devices` 和包含时区的 `--expires-at`。管理命令中的ID用实际签发输出替换：

```bash
python -m licensing_server.cli list --config /var/lib/ljqc-licensing/private/server.json
python -m licensing_server.cli devices --config /var/lib/ljqc-licensing/private/server.json --license-id LICENSE_ID
python -m licensing_server.cli disable --config /var/lib/ljqc-licensing/private/server.json --license-id LICENSE_ID
python -m licensing_server.cli enable --config /var/lib/ljqc-licensing/private/server.json --license-id LICENSE_ID
python -m licensing_server.cli extend --config /var/lib/ljqc-licensing/private/server.json --license-id LICENSE_ID --expires-at '2026-12-15T23:59:00+08:00'
python -m licensing_server.cli unbind --config /var/lib/ljqc-licensing/private/server.json --license-id LICENSE_ID --device-id DEVICE_ID
```

停用/重新启用会提高最低有效许可代次，重新启用后原设备须再次输入验证码联网验证，原席位可以继续使用。延期提高许可代次但保留最低有效代次，原设备能凭有效签名和设备私钥续验，自动获得数据库中的新截止时间；不要求旧租约仍在24小时内，最多允许使用30天前签发的旧凭证作为续验身份。更早凭证需重新输入验证码。

解绑释放一个席位，旧设备密钥列入已撤销记录，旧凭证及旧密钥无法重新激活。若同台电脑丢失密钥，需先由管理员解绑，再让该电脑以新密钥和验证码联网恢复。活跃机器摘要不能被另一把公钥自动替换，避免凭公开机器摘要抢占原设备席位。

**停用或解绑不会立即收回已经签发的离线租约。** 已联网设备在下一次验证时获知变更；离线设备最多可继续至旧租约到期（最长24小时且不越过原总截止时间）。不能承诺即时踢下线，也不能让客户端把停用、满额、过期、解绑或签名错误当作网络故障继续离线。

## 本地验证

```bash
python tests/licensing_server_smoke_test.py
python tests/licensing_ip_smoke_test.py
python tests/licensing_admin_smoke_test.py
```

原服务器14项、IP记录7项及真实HTTP管理专项11项已在隔离环境通过。全部测试数据、密钥和验证码在临时目录生成并清理，不访问业务库，不启动公网服务。覆盖并发11台仅10台成功、同设备并发幂等、请求重放/过期/改写、持钥证明、授权启停/延期/解绑、默认期限上限、机密不入公开配置、授权库版本升级及IP来源可信边界。管理专项覆盖登录、会话、Host/Origin、CSRF、未登录拒绝、管理操作及安全配置。

管理页浏览器验收已通过：错误密码拒绝、正确登录；生成默认10台和11月30日23:59到期的验证码，回车不提交，明确点击后生成并成功复制；查看两台设备的首次记录IP和最近IP；延期日期改为12月15日后，回车或刷新不提交且保留草稿，点击保存后才更新期限；停用取消保持原状，确认停用及重新启用生效；解绑后已用台数从2/10变为1/10并保留历史设备及IP；备注搜索、状态筛选无匹配时正确显示空结果；退出后刷新仍要求登录。截图保存在本地 `output/license-admin-2026-11-12/devices-ip.jpg` 和 `generated-test-code.jpg`，全部为隔离假数据，未设置生产管理账号或签发正式验证码。仅使用默认浏览器尺寸，未进行小屏幕或其他viewport的实际验收。

真实公网HTTPS反代、专用管理主机名与证书、生产管理账号、服务账号与备份，以及Windows/Mac冻结实包须在后续部署/构建阶段再验证。本轮不修改客户桌面程序或业务展示网页。
