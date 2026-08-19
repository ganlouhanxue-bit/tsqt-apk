# -*- coding: utf-8 -*-
"""
协议核心层 (纯 Python, 无 GUI 依赖)
=============================================
从桌面版 FFFFF.py 原样提取, 供 Kivy/Android 版复用。

依赖: 仅标准库 (socket / struct / urllib / re / random / time)
"""
import random
import re as _re
import socket
import struct
import time
import urllib.request as _urllib

# =====================================================================
# 配置区（来自抓包）
# =====================================================================
HOST = "124.250.115.120"                          # 游戏服务器
LOGIN_PORT = 7800                                 # 登录端口
GAME_PORT = 7804                                  # 一线游戏端口

ACCOUNT = "946929328"                             # 账号（抓包中的账号ID）
MD5_KEY1 = "d07274cd7f7193a7cb34268e4af7a3a6"     # 登录密钥（抓包MD5）
MD5_KEY2 = "5b32bb083bba780285d4f0dd4c601c0f"     # 进游戏密钥（抓包MD5）

# =====================================================================
# 协议工具
# =====================================================================


def str_field(s: str) -> bytes:
    """字符串字段: [2字节长度][UTF-8数据]"""
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def build_msg(msg_type: int, payload: bytes) -> bytes:
    """报文: [2字节整包长度][2字节消息类型][payload]"""
    return struct.pack(">HH", 4 + len(payload), msg_type) + payload


def recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("连接被服务器关闭")
        buf += chunk
    return buf


def recv_msg(sock, timeout: float = 3.0):
    """读一个完整报文, 返回 (消息类型, payload); 超时返回 (None, None)"""
    sock.settimeout(timeout)
    try:
        mlen, mtype = struct.unpack(">HH", recv_exact(sock, 4))
        payload = recv_exact(sock, mlen - 4)
        return mtype, payload
    except socket.timeout:
        return None, None
    except ConnectionError:
        return -1, None


def parse_servers(pl: bytes) -> list:
    """解析区服列表 payload（兼容两种格式）

    格式A(0x00C9, 分开): 每项 = [0000][2B序号] + 4个字符串
    格式B(0x00CA, 合并): 直接 4个字符串
    """
    servers = []
    i = 0
    if pl[0:2] == b"\x00\x00":      # 格式A: 跳过 [0000][2B序号]
        i = 4

    def read_str():
        nonlocal i
        if i + 2 > len(pl):
            return ""
        ln = struct.unpack(">H", pl[i:i + 2])[0]
        i += 2
        if i + ln > len(pl):
            return ""
        v = pl[i:i + ln].decode("utf-8", errors="replace")
        i += ln
        return v

    while i + 2 <= len(pl):
        if pl[i:i + 2] == b"\x00\x00":   # 格式A: 每项前有 [0000][2B序号]
            i += 4
        sid = read_str()
        name = read_str()
        ip = read_str()
        ports = read_str()
        if not sid:
            break
        servers.append({"id": sid, "name": name, "ip": ip, "ports": ports})
    return servers


def do_policy(s, debug: bool = False) -> bytes:
    """Flash 跨域策略握手: 发 <policy-file-request/>, 收策略响应"""
    s.sendall(b"<policy-file-request/>\x00")
    s.settimeout(1.0)
    buf = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"</cross-domain-policy>" in buf or len(buf) > 512:
                break
    except socket.timeout:
        pass
    s.settimeout(None)
    if debug:
        print(f"    策略响应: {buf[:80]!r}")
    return buf


# =====================================================================
# 登录流程
# =====================================================================


