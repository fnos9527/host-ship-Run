import os, sys, time, html, requests
import urllib.parse

API_KEY      = os.environ.get("API_KEY") or ""
SERVER_ID    = os.environ.get("SERVER_ID") or ""
COOKIE_RAW   = os.environ.get("COOKIE") or ""
EMAIL        = os.environ.get("EMAIL") or ""
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""

BASE_URL = "https://panel.host-ship.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 20

# 剩余天数 < 该值才续期；>= 该值则跳过（可用环境变量 RENEW_THRESHOLD_DAYS 覆盖）
try:
    RENEW_THRESHOLD_DAYS = int(os.environ.get("RENEW_THRESHOLD_DAYS") or 10)
except ValueError:
    RENEW_THRESHOLD_DAYS = 10

UNKNOWN_SNAP = ("（未知）", "（未知）", "（未获取到）", {})


# ───────────────────────── 工具函数 ─────────────────────────
def mask(s: str, head: int = 6, tail: int = 4) -> str:
    s = s or ""
    return f"{s[:head]}...{s[-tail:]}" if len(s) > head + tail else "***"


def mask_email(addr: str) -> str:
    addr = (addr or "").strip()
    if not addr:
        return "（未填）"
    if "@" in addr:
        name, domain = addr.split("@", 1)
        if len(name) <= 4:
            return f"{name}@{domain}"
        return f"{name[:2]}****{name[-2:]}@{domain}"
    return addr[:2] + "****" if len(addr) > 2 else "***"


def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT_ID, "text": message,
                            "parse_mode": "HTML"}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")


def notify(status: str, detail: str, server_name: str = "",
           old: str = "", new: str = "", error: str = ""):
    """status: 一句话结论；detail: 具体是什么情况"""
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    esc = html.escape
    lines = ["🎰 Host-ship 续期报告", "", f"<b>{esc(status)}</b>",
             f"📝 情况: {esc(detail)}",
             f"📧 账号: {esc(mask_email(EMAIL))}"]
    if server_name and server_name != "（未知）":
        lines.append(f"🖥️ 服务器: {esc(server_name)}")
    if new:
        lines.append(f"⏱ 续期前: {esc(old)}")
        lines.append(f"⏱ 续期后: {esc(new)}")
    elif old:
        lines.append(f"⏱ 当前: {esc(old)}")
    if error:
        lines.append(f"⚠️ 服务端返回: {esc(error[:300])}")
    lines.append(f"🕒 执行时间: {now}(北京时间)")
    send_telegram_message("\n".join(lines))


def parse_cookie_str(raw: str):
    pairs = []
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            pairs.append((name, value))
    return pairs


def build_cookie_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json",
                      "Referer": f"{BASE_URL}/"})
    for name, value in parse_cookie_str(COOKIE_RAW):
        try:
            s.cookies.set(name, value, domain="panel.host-ship.com", path="/")
        except Exception as e:
            print(f"⚠️ 装配 Cookie {name} 失败: {e}")
    return s


def api_headers():
    return {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest", "User-Agent": UA}


# ───────────────────────── 读取服务器状态 ─────────────────────────
def pick_snapshot(attrs: dict):
    """返回 (名称, 状态, 剩余时间文案, gate字典)；gate['renewal'] 为剩余天数"""
    if not isinstance(attrs, dict):
        return UNKNOWN_SNAP
    name = attrs.get("name") or attrs.get("server_name") or "（未知）"
    status = attrs.get("status") or attrs.get("state") or ""
    if isinstance(status, dict):
        status = status.get("status") or status.get("state") or ""
    renewal = attrs.get("renewal")
    try:
        renewal = int(renewal) if renewal is not None else None
    except (TypeError, ValueError):
        renewal = None
    try:
        cooldown = int(attrs.get("renewal_cooldown") or 0)
    except (TypeError, ValueError):
        cooldown = 0
    gate = {"renewable": attrs.get("renewable"),
            "renewal": renewal,
            "renewal_cooldown": cooldown,
            "renewal_locked": attrs.get("renewal_locked")}
    if renewal is not None:
        old = f"剩余{renewal}天"
    else:
        old = str(status) if status else "（未获取到）"
    return str(name), str(status or "（未知）"), str(old), gate


def fetch_snapshot():
    """先走 API_KEY，失败再走 Cookie。返回 (ok, snap, err)"""
    errs = []
    if API_KEY:
        try:
            r = requests.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}",
                             headers=api_headers(), timeout=TIMEOUT)
            if r.status_code == 200:
                return True, pick_snapshot(r.json().get("attributes", {})), ""
            errs.append(f"API GET {r.status_code}")
        except Exception as e:
            errs.append(f"API GET 异常: {e}")
    if COOKIE_RAW:
        try:
            s = build_cookie_session()
            g = s.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}", timeout=TIMEOUT)
            if g.status_code == 200:
                return True, pick_snapshot(g.json().get("attributes", {})), ""
            errs.append(f"Cookie GET {g.status_code}")
        except Exception as e:
            errs.append(f"Cookie GET 异常: {e}")
    return False, UNKNOWN_SNAP, "；".join(errs) or "未配置可用凭据"


