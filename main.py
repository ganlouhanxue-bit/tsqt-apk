# -*- coding: utf-8 -*-
"""
天书奇谈登录器 (Kivy / Android)
=============================================
由桌面版 FFFFF.py (tkinter) 移植:
  - 协议核心: protocol.py (纯 Python, 原样复制)
  - 界面层: Kivy 重写, 支持手机竖屏

打包: buildozer android debug
"""
import contextlib
import io
import json
import os
import queue
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

import protocol as P


def cfg_path():
    """配置存 App 私有目录 (安卓) 或脚本同目录 (PC 调试)"""
    base = os.environ.get("ANDROID_PRIVATE") or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "tsqt_config.json")


def find_cjk_font():
    """打包的中文字体: fonts/simhei.ttf, 没有则返回 None (安卓上中文会变方块)"""
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "simhei.ttf")
    return cand if os.path.exists(cand) else None


class TsqtApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = "天书奇谈登录器"
        self.msg_q = queue.Queue()
        # 会话状态
        self.token = None
        self.login_sock = None
        self.servers = []
        self.gs = None
        self.roles = None
        self.role_id = None
        self.role_info = None
        self.zone_info = None
        self.zone_input = None
        self.cur_line_idx = -1
        self._line_servers = []
        self._line_labels = []
        self._cfg_line = None
        self.state = "idle"
        self.stop_event = threading.Event()
        self.worker = None

    # ---------------- 界面 ----------------
    def build(self):
        font = find_cjk_font()
        if font:
            LabelBase.register(name="cjk", fn_regular=font)
            fn = "cjk"
        else:
            fn = None

        root = BoxLayout(orientation="vertical", padding=8, spacing=6)

        # 登录行: 区号 / 账号 / 密码
        g1 = GridLayout(cols=3, size_hint_y=None, height=52, spacing=6)
        self.e_zone = TextInput(text="q66", hint_text="区号", multiline=False,
                                font_name=fn)
        self.e_user = TextInput(hint_text="账号", multiline=False, font_name=fn)
        self.e_pass = TextInput(hint_text="密码", multiline=False,
                                password=True, font_name=fn)
        g1.add_widget(self.e_zone)
        g1.add_widget(self.e_user)
        g1.add_widget(self.e_pass)
        root.add_widget(g1)

        # 选择行: 线路 / 角色
        g2 = GridLayout(cols=2, size_hint_y=None, height=52, spacing=6)
        self.cb_line = Spinner(text="一线", values=[], font_name=fn)
        self.cb_role = Spinner(text="角色", values=[], font_name=fn)
        g2.add_widget(self.cb_line)
        g2.add_widget(self.cb_role)
        root.add_widget(g2)

        # 按钮行: 登录 / 进入游戏 / 退出游戏
        g3 = GridLayout(cols=3, size_hint_y=None, height=60, spacing=6)
        self.btn_login = Button(text="登录", font_name=fn)
        self.btn_login.bind(on_press=lambda *a: self.on_login())
        self.btn_enter = Button(text="进入游戏", disabled=True, font_name=fn)
        self.btn_enter.bind(on_press=lambda *a: self.on_enter())
        self.btn_logout = Button(text="退出游戏", font_name=fn)
        self.btn_logout.bind(on_press=lambda *a: self.on_logout())
        g3.add_widget(self.btn_login)
        g3.add_widget(self.btn_enter)
        g3.add_widget(self.btn_logout)
        root.add_widget(g3)

        # 状态
        self.lbl_status = Label(text="离线中", size_hint_y=None, height=40,
                                bold=True, font_name=fn)
        root.add_widget(self.lbl_status)

        # 日志
        sv = ScrollView()
        self.txt_log = TextInput(text="", readonly=True, multiline=True,
                                 font_name=fn, font_size="13sp")
        sv.add_widget(self.txt_log)
        root.add_widget(sv)

        # 配置回填
        self._load_cfg()
        # 状态机初始化(退出游戏始终可用)
        self._set_state("idle")
        Clock.schedule_interval(self._poll, 0.1)
        Window.bind(on_keyboard=self._on_key)
        return root

    def _on_key(self, window, key, scancode, codepoint, modifier):
        if key in (27, 1001):     # ESC / 安卓返回键
            self.stop_event.set()
            if self.gs is not None:
                try:
                    self.gs.close()
                except Exception:
                    pass
            if self.login_sock is not None:
                try:
                    self.login_sock.close()
                except Exception:
                    pass
            self.stop()
            return True
        return False

    # ---------------- 日志 & 线程桥 ----------------
    def log(self, msg):
        self.msg_q.put(("log", str(msg)))

    def post(self, kind, val=None):
        self.msg_q.put((kind, val))

    def _poll(self, dt):
        """主线程轮询消息队列(子线程 -> UI)"""
        while True:
            try:
                kind, val = self.msg_q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                text = self.txt_log.text + val + "\n"
                if len(text) > 20000:          # 日志上限, 防内存膨胀
                    text = text[-20000:]
                self.txt_log.text = text
                self.txt_log.cursor = (len(self.txt_log.text), 0)
            elif kind == "status":
                self.lbl_status.text = val
            elif kind == "lines":
                self._ui_fill_lines(val)
            elif kind == "roles":
                self._ui_fill_roles(val)
            elif kind == "roles_clear":
                self.cb_role.values = []
                self.cb_role.text = "角色"
            elif kind == "lock":
                self.e_zone.disabled = True
            elif kind == "state":
                self._set_state(val)

    def _run_capture(self, func, *args, **kwargs):
        """在子线程执行 func, 把 stdout(协议日志) 捕获进 UI 日志"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = func(*args, **kwargs)
        for line in buf.getvalue().splitlines():
            if line.strip():
                self.log(line)
        return result

    # ---------------- 状态机 (仅主线程) ----------------
    def _set_state(self, s):
        self.state = s
        # 退出游戏按钮始终可用 (任何状态都能退出)
        self.btn_logout.disabled = False
        if s == "idle":
            self.btn_login.disabled = False
            self.btn_enter.disabled = True
            self.e_zone.disabled = False
        elif s == "logging":
            self.btn_login.disabled = True
            self.btn_enter.disabled = True
            self.e_zone.disabled = True
        elif s == "ready":
            self.btn_login.disabled = True
            self.btn_enter.disabled = False
            self.e_zone.disabled = True
        elif s == "entering":
            self.btn_login.disabled = True
            self.btn_enter.disabled = True
            self.e_zone.disabled = True
        elif s == "playing":
            self.btn_login.disabled = True
            self.btn_enter.disabled = True
            self.e_zone.disabled = True

    def _ui_fill_lines(self, line_labels):
        self._line_labels = line_labels
        self.cb_line.values = line_labels
        if line_labels:
            keep = self._cfg_line if self._cfg_line in line_labels else line_labels[0]
            self.cb_line.text = keep

    def _ui_fill_roles(self, labels):
        self.cb_role.values = labels
        if labels:
            self.cb_role.text = labels[0]

    # ---------------- 登录 ----------------
    def on_login(self):
        if self.state not in ("idle", "ready"):
            return
        zone = self.e_zone.text.strip()
        user = self.e_user.text.strip()
        pw = self.e_pass.text
        if not user or not pw:
            self.log("[!] 请输入账号和密码")
            return
        self._set_state("logging")
        self.post("status", "登录中")
        threading.Thread(target=self._login_work, args=(zone, user, pw),
                         daemon=True).start()

    def _login_work(self, zone, user, pw):
        try:
            info = self.zone_info
            if not info or self.zone_input != zone:
                info = P.resolve_zone(zone)
                if not info:
                    self.log(f"[!] 区号 {zone} 解析失败, 请检查区号是否正确")
                    self.post("status", "离线中")
                    self.post("state", "idle")
                    return
                self.zone_info = info
                self.zone_input = zone
                self.log(f"[+] 解析: {info['host']} = {info.get('name')}  "
                         f"{info['ip']}:{info['login_port']}")
            token, servers, login_sock = self._run_capture(
                P.do_login, user, pw, P.MD5_KEY1, False, 2,
                info["ip"], int(info["login_port"]))
            if not token:
                self.log("[!] 登录失败")
                self.post("status", "离线中")
                self.post("state", "idle")
                return
            if self.stop_event.is_set():
                return
            self.token = token
            self.login_sock = login_sock
            # 区服按 id 去重(四线会重复返回)
            dedup = {}
            for sv in servers:
                dedup.setdefault(sv["id"], sv)
            self.servers = list(dedup.values())
            self.log(f"[*] 登录成功, 共 {len(self.servers)} 条线路")
            # 线路标签排序: 一线/二线/三线/四线
            zname = info.get("name", "")
            order = {"一线": 0, "二线": 1, "三线": 2, "四线": 3}
            items = []
            for sv in self.servers:
                ports = [p.strip() for p in sv["ports"].split(",") if p.strip()]
                label = sv["name"]
                if zname and label.startswith(zname):
                    label = label[len(zname):]
                label = label.strip() or sv["name"]
                items.append((order.get(label, 99), label,
                              {"sv": sv, "port": int(ports[0]) if ports else P.GAME_PORT}))
            items.sort(key=lambda x: x[0])
            self._line_servers = [it[2] for it in items]
            self.post("lines", [it[1] for it in items])
            self.post("lock")
            self.post("state", "ready")
            # 自动进入拉角色列表
            try:
                item = self._line_servers[0]
                self.cur_line_idx = 0
                self._enter_work(item["sv"], item["port"], 0)
            except Exception as _e:
                self.log(f"[!] 自动获取角色异常: {_e}")
        except Exception as e:
            self.log(f"[!] 异常: {e}")
            self.post("status", "离线中")
            self.post("state", "idle")

    # ---------------- 进入游戏 ----------------
    def on_enter(self):
        if self.state != "ready":
            return
        li = self._line_labels.index(self.cb_line.text) if self.cb_line.text in self._line_labels else -1
        if li < 0 or not self._line_servers or li >= len(self._line_servers):
            self.log("[!] 请选择线路")
            return
        item = self._line_servers[li]
        sv, port = item["sv"], item["port"]
        # 是否需要(重新)连接该线路
        have_conn = self.gs is not None
        same_line = self.cur_line_idx == li
        need_connect = (not have_conn) or (not self.roles) or (not same_line)
        if need_connect:
            if self.gs is not None:          # 切换线路: 先断开旧连接
                try:
                    self.gs.close()
                except Exception:
                    pass
                self.gs = None
            self.cur_line_idx = li
            self._set_state("entering")
            self.post("status", "进入中...")
            threading.Thread(target=self._enter_work, args=(sv, port, li),
                             daemon=True).start()
        else:
            label = self.cb_role.text
            role = next((r for r in self.roles
                         if f"{r['name']} {r['level']}级{r['gender']}{r['job']}" == label), None)
            if role is None:
                self.log("[!] 请选择角色")
                return
            self._set_state("entering")
            self.post("status", "进入中...")
            threading.Thread(target=self._select_work, args=(role,),
                             daemon=True).start()

    def _enter_work(self, sv, port, line_idx):
        """连接游戏服务器, 获取角色列表"""
        self.cur_line_idx = line_idx
        try:
            self.gs, self.roles = self._run_capture(
                P.enter_game, self.token, sv["ip"], port)
            if self.stop_event.is_set():
                return
            if self.gs is None or not self.roles:
                self.log("[!] 未获取到角色列表: 可能账号在其他设备/程序在线, "
                         "或该账号在本区无角色")
                self.roles = None
                self.post("state", "ready")
                return
            labels = [f"{r['name']} {r['level']}级{r['gender']}{r['job']}"
                      for r in self.roles]
            self.post("roles", labels)
            self.post("status", f"有{len(self.roles)}个角色，选择进入")
            self.log("[*] 请选择角色, 再点\"进入游戏\"确认")
            self.post("state", "ready")
        except Exception as e:
            self.log(f"[!] 异常: {e}")
            self.post("state", "ready")

    def _select_work(self, role):
        """发送选角色心跳, 进入游戏世界, 开始保活"""
        try:
            self._run_capture(P.select_role, self.gs, role["id"])
            if self.stop_event.is_set():      # 过渡期间已点退出, 放弃进入
                self.log("[*] 进入前检测到已退出, 取消")
                self.post("state", "ready")
                return
            self.role_id = role["id"]
            self.role_info = role
            self._save_cfg()                  # 进角色即自动保存配置
            self.post("status", "在线中")
            self.log("[*] 已进入游戏, 自动保持在线")
            self.stop_event.clear()
            threading.Thread(target=self._keep_alive, daemon=True).start()
            self.post("state", "playing")
        except Exception as e:
            self.log(f"[!] 异常: {e}")
            self.post("state", "ready")

    def _keep_alive(self):
        last_beat = time.time()
        last_move = time.time()
        move_n = 0
        while not self.stop_event.is_set() and self.gs is not None:
            now = time.time()
            if now - last_beat >= 10 and self.role_id:
                try:
                    self.gs.sendall(P.build_msg(0x000A, self.role_id))
                    last_beat = now
                except Exception:
                    break
            if now - last_move >= 30:
                try:
                    self.gs.sendall(P.build_msg(0x0042, P.struct_pack_42(move_n)))
                    move_n += 1
                    last_move = now
                except Exception:
                    break
            mtype, pl = P.recv_msg(self.gs, 1)
            if mtype == -1:
                self.log("[!] 连接被服务器关闭")
                break
            if mtype is not None:
                self.log(f"[消息] 0x{mtype:04X} ({len(pl)}字节)")
        if self.stop_event.is_set():
            return                            # 主动退出, 状态已由退出流程处理
        self.log("[*] 保活结束(离线)")
        self.post("status", "离线中")
        self.post("state", "ready")

    # ---------------- 退出游戏 (任何状态可用) ----------------
    def on_logout(self):
        self.log("[*] 正在退出游戏...")
        self.stop_event.set()
        if self.gs is not None:
            try:
                self.gs.close()
            except Exception:
                pass
            self.gs = None
        if self.login_sock is not None:
            try:
                self.login_sock.close()
            except Exception:
                pass
            self.login_sock = None
        # 清空会话态: 角色清空(线路保留, 重新登录自动刷新)
        self.token = None
        self.roles = None
        self.role_id = None
        self.role_info = None
        self.zone_info = None
        self.cur_line_idx = -1
        self.post("roles_clear")
        self.log("[*] 已退出整个账号, 请重新登录")
        self.post("status", "离线中")
        self.post("state", "idle")

    # ---------------- 配置存取 ----------------
    def _load_cfg(self):
        try:
            if not os.path.exists(cfg_path()):
                return
            with open(cfg_path(), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("zone"):
                self.e_zone.text = cfg["zone"]
            if cfg.get("user"):
                self.e_user.text = cfg["user"]
            if cfg.get("pass"):
                self.e_pass.text = cfg["pass"]
            self._cfg_line = cfg.get("line")
        except Exception:
            pass

    def _save_cfg(self):
        try:
            cfg = {
                "zone": self.e_zone.text.strip(),
                "user": self.e_user.text.strip(),
                "pass": self.e_pass.text,
                "line": self.cb_line.text,
            }
            with open(cfg_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.log("[*] 已保存登录配置 (大区/账号/密码/线路)")
        except Exception as e:
            self.log(f"[!] 保存配置失败: {e}")


if __name__ == "__main__":
    TsqtApp().run()