def resolve_zone(text: str):
    """输入区号自动解析区服信息(抓网页配置), 返回 dict 或 None

    支持任意格式: '66' / 'q66' / 'Q1' / 'V1' / 'v37'
    - 域名 = {前缀}{区号}.t.imop.com, 默认前缀 q
    - config.xml: 服务器IP + 登录端口
    - 首页 title: 中文区名 (如 '天书奇谈 - 似水流年 - q66.t.imop.com')
    """
    text = str(text).strip()
    if not text:
        return None
    m = _re.match(r"^([a-zA-Z]*)(\d+)$", text)
    if not m:
        return None
    prefix = (m.group(1) or "q").lower()
    zone = m.group(2)
    host = f"{prefix}{zone}.t.imop.com"
    info = {"prefix": prefix, "zone": zone, "host": host}
    try:
        req = _urllib.Request(f"http://{host}/config.xml",
                              headers={"User-Agent": "Mozilla/5.0"})
        with _urllib.urlopen(req, timeout=8) as r:
            xml = r.read().decode("utf-8", errors="replace")
        m = _re.search(r"<server_ip>(.*?)</server_ip>", xml)
        if not m:
            return None
        info["ip"] = m.group(1).strip()
        m2 = _re.search(r"<server_port>(.*?)</server_port>", xml)
        info["ports"] = m2.group(1).strip() if m2 else "7800"
        info["login_port"] = info["ports"].split(",")[0].strip()
    except Exception:
        return None
    # 中文区名 (title: 天书奇谈 - 似水流年 - q66.t.imop.com)
    try:
        req = _urllib.Request(f"http://{host}/",
                              headers={"User-Agent": "Mozilla/5.0"})
        with _urllib.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="replace")
        m = _re.search(r"<title>(.*?)</title>", html, _re.S)
        if m:
            parts = m.group(1).strip().split(" - ")
            info["name"] = parts[1] if len(parts) >= 3 else parts[0]
    except Exception:
        info["name"] = f"{prefix}{zone}区"
    return info


def do_login(account: str = ACCOUNT, password: str = "", key1: str = MD5_KEY1,
             debug: bool = False, rounds: int = 2, host: str = HOST,
             port: int = LOGIN_PORT):
    """连接 host:port 完成登录, 返回 (token, 区服列表, 登录socket)

    协议(来自新抓包 1111.txt):
       - 独立连接做 Flash 跨域策略握手 (帧66/69/71)
       - 再开新连接做登录 (帧76/79): 0x0000 账号+明文密码, 0x0008 密钥, 各两轮
    """
    # 独立策略连接
    print(f"[*] 策略握手 {host}:{port} ...")
    try:
        ps = socket.create_connection((host, port), timeout=5)
        do_policy(ps, debug)
        ps.close()
    except Exception as e:
        print(f"[!] 策略连接异常: {e}")

    print(f"[*] 连接登录服务器 {host}:{port} ...")
    s = socket.create_connection((host, port), timeout=10)

    # 1) 账号 + 明文密码（真实客户端发两轮, 且每轮独立TCP包: 帧79/83）
    payload = str_field(account) + str_field(password)
    s.sendall(build_msg(0x0000, payload))
    if debug:
        print(f"    -> 0x0000 账号+密码: {payload.hex()}")
    time.sleep(0.02)                     # 强制独立TCP包

    # 2) 登录密钥: [2B len=32][32字节ASCII hex文本] (抓包确认是文本不是二进制!)
    payload = struct.pack(">H", 32) + key1.encode()
    s.sendall(build_msg(0x0008, payload))
    if debug:
        print(f"    -> 0x0008 密钥: {payload.hex()}")
    time.sleep(0.02)

    # 第二轮
    if rounds >= 2:
        payload = str_field(account) + str_field(password)
        s.sendall(build_msg(0x0000, payload))
        time.sleep(0.02)
        payload = struct.pack(">H", 32) + key1.encode()
        s.sendall(build_msg(0x0008, payload))
        if debug:
            print(f"    -> 0x0000/0x0008 第二轮已发送")

    token = None
    servers = []
    deadline = time.time() + 3        # 收到第一轮响应后尽快进游戏, token有时效
    last_got = time.time()
    while time.time() < deadline:
        mtype, pl = recv_msg(s, 1)
        if mtype is None:
            # 2秒无新消息则结束等待
            if token and time.time() - last_got > 2:
                break
            continue
        last_got = time.time()
        if debug:
            print(f"    <- 0x{mtype:04X}: {pl[:100].hex() if pl else '(空)'}")
        if mtype == 0x0001:      # 登录确认: [4字节标识][时间串][IP串][4字节结果]
            off = 4

            def rd_str():
                nonlocal off
                ln = struct.unpack(">H", pl[off:off + 2])[0]
                off += 2
                v = pl[off:off + ln].decode(errors="replace")
                off += ln
                return v

            srv_time = rd_str()
            client_ip = rd_str()
            print(f"[+] 登录确认: 服务器时间 {srv_time}  公网IP {client_ip}")
        elif mtype == 0x00CA:    # 会话令牌 + 区服列表（实际服务器格式）
            tlen = struct.unpack(">H", pl[0:2])[0]
            token = pl[2:2 + tlen]          # 保留原始字节（含尾部填充）
            _tok = token.rstrip(b'\x00').decode(errors='replace')
            print(f"[+] 会话令牌: {_tok}")
            rest = pl[2 + tlen:]
            if rest:
                servers = parse_servers(rest)
                print(f"[+] 区服列表: {len(servers)} 个")
                for sv in servers:
                    print(f"      {sv['name']}   (ID {sv['id']}, {sv['ip']}:{sv['ports']})")
        elif mtype in (0x00C8, 0x00C9):  # 兼容旧格式: token 与区服列表分开
            if mtype == 0x00C8:
                tlen = struct.unpack(">H", pl[0:2])[0]
                token = pl[2:2 + tlen]
                _tok = token.rstrip(b'\x00').decode(errors='replace')
                print(f"[+] 会话令牌: {_tok}")
            else:
                servers = parse_servers(pl)
                print(f"[+] 区服列表: {len(servers)} 个")
                for sv in servers:
                    print(f"      {sv['name']}   (ID {sv['id']}, {sv['ip']}:{sv['ports']})")
        elif mtype == 0x00E5:    # 账号信息确认
            print("[+] 账号信息确认")
        elif mtype == 0x0000:    # 服务器回执(正常消息, 不中断; 含账号/状态)
            print(f"[*] 回执 0x0000: {pl.hex() if pl else ''}")
    # 注意: 登录连接保持打开不关闭! 服务器会主动FIN, 主动关闭会导致token失效
    return token, servers, s