# ───────────────────────── 执行续期 ─────────────────────────
def api_renew():
    """返回 (ok, http_code, info)"""
    try:
        r = requests.post(f"{BASE_URL}/api/client/servers/{SERVER_ID}/renew",
                          headers=api_headers(), timeout=TIMEOUT)
        print(f"📡 主链 POST renew → HTTP {r.status_code}")
        if r.status_code in (200, 201, 202, 204):
            return True, r.status_code, ""
        return False, r.status_code, r.text[:300]
    except Exception as e:
        return False, 0, f"POST 异常: {e}"


def cookie_renew():
    """返回 (ok, http_code, info)"""
    if not parse_cookie_str(COOKIE_RAW):
        return False, 0, "COOKIE 为空或格式错误"
    s = build_cookie_session()
    try:
        g = s.get(f"{BASE_URL}/api/client/servers/{SERVER_ID}", timeout=TIMEOUT)
        print(f"📡 备链 GET → HTTP {g.status_code}")
        if g.status_code in (401, 403):
            return False, g.status_code, "Cookie 已失效"
        xsrf = s.cookies.get("XSRF-TOKEN", domain="panel.host-ship.com") or ""
        xsrf = urllib.parse.unquote(xsrf)
        headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
        if xsrf:
            headers["X-XSRF-TOKEN"] = xsrf
        r = s.post(f"{BASE_URL}/api/client/servers/{SERVER_ID}/renew",
                   headers=headers, timeout=TIMEOUT)
        print(f"📡 备链 POST renew → HTTP {r.status_code}")
        if r.status_code in (200, 201, 202, 204):
            return True, r.status_code, ""
        return False, r.status_code, r.text[:300]
    except Exception as e:
        return False, 0, f"备链异常: {e}"


def is_too_early(info: str) -> bool:
    """服务端提示「还不能续」（时间未到 / 已达上限 / 冷却等），属于正常情况，不算失败"""
    low = " ".join((info or "").lower().split())
    keys = ("more than 14 days", "cannot add more", "renew limit",
            "cooldown", "too many", "locked")
    return any(k in low for k in keys)


def do_renew():
    """返回 (ok, code, info, via)"""
    if API_KEY:
        ok, code, info = api_renew()
        if ok or code not in (401, 403):
            return ok, code, info, "主链 API"
        print("🔄 API_KEY 认证失败，切换备链 Cookie 重试...")
    if COOKIE_RAW:
        ok, code, info = cookie_renew()
        return ok, code, info, "备链 Cookie"
    return False, 0, "没有可用的凭据（API_KEY 失效且未配置 COOKIE）", "无"