# =====================================================================
# 进入游戏
# =====================================================================


def parse_roles_payload(pl: bytes) -> list:
    """解析 0x000C 角色列表 payload: [2B数量][每段: 4B角色ID][名字][性别][职业][4B等级]...
    段边界 = 下一个角色ID(00 17 ad XX) 出现位置
    """
    if len(pl) < 2:
        return []
    n = struct.unpack(">H", pl[0:2])[0]
    roles = []
    pos = 2
    for _ in range(n):
        if pos + 4 > len(pl):
            break
        rid = pl[pos:pos + 4]
        i = pos + 4
        nxt = pl.find(b"\x00\x17\xad", i)
        end = nxt if nxt > 0 else len(pl)

        def rs():
            nonlocal i
            if i + 2 > end:
                return ""
            ln = struct.unpack(">H", pl[i:i + 2])[0]
            i += 2
            if i + ln > end:
                return ""
            v = pl[i:i + ln].decode("utf-8", errors="replace")
            i += ln
            return v

        name = rs()
        gender = rs()
        job = rs()
        lv = struct.unpack(">I", pl[i:i + 4])[0] if i + 4 <= end else 0
        roles.append({"id": rid, "name": name, "gender": gender, "job": job, "level": lv})
        pos = nxt if nxt > 0 else len(pl)
    return roles


def enter_game(token: bytes, server_ip: str, port: int,
               key2: str = MD5_KEY2, debug: bool = False):
    """连接游戏端口, 发送 token, 接收角色列表(选角色界面)

    返回 (socket, roles): roles 为该账号全部角色 [{id,name,gender,job,level},...]
    选角色请调用 select_role(sock, role_id)
    """
    print(f"[*] 进入游戏 {server_ip}:{port} ...")
    s = socket.create_connection((server_ip, port), timeout=10)

    payload = (struct.pack(">H", 64) + token.ljust(64, b"\x00")
               + b"\x00" * 9 + b"\x21" + key2.encode() + b"\x20")
    s.sendall(build_msg(0x0006, payload))
    if debug:
        print(f"    -> 0x0006 进游戏: {payload.hex()}")

    roles = []
    received = []
    deadline = time.time() + 8
    while time.time() < deadline:
        mtype, pl = recv_msg(s, 2)
        if mtype is None:
            continue
        if mtype == -1:
            print("[!] 游戏连接被关闭")
            return None, None
        if debug and mtype >= 0:
            print(f"    <- 0x{mtype:04X}: {pl[:80].hex() if pl else '(空)'}")
        if mtype == 0x000C:      # 角色列表
            roles = parse_roles_payload(pl)
            print(f"[+] 角色列表: {len(roles)} 个")
            for r in roles:
                print(f"      {r['name']}  {r['level']}级{r['gender']}{r['job']}  "
                      f"ID={r['id'].hex()}")
            return s, roles
        else:
            received.append(f"0x{mtype:04X}")
            if mtype == 0x0000 and pl:
                txt = pl.decode("utf-8", errors="replace").strip("\x00 ")
                if txt and any("\u4e00" <= c <= "\u9fff" for c in txt):
                    print(f"[!] 服务器提示: {txt}")
            else:
                print(f"[*] 其他消息 0x{mtype:04X}: {pl[:60].hex() if pl else '(空)'}")
    print(f"[!] 8秒内未收到角色列表(收到: {received or '无'}), "
          f"可能账号会话被占用或在其他设备在线")
    return s, roles


def select_role(s, role_id: bytes, timeout: float = 6, debug: bool = False):
    """选择角色进入游戏: 发 0x000A 心跳携带目标角色ID, 服务器确认进入

    返回 True 表示已进入游戏世界
    """
    print(f"[*] 选择角色 {role_id.hex()} ...")
    try:
        s.sendall(build_msg(0x000A, role_id))
    except Exception as e:
        print(f"[!] 发送失败: {e}")
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        mtype, pl = recv_msg(s, 2)
        if mtype is None:
            continue
        if mtype == -1:
            print("[!] 游戏连接被关闭")
            return False
        if debug:
            print(f"    <- 0x{mtype:04X}: {pl[:60].hex() if pl else '(空)'}")
        if mtype == 0x0014:      # 角色进入确认(含角色名/职业等)
            txt = pl.decode("utf-8", errors="replace")
            name = txt[4:4 + (pl[3] if len(pl) > 3 else 0)].strip("\x00")
            print(f"[+] 已进入游戏世界 (角色确认)")
            return True
    print("[!] 选择角色超时")
    return True  # 即使没等到确认也继续, 心跳本身已生效


# =====================================================================
# 挂机
# =====================================================================


def struct_pack_42(move_n):
    """移动包 0x0042 参数 (从正常在线抓包破解)"""
    return struct.pack(">IIIII", move_n, 0x000001A0,
                       0x158A33C6 + move_n * 30000,
                       random.getrandbits(32), 3 + move_n)


def idle_loop(s, duration: int, role_id: bytes, debug: bool = False):
    """挂机保活: 心跳 + 规律移动包(0x0042, 参数从正常在线抓包破解)"""
    end = time.time() + duration
    last_beat = time.time()
    last_move = time.time()
    move_n = 0
    count = 0
    while time.time() < end:
        now = time.time()
        # 心跳: 每10秒 (0x000A + 角色ID)
        if now - last_beat >= 10 and role_id:
            s.sendall(build_msg(0x000A, role_id))
            last_beat = now
            print("[挂机] 心跳")
        # 移动包: 每30秒 (0x0042, 真实参数: f2=416固定, f3每30秒+30000, f4随机)
        if now - last_move >= 30:
            f1 = move_n
            f2 = 0x000001A0
            f3 = 0x158A33C6 + move_n * 30000
            f4 = random.getrandbits(32)
            seq = 3 + move_n
            mv = struct.pack(">IIIII", f1, f2, f3, f4, seq)
            s.sendall(build_msg(0x0042, mv))
            move_n += 1
            last_move = now
            print(f"[挂机] 移动 f3={f3:#x} seq={seq}")
        # 收数据 (短超时, 不阻塞定时任务)
        mtype, pl = recv_msg(s, 1)
        if mtype == -1:
            print("[!] 游戏连接被关闭")
            return
        if mtype is not None:
            count += 1
            info = pl[:60].hex() if debug else ""
            print(f"[挂机] 收到 0x{mtype:04X} ({len(pl)}字节) {info}")
    print(f"[挂机] 结束, 共收到 {count} 条消息")