# ───────────────────────── 主流程 ─────────────────────────
def main():
    print("#" * 25)
    print("   Host-ship 自动续期（纯 HTTP）")
    print("#" * 25)

    if not API_KEY and not COOKIE_RAW:
        msg = "未配置 API_KEY 和 COOKIE，请在仓库 Secrets 里至少配置一个"
        print(f"❌ {msg}")
        notify("❌ 配置错误", msg)
        sys.exit(1)
    if not SERVER_ID:
        msg = "未配置 SERVER_ID（服务器 UUID），请在仓库 Secrets 里添加"
        print(f"❌ {msg}")
        notify("❌ 配置错误", msg)
        sys.exit(1)

    print(f"🎯 目标服务器: {mask(SERVER_ID, 8, 4)}")
    print(f"🔑 主链 API_KEY: {'已配置' if API_KEY else '未配置'} / "
          f"备链 COOKIE: {'已配置' if COOKIE_RAW else '未配置'}")
    print(f"📏 续期规则: 剩余 < {RENEW_THRESHOLD_DAYS} 天才续期，否则跳过")

    # 1) 读取当前剩余时间
    ok, snap, err = fetch_snapshot()
    name, status, old, gate = snap
    days = gate.get("renewal") if gate else None
    if ok:
        print(f"📋 快照：{name} / {status} / {old} / {gate}")
    else:
        print(f"⚠️ 读取服务器状态失败: {err}")

    # 2) 剩余时间够长 → 不续期
    if isinstance(days, int) and days >= RENEW_THRESHOLD_DAYS:
        detail = f"剩余 {days} 天，不少于 {RENEW_THRESHOLD_DAYS} 天，今天无需续期"
        print(f"⏭️ {detail}")
        notify("⏭️ 无需续期", detail, server_name=name, old=old)
        print("🏁 执行完毕：无需续期")
        return

    # 3) 剩余时间不足，但面板标记当前不可续
    if isinstance(days, int):
        blocked = ""
        if gate.get("renewable") is False:
            blocked = "面板标记 renewable=false"
        elif gate.get("renewal_locked") is True:
            blocked = "面板标记 renewal_locked=true（已锁定）"
        elif (gate.get("renewal_cooldown") or 0) > 0:
            blocked = f"处于冷却中（renewal_cooldown={gate['renewal_cooldown']}）"
        if blocked:
            detail = f"剩余仅 {days} 天，但{blocked}，暂时无法续期，请留意是否需要手动处理"
            print(f"⚠️ {detail}")
            notify("⚠️ 需要续期但暂不可续", detail, server_name=name, old=old)
            print("🏁 执行完毕：暂不可续期")
            return
        print(f"⏰ 剩余 {days} 天，低于 {RENEW_THRESHOLD_DAYS} 天，开始续期")
    else:
        print("⚠️ 未能获取剩余天数，直接尝试续期（服务端会拒绝过早的续期）")

    # 4) 执行续期
    ok, code, info, via = do_renew()

    if ok:
        new = old
        ok2, snap2, _ = fetch_snapshot()
        if ok2:
            name, _st, new, _g = snap2
        print(f"✅ 续期成功（{via}），{old} → {new}")
        notify("✅ 续期成功", f"通过{via}续期成功", server_name=name,
               old=old, new=new if new != old else f"{new}（已提交，待面板刷新）")
        print("🏁 执行完毕：续期成功")
        return

    # 5) 服务端说「还不能续」→ 正常情况，不报错
    if is_too_early(info):
        detail = "服务端提示暂时不能续期（时间未到 / 已达续期上限），属于正常情况，下次再检查"
        print(f"⏭️ {detail}: {info}")
        notify("⏭️ 无需续期", detail, server_name=name, old=old, error=info)
        print("🏁 执行完毕：无需续期")
        return

    # 6) 真正的失败
    if code in (401, 403, 419):
        detail = "认证失败，API_KEY 或 COOKIE 可能已失效，请重新获取并更新 Secrets"
    elif code == 0:
        detail = "网络异常或凭据缺失，请查看工作流日志"
    else:
        detail = f"续期请求被拒绝（HTTP {code}），原因未知，请查看工作流日志"
    print(f"❌ 续期失败（{via}）: HTTP {code} {info}")
    notify("❌ 续期失败", detail, server_name=name, old=old, error=f"HTTP {code} {info}")
    print("🏁 执行完毕：续期失败")
    sys.exit(1)


if __name__ == "__main__":
    main()
